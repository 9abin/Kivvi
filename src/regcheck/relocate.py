"""끊긴 인용의 대상 재탐색 (화면: 「옮겨 간 곳 찾기」): 인용한 조가 없거나 내용이 다를 때, 원래 가리키려던 조를 다시 찾는다.

  relocate(edge, regs, index, embed, rerank, scope="cascade")
    → {"candidates": [{reg_id, reg_name, article_id, label, title, score, tier, why}], "found": bool, "query"}

단서를 확실한 것부터 쓴다 (근거 종류):
  문서 명시   대상 규정 원문의 이동 표시 — 「[종전 제X조에서 이동]」이 붙은 조, 「[종전 제X조는 제Y조로 이동]」의 제Y조
  모델 추정   인용 문장(인용 표현은 가림)과 조문을 reranker로 비교한 순위
범위 (scope, 리포트 45의 비교 실험):
  cascade  인용한 규정의 조를 먼저 보고, 1위 점수가 MIN_SCORE 아래일 때만 90개 규정 전체 벡터 검색 상위 SEARCH_K개를 더함 (기본)
  target   인용한 규정 안에서만 (기존 의미 검사의 대안 제시와 같음)
  corpus   전체 벡터 검색만 / union  둘을 처음부터 합침
인용한 규정 안에서 맞는 조를 못 찾으면 found=False → 「근거 소멸 가능성」으로 알린다 (내용 자체가 없어졌을 수 있음).
판정이 아니라 담당자가 볼 후보다 (AGENTS §3). 인용하는 조 자신은 후보에서 뺀다.
"""

from __future__ import annotations

import re

SEARCH_K = 40
MIN_GAP = 0.2        # 1위가 2위보다 이만큼 높아야 「확실」 (정답 원장 19건: 점수 0.3만 보면 오답 1위 11건 중 9건이 통과, 간격 0.2를 더하면 3건 — 리포트 48)
MIN_SCORE = 0.3      # eval_relocate.py로 정함: 정답 있는 사례 22/25 찾음, 판단 불가 3/4 못 찾음 (리포트 45)
MAX_CHARS = 700


def article_text(a: dict, n: int = MAX_CHARS) -> str:
    body = a.get("norm") or a.get("text", "")
    return f"{a['label']}({a['title']}) {body.split(chr(10), 1)[-1]}"[:n]


def citing_text(regs: dict, edge: dict) -> str:
    """인용 문장 (인용 표현은 [인용]으로 가림 — 번호가 아니라 내용으로 찾게)."""
    reg = regs.get(edge["src_reg"])
    text = edge.get("context", "")
    if reg:
        for a in reg["articles"] + [x for ad in reg.get("addenda", []) for x in ad["articles"]]:
            for it in a["items"]:
                if it["id"] == edge["src"]:
                    text = it["norm"]
    return text.replace(edge["raw"], "[인용]") if edge.get("raw") else text


def src_article(edge: dict) -> str:
    return edge["src"].rsplit("-", 3)[0] if "-" in edge["src"] else edge["src"]


def moved_from(reg: dict, jo: int) -> list[dict]:
    """대상 규정 원문의 이동 표시로 찾은 옛 제{jo}조의 지금 자리 (문서 명시).
    「[종전 제{jo}조에서 이동 …]」이 붙은 조, 「[종전 제{jo}조는 제Y조로 이동 …]」의 제Y조.
    (261015: 「종전 제21조는 제22조로 이동」을 「제21조에서 이동」으로 잘못 읽던 것을 고침)"""
    num = rf"제\s*{jo}\s*조(?!\s*의\s*\d)"
    came = re.compile(rf"종전\s*{num}\s*에서\s*이동")
    went = re.compile(rf"종전\s*{num}\s*는\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?\s*로\s*이동")
    live = [a for a in reg["articles"] if not a.get("deleted")]
    found = {a["id"]: a for a in live if came.search(a.get("text", "")) or any(came.search(n) for n in a.get("amend_notes", []))}
    for a in live:
        for m in went.finditer(a.get("text", "")):
            label = f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
            b = next((x for x in live if x["label"] == label), None)
            if b:
                found[b["id"]] = b
    return list(found.values())


