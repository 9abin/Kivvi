"""대표 사례 고정 테스트. 실행: python -m pytest tests (또는 python -m unittest discover tests)"""
import io
import json
import os
import re
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from src.chunk.articles import split_articles, split_paragraphs  # noqa: E402
from src.preprocess.clean import clean_text  # noqa: E402

CHUNKS = os.path.join(ROOT, "out", "chunks.jsonl")


def load_chunks():
    with io.open(CHUNKS, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


class ArticleSplitTest(unittest.TestCase):
    def test_gaji_space_form(self):  # '제4조의 2' (띄어쓰기 포함)
        arts = split_articles("제4조(a) 본문\n제4조의 2(b) 본문\n제4조의 3(c) 본문")
        self.assertEqual([(n, k) for n, k, _ in arts], [(4, 0), (4, 2), (4, 3)])

    def test_hyphen_form(self):  # '제12-1조'
        arts = split_articles("제12조(a) 본문\n제12-1조(b) 본문")
        self.assertEqual([(n, k) for n, k, _ in arts], [(12, 0), (12, 1)])

    def test_typo_form(self):  # '제9조2' → 제9조의2
        arts = split_articles("제9조(a) 본문\n제9조2(동점자) 본문")
        self.assertEqual([(n, k) for n, k, _ in arts], [(9, 0), (9, 2)])

    def test_list_marker_is_not_header(self):  # '1. 제6조(…)에 따른' 은 조문 제목이 아니다
        arts = split_articles("제6조(a) 본문\n1. 제6조(정보보안담당관의 임무)에 따른 활동")
        self.assertEqual(len(arts), 1)

    def test_circled_inline_is_not_paragraph(self):  # 본문 속 '①, ②' 예시는 항이 아니다
        paras = split_paragraphs("제20조(항목) ① 첫 항목. 7. 일곱째 항목은 ①, ②, ③ 으로 표시.", "x")
        self.assertEqual(len(paras), 1)

    def test_duplicate_paragraph_number_renumbered(self):  # 원문 ②②는 뒤쪽을 ③으로
        paras = split_paragraphs("제9조(a)\n① 가나다라.\n② 나다라마.\n② 다라마바.", "x")
        self.assertEqual([no for no, _ in paras], [1, 2, 3])


class CleanTest(unittest.TestCase):
    def test_junk_removed(self):
        self.assertNotIn("漠杳", clean_text("제1조(목적) 내용\n漠杳"))


class ChunkOutputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CHUNKS):
            raise unittest.SkipTest("out/chunks.jsonl 이 없습니다. 먼저 python run.py 를 실행하세요.")
        cls.rows = load_chunks()
        cls.by_id = {r["id"]: r for r in cls.rows}

    def test_chunk_structure(self):  # 고정 개수 대신 구조를 본다
        self.assertGreater(len(self.rows), 4000)
        self.assertEqual(len({r['고유번호'] for r in self.rows}), 90)
        for r in self.rows:
            for key in ('id', '고유번호', '문서명', '조항', '상태', '본문', 'cites'):
                self.assertIn(key, r)
            self.assertIn(r['상태'], ('정상', '삭제'))

    def test_no_duplicate_ids(self):
        ids = [r["id"] for r in self.rows]
        self.assertEqual(len(ids), len(set(ids)))

    def test_yunli_heonjang_items(self):  # 조문 없는 문서는 전문 + 항목 1~4
        for cid in ["52754_전문", "52754_1", "52754_2", "52754_3", "52754_4"]:
            self.assertIn(cid, self.by_id)

    def test_deleted_article_marked(self):
        deleted = [r for r in self.rows if r['상태'] == '삭제']
        self.assertTrue(deleted)
        self.assertTrue(all(len(r['본문']) < 200 for r in deleted))

    def test_gamsa_jiphyang_resolves_to_gamsa_gyuijeong(self):  # 감사지침 제6조 '규정 제4조' 는 감사규정(23234)을 가리킨다
        cites = [c for c in self.by_id["23245_제6조"]["cites"]]
        self.assertTrue(any(c["대상"] and c["대상"].startswith("23234_") for c in cites), cites)


if __name__ == "__main__":
    unittest.main()
