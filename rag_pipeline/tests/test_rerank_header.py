import unittest

from src.search.search import Searcher


def bare_searcher(header):
    """모델과 DB 없이 리랭커 입력 글자만 확인하기 위한 Searcher."""
    searcher = Searcher.__new__(Searcher)
    searcher.rerank_header = header
    searcher.documents = {"52754": {"title": "한국벤처투자 윤리헌장"}}
    searcher.row_info = {
        "52754_2": {
            "doc_id": "52754",
            "article": "2번째 항목(원문 '하나')",
            "body_search": "하나, 우리는 고객 의견을 존중한다.",
        }
    }
    return searcher


class RerankHeaderTests(unittest.TestCase):
    def test_header_adds_document_title_and_article(self):
        self.assertEqual(
            bare_searcher(True).rerank_text("52754_2"),
            "[한국벤처투자 윤리헌장 2번째 항목(원문 '하나')] 하나, 우리는 고객 의견을 존중한다.",
        )

    def test_header_off_keeps_search_body_only(self):
        self.assertEqual(
            bare_searcher(False).rerank_text("52754_2"), "하나, 우리는 고객 의견을 존중한다."
        )


if __name__ == "__main__":
    unittest.main()
