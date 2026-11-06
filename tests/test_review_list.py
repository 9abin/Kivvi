import sys
import unittest

sys.path.insert(0, "src")
from regcheck.collect import Candidate  # noqa: E402
from regcheck.review_list import split_view  # noqa: E402


def c(i, paths, score, tier="참고"):
    return Candidate(i, score, paths, tier)


class ReviewListTest(unittest.TestCase):
    def setUp(self):
        self.cands = [
            c("S1", ["CITES_in", "RAG"], 1.0, "확정급"),
            c("U1", ["USES_TERM"], 0.9, "확인 권장"),
            c("N1", ["CITE_MENTION"], 0.5, "참고"),      # 소음 경로만
            c("N2", ["MENTIONS_ORG", "PHRASE"], 0.4, "참고"),
            c("R1", ["RAG"], 0.2, "참고"),
            c("R2", ["RAG"], 0.19, "참고"),
        ]

    def test_noise_only_graph_candidates_are_folded_away_but_kept_in_all(self):
        v = split_view(self.cands)
        ids = [x.id for x in v["default"]]
        self.assertIn("U1", ids)
        self.assertNotIn("N1", ids)
        self.assertNotIn("N2", ids)
        self.assertEqual(len(v["all"]), 6)
        self.assertEqual(v["hidden"], 2)

    def test_default_keeps_original_order(self):
        ids = [x.id for x in split_view(self.cands)["default"]]
        self.assertEqual(ids, ["S1", "U1", "R1", "R2"])

    def test_rag_budget_and_graph_budget(self):
        many = [c(f"R{i}", ["RAG"], 0.2 - 0.001 * i) for i in range(40)] + [c(f"G{i}", ["CITES_in"], 1.0 - 0.01 * i, "확정급") for i in range(30)]
        v = split_view(many, rag_k=30, graph_n=20)
        self.assertEqual(len(v["default"]), 50)
        self.assertEqual(v["hidden"], 20)

    def test_candidate_found_by_rag_and_noise_path_stays_via_rag(self):
        cands = [c("X", ["CITES_REG", "RAG"], 0.2)]
        self.assertEqual([x.id for x in split_view(cands)["default"]], ["X"])


if __name__ == "__main__":
    unittest.main()
