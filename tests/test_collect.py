import sys
import unittest

sys.path.insert(0, "src")
sys.path.insert(0, "tests")
from regcheck.change_query import ChangeQuery  # noqa: E402
from regcheck.collect import TIER_ORDER, collect  # noqa: E402
from regcheck.kg import KG, build  # noqa: E402
from test_kg import EDGES, REGS  # noqa: E402

TEXTS = {"title": "육아휴직", "old": "육아휴직은 최대 2회", "new": "육아휴직은 최대 3회", "amend": "제3조 중 2회를 3회로 한다."}


def no_find(_):
    return []


class CollectTest(unittest.TestCase):
    def setUp(self):
        nodes, rows = build(REGS, EDGES)
        self.kg = KG(nodes, rows)

    def cq(self, kind, **kw):
        return ChangeQuery(id="T", reg_id="A", kind=kind, starts=["A:3"], **kw)

    def test_citing_article_is_strong_and_start_is_excluded(self):
        out = collect(self.kg, self.cq("삭제", removed=["육아휴직"]), no_find, lambda q, k: [], TEXTS)
        by = {c.id: c for c in out}
        self.assertEqual(by["A:4"].tier, "확정급")
        self.assertIn("CITES_in", by["A:4"].paths)
        self.assertNotIn("A:3", by)

    def test_rag_only_candidate_is_reference_tier(self):
        out = collect(self.kg, self.cq("값 변경"), no_find, lambda q, k: ["B:3"], TEXTS)
        c = {c.id: c for c in out}["B:3"]
        self.assertEqual((c.tier, c.paths), ("참고", ["RAG"]))

    def test_tier_orders_before_score(self):
        out = collect(self.kg, self.cq("삭제", removed=["육아휴직"]), no_find, lambda q, k: ["B:3", "A:4"], TEXTS)
        tiers = [TIER_ORDER[c.tier] for c in out]
        self.assertEqual(tiers, sorted(tiers))
        self.assertEqual(out[0].tier, "확정급")

    def test_unreadable_or_wording_only_change_collects_nothing(self):
        for kind in ("분석 실패", "표현 수정"):
            self.assertEqual(collect(self.kg, self.cq(kind), no_find, lambda q, k: ["B:3"], TEXTS), [])

    def test_rag_query_follows_change_kind(self):
        seen = {}
        collect(self.kg, self.cq("삭제", removed=["지운말"]), no_find, lambda q, k: seen.update(q=q) or [], TEXTS)
        self.assertIn("지운말", seen["q"])
        self.assertNotIn("최대 2회", seen["q"])
        collect(self.kg, self.cq("번호 이동"), no_find, lambda q, k: seen.update(q=q) or [], TEXTS)
        self.assertIn("최대 2회", seen["q"])   # 번호 이동은 수정 전 조항으로 찾는다

    def test_new_phrase_hits_only_when_few_articles_contain_it(self):
        cq = self.cq("정의 변경", phrases=["지속적 거래 모니터링"])
        few = collect(self.kg, cq, lambda t: ["B:3"], lambda q, k: [], TEXTS)
        self.assertEqual({c.id: c.paths for c in few}["B:3"], ["PHRASE"])
        many = collect(self.kg, cq, lambda t: [f"X:{i}" for i in range(30)], lambda q, k: [], TEXTS)
        self.assertNotIn("PHRASE", [p for c in many for p in c.paths])


if __name__ == "__main__":
    unittest.main()
