"""src/answer/guard.py: 질문의 핵심 말이 근거에 없는데 답에 쓰였는지 확인."""
import unittest

from kiwipiepy import Kiwi

from src.answer.guard import (contradicted_distinctions, filter_tables_by_scope, missing_terms, question_terms,
                              unsupported_numbers)


class GuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kiwi = Kiwi()

    def test_adjacent_nouns_are_joined(self):
        self.assertIn("계약보증금", question_terms(self.kiwi, "계약보증금은 계약금액의 몇 %로 받아요?"))

    def test_term_missing_from_evidence_but_used_in_answer_is_flagged(self):
        evidence = ["임차보증금은 시가의 85%를 초과할 수 없다.", "선금은 계약금액의 100분의 70 이내로 지급한다."]
        answer = "계약보증금은 시가의 85%입니다 [1]."
        self.assertEqual(missing_terms(self.kiwi, "계약보증금은 계약금액의 몇 %로 받아요?", evidence, answer), ["계약보증금"])

    def test_term_present_in_evidence_is_not_flagged(self):
        evidence = ["계약보증금은 계약금액의 10%로 한다."]
        self.assertEqual(missing_terms(self.kiwi, "계약보증금은 몇 %로 받아요?", evidence, "계약보증금은 10%입니다 [1]."), [])

    def test_term_missing_from_evidence_and_not_in_answer_is_not_flagged(self):
        # "확인되지 않는다"고 답한 경우처럼 답이 그 말을 쓰지 않으면 문제 없다.
        evidence = ["임차보증금은 시가의 85%를 초과할 수 없다."]
        self.assertEqual(missing_terms(self.kiwi, "계약보증금은 몇 %로 받아요?", evidence, "규정에서 확인되지 않습니다."), [])


class NumberTests(unittest.TestCase):
    ENT = [{"body": "일비 40 (외화 기준). 제17조에 따른다."}, {"body": "휴가는 5일, 최대 120일 이내에 사용한다."}]

    def test_number_missing_from_cited_evidence_is_flagged(self):
        # 근거 표는 40인데 답이 40,000원이라고 쓴 사례
        found = unsupported_numbers("일비는?", "이사 일비는 40,000원이다 [1].", self.ENT)
        self.assertEqual(found[0][0], ["40000"])

    def test_number_found_in_cited_evidence_passes(self):
        self.assertEqual(unsupported_numbers("휴가는?", "휴가는 5일이다 [2].", self.ENT), [])

    def test_number_from_the_question_clause_and_reference_numbers_are_ignored(self):
        self.assertEqual(unsupported_numbers("입사 4개월인데 연차?", "4개월이면 [1] 제3조 제2항에 따른다.", self.ENT), [])

    def test_correction_note_lines_are_ignored(self):
        self.assertEqual(unsupported_numbers("q", "※ 정정 안내 [1]: 표기 정정 (P021)", self.ENT), [])

    def test_number_cited_from_a_different_entry_is_flagged(self):
        # 줄이 [1]을 인용했는데 숫자 5는 [2]에만 있다. 다른 표의 같은 숫자로 틀린 답이 통과하는 것을 막는다.
        self.assertEqual(unsupported_numbers("q", "휴가는 5일이다 [1].", self.ENT)[0][0], ["5"])

    def test_multiplier_computed_from_a_surcharge_percent_passes(self):
        ent = [{"body": "시간외 근로수당은 통상임금에 50%를 가산하여 지급한다."}]
        self.assertEqual(unsupported_numbers("몇 배?", "통상임금의 1.5배로 계산됩니다 [1].", ent), [])
        self.assertEqual(unsupported_numbers("몇 배?", "통상임금의 2배로 계산됩니다 [1].", ent)[0][0], ["2"])

    def test_computed_number_with_a_correct_formula_passes(self):
        # Q058: 근거 "1시간당 40만원", "100분의 150" → 답 "60만원(40만원 × 150/100)"
        ent = [{"body": "사례금 상한액은 1시간당 40만원으로 하고, 총액은 그 100분의 150을 초과할 수 없다."}]
        self.assertEqual(unsupported_numbers("총액은?", "총액 상한은 60만원(40만원 × 150/100)입니다 [1].", ent), [])
        self.assertEqual(unsupported_numbers("총액은?", "총액 상한은 60만원(40만원 × 100분의 150)입니다 [1].", ent), [])
        # Q061: "50% 가산" → "150%(100% + 50%)"
        ent2 = [{"body": "시간외 근로수당은 통상임금에 50%를 가산하여 지급한다."}]
        self.assertEqual(unsupported_numbers("몇 %?", "통상임금의 150%(100% + 50% 가산)입니다 [1].", ent2), [])

    def test_computed_number_without_formula_or_with_a_wrong_one_is_flagged(self):
        ent = [{"body": "사례금 상한액은 1시간당 40만원으로 하고, 총액은 그 100분의 150을 초과할 수 없다."}]
        self.assertEqual(unsupported_numbers("총액은?", "총액 상한은 60만원입니다 [1].", ent)[0][0], ["60"])
        self.assertEqual(unsupported_numbers("총액은?", "총액 상한은 70만원(40만원 × 150/100)입니다 [1].", ent)[0][0], ["70"])

    def test_formula_with_a_material_not_in_the_evidence_is_flagged(self):
        # Q054처럼 근거에 없는 1000을 곱해 40을 40,000원으로 만든 경우는 여전히 막는다
        found = unsupported_numbers("일비는?", "일비는 40,000원(40 × 1000)이다 [1].", self.ENT)
        self.assertIn("40000", found[0][0])

    def test_half_hour_in_the_question_may_be_written_as_30(self):
        ent = [{"body": "휴게시간은 12:00부터 13:00까지 1시간으로 한다."}]
        self.assertEqual(unsupported_numbers("12시 반부터 1시간 반 쓰면 안 되나요?", "12시 30분부터는 정해져 있지 않습니다 [1].", ent), [])

    def test_number_found_nowhere_in_the_evidence_is_flagged(self):
        self.assertEqual(unsupported_numbers("q", "휴가는 7일이다 [2].", self.ENT)[0][0], ["7"])


