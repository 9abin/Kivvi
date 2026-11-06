"""근거 카드: 후보 하나에 대해 사람이 바로 판단할 수 있는 근거 · 치환안 · (선택) LLM 표시를 한 장으로 묶는다.

  card(cand, cq, nodes, texts, llm=None) -> dict

원칙 (리포트 74)
  · 근거 문장은 규칙이 후보 원문에서 글자 그대로 뽑는다. 원문에 없으면 싣지 않는다.
  · 치환안은 번호 이동으로 인용 번호가 밀릴 때만 규칙으로 만든다. 법적 판단 · 문안 작성은 하지 않는다.
  · LLM 표시(예 / 불확실 / 아니오 + 한 줄 이유)는 있으면 그대로 붙이고 후보를 지우지 않는다.
  · 확정급이 아닌 단계는 「근거 미확인」으로 표시해 사람이 확인하게 한다.
"""

from __future__ import annotations

import re

from .change_query import ChangeQuery
from .collect import Candidate

REASON = {
    "CITES_in": "고쳐지는 조를 번호로 인용함",
    "CITES_internal": "함께 번호가 바뀌는 다른 조를 번호로 인용함",
    "STALE_CITE": "옛 번호를 그대로 인용하고 있을 수 있음",
    "USES_TERM": "정의가 바뀌는 용어를 씀",
    "TERM_CORE": "정의가 바뀌는 용어의 핵심 부분이 나옴",
    "PHRASE": "개정으로 새로 들어간 말이 나옴",
    "MENTIONS_TITLE": "고쳐지는 조의 제목이 본문에 나옴",
    "CO_CITE": "고쳐지는 조가 인용하는 조를 함께 인용함",
    "CITE_MENTION": "고쳐지는 조가 인용하는 조의 제목을 언급함",
    "CO_MENTION": "고쳐지는 조와 같은 조 제목을 언급함",
    "RAG": "의미가 비슷함 (자동 검색)",
}
CITE_PATHS = ("CITES_in", "STALE_CITE", "CO_CITE", "CITES_internal")
TERM_PATHS = ("USES_TERM", "TERM_CORE")


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.다요함음])\s+|\n+", text or "")
    return [p.strip() for p in parts if p and p.strip()]


def _label_re(label: str) -> re.Pattern:
    """제3조를 찾되 제30조 · 제3조의2에 걸리지 않게 한다."""
    return re.compile(re.escape(label) + r"(?![0-9]|의[0-9])")


def _first(sents: list[str], pred) -> str | None:
    return next((s for s in sents if pred(s)), None)


def _verified(quote: str | None, text: str) -> str | None:
    """글자 그대로 후보 원문에 있을 때만 인정한다 (띄어쓰기 차이만 허용)."""
    if not quote:
        return None
    n = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
    return quote if n(quote) in n(text) else None


def _quote(c: Candidate, cq: ChangeQuery, nodes: dict, text: str) -> tuple[str | None, str]:
    sents = _sentences(text)
    labels = [nodes[s]["label"] for s in cq.starts if s in nodes and s != c.id]   # 후보 자신은 뺀다(이동 범위 안의 조는 다른 이동 조를 인용)
    if any(p in CITE_PATHS for p in c.paths):
        q = _first(sents, lambda s: any(_label_re(lb).search(s) for lb in labels))
        if q:
            return q, "citation"
    if any(p in TERM_PATHS for p in c.paths):
        keys = [re.sub(r"\s+", "", t) for t in cq.terms + cq.term_cores]
        q = _first(sents, lambda s: any(k and k in re.sub(r"\s+", "", s) for k in keys))
        if q:
            return q, "term"
    if "PHRASE" in c.paths:
        keys = [re.sub(r"\s+", "", p) for p in cq.phrases]
        q = _first(sents, lambda s: any(k and k in re.sub(r"\s+", "", s) for k in keys))
        if q:
            return q, "phrase"
    return None, ""


HEADING = re.compile(r"^제[0-9]+조(의[0-9]+)?\([^)]*\)\s*")


def _replacement(quote: str, cq: ChangeQuery, nodes: dict, cand_id: str) -> dict | None:
    """번호 이동에서 인용 번호가 바뀌는 경우의 치환안 (옛 번호 -> 새 번호). 한 번에 바꾼다(연쇄 치환 없음).
    같은 규정 안의 인용이거나, 다른 규정이면 문장에 고쳐지는 규정 이름이 있을 때만 낸다 (번호만으로는 어느 규정의 조인지 모른다)."""
    if cq.kind != "번호 이동" or not cq.renumber:
        return None
    reg_name = nodes.get(cq.reg_id, {}).get("name", "")
    same_reg = nodes.get(cand_id, {}).get("reg") == cq.reg_id
    if not same_reg and not (reg_name and reg_name in quote):
        return None
    mapping = {nodes[o]["label"]: n for o, n in cq.renumber.items() if o in nodes and n and nodes[o]["label"] != n}
    if not mapping:
        return None
    head = HEADING.match(quote)
    body = quote[head.end():] if head else quote
    pat = re.compile("(" + "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)) + ")(?![0-9]|의[0-9])")
    after = pat.sub(lambda m: mapping[m.group(1)], body)
    return {"before": quote, "after": (quote[:head.end()] if head else "") + after} if after != body else None


def card(c: Candidate, cq: ChangeQuery, nodes: dict, texts: dict[str, str], llm: dict | None = None) -> dict:
    nd = nodes.get(c.id, {})
    text = texts.get(c.id, "")
    quote, kind = _quote(c, cq, nodes, text)
    quote = _verified(quote, text)
    out = {
        "id": c.id,
        "target": f"{nd.get('label', c.id)} {nd.get('title', '')}".strip(),
        "tier": c.tier,
        "reasons": list(dict.fromkeys(REASON.get(p.split(":")[0], p) for p in c.paths)),
        "paths": c.paths,
        "quote": quote,
        "quote_kind": kind if quote else "",
        "replacement": _replacement(quote, cq, nodes, c.id) if quote and kind == "citation" else None,
        "llm": llm,
        "needs_check": c.tier != "확정급" or not quote,
    }
    return out
