"""조합 실험 도구(src/combo)의 순수 계산 부분 시험: 지표, 설정 읽기, 바꿔 쓰기 점검, 선택 출력 이상 집계, 리포트 통계. GPU · DB · LLM 없이 돈다."""
import sys
import unittest

sys.path.insert(0, ".")
from scripts import combo_report as rep  # noqa: E402
from src.combo import metrics as M  # noqa: E402
from src.combo.models import strip_think  # noqa: E402
from src.combo.searcher import ComboSearcher  # noqa: E402
from src.combo.stages import Run, deep_merge, load_config, pct  # noqa: E402


class MetricsTest(unittest.TestCase):
    def test_node_key_merges_byeolpyo_sub_notation(self):
        self.assertEqual(M.node_key("D1", "제5조 제2항"), "D1:제5조")
        self.assertEqual(M.node_key("D1", "제5조의2 제1항"), "D1:제5조의2")
        self.assertEqual(M.node_key("D1", "별표 5 주1"), "D1:별표5")
        self.assertEqual(M.node_key("D1", "별표 2 제1호 나목"), "D1:별표2")

    def test_gold_nodes_excludes_unindexed_and_counts_categories(self):
        g = {"근거": [{"규정": "가규정", "조항": "제1조 제1항", "tier": "필수"}, {"규정": "가규정", "조항": "제2조", "tier": "보조"},
                      {"규정": "가규정", "조항": "부칙 제1조"}, {"규정": "가규정", "조항": "제1조 제2항", "tier": "보조"}]}
        out = M.gold_nodes(g, {"가규정": "D1"}, {"D1:제1조", "D1:제2조"})
        self.assertEqual(out["core"], {"D1:제1조"})
        self.assertEqual(out["aux"], {"D1:제2조"})  # 같은 노드가 필수와 보조에 있으면 필수로만 센다
        self.assertEqual(out["excluded"], {"부칙": 1})

    def test_hit_rr_ndcg(self):
        order = ["a", "b", "c"]
        self.assertEqual(M.hits_at(order, {"a", "c", "z"}, 2), 1)
        self.assertEqual(M.rr(order, {"b"}), 0.5)
        self.assertEqual(M.rr(order, {"z"}), 0.0)
        self.assertAlmostEqual(M.ndcg(["a", "b"], {"a"}, {"b"}, 8), 1.0)  # 이상적인 순서
        self.assertLess(M.ndcg(["b", "a"], {"a"}, {"b"}, 8), 1.0)

    def test_micro_and_bootstrap(self):
        pairs = [(1, 2), (2, 2), (0, 1)]
        self.assertAlmostEqual(M.micro(pairs), 3 / 5)
        lo, hi = M.bootstrap(pairs, n=200)
        self.assertLessEqual(lo, M.micro(pairs))
        self.assertGreaterEqual(hi, M.micro(pairs))
        d = M.paired_diff(pairs, pairs, n=100)
        self.assertEqual(d[0], 0.0)
        self.assertEqual((d[1], d[2]), (0.0, 0.0))

    def test_paired_diff_cluster(self):
        # 작업 3개: (a 맞은 수, a 전체, b 맞은 수, b 전체). a가 모든 작업에서 b보다 1개 많다
        rows = [(3, 4, 2, 4), (2, 3, 1, 3), (4, 5, 3, 5)]
        d, lo, hi = M.paired_diff_cluster(rows, n=200)
        self.assertAlmostEqual(d, 3 / 12)
        self.assertLessEqual(lo, d)
        self.assertGreaterEqual(hi, d)
        same = M.paired_diff_cluster([(1, 2, 1, 2), (2, 2, 2, 2)], n=50)
        self.assertEqual(same, (0.0, 0.0, 0.0))
        self.assertIsNone(M.paired_diff_cluster([(0, 0, 0, 0)]))


