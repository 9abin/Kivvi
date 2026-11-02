import sys
import unittest

sys.path.insert(0, "src")
from regcheck.impact import Index, summarize  # noqa: E402

REGS = {
    "A": {"reg_id": "A", "name": "가규정", "articles": [{"id": "A:1", "label": "제1조", "title": "목적"}, {"id": "A:2", "label": "제2조", "title": "정의"}]},
    "B": {"reg_id": "B", "name": "나지침", "articles": [{"id": "B:1", "label": "제1조", "title": ""}, {"id": "B:3", "label": "제3조", "title": "기한"}]},
    "C": {"reg_id": "C", "name": "다세칙", "articles": [{"id": "C:5", "label": "제5조", "title": ""}]},
}


def edge(src, target_type, target_id, status="ok", kind="본문", **kw):
    return {"src": src, "src_kind": kind, "raw": "제x조", "context": "문맥", "target_type": target_type, "target_doc": kw.get("doc", ""),
            "target_ref": kw.get("ref", {}), "target_reg": "", "target_id": target_id, "status": status}


class ImpactTest(unittest.TestCase):
    def setUp(self):
        self.edges = [
            edge("B:1-1-0-0", "article", "A:1"),           # 나지침 제1조 → 가규정 제1조
            edge("B:1-2-0-0", "article", "A:1"),           # 같은 조에서 두 번: 한 노드로 합친다
            edge("C:5-1-0-0", "article", "B:1"),           # 다세칙 제5조 → 나지침 제1조 (2단계)
            edge("B:3-1-0-0", "article", "A:2"),           # 다른 대상
            edge("C:5-2-0-0", "regulation", "A"),          # 규정 단위
            edge("B:3-2-0-0", "article", "A:1", status="missing_article"),   # 구조 검사 의심
            edge("A:2-1-0-0", "article", "A:1"),           # 같은 규정 안
            edge("A:1-1-0-0", "article", "A:1"),           # 자기 인용
            edge("C:5-3-0-0", "law", "", status="law_pending", doc="근로기준법", ref={"jo": 60}),
            edge("B:1-3-0-0", "article", "A:1", status="addendum_history"),
        ]
        self.idx = Index(self.edges, REGS)

    def test_resolve(self):
        self.assertEqual(self.idx.resolve("가규정 제1조"), "A:1")
        self.assertEqual(self.idx.resolve("A:1"), "A:1")
        self.assertEqual(self.idx.resolve("근로기준법 제60조"), "근로기준법 제60조")
        self.assertIsNone(self.idx.resolve("없는규정 제1조"))

    def test_hops_and_dedup(self):
        res = self.idx.impact("A:1", hops=1)
        nodes = {h["node"]: h for h in res["hits"]}
        self.assertEqual(set(nodes), {"B:1", "B:3", "A:2"})  # 자기 인용(A:1 → A:1)은 start라 제외
        self.assertEqual(len(nodes["B:1"]["edges"]), 2)
        self.assertTrue(all(h["hops"] == 1 for h in res["hits"]))

    def test_second_hop_is_transitive_only(self):
        res = self.idx.impact("A:1", hops=2)
        by = {h["node"]: h for h in res["hits"]}
        self.assertEqual(by["C:5"]["hops"], 2)
        self.assertEqual(by["C:5"]["via"], "B:1")

    def test_suspect_flag_only_when_all_edges_suspect(self):
        res = self.idx.impact("A:1", hops=1)
        by = {h["node"]: h for h in res["hits"]}
        self.assertTrue(by["B:3"]["suspect"])
        self.assertFalse(by["B:1"]["suspect"])

    def test_regulation_level_kept_separate(self):
        res = self.idx.impact("A:1", hops=1)
        self.assertEqual([r["node"] for r in res["reg_level"]], ["C:5"])
        self.assertEqual(summarize(res)["규정 단위 인용"], 1)

    def test_law_start_and_history_skipped(self):
        res = self.idx.impact("근로기준법 제60조")
        self.assertEqual([h["node"] for h in res["hits"]], ["C:5"])
        self.assertNotIn("addendum_history", {e["status"] for h in self.idx.impact("A:1")["hits"] for e in h["edges"]})


if __name__ == "__main__":
    unittest.main()
