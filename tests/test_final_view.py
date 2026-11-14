import sys
import unittest

sys.path.insert(0, "src")
from regcheck.collect import Candidate  # noqa: E402
from regcheck.final_view import cand_window, decide_inputs, final_view  # noqa: E402


def cand(cid, tier, *paths):
    c = Candidate(cid, 1.0); c.paths = list(paths); c.tier = tier
    return c


class FinalViewTest(unittest.TestCase):
    def setUp(self):
        self.cs = [cand("A:1", "확정급", "CITES_in"), cand("A:2", "확인 권장", "SAME_VALUE"), cand("A:3", "참고", "CO_MENTION", "RAG"),
                   cand("B:1", "참고", "RAG"), cand("B:2", "참고", "RAG"), cand("B:3", "참고", "RAG")]

    def test_confirmed_rule_kept_and_low_scored_rule_hidden(self):
        v = final_view(self.cs, {"A:1": 0.0, "A:2": 0.1, "A:3": 2.0})
        self.assertEqual([c.id for c in v["rule"]], ["A:1", "A:3"])

    def test_unscored_rule_candidate_is_not_hidden(self):
        self.assertIn("A:2", [c.id for c in final_view(self.cs, {})["rule"]])

    def test_rag_only_mixes_search_rank_and_model_rank(self):
        cs = self.cs + [cand("B:4", "참고", "RAG")]   # 검색 순위 B1 B2 B3 B4, 판단 모델 순위 B4 B1 B2 B3
        v = final_view(cs, {"B:1": 0.2, "B:2": 0.1, "B:3": 0.0, "B:4": 3.0}, rag_n=3)
        self.assertEqual([c.id for c in v["rag"]], ["B:1", "B:2", "B:4"])
        self.assertEqual(v["hidden"], 1)   # 점수 없는 규칙 후보 A:2 · A:3은 보이고, B:3만 숨긴다

    def test_model_inputs_skip_confirmed_rule(self):
        s = decide_inputs(self.cs, "값 변경", "개정문", "시작 조", {"A:2": "대상 조문"})
        self.assertNotIn("A:1", s)
        self.assertIn("[확인 대상 조문]\n대상 조문", s["A:2"])


class CandWindowTest(unittest.TestCase):
    """261112: 긴 조문은 앞 700자 대신 바뀌는 말 주변을 판단 모델에 넣는다(시험 T073: 근거가 1,792자 위치)."""
    def test_long_text_shows_window_around_first_key_and_keeps_head(self):
        text = "제1조(별표) 전결사항 " + "가" * 2000 + " 라. 직원의 신원 보증보험 가입 " + "나" * 500
        out = cand_window(text, ["없는말", "신원보증보험"], n=700)
        self.assertTrue(out.startswith("제1조(별표)"))
        self.assertIn("신원 보증보험 가입", out)   # 띄어쓰기가 달라도 찾는다
        self.assertLessEqual(len(out), 700)

    def test_short_text_or_no_key_is_unchanged_front(self):
        self.assertEqual(cand_window("짧은 조문", ["말"]), "짧은 조문")
        self.assertEqual(cand_window("가" * 900, ["없음"], n=700), "가" * 700)


if __name__ == "__main__":
    unittest.main()
