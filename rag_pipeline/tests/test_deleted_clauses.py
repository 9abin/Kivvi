"""항 본문이 삭제 표기뿐인 청크를 '삭제'로 분류하는지. 살아 있는 본문이 있는 항은 그대로 정상이어야 한다."""
import unittest

from src.chunk.articles import is_deletion_only


class DeletionOnlyTests(unittest.TestCase):
    def test_clauses_that_are_only_a_deletion_notice(self):
        for text in ("④ 삭제 <2025.04.17.>", "② (삭제) <삭제 2015.06.25.>", "① <삭제 2014.05.29.>", "③ <삭제>",
                     "② (삭제)", "⑥ 삭제 <2023.10.23.>", "④ <삭제 2017.06.28.>", "② <삭제> [2014.12.29. 개정]"):
            self.assertTrue(is_deletion_only(text), text)

    def test_live_clause_that_mentions_deletion_is_kept(self):
        for text in ("① 회사는 보관 기간이 지난 기록물을 삭제할 수 있다.",
                     "② 개인정보는 목적이 달성되면 지체 없이 삭제한다. <개정 2024.01.01.>",
                     "③ 삭제 <2020.01.01.> 단, 제2항에 따른 경우는 예외로 한다."):
            self.assertFalse(is_deletion_only(text), text)

    def test_clause_without_the_word_is_not_a_deletion(self):
        self.assertFalse(is_deletion_only("① 직원은 휴가를 신청할 수 있다."))
        self.assertFalse(is_deletion_only("② <개정 2025.03.27.>"))


if __name__ == "__main__":
    unittest.main()
