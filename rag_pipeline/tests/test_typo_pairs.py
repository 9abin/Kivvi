"""치환 쌍 추출 도구 테스트"""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from src.corrections.typo_pairs import derive_pairs  # noqa: E402
from src.corrections.store import check_pairs  # noqa: E402


class TypoPairTest(unittest.TestCase):
    def test_quoted_fix_becomes_pair(self):
        cor = [{"id": "P1", "고유번호": "1", "종류": "오탈자", "원문": "아니된다",
                "정정안": "“아니 된다”로 수정. 같은 위치"}]
        self.assertEqual(derive_pairs(cor), [("P1", "1", "아니된다", "아니 된다")])

    def test_description_only_is_not_a_pair(self):
        cor = [{"id": "P2", "고유번호": "1", "종류": "오탈자", "원문": "x", "정정안": "조사를 띄어 씀"}]
        self.assertEqual(derive_pairs(cor), [])

    def test_check_pairs_warns_when_text_missing(self):
        cor = [{"id": "P3", "고유번호": "1", "상태": "확정", "치환전": "없는글자", "치환후": "x"}]
        self.assertEqual(len(check_pairs(cor, {"1": "본문"})), 1)


if __name__ == "__main__":
    unittest.main()
