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

    def test_unreadable_change_collects_nothing(self):
        self.assertEqual(collect(self.kg, self.cq("분석 실패"), no_find, lambda q, k: ["B:3"], TEXTS), [])

    def test_wording_only_change_uses_semantic_search_by_default(self):
        # 표현 수정도 수정 전 원문 + 개정문으로 의미 검색 후보를 낸다 (기본 켬, 끄려면 EXPR_RAG=off)
        out = collect(self.kg, self.cq("표현 수정"), no_find, lambda q, k: ["B:3"], TEXTS)
        self.assertEqual([c.id for c in out], ["B:3"])
        self.assertEqual(out[0].paths, ["RAG"])
        import os
        os.environ["EXPR_RAG"] = "off"
        try:
            self.assertEqual(collect(self.kg, self.cq("표현 수정"), no_find, lambda q, k: ["B:3"], TEXTS), [])
        finally:
            del os.environ["EXPR_RAG"]

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


class DeletedTermTest(unittest.TestCase):
    def test_rare_word_of_deleted_article_finds_other_articles(self):
        from regcheck.collect import _keywords
        self.assertIn("마일리지", _keywords("회사는 마일리지를 적립하고 관리한다"))
        nodes, rows = build(REGS, EDGES)
        kg = KG(nodes, rows)
        cq = ChangeQuery(id="T", reg_id="A", kind="삭제", starts=["A:3"], removed=["적립"])
        texts = {"title": "적립", "old": "회사는 마일리지를 적립한다", "new": "", "amend": "제3조를 삭제한다."}
        hit = collect(kg, cq, lambda w: ["B:3"] if w == "마일리지" else [], lambda q, k: [], texts)
        self.assertEqual({c.id: c.paths for c in hit}["B:3"], ["DELETED_TERM"])
        many = collect(kg, cq, lambda w: [f"X:{i}" for i in range(30)] if w == "마일리지" else [], lambda q, k: [], texts)
        self.assertNotIn("DELETED_TERM", [p for c in many for p in c.paths])
