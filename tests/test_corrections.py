"""오탈자 정정 적용 규칙 테스트: 확정 항목만 검색 텍스트에 반영, 원본은 그대로."""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from src.corrections.apply import apply_search_text, answer_note  # noqa: E402


def row(idx, text):
    return {"고유번호": idx, "id": f"{idx}_제1조", "본문": text}


class ApplyTest(unittest.TestCase):
    def test_confirmed_pair_changes_search_text_only(self):
        rows = [row("1", "제1조(목적) 처리 하는 경우에 한다.")]
        cor = [{"id": "T1", "고유번호": "1", "종류": "오탈자", "상태": "확정(표기 정정)",
                "치환전": "처리 하는", "치환후": "처리하는"}]
        rows, applied = apply_search_text(rows, cor)
        self.assertEqual(rows[0]["본문_검색"], "제1조(목적) 처리하는 경우에 한다.")
        self.assertEqual(rows[0]["본문"], "제1조(목적) 처리 하는 경우에 한다.")  # 원본 유지
        self.assertEqual(len(applied), 1)
        self.assertTrue(answer_note(rows[0]))  # 답변용 정정 안내가 붙는다

    def test_candidate_is_not_applied(self):
        rows = [row("1", "처리 하는 경우")]
        cor = [{"id": "T2", "고유번호": "1", "종류": "오탈자", "상태": "후보(표기 정정)",
                "치환전": "처리 하는", "치환후": "처리하는"}]
        rows, applied = apply_search_text(rows, cor)
        self.assertEqual(rows[0]["본문_검색"], "처리 하는 경우")
        self.assertEqual(applied, [])

    def test_missing_pair_is_skipped(self):
        rows = [row("1", "처리 하는 경우")]
        cor = [{"id": "T3", "고유번호": "1", "종류": "오탈자", "상태": "확정(표기 정정)"}]
        rows, applied = apply_search_text(rows, cor)
        self.assertEqual(applied, [])
        self.assertEqual(rows[0]["본문_검색"], "처리 하는 경우")

    def test_other_document_not_affected(self):
        rows = [row("2", "처리 하는 경우")]
        cor = [{"id": "T4", "고유번호": "1", "종류": "오탈자", "상태": "확정", "치환전": "처리 하는", "치환후": "처리하는"}]
        rows, applied = apply_search_text(rows, cor)
        self.assertEqual(rows[0]["본문_검색"], "처리 하는 경우")


if __name__ == "__main__":
    unittest.main()
