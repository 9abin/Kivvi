"""개정 위치 찾기 (regcheck.target): 규정 이름이 있으면 그 규정 안에서, 점수가 낮으면 바꿔 써 다시 찾는다."""
import numpy as np

from regcheck.target import find_targets


def art(aid, label, title, text):
    return {"id": aid, "label": label, "title": title, "text": text, "norm": text, "deleted": False, "items": []}


REGS = {"1": {"reg_id": "1", "name": "여비규정", "articles": [art("1:1", "제1조", "목적", "여비 목적"), art("1:14", "제14조", "체재비", "숙박비와 일비")]},
        "2": {"reg_id": "2", "name": "복무규정", "articles": [art("2:21", "제21조", "연차유급휴가", "연차 휴가")]}}
INDEX = (np.eye(3), [{"article_id": "1:1"}, {"article_id": "1:14"}, {"article_id": "2:21"}])


def embed(ts):
    return [np.array([0.0, 1.0, 0.0])]          # 여비규정 제14조와 가장 가까움


def test_named_regulation_restricts_search():
    seen = []
    def rerank(q, ts):
        seen.extend(ts)
        return [0.9 if "체재비" in t else 0.1 for t in ts]
    res = find_targets("여비규정에서 숙박비를 올리고 싶다", REGS, INDEX, embed, rerank)
    assert res["candidates"][0]["label"] == "제14조" and res["confident"]
    assert not any("연차" in t for t in seen)     # 이름이 나온 규정 밖은 보지 않음


def test_low_score_triggers_rewrite_and_keeps_better():
    calls = []
    def rerank(q, ts):
        calls.append(q)
        return [0.8 if ("숙박" in q and "체재비" in t) else 0.05 for t in ts]
    res = find_targets("출장 가서 자는 돈 늘려줘", REGS, INDEX, embed, rerank, rewrite=lambda q: "출장 숙박비 기준")
    assert calls == ["출장 가서 자는 돈 늘려줘", "출장 가서 자는 돈 늘려줘 출장 숙박비 기준"]
    assert res["candidates"][0]["label"] == "제14조" and res["rewritten"] == "출장 숙박비 기준" and "다시 검색" in res["candidates"][0]["how"]


def test_still_low_is_not_confident():
    res = find_targets("무엇인가 바꾸고 싶다", REGS, INDEX, embed, lambda q, ts: [0.01] * len(ts), rewrite=lambda q: "")
    assert not res["confident"]