def _corpus(arts: dict, query: str, index, embed, regs: dict, skip: str, exclude_reg: set | None = None) -> None:
    """90개 규정 전체 벡터 검색 상위 SEARCH_K개 조를 arts에 더한다 (exclude_reg 규정은 뺌)."""
    import numpy as np

    vecs, chunks = index
    q = embed([query])[0]
    by_id = {a["id"]: (r, a) for r in regs.values() for a in r["articles"] if not a.get("deleted")}
    n = 0
    for i in np.argsort(-(vecs @ q)):
        aid = chunks[i]["article_id"]
        if aid == skip or aid in arts or aid not in by_id or (exclude_reg and by_id[aid][0]["reg_id"] in exclude_reg):
            continue
        arts[aid] = by_id[aid]
        n += 1
        if n >= SEARCH_K:
            break


def _rank(arts: dict, query: str, rerank) -> list[tuple[float, str]]:
    if not arts:
        return []
    ids = list(arts)
    return sorted(zip(rerank(query, [article_text(arts[i][1]) for i in ids]), ids), key=lambda x: -x[0])


def relocate(edge: dict, regs: dict, index, embed, rerank, scope: str = "cascade", k: int = 3) -> dict:
    """index: (vecs, chunks) — out/vector/{모델}, embed(texts) → 정규화 벡터, rerank(query, texts) → 점수 목록."""
    skip = src_article(edge)
    tgt = regs.get(edge.get("target_reg") or "")
    out: list[dict] = []
    jo = (edge.get("target_ref") or {}).get("jo")
    if tgt and jo:
        for a in moved_from(tgt, jo):
            out.append({"reg_id": tgt["reg_id"], "reg_name": tgt["name"], "article_id": a["id"], "label": a["label"], "title": a["title"],
                        "score": 1.0, "tier": "문서 명시", "why": f"원문 이동 표시: 옛 제{jo}조가 지금 {a['label']}"})
    query = citing_text(regs, edge)
    arts: dict[str, tuple[dict, dict]] = {}
    if tgt and scope in ("target", "union", "cascade"):
        arts.update({a["id"]: (tgt, a) for a in tgt["articles"] if not a.get("deleted") and a["id"] != skip})
    if scope in ("corpus", "union"):
        _corpus(arts, query, index, embed, regs, skip)
    ranked = _rank(arts, query, rerank)
    # 단계적: 인용한 규정 안에서 충분히 맞는 조가 없을 때만 다른 규정을 본다 (정답 원장 25건은 모두 인용한 규정 안 — 리포트 45)
    if scope == "cascade" and (not ranked or ranked[0][0] < MIN_SCORE):
        more: dict = {}
        _corpus(more, query, index, embed, regs, skip, exclude_reg={edge["src_reg"], tgt["reg_id"] if tgt else ""})
        ranked += _rank(more, query, rerank)
        arts.update(more)
    for s, aid in ranked:
        if len(out) >= k:
            break
        if any(c["article_id"] == aid for c in out):
            continue
        r, a = arts[aid]
        other = not (tgt and r["reg_id"] == tgt["reg_id"])
        out.append({"reg_id": r["reg_id"], "reg_name": r["name"], "article_id": aid, "label": a["label"], "title": a["title"],
                    "score": round(float(s), 3), "tier": "모델 추정",
                    "why": "인용 문장과 내용이 가장 비슷한 조" + (" (다른 규정 — 인용한 규정 안에서 맞는 조를 못 찾음)" if other else "")})
    in_target = [c for c in out if tgt and c["reg_id"] == tgt["reg_id"]]
    found = bool(out) and (out[0]["tier"] == "문서 명시" or bool(in_target and in_target[0]["score"] >= MIN_SCORE))
    # 확실 = 문서 명시이거나, 점수(바닥)와 1위-2위 간격(상대)이 둘 다 충분. 벡터·reranker는 「없음」을 돌려주지 않으므로 바닥이 필요하고
    # 후보가 여럿이면(간격 작음) 1위를 믿지 못하므로 간격이 필요하다
    model_ranked = [x for x in ranked if arts[x[1]][0]["reg_id"] == (tgt["reg_id"] if tgt else "")]
    gap = (model_ranked[0][0] - model_ranked[1][0]) if len(model_ranked) > 1 else 1.0
    confident = bool(out) and (out[0]["tier"] == "문서 명시" or (found and gap >= MIN_GAP))
    return {"candidates": out, "found": found, "confident": confident, "gap": round(float(gap), 3), "query": query}
