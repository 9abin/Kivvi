"""휴직 개념 층: 주제 층(topic_layer)의 첫 사전. 클래스 = 휴직 종류, 프로퍼티 = 속성, 인스턴스 = 조 · 별표."""

from __future__ import annotations

from .topic_layer import TopicLayer, TopicSpec

LEAVE = TopicSpec(
    prefix="LEAVE",
    trigger="휴직",
    common="휴직(공통)",
    kinds={
        "육아휴직": ["육아휴직"],
        "질병휴직": ["질병휴직", "인병휴직"],
        "가족돌봄휴직": ["가족돌봄휴직", "가족돌봄"],
        "청원휴직": ["청원휴직"],
        "직권휴직": ["직권휴직", "명령휴직"],
    },
    attrs={
        "기간": ["휴직기간", "기간", "이내", "연장"],
        "분할": ["분할", "나누어", "회에한", "2회", "3회", "4회"],
        "보수": ["보수", "급여", "수당", "임금"],
        "승진": ["승진", "경력", "근속", "평가"],
        "절차": ["신청", "승인", "사유서", "서류", "통보", "제출"],
        "대상": ["대상", "자격", "사유", "요건"],
        "복직": ["복직", "복귀"],
    },
)
KINDS, ATTRS, COMMON = LEAVE.kinds, LEAVE.attrs, LEAVE.common


class LeaveLayer(TopicLayer):
    def __init__(self, unit_text: dict[str, str]):
        super().__init__(LEAVE, unit_text)


def kinds_of(text: str) -> list[str]:
    return TopicLayer(LEAVE, {}).kinds_of(text)


def attrs_of(text: str) -> list[str]:
    return TopicLayer(LEAVE, {}).attrs_of(text)
