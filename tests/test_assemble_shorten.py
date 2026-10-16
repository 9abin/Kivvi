"""조 전체가 예산에 안 들어갈 때 통째로 버리지 않고 찾은 항 + 이웃 항만 줄여 넣는지 확인한다 (search.assemble, shorten_article)."""
import unittest

from src.search.search import HIT_MARK, SHORTENED_NOTE, SIB_MARK, Searcher, shorten_article


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


class AssembleTest(unittest.TestCase):
    def assemble(self, results, budget):
        return Searcher.assemble(object(), results, budget=budget)

    def test_long_article_is_shortened_not_dropped(self):
        results = [entry("D1_제5조", article(30, hit=2))]  # 12,000자 넘는 조
        asm = self.assemble(results, 8000)
        self.assertEqual(len(asm["groups"]), 1)
        self.assertLessEqual(asm["chars"], 8000)
        self.assertIn(SHORTENED_NOTE.strip(), asm["groups"][0]["items"][0]["body"])

    def test_article_that_fits_is_unchanged(self):
        body = article(3)
        asm = self.assemble([entry("D1_제5조", body)], 8000)
        self.assertEqual(asm["groups"][0]["items"][0]["body"], body)

    def test_later_article_is_shortened_into_remaining_budget(self):
        first = entry("D1_제5조", article(14, hit=0), "제5조")  # 약 5,600자
        second = entry("D1_제6조", article(10, hit=1), "제6조")  # 약 4,000자, 남은 예산에 안 들어감
        asm = self.assemble([first, second], 8000)
        self.assertEqual([g["조"] for g in asm["groups"]], ["제5조", "제6조"])
        self.assertLessEqual(asm["chars"], 8000)

    def fill(self, n_articles):
        """ARTICLE_CAP 안에 드는 (항 하나짜리) 조 n개. 조마다 1,950자."""
        return [entry(f"D1_제{i}조", "가" * 1950, f"제{i}조") for i in range(1, n_articles + 1)]

    def test_long_article_is_capped_even_if_it_fits_the_budget(self):
        asm = self.assemble([entry("D1_제5조", article(8, hit=1))], 8000)  # 약 3,200자: 예산 안이지만 ARTICLE_CAP보다 김
        self.assertIn(SHORTENED_NOTE.strip(), asm["groups"][0]["items"][0]["body"])
        self.assertLessEqual(asm["chars"], 2000 + len(SHORTENED_NOTE))

    def test_tiny_remaining_budget_still_skips(self):
        results = self.fill(4) + [entry("D1_제9조", article(10), "제9조")]  # 7,800자를 쓰고 남은 200자
        asm = self.assemble(results, 8000)
        self.assertEqual([g["조"] for g in asm["groups"]], ["제1조", "제2조", "제3조", "제4조"])

    def test_cited_entries_are_not_shortened(self):
        results = self.fill(4)
        results[0]["cited"] = [entry("D1_제9조", article(10), "제9조")]  # 남은 200자에 안 들어가면 줄이지 않고 건너뜀
        asm = self.assemble(results, 8000)
        self.assertNotIn("제9조", [g["조"] for g in asm["groups"]])


if __name__ == "__main__":
    unittest.main()