class ConfigAndHelpersTest(unittest.TestCase):
    def test_strip_think(self):
        self.assertEqual(strip_think("<think>생각</think>\n답"), "답")
        self.assertEqual(strip_think("생각 중</think>답"), "답")
        self.assertEqual(strip_think("그대로"), "그대로")

    def test_deep_merge_and_pct(self):
        self.assertEqual(deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 3}}), {"a": {"x": 1, "y": 3}})
        self.assertEqual(pct([1, 2, 3, 4, 5], 50), 3)
        self.assertIsNone(pct([], 50))

    def test_load_config_overrides_and_instruction(self):
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.toml")
            open(p, "w", encoding="utf-8").write('[embedding]\nmodel = "Qwen/Qwen3-Embedding-0.6B"\ninstruction = "@config"\n')
            cfg = load_config(p, ["select.enabled=true", "data.limit=5", 'reranker.label="r-fp16"'])
        self.assertTrue(cfg["select"]["enabled"])
        self.assertEqual(cfg["data"]["limit"], 5)
        self.assertEqual(cfg["reranker"]["label"], "r-fp16")
        self.assertTrue(cfg["embedding"]["instruction"].startswith("Instruct:"))

    def test_shipped_configs_load(self):
        import glob
        files = glob.glob("configs/combo/**/*.toml", recursive=True)
        self.assertGreaterEqual(len(files), 22)  # baseline + 20 + 선택 확장
        for p in files:
            cfg = load_config(p)
            self.assertIn("model", cfg["embedding"])

    def test_check_rewrite(self):
        titles = ["직원채용지침", "여비규정"]
        ok = Run.check_rewrite({"raw": "여비규정 출장비", "rewritten": "여비규정 출장비"}, titles)
        self.assertEqual(ok, {"empty": False, "format": False, "invented": False})
        self.assertTrue(Run.check_rewrite({"raw": None, "rewritten": None}, titles)["empty"])
        self.assertTrue(Run.check_rewrite({"raw": "a\nb", "rewritten": "a"}, titles)["format"])
        self.assertTrue(Run.check_rewrite({"raw": "제5조", "rewritten": "제5조"}, titles)["invented"])
        self.assertTrue(Run.check_rewrite({"raw": "가나다규정 예외", "rewritten": "가나다규정 예외"}, titles)["invented"])


class _FakeLLM:
    def __init__(self, raw):
        self.raw = raw

    def complete(self, prompt, usage=False):
        return (self.raw, {"in": 1, "out": 1}) if usage else self.raw


class _FakeSearcher:
    documents = {"D1": {"title": "가규정"}}

    def __init__(self, n):
        self.row_info = {f"c{i}": {"doc_id": "D1", "article": f"제{i}조", "body_search": f"본문{i}"} for i in range(1, n + 1)}


class SelectTest(unittest.TestCase):
    def run_select(self, raw, k=3, n=5):
        run = object.__new__(Run)
        run.cfg = {"select": {"pool": 16}}
        s = _FakeSearcher(n)
        arts = [{"chunk_id": f"c{i}"} for i in range(1, n + 1)]
        return run.select_one(s, _FakeLLM(raw), "질문", arts, k)

    def test_clean_selection(self):
        r = self.run_select('{"순서": [3, 1, 2]}')
        self.assertEqual(r["chosen"], ["c3", "c1", "c2"])
        self.assertFalse(r["anom"]["count_violation"] or r["anom"]["parse_fail"])

    def test_parse_failure_falls_back_to_rerank_order(self):
        r = self.run_select("모르겠음")
        self.assertEqual(r["chosen"], ["c1", "c2", "c3"])
        self.assertTrue(r["anom"]["parse_fail"] and r["anom"]["count_violation"])

    def test_out_of_range_duplicate_and_short(self):
        r = self.run_select('{"순서": [2, 2, 9]}')
        self.assertEqual(r["chosen"][0], "c2")
        self.assertEqual(len(r["chosen"]), 3)  # 모자란 자리는 리랭크 순서로 채운다
        self.assertEqual((r["anom"]["duplicate"], r["anom"]["out_of_range"]), (1, 1))
        self.assertTrue(r["anom"]["count_violation"])

    def test_skipped_when_pool_not_larger_than_k(self):
        r = self.run_select('{"순서": [1]}', k=5, n=5)
        self.assertTrue(r["anom"]["skipped"])
        self.assertEqual(len(r["chosen"]), 5)


class SearcherAndReportTest(unittest.TestCase):
    def test_no_reranker_keeps_rrf_order_without_scores(self):
        s = object.__new__(ComboSearcher)
        s.reranker = None
        cands = [{"chunk_id": "a"}, {"chunk_id": "b"}]
        out = s.rerank("q", cands)
        self.assertEqual([c["chunk_id"] for c in out], ["a", "b"])
        self.assertTrue(all(c["rerank"] is None for c in out))

    def test_mcnemar_and_pareto(self):
        a = {1: True, 2: True, 3: True, 4: False}
        b = {1: False, 2: False, 3: True, 4: False}
        p, n10, n01 = rep.mcnemar_p(a, b)
        self.assertEqual((n10, n01), (2, 0))
        self.assertAlmostEqual(p, 0.5)
        self.assertEqual(rep.mcnemar_p({1: True}, {1: True})[0], 1.0)
        self.assertEqual(rep.pareto([(1.0, 0.7), (2.0, 0.9), (3.0, 0.8)]), {0, 1})


if __name__ == "__main__":
    unittest.main()
