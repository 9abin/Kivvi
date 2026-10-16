"""src/search/search.py mark_provisos: 단서·예외 문장 앞에 【단서】를 붙인다."""
import unittest

from src.search.search import HIT_MARK, PROVISO_MARK, mark_provisos, strip_marks


class ProvisoMarkTests(unittest.TestCase):
    def test_only_the_proviso_sentence_is_marked(self):
        out = mark_provisos("③ 복명은 서면으로 한다. 다만, 간이한 용무는 구두로 보고할 수 있다.")
        self.assertEqual(out, f"③ 복명은 서면으로 한다. {PROVISO_MARK}다만, 간이한 용무는 구두로 보고할 수 있다.")

    def test_exclusion_and_limit_sentences_are_marked_per_line(self):
        out = mark_provisos("① 휴가는 5일로 한다.\n② 퇴직자는 제외한다.")
        self.assertEqual(out.splitlines(), ["① 휴가는 5일로 한다.", f"{PROVISO_MARK}② 퇴직자는 제외한다."])

    def test_plain_text_is_unchanged(self):
        self.assertEqual(mark_provisos("출장여비는 별도로 정한 바에 따라 지급한다."), "출장여비는 별도로 정한 바에 따라 지급한다.")

    def test_marks_are_removed_for_display(self):
        self.assertEqual(strip_marks(HIT_MARK + mark_provisos("② 퇴직자는 제외한다.")), "② 퇴직자는 제외한다.")


if __name__ == "__main__":
    unittest.main()
