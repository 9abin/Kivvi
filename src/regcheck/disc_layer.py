"""징계 · 면직 개념 층: 주제 층(topic_layer)의 세 번째 사전 (규칙 기반 자동 초안, 사람 검토 전)."""

from __future__ import annotations

from .topic_layer import TopicLayer, TopicSpec

DISC = TopicSpec(
    prefix="DISC",
    trigger="징계",
    extra_triggers=("직위해제", "면직", "강등", "정직", "감봉", "견책", "문책"),
    common="징계(공통)",
    kinds={
        "면직": ["면직", "파면", "해임"],
        "강등": ["강등"],
        "정직": ["정직"],
        "감봉": ["감봉"],
        "견책": ["견책"],
        "직위해제": ["직위해제"],
        "문책": ["문책"],
    },
    attrs={
        "기간": ["개월", "월이상", "기간", "일이내", "년이"],
        "보수": ["보수", "임금", "월급여", "성과급", "수당", "감액", "감한다"],
        "승진": ["승진", "평정", "감점", "근무평정"],
        "절차": ["위원회", "의결", "소명", "재심", "통보", "요구", "심의"],
        "시효": ["시효", "3년", "5년", "지나면"],
        "감경": ["감경", "감면", "면제"],
        "효력": ["효력", "확정", "소급", "집행"],
    },
)


class DiscLayer(TopicLayer):
    def __init__(self, unit_text: dict[str, str]):
        super().__init__(DISC, unit_text)
