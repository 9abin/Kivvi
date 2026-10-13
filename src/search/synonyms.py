"""구어체 말을 규정 용어로 바꾸는 동의어 사전 (data/manual/synonyms.json).

직원은 "법카", "사표", "코인"처럼 말하지만 규정은 "법인카드", "사직", "가상자산"이라고 쓴다. 검색어에 규정 용어를 더해
후보를 더 잘 찾게 한다. 사전은 규정 원문에 거의 안 나오는 구어체 말(청크 8개 이하)만 담고, 목표어는 모두
규정 원문에 있는 말이다. 새 항목은 실제로 검색에 실패한 질문을 모아 채운다.
"""
import json
import os

from config import MANUAL_DIR

SYNONYMS_PATH = os.path.join(MANUAL_DIR, "synonyms.json")


def load(path=SYNONYMS_PATH):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def expand(text, table):
    """질문에 구어체 말이 들어 있으면 규정 용어를 한 줄로 이어 돌려준다. 없으면 빈 문자열."""
    lowered = text.lower()
    terms = []
    for trigger, targets in table.items():
        if trigger in lowered:
            terms.extend(t for t in targets if t not in terms and t not in text)
    return " ".join(terms)
