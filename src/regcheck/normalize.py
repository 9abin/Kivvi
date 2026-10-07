"""비교·검색·LLM 입력용 정규화.

원문(`text`)은 근거 표시와 신·구조문대비표에 그대로 쓰므로 바꾸지 않는다.
정규화 결과는 `norm` 필드로 따로 둔다.
"""

from __future__ import annotations

import re

# 본문 안의 개정 표시: <개정 2022.08.09., 2025.07.02.>, [2014.05.29. 개정], <신설 …>, <삭제 …>
RE_AMEND_MARK = re.compile(
    r"\s*[<\[]\s*(?:(?:개정|신설|삭제|전문개정|본조신설|제목개정)\s*)?"
    r"(?:\d{4}\s*\.\s*\d{1,2}\s*\.\s*\d{0,2}\s*\.?\s*,?\s*)+(?:\s*(?:개정|신설|삭제))?\s*[>\]]"
)
# 내규집 목차 번호가 붙은 머리말 줄: "2-6. 퇴직급지급규정", "1-2   이사회규정"
RE_INDEX_HEADER = re.compile(r"^\s*\d+(?:-\d+)*\.?\s+([가-힣].*)$")

_TRANS = str.maketrans({
    "“": '"', "”": '"', "‘": "'", "’": "'", "｢": "「", "｣": "」",
    "ㆍ": "·", "･": "·", "・": "·", "∙": "·",
    "（": "(", "）": ")", "［": "[", "］": "]", "，": ",", "：": ":",
    "　": " ",
})


def amend_marks(text: str) -> list[str]:
    return [m.group(0).strip() for m in RE_AMEND_MARK.finditer(text)]


def normalize(text: str) -> str:
    """개정 표시 제거, 따옴표·가운뎃점·전각 기호 통일, 공백 정리."""
    t = RE_AMEND_MARK.sub("", text)
    t = t.translate(_TRANS)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    return t.strip()


def compact(text: str) -> str:
    """띄어쓰기 차이를 무시한 비교용 (예: '퇴 직 금 지 급 규 정' ↔ '퇴직금지급규정')."""
    return re.sub(r"\s", "", normalize(text))


def index_header(line: str) -> str | None:
    """머리말 줄이면 목차 번호를 뗀 문서명을 돌려준다."""
    m = RE_INDEX_HEADER.match(line)
    return m.group(1).strip() if m else None
