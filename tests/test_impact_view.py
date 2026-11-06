import sys
import unittest

sys.path.insert(0, "src")
sys.path.insert(0, "tests")
from regcheck.build import to_records  # noqa: E402
from regcheck.impact_view import ImpactContext, run  # noqa: E402
from regcheck.kg import build as kg_build  # noqa: E402
from regcheck.parse import parse  # noqa: E402
from test_change_query import TEXT  # noqa: E402


def make():
    reg = {"reg_id": "9", "name": "시험규정", **to_records("9", parse(TEXT.splitlines()))}
    regs = {"9": reg}
    nodes, rows = kg_build(regs, [])
    return regs, ImpactContext.from_parts(regs, nodes, rows)


class ImpactViewTest(unittest.TestCase):
    def test_unreadable_amendment_returns_error_not_cards(self):
        regs, ctx = make()
        res = run(ctx, regs, "9", "제9조를 마음대로 고친다.", "T")
        self.assertTrue(res["error"])
        self.assertEqual(res["cards"], [])

    def test_rag_candidates_become_reference_cards_without_quote(self):
        regs, ctx = make()
        res = run(ctx, regs, "9", '제2조제1항 중 "2회"를 "3회"로 한다.', "T", rag=lambda q, k: ["9:6", "9:2"])
        ids = [c["id"] for c in res["cards"]]
        self.assertIn("9:6", ids)
        self.assertNotIn("9:2", ids)   # 고쳐지는 조 자신은 후보가 아니다
        c = next(c for c in res["cards"] if c["id"] == "9:6")
        self.assertEqual(c["tier"], "참고")
        self.assertTrue(c["needs_check"])
        self.assertIsNone(c["quote"])

    def test_without_rag_only_graph_and_phrase_candidates(self):
        regs, ctx = make()
        res = run(ctx, regs, "9", '제3조제1호 중 "신원 확인 및 검증을 하는 것"을 "신원 확인 및 검증과 지속적 거래 모니터링을 하는 것"으로 한다.', "T")
        self.assertEqual(res["error"], "")
        self.assertEqual(res["cq"].kind, "정의 변경")


if __name__ == "__main__":
    unittest.main()
