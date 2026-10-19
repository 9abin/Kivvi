"""용어 관계 용어집 (data/manual/glossary.json). 검색 신호와 '확인된 다른 개념'의 수치 근거 제외에 쓴다.

원칙 (검토표 2026.10.11):
- 관계가 미확정이면 차단하지 않는다. 검색어에 관련 용어를 더하는 신호로만 쓴다.
- 서로 다른 개념으로 '확인'된 쌍에서만, 질문이 한쪽 용어만 말하는데 근거가 다른 쪽 용어만 담고 수치·기간·비율이 있으면
  그 항을 답의 재료에서 뺀다. 절차·보완 조문처럼 수치가 없는 항은 남긴다.
- 용어가 같은 개념이어도 적용 기준(직원/임원별 일수 등)은 별개라서, 동의어 관계로 기준을 합치지 않는다.
- 추가 검색: 신호 용어는 후보를 찾는 단계(Searcher)에서 더해지므로, 근거를 뺀 뒤에도 관련 조문이 다시 후보에 오를 수 있다.
"""
import json
import os
import re

from config import MANUAL_DIR

GLOSSARY_PATH = os.path.join(MANUAL_DIR, "glossary.json")
# 수치·기간·비율이 들어 있는 항인지: '5만원', '30%', '10일', '100분의 70', '3개월' 등
_QUANTITY = re.compile(r"\d[\d,\.]*\s*(?:%|퍼센트|원|일|개월|년|배|시간|회|명|주)|\d+\s*분의\s*\d+")


def load(path=GLOSSARY_PATH):
    if not os.path.exists(path):
        return {"distinct": [], "families": [], "signals": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def has_quantity(body):
    return bool(_QUANTITY.search(body))


def signal_terms(text, table):
    """질문에 신호 용어가 있으면 검색어에 더할 관련 용어를 한 줄로 돌려준다 (없으면 빈 문자열)."""
    add = []
    for s in table.get("signals", []):
        if any(t in text for t in s["trigger"]):
            add += [a for a in s["add"] if a not in text and a not in add]
    for fam in table.get("families", []):
        if any(t in text for t in fam.get("absent_terms", [])):
            add += [a for a in fam.get("search_terms", []) if a not in text and a not in add]
    return " ".join(add)


def _names(side):
    return [side["term"], *side.get("aliases", [])]


def _exclusions(question, bodies, table):
    """본문 목록에서 답의 재료로 쓰지 않을 항의 {위치: 이유}. 후보 단계와 근거 조립 뒤 단계가 같은 규칙을 쓴다."""
    excluded = {}

    # 1. 확인된 '다름' 쌍
    for pair in table.get("distinct", []):
        if pair.get("상태") != "확인":
            continue
        a, b = pair["terms"]
        for ours, other in ((a, b), (b, a)):
            if any(n in question for n in _names(ours)) and not any(n in question for n in _names(other)):
                for i, body in enumerate(bodies):
                    if (any(n in body for n in _names(other)) and not any(n in body for n in _names(ours))
                            and has_quantity(body)):
                        excluded.setdefault(i, f"질문은 '{ours['term']}'을(를) 묻는데 이 근거는 서로 다른 개념인 '{other['term']}'만 다룸")

    # 2. 보증금처럼 같은 말머리를 쓰는 서로 다른 제도 가족: 질문이 가족에 없는 다른 용어를 말하고 맥락 단서도 없을 때
    for fam in table.get("families", []):
        members = fam["members"]
        names = {m["term"] for m in members}
        if any(n in question for n in names):
            continue
        if any(c in question for m in members for c in m.get("cues", [])):
            continue  # 사택, 해외근무 같은 맥락을 직접 말한 질문은 건드리지 않는다
        asked = [t for t in fam.get("absent_terms", []) if t in question]
        if not asked:
            asked = [m.group(0) for m in re.finditer(r"[가-힣]{1,6}" + re.escape(fam["head"]), question)
                     if m.group(0) not in names]
        if not asked:
            continue
        drop_terms = {m["term"] for m in members if m.get("다른_용어를_물으면_수치_근거_제외")}
        for i, body in enumerate(bodies):
            hit = [t for t in drop_terms if t in body]
            if hit and not any(t in body for t in asked) and has_quantity(body):
                excluded.setdefault(i, f"질문의 '{asked[0]}'은(는) 이 근거의 '{hit[0]}'(다른 제도)와 다른 용어이며 관계가 확인되지 않음")
    return excluded


_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_PARA_START = re.compile(r"(?:^|(?<=\s))(?=[①-⑳])")
_TITLE_ONLY = re.compile(r"^\s*제\d+조(?:의\d+)?\([^)]*\)\s*$")


def paragraphs(body):
    """조 본문을 항(①, ② …)으로 나눈다. 조 제목만 있는 첫 조각('제5조(정의)')은 첫 항에 붙인다.
    항 표시가 없으면(한 항짜리 조, 별표) 본문 전체가 한 조각이다."""
    parts = [p for p in _PARA_START.split(body or "") if p]
    if len(parts) > 1 and _TITLE_ONLY.match(parts[0]):
        parts = [parts[0] + parts[1], *parts[2:]]
    return parts or [body or ""]


def _para_label(article, part):
    m = re.search(r"[①-⑳]", part[:40])
    return f"{article} 제{_CIRCLED.index(m.group(0)) + 1}항" if m else article


def _trim(question, items, table):
    """청크가 조 단위라 항마다 판정한다. 뺄 항만 본문에서 지우고, 모든 항이 빠지는 청크만 통째로 뺀다.
    (조 전체를 한 덩어리로 보면 다른 개념의 항 하나 때문에 수치 없는 절차 항까지 빠지고,
    한 조의 다른 항에 두 용어가 함께 있으면 다른 개념의 수치 항이 그대로 남는다.)
    반환: (남긴 항목, [(항목 위치, 조항 표기, 이유), ...]). 다 빼게 되면 하나도 빼지 않는다."""
    kept, dropped = [], []
    for i, item in enumerate(items):
        parts = paragraphs(item["body"])
        excluded = _exclusions(question, parts, table)
        dropped += [(i, _para_label(item["article"], parts[j]) if len(parts) > 1 else item["article"], why)
                    for j, why in sorted(excluded.items())]
        if len(excluded) == len(parts):
            continue
        if excluded:
            item = {**item, "body": "".join(p for j, p in enumerate(parts) if j not in excluded).strip()}
        kept.append(item)
    if not kept:
        return items, []
    return kept, dropped


def filter_by_glossary(question, entries, table):
    """근거 조립 뒤 단계(형제 항 포함). (남긴 근거, 뺀 근거 목록). 뺀 근거는 (문서명, 조항, 이유). 다 빼게 되면 하나도 빼지 않는다."""
    kept, dropped = _trim(question, entries, table)
    return kept, [(entries[i]["문서명"], label, why) for i, label, why in dropped]


def filter_candidates(question, cands, table):
    """후보를 고르는 단계. 통째로 빠진 후보 자리는 다음 후보가 채운다. (남긴 후보, 뺀 후보 목록). 뺀 후보는 (doc_id, 조항, 이유).
    일부 항만 빠진 후보는 그 항을 지운 본문으로 남는다. 정확 조회 결과는 이 함수를 거치지 않는다. 다 빼게 되면 하나도 빼지 않는다."""
    kept, dropped = _trim(question, cands, table)
    return kept, [(cands[i]["doc_id"], label, why) for i, label, why in dropped]
