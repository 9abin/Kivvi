"""용어 정의-사용 관계: 인용이 없어도 이어지는 영향 — 정의를 바꾸면 그 용어를 쓰는 곳이 영향을 받는다.

  definitions(reg)                       정의 조(제목에 「정의」)의 항·호에서 정의 용어 → [{term, article_id, label, text}]
  uses(term, reg, skip_article)           그 규정에서 용어가 쓰인 조 → [(조, 항, 문장)]
  impacts(old_reg, new_reg, regs, rels)   개정안에서 정의가 바뀌거나 생기거나 없어진 용어 → 영향 후보 (change_check 형식)

정의 문장 모양 (90개 규정 정의 조 61개에서 확인): 「"X"란 …」, 「"X"이란 …」, 「"X"(이)라 함은 …」, 항 첫머리의 「X(이)라 함은 …」.
용어 찾기: 앞 글자가 한글·영문·숫자이면 더 긴 낱말의 일부로 보고 뺀다 (「직원」은 「임직원」 안에서 찾지 않음, rename과 같은 경계 규칙).
범위: 정의 조는 「이 규정에서 사용하는 용어」라고 적으므로 정의는 그 규정 안에 적용된다.
  같은 규정 안 사용          → 확인 필요(중간): 바뀐 정의가 이 조의 뜻을 바꾸는지
  다른 규정이 같은 용어를 정의 → 확인 필요(낮음): 두 정의 비교 (용어 정의 불일치 후보)
  다른 규정이 정의 없이 사용   → 그 규정이 이 규정을 인용하면 중간, 아니면 낮음 (규정마다 카드 하나, 흔한 용어는 뺌)
판정이 아니라 후보다 (AGENTS §3).
"""

from __future__ import annotations

import re

# 「"X"(이하 'Y'라 한다)이라 함은」처럼 따옴표 뒤에 줄임말 괄호가 끼기도 한다
RE_QUOTED = re.compile(r"[\"“”']\s*([^\"“”']{1,25}?)\s*[\"“”']\s*(?:\([^)]*\)\s*)?(?:이)?(?:란|라\s*함은)")
# 항 첫머리의 따옴표 없는 정의. 「본 규칙에서 직원이라 함은」처럼 범위 문구가 앞에 오기도 한다
RE_BARE = re.compile(r"^\s*(?:\d+\.\s*)?(?:(?:이|본)\s*\S+에서\s*)?([가-힣A-Za-z·][가-힣A-Za-z0-9· ]{0,14}?)\s*(?:이)?(?:란|라\s*함은)\s")
MAX_SAME = 5           # 같은 규정 안에서 이보다 많이 쓰면 조마다 카드를 만들지 않고 한 장으로 묶음
MAX_REGS = 8           # 이보다 많은 규정이 쓰는 용어는 다른 규정 카드를 만들지 않음 (흔한 말)


def norm_term(t: str) -> str:
    """「감사(監査)」 → 「감사」, 앞뒤 공백 정리."""
    return re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", "", t)).strip()


def is_definition_article(a: dict) -> bool:
    return bool(a.get("title")) and "정의" in a["title"] and not a.get("deleted")


def definitions(reg: dict) -> list[dict]:
    out = []
    for a in reg["articles"]:
        if not is_definition_article(a):
            continue
        for it in a["items"]:
            if it.get("deleted"):
                continue
            s = it["norm"]
            m = RE_QUOTED.search(s[:60]) or RE_BARE.match(s)
            if not m:
                continue
            t = norm_term(m.group(1))
            if len(t) >= 2 and not t.startswith(("이 ", "본 ")):
                out.append({"term": t, "article_id": a["id"], "label": a["label"], "text": s})
    return out


# 용어 뒤에는 조사(여럿 이어져도 됨)나 「하다·되다」 활용만 온다. 다른 명사가 붙으면 다른 낱말로 본다
# (「기록물등록대장」의 「기록물」, 「직책수당」의 「직책」은 그 용어가 아님 — 261015 표본 판정에서 나온 잡음)
# 「인」(직원인 경우)은 조사로 보지 않는다: 「준법감시」가 「준법감시인」에 걸림 (261015 표본 판정)
_PARTICLE = r"(?:은|는|이|가|을|를|의|에|에서|에게|께|으로|로|과|와|도|만|및|등|까지|부터|이나|나|이며|며|이라|라|란|이란|으로서|로서|으로써|로써|이다|다|마다|들|별|상|중|내|외|간|처럼|보다|만큼)"
_AFTER = rf"(?=$|[^가-힣]|{_PARTICLE}+(?![가-힣])|(?:하|되|시키|받)[가-힣]*)"


def _pattern(term: str) -> re.Pattern:
    return re.compile(r"(?<![가-힣A-Za-z0-9])" + re.escape(term).replace(r"\ ", r"\s*") + _AFTER)


def uses(term: str, reg: dict, skip_article: set[str] | frozenset = frozenset()) -> list[tuple[dict, int, str]]:
    """그 규정 본문에서 용어가 쓰인 곳 (조마다 처음 한 번)."""
    pat = _pattern(term)
    out = []
    for a in reg["articles"]:
        if a.get("deleted") or a["id"] in skip_article or is_definition_article(a):
            continue
        for it in a["items"]:
            if not it.get("deleted") and pat.search(it["norm"]):
                out.append((a, it["path"][0], it["norm"]))
                break
    return out


