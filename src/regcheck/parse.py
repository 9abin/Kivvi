"""추출한 문단 목록을 규정 구조(본문 조·항·호·목, 부칙, 별표, 별지)로 나눈다.

순서 가정: 머리(제명·연혁) → 본문 → 부칙들 → 별표·별지. 별표·별지가 시작되면 본문으로 돌아가지 않는다.
별지 안의 「제N조」(계약서형 서식)는 본문 조문으로 보지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"

RE_CHAPTER = re.compile(r"^\s*제\s*(\d+)\s*(장|절)\s*(.*)$")
# 조문 시작은 「제N조(제목)」 또는 「제N조 삭제」만 인정한다. 「제N조에 따라 …」로 시작하는 이어지는 줄을 막기 위함.
RE_ARTICLE = re.compile(
    # 가지번호 표기 세 가지: 제8조의2 / 제9조2(…) / 제12-1조(…)  → 모두 (조, 가지)로 읽는다
    r"^\s*제\s*(\d+)\s*(?:-\s*(\d+)\s*)?조(?:\s*의\s*(\d+)|(\d+)(?=\s*[(（]))?"
    r"\s*(?:\(([^)]*)\)|（([^）]*)）|(?=[(<\[]?\s*삭\s*제))\s*(.*)$"
)
RE_ADDENDUM = re.compile(r"^\s*[<\[【]?\s*부\s*칙\s*[>\]】]?\s*(?:[<\[(（]\s*([^>\])）]*)\s*[>\])）])?\s*(.*)$")
RE_APPENDIX = re.compile(
    r"^\s*(?:■[^\[]*)?[\[【<]\s*(별\s*표|별\s*지)\s*([^\]】>]*)[\]】>]\s*(.*)$"
)
RE_PARA = re.compile(rf"^\s*([{CIRCLED}])\s*(.*)$")
RE_ITEM = re.compile(r"^\s*(\d+)\s*\.\s*(.*)$")
MOK = "가나다라마바사아자차카타파하"
RE_SUBITEM = re.compile(rf"^\s*([{MOK}])\s*\.\s*(.*)$")
RE_AMEND = re.compile(
    r"[<\[]\s*(?:(개정|신설|삭제|전문개정|본조신설|제목개정)\s*)?"
    r"((?:\d{4}\s*\.\s*\d{1,2}\s*\.\s*\d{0,2}\.?\s*,?\s*)+)(?:\s*(개정|신설|삭제))?\s*[>\]]"
)
RE_DELETED = re.compile(r"^\s*[(<\[]?\s*삭\s*제(?:\s*[)>\]])?.*$")


@dataclass
class Item:
    """항·호·목. path는 (항, 호, 목) 번호. 없는 단계는 0."""

    path: tuple[int, int, int]
    text: str
    deleted: bool = False


@dataclass
class Article:
    no: int
    branch: int  # 제N조의M 의 M, 없으면 0
    title: str
    text: str  # 조 전체 원문
    items: list[Item] = field(default_factory=list)
    chapter: str = ""
    deleted: bool = False
    amend_notes: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"제{self.no}조" + (f"의{self.branch}" if self.branch else "")


@dataclass
class Addendum:
    date: str
    text: str
    articles: list[Article] = field(default_factory=list)


@dataclass
class Appendix:
    kind: str  # 별표 | 별지
    number: str  # 마커 안의 번호 표현 원문 (예: "제1호서식", "1", "2")
    title: str
    text: str


@dataclass
class ParsedRegulation:
    title: str
    history: list[str]
    articles: list[Article]
    addenda: list[Addendum]
    appendices: list[Appendix]
    preamble: list[str]  # 조문 형식이 아닌 본문 (예: 헌장)

    def to_dict(self) -> dict:
        return asdict(self)


def _amend_notes(text: str) -> list[str]:
    return [m.group(0) for m in RE_AMEND.finditer(text)]


# 앞 글자가 쉼표·기호 나열(「항은 ①, ② 등과 같이」, 내규관리규정 제15조)이면 나누지 않는다
RE_INLINE_PARA = re.compile(rf"(?<=[^\s제\d,、{CIRCLED}“\"'])\s*(?=[{CIRCLED[1:]}]\s+[가-힣「“\"(])")
RE_INLINE_ITEM = re.compile(r"(?<=[가-힣)」”.:])\s*(?=(\d{1,2})\.\s*[가-힣「“\"(])")


def _split_inline_paras(lines: list[str]) -> list[str]:
    """줄 안에 붙은 다음 항 기호·호 번호를 줄로 나눈다 (자금세탁행위 방지 업무지침: 「…하여야 한다.② …」, 「…위험 등③ …」,
    「…반영하여야 한다.1. 국가위험2. 고객고유위험」).
    지금까지 나온 번호의 바로 다음 번호일 때만 나눈다. 호는 그 항에 「각 호」가 있을 때만. 「제1항 ②」·「2.5%」 같은 본문 속 기호는 그대로 둔다."""
    out: list[str] = []
    para = item = 0
    lists = False   # 지금 항이 「다음 각 호」로 목록을 여는가
    for line in lines:
        if m := RE_PARA.match(line):
            para, item, lists = CIRCLED.index(m.group(1)) + 1, 0, False
        elif m := RE_ITEM.match(line):
            item = int(m.group(1))
        rest = line
        while True:
            lists = lists or "각 호" in rest or "각호" in rest
            cuts = [(m.start(), "p") for m in RE_INLINE_PARA.finditer(rest)
                    if m.start() > 0 and CIRCLED.index(rest[m.end()]) + 1 == max(para, 1) + 1]
            cuts += [(m.start(), "i") for m in RE_INLINE_ITEM.finditer(rest)
                     if m.start() > 0 and lists and int(m.group(1)) == item + 1]
            if not cuts:
                break
            cut, kind = min(cuts)
            out.append(rest[:cut])
            rest = rest[cut:].lstrip()
            if kind == "p":
                para, item, lists = CIRCLED.index(rest[0]) + 1, 0, False
            else:
                item += 1
        out.append(rest)
    return out


def _split_items(lines: list[str]) -> list[Item]:
    """조문의 첫 줄(제목 뒤 본문)부터 항·호·목을 나눈다."""
    items: list[Item] = []
    para = item = sub = 0
    for line in _split_inline_paras(lines):
        if m := RE_PARA.match(line):
            para, item, sub = CIRCLED.index(m.group(1)) + 1, 0, 0
            body = m.group(2)
        elif m := RE_ITEM.match(line):
            item, sub = int(m.group(1)), 0
            body = m.group(2)
        elif m := RE_SUBITEM.match(line):
            sub = MOK.index(m.group(1)) + 1
            body = m.group(2)
        else:
            if items:  # 이어지는 줄은 앞 단위에 붙인다
                items[-1].text += " " + line.strip()
                continue
            body = line.strip()
        items.append(Item((para, item, sub), body.strip(), bool(RE_DELETED.match(body))))
    return items


def _make_article(m: re.Match, body_lines: list[str], chapter: str) -> Article:
    no, branch = int(m.group(1)), int(m.group(2) or m.group(3) or m.group(4) or 0)  # 제N-M조, 제N조의M, 제N조M(…)
    title = (m.group(5) or m.group(6) or "").strip()
    first = m.group(7).strip()
    lines = ([first] if first else []) + body_lines
    text = "\n".join([m.group(0).strip()] + body_lines)
    deleted = bool(RE_DELETED.match(first)) and not body_lines
    return Article(
        no=no,
        branch=branch,
        title=title,
        text=text,
        items=_split_items(lines),
        chapter=chapter,
        deleted=deleted,
        amend_notes=_amend_notes(text),
    )


def parse(lines: list[str]) -> ParsedRegulation:
    # 1) 머리: 첫 장·조·부칙 전까지
    start = next(
        (i for i, l in enumerate(lines) if RE_CHAPTER.match(l) or RE_ARTICLE.match(l) or RE_ADDENDUM.match(l)),
        len(lines),
    )
    head = [l.strip() for l in lines[:start]]
    title = next((h for h in head if not re.match(r"^[\d\-.\s]+", h) or re.search(r"[가-힣]", h) and not re.match(r"^\d", h)), "")
    history = [h for h in head if re.search(r"\d{4}\s*\.\s*\d{1,2}", h)]
    preamble: list[str] = []

    articles: list[Article] = []
    addenda: list[Addendum] = []
    appendices: list[Appendix] = []

    mode = "body"  # body | addendum | appendix
    chapter = ""
    cur_match: re.Match | None = None
    cur_lines: list[str] = []
    add: Addendum | None = None
    app: Appendix | None = None

    def flush_article():
        nonlocal cur_match, cur_lines
        if cur_match is None:
            return
        art = _make_article(cur_match, cur_lines, chapter if mode == "body" else "")
        (articles if mode == "body" else add.articles).append(art)  # type: ignore[union-attr]
        cur_match, cur_lines = None, []

    # 헌장처럼 조문이 없는 본문은 머리 이후 첫 부칙 전까지를 preamble로 둔다
    if start == len(lines) or RE_ADDENDUM.match(lines[start]):
        body_end = start
        title_idx = next((i for i, l in enumerate(head) if l == title), 0)
        preamble = head[title_idx + 1 :]
        preamble = [p for p in preamble if p not in history]

    for line in lines[start:]:
        if m := RE_APPENDIX.match(line):
            flush_article()
            if add:
                add.text = add.text.strip()
            mode = "appendix"
            app = Appendix(re.sub(r"\s", "", m.group(1)), m.group(2).strip(), m.group(3).strip(), "")
            appendices.append(app)
            continue
        # 별표·별지 뒤에 부칙이 오는 문서(인사규정 등): 짧은 부칙 머리 줄이면 부칙으로 돌아간다
        if mode == "appendix" and not (len(line.strip()) <= 30 and RE_ADDENDUM.match(line)):
            app.text += line + "\n"  # type: ignore[union-attr]
            continue
        if m := RE_ADDENDUM.match(line):
            flush_article()
            mode = "addendum"
            add = Addendum(date=(m.group(1) or "").strip(), text=(m.group(2) or "").strip() + "\n")
            addenda.append(add)
            continue
        if mode == "body" and (m := RE_CHAPTER.match(line)):
            flush_article()
            chapter = line.strip()
            continue
        if m := RE_ARTICLE.match(line):
            flush_article()
            cur_match, cur_lines = m, []
            if mode == "addendum":
                add.text += line + "\n"  # type: ignore[union-attr]
            continue
        if cur_match is not None:
            cur_lines.append(line)
        if mode == "addendum":
            add.text += line + "\n"  # type: ignore[union-attr]
    flush_article()
    for a in appendices:
        a.text = a.text.strip()
    for a in addenda:
        a.text = a.text.strip()
    return ParsedRegulation(title, history, articles, addenda, appendices, preamble)
