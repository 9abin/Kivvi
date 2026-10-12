"""명칭 일괄 변경(직제 개편) 에이전트: 명칭 변경표 → 규정별 개정문·대비표.

  2026.08.28 실제 사례: 하루에 15개 규정 35개 항에서 직책·조직 명칭(부기관장, 지역투자센터 등)을 바꿨다.
  찾기 → 분류 → 개정문 작성 → 적용·검증 순서. LLM 없음.

분류
  자동      명칭 앞이 한글이 아님(공백·문장부호·괄호 등) → 「제N조제M항 중 "A"를 "B"로 한다」, 조 제목은 「제N조의 제목 중 …」
  확인 필요  명칭 앞이 한글 → 더 긴 다른 낱말의 일부일 수 있음 (「상임이사」를 바꿀 때 「비상임이사」). 같은 항에 섞이면 그 항 전체를 확인 필요로
  수작업    별표·별지·부칙 안 (개정문 적용기가 다루지 않는 단위)
내규관리규정 제8조③(명칭이 바뀐 부서·기관은 새 이름으로 읽음)이 있어도 문안 정비는 필요하다.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .agent import fix_sentence
from .amend import AmendError, build_changeset
from .normalize import normalize

RE_HANGUL = re.compile(r"[가-힣]")


def _token_around(text: str, start: int, end: int) -> str:
    """명칭이 들어 있는 한글 낱말 전체 (예: 「비상임이사」)."""
    s, e = start, end
    while s > 0 and RE_HANGUL.match(text[s - 1]):
        s -= 1
    while e < len(text) and RE_HANGUL.match(text[e]) and e - end < 6:
        e += 1
    return text[s:end]


def find(regs: dict[str, dict], mapping: dict[str, str]) -> list[dict]:
    """모든 출현 위치 [{reg_id, reg_name, unit, article, para, field, old, new, kind, token, context}]"""
    out = []
    for r in regs.values():
        for a in r["articles"]:
            if a.get("deleted"):
                continue
            fields = [("title", 0, a["title"] or "")] + [("item", it["path"][0], it["norm"]) for it in a["items"]]
            for field, para, text in fields:
                for old, new in mapping.items():
                    for m in re.finditer(re.escape(old), text):
                        prev = text[m.start() - 1] if m.start() else ""
                        kind = "auto" if not RE_HANGUL.match(prev) else "check"
                        out.append({"reg_id": r["reg_id"], "reg_name": r["name"], "unit": "본문", "article": a["label"],
                                    "para": para, "field": field, "old": old, "new": new, "kind": kind,
                                    "token": _token_around(text, m.start(), m.end()),
                                    "context": text[max(0, m.start() - 40): m.end() + 40]})
        for ad in r.get("addenda", []):
            for a in ad["articles"]:
                for it in a["items"]:
                    for old, new in mapping.items():
                        if old in it["norm"]:
                            out.append({"reg_id": r["reg_id"], "reg_name": r["name"], "unit": "부칙", "article": a["label"],
                                        "para": it["path"][0], "field": "item", "old": old, "new": new, "kind": "manual",
                                        "token": old, "context": it["norm"][:120]})
        for ap in r.get("appendices", []):
            t = normalize(ap["text"])
            for old, new in mapping.items():
                n = t.count(old)
                if n:
                    i = t.find(old)
                    out.append({"reg_id": r["reg_id"], "reg_name": r["name"], "unit": ap["kind"], "article": ap["id"].split(":")[1],
                                "para": 0, "field": "appendix", "old": old, "new": new, "kind": "manual", "token": old,
                                "count": n, "context": t[max(0, i - 40): i + 60]})
    return out


def build(regs: dict[str, dict], mapping: dict[str, str]) -> dict:
    hits = find(regs, mapping)
    # 같은 항에 확인 필요가 섞이면 그 항의 자동 출현도 확인 필요로 (「중 "A"를 …」은 항 안의 A를 모두 바꾸므로)
    mixed = {(h["reg_id"], h["article"], h["para"], h["field"], h["old"]) for h in hits if h["kind"] == "check"}
    for h in hits:
        if h["kind"] == "auto" and (h["reg_id"], h["article"], h["para"], h["field"], h["old"]) in mixed:
            h["kind"] = "check"
            h["note"] = "같은 항에 다른 낱말 속 출현이 섞여 있어 자동으로 바꾸지 않음"
    by_reg: dict[str, list[dict]] = defaultdict(list)
    for h in hits:
        if h["kind"] == "auto":
            by_reg[h["reg_id"]].append(h)
    packages = []
    for rid, hs in sorted(by_reg.items(), key=lambda kv: regs[kv[0]]["name"]):
        units: dict[tuple, list[tuple[str, str]]] = defaultdict(list)
        for h in hs:
            units[(h["article"], h["para"], h["field"])].append((h["old"], h["new"]))
        sents = []
        for (label, para, field), pairs in units.items():
            pairs = list(dict.fromkeys(pairs))
            if field == "title":
                sents.append(fix_sentence(f"{label}의 제목", pairs))
            else:
                sents.append(fix_sentence(label + (f"제{para}항" if para else ""), pairs))
        entry = {"reg_id": rid, "reg_name": regs[rid]["name"], "amendment": "\n".join(sents), "count": len(hs), "status": "ok"}
        try:
            cs, new = build_changeset(regs[rid], entry["amendment"], f"rename-{rid}")
            entry["rows"] = cs["rows"]
            left = sum(h_text.count(old) for a in new["articles"] for h_text in [a["title"]] + [it["norm"] for it in a["items"]]
                       for old in mapping)
            entry["left_after"] = left   # 적용 후에도 남은 옛 명칭 (확인 필요분 포함)
        except AmendError as e:
            entry["status"] = f"적용 못함: {e}"
        packages.append(entry)
    checks = [h for h in hits if h["kind"] == "check"]
    manual = [h for h in hits if h["kind"] == "manual"]
    return {"mapping": mapping, "total": len(hits), "auto": sum(h["kind"] == "auto" for h in hits),
            "packages": packages, "check": checks, "manual": manual,
            "regs_touched": len({h["reg_id"] for h in hits})}