def _defmap(reg: dict) -> dict[str, str]:
    m: dict[str, list[str]] = {}
    for d in definitions(reg):
        m.setdefault(d["term"], []).append(d["text"])
    return {t: " / ".join(v) for t, v in m.items()}


def changed_terms(old_reg: dict, new_reg: dict) -> dict[str, tuple[str, str]]:
    """정의가 바뀌거나 생기거나 없어진 용어 → (현행 정의, 개정안 정의). 없으면 빈 문자열."""
    o, n = _defmap(old_reg), _defmap(new_reg)
    return {t: (o.get(t, ""), n.get(t, "")) for t in set(o) | set(n) if o.get(t, "") != n.get(t, "")}


def impacts(old_reg: dict, new_reg: dict, regs: dict[str, dict], rels: list[dict]) -> list[dict]:
    rid = old_reg["reg_id"]
    terms = changed_terms(old_reg, new_reg)
    if not terms:
        return []
    def_label = next((d["label"] for d in definitions(old_reg) + definitions(new_reg) if d["term"] in terms), "정의 조")
    linked = {r["src"].split(":")[0] for r in rels if str(r.get("target", "")).split(":")[0] == rid}   # 이 규정을 인용하는 규정
    others_def = {orid: _defmap(r) for orid, r in regs.items() if orid != rid}
    out = []
    for term, (old_def, new_def) in sorted(terms.items()):
        what = ("정의가 없어짐" if not new_def else "정의가 새로 생김" if not old_def else "정의가 바뀜")
        target = f"{old_reg['name']} {def_label} 「{term}」"
        # 같은 규정 안
        same = uses(term, new_reg)
        if len(same) <= MAX_SAME:
            for a, para, sent in same:
                out.append({"category": "review", "reg_id": rid, "reg_name": old_reg["name"], "unit": "본문", "article": a["label"],
                            "para": para, "quote": term, "context": sent, "path": "정의 용어", "target": target,
                            "priority": "중간", "handling": "판단 가능",
                            "reason": f"이 규정의 정의 용어 「{term}」의 {what}. 이 조의 「{term}」 뜻이 달라지는지 확인",
                            "definition": {"old": old_def, "new": new_def}})
        else:   # 많이 쓰는 용어는 한 장으로 (검토 단위 = 「이 규정 안 N곳에 미치는 영향」)
            labels = [a["label"] for a, _, _ in same]
            out.append({"category": "review", "reg_id": rid, "reg_name": old_reg["name"], "unit": "본문", "article": labels[0],
                        "para": same[0][1], "quote": term, "context": same[0][2], "path": "정의 용어", "target": target,
                        "priority": "중간", "handling": "판단 가능", "definition": {"old": old_def, "new": new_def}, "articles": labels,
                        "reason": f"이 규정의 정의 용어 「{term}」의 {what}. 이 규정에서 「{term}」를 쓰는 {len(labels)}곳의 뜻이 함께 달라짐: "
                        + ", ".join(labels[:30]) + (" 외" if len(labels) > 30 else "")})
        # 다른 규정
        using = {orid: uses(term, r) for orid, r in regs.items() if orid != rid}
        using = {k: v for k, v in using.items() if v}
        for orid, hits in sorted(using.items()):
            r2 = regs[orid]
            own = others_def[orid].get(term)
            if own:
                out.append({"category": "review", "reg_id": orid, "reg_name": r2["name"], "unit": "본문",
                            "article": next((d["label"] for d in definitions(r2) if d["term"] == term), ""), "para": 0,
                            "quote": term, "context": own, "path": "정의 용어(다른 규정 정의)", "target": target,
                            "priority": "낮음", "handling": "판단 가능", "definition": {"old": old_def, "new": new_def, "other": own},
                            "reason": f"「{r2['name']}」도 「{term}」를 스스로 정의함. 개정안의 정의와 뜻이 맞는지 비교 (용어 정의 불일치 후보)"})
                continue
            if len(using) > MAX_REGS and orid not in linked:
                continue   # 여러 규정이 두루 쓰는 말 → 이 규정의 정의가 적용된다고 보기 어려움
            labels = [a["label"] for a, _, _ in hits]
            a0, p0, s0 = hits[0]
            cites = orid in linked
            out.append({"category": "review", "reg_id": orid, "reg_name": r2["name"], "unit": "본문", "article": a0["label"],
                        "para": p0, "quote": term, "context": s0, "path": "정의 용어(다른 규정 사용)", "target": target,
                        "priority": "중간" if cites else "낮음", "handling": "판단 가능", "definition": {"old": old_def, "new": new_def},
                        "reason": (f"「{old_reg['name']}」을 인용하는 규정이고 " if cites else "인용은 없지만 ")
                        + f"「{term}」를 정의 없이 씀 ({len(labels)}곳: {', '.join(labels[:5])}). 이 규정의 정의를 따르는 뜻인지 확인"})
    return out
