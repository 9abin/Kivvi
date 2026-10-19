"""조 전체가 예산에 안 들어갈 때 통째로 버리지 않고 긴 조부터 찾은 항 + 이웃 항만 남기는지 확인한다 (search.assemble, shorten_article, water_level)."""
import unittest
from unittest import mock

import src.search.search as ss
from src.search.search import HIT_MARK, SHORTENED_NOTE, SIB_MARK, Searcher, shorten_article, water_level


def article(n_paras, para_len=400, hit=0):
    paras = [f"({i + 1})" + "가" * para_len for i in range(n_paras)]
    return "\n".join((HIT_MARK if i == hit else SIB_MARK) + p for i, p in enumerate(paras))


def entry(chunk_id, body, article_name="제5조", match="검색"):
    return {"chunk_id": chunk_id, "doc_id": "D1", "article": article_name, "body": body, "match": match, "cited": [], "siblings": []}


class ShortenTest(unittest.TestCase):
    def test_keeps_hit_para_and_neighbors_in_order(self):
        body = article(8, hit=3)
        out = shorten_article(body, 1300)
        self.assertLessEqual(len(out), 1300 + len(SHORTENED_NOTE))
        self.assertIn(HIT_MARK, out)
        self.assertTrue(out.endswith(SHORTENED_NOTE.strip()))
        self.assertLess(out.index("(4)"), out.index("(5)"))  # 항 순서 유지

    def test_unchanged_when_it_fits(self):
        body = article(2)
        self.assertEqual(shorten_article(body, 5000), body)

    def test_single_long_para_is_cut_by_line(self):
        body = "\n".join("표 줄 " + str(i) * 50 for i in range(100))
        out = shorten_article(body, 1000)
        self.assertLessEqual(len(out), 1000 + len(SHORTENED_NOTE))
        self.assertTrue(out.endswith(SHORTENED_NOTE.strip()))


class WaterLevelTest(unittest.TestCase):
    def test_cuts_only_the_longest(self):
        self.assertEqual(water_level([12000, 2000, 1000], 8000), 5000)  # 5000 + 2000 + 1000

    def test_all_equal_share(self):
        self.assertEqual(water_level([1500] * 8, 8000), 1000)

    def test_floor(self):
        self.assertEqual(water_level([9000] * 40, 8000), 500)


class AssembleTest(unittest.TestCase):
    def assemble(self, results, budget=8000):
        return Searcher.assemble(object(), results, budget=budget)

    def titles(self, asm):
        return [g["조"] for g in asm["groups"]]

    def test_article_that_fits_is_unchanged_even_if_long(self):
        body = article(8, hit=1)  # 약 3,200자
        asm = self.assemble([entry("D1_제5조", body)])
        self.assertEqual(asm["groups"][0]["items"][0]["body"], body)

    def test_long_article_alone_is_shortened_not_dropped(self):
        asm = self.assemble([entry("D1_제5조", article(30, hit=2))])  # 12,000자 넘는 조
        self.assertEqual(self.titles(asm), ["제5조"])
        self.assertLessEqual(asm["chars"], 8000)
        self.assertIn(SHORTENED_NOTE.strip(), asm["groups"][0]["items"][0]["body"])

    def test_short_later_article_is_not_pushed_out_by_a_long_one(self):
        results = [entry("D1_제5조", article(30, hit=0), "제5조"), entry("D1_제6조", "가" * 238, "제6조")]
        asm = self.assemble(results)
        self.assertEqual(self.titles(asm), ["제5조", "제6조"])
        self.assertEqual(asm["groups"][1]["items"][0]["body"], "가" * 238)  # 짧은 조는 그대로
        self.assertLessEqual(asm["chars"], 8000)

    def test_only_the_longest_are_cut(self):
        results = [entry(f"D1_제{i}조", article(14, hit=0) if i == 1 else "가" * 1000, f"제{i}조") for i in range(1, 6)]
        asm = self.assemble(results)
        bodies = {g["조"]: g["items"][0]["body"] for g in asm["groups"]}
        self.assertEqual(len(bodies), 5)
        self.assertIn(SHORTENED_NOTE.strip(), bodies["제1조"])
        self.assertTrue(all(bodies[f"제{i}조"] == "가" * 1000 for i in range(2, 6)))

    def test_many_articles_share_the_cut(self):
        results = [entry(f"D1_제{i}조", "가" * 1500, f"제{i}조") for i in range(1, 9)]  # 12,000자
        asm = self.assemble(results)
        self.assertEqual(len(self.titles(asm)), 8)
        self.assertLessEqual(asm["chars"], 8000)

    def test_switch_off_keeps_old_behavior(self):
        results = [entry("D1_제5조", article(30, hit=2))]
        with mock.patch.object(ss, "SHORTEN_LONG", False):
            asm = self.assemble(results)
        self.assertEqual(asm["groups"], [])

    def test_cited_entries_are_not_shortened(self):
        results = [entry(f"D1_제{i}조", "가" * 1950, f"제{i}조") for i in range(1, 5)]  # 7,800자: 예산 안이라 줄이지 않음
        results[0]["cited"] = [entry("D1_제9조", article(10), "제9조")]  # 남은 200자에 안 들어가면 줄이지 않고 건너뜀
        asm = self.assemble(results)
        self.assertNotIn("제9조", self.titles(asm))


if __name__ == "__main__":
    unittest.main()