def _t(article, title, doc="여비규정"):
    return {"문서명": doc, "article": article, "body": title + " 본문"}


class TableScopeTests(unittest.TestCase):
    TABLES = [
        _t("제23조 제1항", "① 체재비는 국외업무출장 시 지급하는"),
        _t("별표1", "【별표 1】 운임 및 체재비 (단위 : 원) 국내여비 직급별 기준."),
        _t("별표5", "【별표 5】 국외여비 운임 및 체재비"),
        _t("별표1의2", "【별표 1의 2】 파견 및 사무소 근무시의 체재비"),
        _t("별표6", "【별표 6】 외국인 전문가 초청여비"),
        _t("별표2", "【별표 2】 가액 범위", doc="임직원행동강령"),
    ]

    def _kept(self, q):
        kept, excluded = filter_tables_by_scope(q, self.TABLES)
        return [e["article"] for e in kept], [x[1] for x in excluded]

    def test_foreign_question_drops_domestic_dispatch_and_invitee_tables_but_keeps_articles(self):
        kept, dropped = self._kept("미국 출장 일비 하루에 얼마 나와요?")
        self.assertEqual(dropped, ["별표1", "별표1의2", "별표6"])
        self.assertIn("제23조 제1항", kept)
        self.assertIn("별표5", kept)
        self.assertIn("별표2", kept)  # 범위를 읽을 수 없는 다른 규정의 표는 그대로 둔다

    def test_domestic_question_drops_foreign_table(self):
        kept, dropped = self._kept("광주 출장 일비는 얼마예요?")
        self.assertIn("별표5", dropped)
        self.assertIn("별표1", kept)

    def test_unclear_scope_keeps_everything(self):
        kept, dropped = self._kept("출장 일비는 얼마예요?")
        self.assertEqual(dropped, [])

    def test_comparison_question_keeps_everything(self):
        self.assertEqual(self._kept("국내출장이랑 해외출장 일비 차이가 뭐예요?")[1], [])

    def test_question_with_both_scopes_keeps_everything(self):
        self.assertEqual(self._kept("서울에서 미국으로 출장 갈 때 일비는?")[1], [])

    def test_special_table_is_kept_when_the_question_mentions_it(self):
        self.assertNotIn("별표1의2", self._kept("해외 파견 직원이 미국 출장 갈 때 일비는?")[1])

    def test_a_table_that_only_mentions_the_word_is_not_classified(self):
        # 직무전결규정 별표: 첫머리에 '국외출장' 행이 있어도 모든 업무의 전결권자 표다
        delegation = _t("별표", "[별표] 전결사항 1. 국외출장 > 가. 방침결정", doc="직무전결규정")
        dispatch_guideline = _t("별표1", "[별표 1] 해외 파견 직원 지원 기준", doc="해외 파견 업무처리 및 해외사무소 운영지침")
        kept, excluded = filter_tables_by_scope("국내 출장 결재는 누가 해요?", [delegation, dispatch_guideline, self.TABLES[1]])
        self.assertEqual(excluded, [])

    def test_never_drops_everything(self):
        only = [_t("별표1", "【별표 1】 국내여비 직급별 기준.")]
        self.assertEqual(filter_tables_by_scope("미국 출장 일비", only), (only, []))


class DistinctionTests(unittest.TestCase):
    ENT = [{"body": "일비는 표에서 등급 구분 없이 직급당 한 값으로 지급된다. 숙박비는 가/나/다/라 등급별로 다르다."}]

    def test_answer_that_explains_the_item_by_the_denied_distinction_is_flagged(self):
        for line in ("미국은 나등급에 해당하며, 일비는 40이다 [1].", "나등급의 일비는 40이다.", "미국 출장 일비는 나등급에 해당하여 40입니다."):
            self.assertTrue(contradicted_distinctions(line, self.ENT), line)

    def test_restating_the_note_is_not_flagged(self):
        self.assertEqual(contradicted_distinctions("일비는 표에서 등급 구분 없이 직급당 한 값으로 지급된다 [1].", self.ENT), [])

    def test_other_items_may_still_use_the_distinction(self):
        self.assertEqual(contradicted_distinctions("미국은 나등급이라 숙박비는 207입니다 [1].", self.ENT), [])
        self.assertEqual(contradicted_distinctions("미국은 나등급에 해당합니다 [1].", self.ENT), [])

    def test_no_note_in_the_evidence_flags_nothing(self):
        self.assertEqual(contradicted_distinctions("나등급의 일비는 40이다.", [{"body": "일비는 직급별로 정한다."}]), [])


if __name__ == "__main__":
    unittest.main()
