"""정확 조회 경로: 질문에 규정 이름 하나와 조(·항) 번호가 확정되면 그 조문을 직접 찾는다.

규칙 (모두 만족할 때만 작동)
  1. 질문에 규정 이름이 정확히 하나 있다. 공백은 무시하고, 가장 긴 이름을 쓴다.
  2. 질문에 "제N조" 또는 "제N조의M"이 정확히 한 번 있다.
  3. 항 번호("제N항" 또는 ①~⑳)는 있어도 되고 없어도 된다. 있으면 그 항만, 없으면 조 전체.
그 밖의 경우(규정 여러 개, 조항 여러 개, 규정 이름 없음)는 None을 돌려주고 기존 검색을 쓴다.
"""
import re
from dataclasses import dataclass

CIRCLED = {c: i for i, c in enumerate("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳", start=1)}
ARTICLE_REF = re.compile(r"제(\d+)조(?:의\s*(\d+))?(?:\s*(?:제(\d+)항|([①-⑳])))?")


@dataclass
class Reference:
    doc_id: str
    base: str            # 조 부분, 예: "제3조의1"
    para: int | None     # 항 번호, 없으면 None
    label: str           # 사람이 읽는 표기, 예: "직원채용지침 제2조 제2항"


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def find_doc(query, documents):
    """질문에 들어 있는 규정 이름을 찾는다. 이름이 하나로 확정될 때만 doc_id를 돌려준다."""
    q = _norm(query)
    hits = [d for d in documents if _norm(d["title"]) and _norm(d["title"]) in q]
    # 다른 후보 이름 안에 통째로 들어가는 짧은 이름은 버린다 (예: "복지후생 운영지침" ⊂ "해외 근무직원 복지후생 운영지침")
    hits = [d for d in hits
            if not any(d is not o and _norm(d["title"]) in _norm(o["title"]) for o in hits)]
    return hits[0]["doc_id"] if len(hits) == 1 else None


def parse_reference(query, documents):
    """확정 가능한 조문 참조면 Reference를, 아니면 None을 돌려준다."""
    refs = list(ARTICLE_REF.finditer(query))
    if len(refs) != 1:
        return None
    doc_id = find_doc(query, documents)
    if doc_id is None:
        return None

    m = refs[0]
    base = f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    para = None
    if m.group(3):
        para = int(m.group(3))
    elif m.group(4):
        para = CIRCLED[m.group(4)]

    title = next(d["title"] for d in documents if d["doc_id"] == doc_id)
    label = f"{title} {base}" + (f" 제{para}항" if para else "")
    return Reference(doc_id=doc_id, base=base, para=para, label=label)
