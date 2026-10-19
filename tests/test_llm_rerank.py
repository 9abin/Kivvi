"""2단계 LLM 리랭킹 (src/search/llm_rerank.py). 모델·DB 없이 돈다."""
import os
import tempfile
import unittest

from src.search.llm_rerank import LLMReranker, parse_score, reorder


class FakeReranker(LLMReranker):
    def __init__(self, answers, top=3, cache_path=None):
        super().__init__(model="fake", top=top, cache_path=cache_path)
        self.answers, self.asked = answers, []

    def _ask(self, question, text):
        self.asked.append(text)
        return self.answers.get(text)


def cands(*ids):
    return [{"chunk_id": c, "rerank": 1.0 - i * 0.1} for i, c in enumerate(ids)]


class ParseTests(unittest.TestCase):
    def test_accepts_bare_number_and_wrapped_forms(self):
        for raw in ("7", " 7 ", "7점", "점수: 7", '{"점수": 7}'):
            self.assertEqual(parse_score(raw), 7)

    def test_rejects_out_of_range_or_missing(self):
        self.assertIsNone(parse_score("11"))
        self.assertIsNone(parse_score("모르겠다"))
        self.assertIsNone(parse_score(None))


class ReorderTests(unittest.TestCase):
    def test_sorts_by_score_and_keeps_cross_encoder_order_on_ties(self):
        c = cands("a", "b", "c", "d")
        self.assertEqual([x["chunk_id"] for x in reorder(c, [3, 9, 9, 0])], ["b", "c", "a", "d"])

    def test_failed_scores_keep_their_position(self):
        c = cands("a", "b", "c", "d")
        # b는 실패: 2위 자리를 그대로 지키고 나머지 셋만 점수대로 1·3·4위를 채운다
        self.assertEqual([x["chunk_id"] for x in reorder(c, [1, None, 9, 5])], ["c", "b", "d", "a"])

    def test_all_failed_keeps_input_order(self):
        c = cands("a", "b", "c")
        self.assertEqual([x["chunk_id"] for x in reorder(c, [None, None, None])], ["a", "b", "c"])


class RerankTests(unittest.TestCase):
    def test_only_top_is_rescored_and_tail_keeps_order(self):
        r = FakeReranker({"a": "2", "b": "9", "c": "9"}, top=3)
        out = r.rerank("q", cands("a", "b", "c", "d", "e"), lambda cid: cid)
        self.assertEqual([x["chunk_id"] for x in out], ["b", "c", "a", "d", "e"])
        self.assertEqual([x["llm_rerank"] for x in out], [9, 9, 2, None, None])
        self.assertEqual(sorted(r.asked), ["a", "b", "c"])

    def test_unparseable_answer_falls_back_for_that_document(self):
        r = FakeReranker({"a": "9", "b": "음...", "c": "10"}, top=3)
        out = r.rerank("q", cands("a", "b", "c"), lambda cid: cid)
        self.assertEqual([x["chunk_id"] for x in out], ["c", "b", "a"])  # b는 교차 인코더 2위 자리 유지, c 10 > a 9
        self.assertEqual([x["llm_rerank"] for x in out], [10, None, 9])

    def test_cache_skips_repeat_calls_and_persists(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cache.json")
            r = FakeReranker({"a": "8", "b": "3"}, top=2, cache_path=path)
            r.rerank("q", cands("a", "b"), lambda cid: cid)
            self.assertEqual(r.calls, 2)
            r.rerank("q", cands("a", "b"), lambda cid: cid)
            self.assertEqual(r.calls, 2)  # 캐시 적중
            r2 = FakeReranker({}, top=2, cache_path=path)  # 파일에서 다시 읽는다 (_ask는 아무 답도 못 준다)
            out = r2.rerank("q", cands("b", "a"), lambda cid: cid)
            self.assertEqual([x["chunk_id"] for x in out], ["a", "b"])
            self.assertEqual(r2.calls, 0)

    def test_failures_are_not_cached(self):
        r = FakeReranker({"a": None}, top=1, cache_path=None)
        r.rerank("q", cands("a"), lambda cid: cid)
        r.rerank("q", cands("a"), lambda cid: cid)
        self.assertEqual(r.calls, 2)


if __name__ == "__main__":
    unittest.main()
