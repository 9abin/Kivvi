"""점검 결과를 한 형식(finding)으로 모은다. 평가(정답 원장과 대조)와 검토 화면이 이 형식을 쓴다.

위치는 원문에 고정한다: 규정ID · 단위(본문·부칙·별표·별지) · 원문 조 표기 · 항 · 인용 원문.
파이프라인별 ID(우리 조문 ID, 가빈 청크 ID)는 연결 정보일 뿐이다 (리포트 261007-*-12).
"""

from __future__ import annotations

import re

# 인용 엣지 상태 → 오류 종류 (02-specs 오류 유형과 대응)
EDGE_TYPE = {
    "missing_article": "인용_대상없음",
    "missing_item": "인용_대상없음",
    "missing_chapter": "인용_대상없음",
    "missing_appendix": "인용_대상없음",
    "deleted_target": "근거소멸",
    "title_mismatch": "인용_제목불일치",
    "name_mismatch": "규정명_불일치",
    "unknown_regulation": "규정명_미확인",
}


def locate(src: str) -> dict:
    """우리 출발 ID → 원문 위치. 24125:3-1-1-0 / 24010:부칙29:2-0-0-0 / 40209:별지제3호서식"""
    reg_id, rest = src.split(":", 1)
    if rest.startswith(("별지", "별표")):
        return {"reg_id": reg_id, "unit": rest[:2], "article": rest, "para": 0}
    unit = "본문"
    if rest.startswith("부칙"):
        unit, rest = "부칙", rest.split(":", 1)[1]
    jo, *path = rest.split("-")
    label = f"제{jo}조" if "의" not in jo else f"제{jo.split('의')[0]}조의{jo.split('의')[1]}"
    return {"reg_id": reg_id, "unit": unit, "article": label, "para": int(path[0]) if path else 0}


def from_edges(edges: list[dict]) -> list[dict]:
    out = []
    for e in edges:
        kind = EDGE_TYPE.get(e["status"])
        if not kind:
            continue
        out.append({
            **locate(e["src"]),
            "quote": e["raw"],
            "context": e["context"],
            "type": kind,
            "method": "rule",
            "suggest": "",
            "note": e["note"],
            "src_id": e["src"],
        })
    return out


def from_semantic(rows: list[dict]) -> list[dict]:
    """scripts/semantic_check.py 의 '대상 불일치 후보' (설명구 + reranker)."""
    out = []
    for r in rows:
        # 설명구 근거가 있는 후보는 우선순위 '높음', 벡터 1단계만 걸린 것은 '낮음'
        if r.get("stage2") not in ("대상 불일치 후보", "확인 필요(낮음)"):
            continue
        high = r["stage2"] == "대상 불일치 후보"
        e, s = r["edge"], r["suggest"]
        sug = s["chapter"].split()[0] if e["target_type"] == "chapter" and s.get("chapter") else s["label"]
        out.append({
            **locate(e["src"]),
            "quote": e["raw"],
            "context": e["context"],
            "type": "인용_대상불일치",
            "method": "semantic",
            "suggest": sug,
            "priority": "높음" if high else "낮음",
            "note": (f"설명구 「{r['desc']}」, 인용 조 순위 {r['desc_rank']}/{r['n_articles']}" if high
                     else f"벡터 1단계: 인용 조 순위 {r['rank']} (설명구 없음)"),
            "src_id": e["src"],
        })
    return out


CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def duplicate_paragraphs(regs: dict[str, dict]) -> list[dict]:
    """같은 조 안에서 항 번호(①②…)가 다시 나오거나 거꾸로 가는 경우. 원문 표기 문제 (원문은 고치지 않음)."""
    out = []
    for reg in regs.values():
        for a in reg["articles"]:
            seen: list[int] = []
            for it in a["items"]:
                p = it["path"][0]
                # (항, 0, 0) 항목은 모두 새 항의 시작이다 (이어지는 줄은 앞 항목에 붙으므로). 연속 중복(② ②)도 잡는다
                # 조문 안 표의 칸(「5점」, 「2점 이내」)은 항이 아니므로 짧은 항목은 뺀다
                if p and it["path"][1:] == [0, 0] and len(it["text"]) >= 12:
                    if p in seen:
                        out.append({
                            "reg_id": reg["reg_id"], "unit": "본문", "article": a["label"], "para": p,
                            "quote": f"{CIRCLED[p - 1]} {it['text'][:30]}",
                            "context": it["text"],
                            "type": "항번호_중복", "method": "rule", "suggest": "",
                            "note": f"{a['label']}에서 {CIRCLED[p - 1]}가 다시 나옴 (앞선 항: {''.join(CIRCLED[x - 1] for x in seen)})",
                            "src_id": it["id"],
                        })
                    seen.append(p)
    return out


def same_place(gold: dict, f: dict) -> bool:
    """정답 한 건과 탐지 한 건이 같은 위치인가: 같은 규정·단위·조, 인용 원문이 서로 겹침. 항은 둘 다 있을 때만 비교."""
    if gold["reg_id"] != f["reg_id"] or gold["unit"] != f["unit"]:
        return False
    if re.sub(r"\s", "", gold["article"]) != re.sub(r"\s", "", f["article"]):
        return False
    if gold.get("para") and f.get("para") and gold["para"] != f["para"] and gold.get("type") != "항번호_중복":
        return False
    q1, q2 = re.sub(r"\s", "", gold.get("quote", "")), re.sub(r"\s", "", f.get("quote", ""))
    return not q1 or not q2 or q1 in q2 or q2 in q1 or q1[:6] in re.sub(r"\s", "", f.get("context", ""))


# 정답 종류 → 탐지로 인정할 우리 결과 종류
COMPATIBLE = {
    "인용_대상없음": {"인용_대상없음", "인용_제목불일치", "인용_대상불일치"},
    "인용_대상불일치": {"인용_대상불일치", "인용_제목불일치", "인용_대상없음"},
    "인용_제목불일치": {"인용_제목불일치", "인용_대상불일치"},
    "규정명_불일치": {"규정명_불일치", "규정명_미확인"},
    "근거소멸": {"근거소멸", "인용_대상없음"},
    "항번호_중복": {"항번호_중복"},
    # 아래 종류는 아직 탐지기가 없거나(규정간_충돌·구명칭·법령) 결과 형식이 달라 채점되면 놓침으로 나온다
    "조번호_이상": {"조번호_이상"},
    "구명칭_용어": {"구명칭_용어"},
    "규정간_충돌": {"규정간_충돌"},
    "법령_불일치": {"법령_불일치"},
    "표기_오류": {"표기_오류"},
}
OUT_OF_SCOPE = {"오탈자"}  # 맞춤법 검사는 에이전트 범위 밖 (결정 전)
