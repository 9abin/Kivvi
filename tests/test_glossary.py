"""용어집: 검색 신호와 '확인된 다른 개념'의 수치 근거 제외. 미확정 관계와 맥락 있는 질문은 건드리지 않는다."""
import unittest

from src.search import glossary

TABLE = glossary.load()


def _e(doc, article, body):
    return {"문서명": doc, "article": article, "body": body}


SATAEK = _e("사택 대여 및 관리지침", "제14조 제3항", "임차보증금은 임차예정주택의 시가의 85%를 초과할 수 없다.")
IPCHAL = _e("계약사무처리지침", "제7조", "제7조(입찰보증금) 경쟁입찰에 참가하려는 자에게 입찰보증금을 내도록 하여야한다.")
PROCEDURE = _e("사택 대여 및 관리지침", "제24조", "임차보증금은 즉시 회수하여 반납하여야 한다.")  # 수치 없음
SICK_LEAVE = _e("복무규정", "제24조 제1항", "연 누계 180일 범위 내에서 인병휴가를 허가할 수 있다.")
SICK_LEAVE_OF_ABSENCE = _e("인사규정", "제28조 제5항", "인병휴직 기간 중에는 기본급의 1/2을 지급한다. 1년 6개월을 넘지 못한다.")


class SignalTests(unittest.TestCase):
    def test_unconfirmed_synonym_adds_the_other_term_as_a_search_signal(self):
        self.assertIn("연차유급휴가", glossary.signal_terms("연차휴가 며칠이에요?", TABLE))
        self.assertIn("숙박비", glossary.signal_terms("숙박료는 얼마예요?", TABLE))

    def test_absent_term_adds_related_search_terms(self):
        self.assertIn("입찰보증금", glossary.signal_terms("계약보증금은 몇 %예요?", TABLE))

    def test_inclusion_term_adds_narrower_terms_without_blocking_anything(self):
        self.assertIn("인병휴가", glossary.signal_terms("유급휴가 종류가 뭐예요?", TABLE))

    def test_plain_question_adds_nothing(self):
        self.assertEqual(glossary.signal_terms("반반차 쓸 수 있어요?", TABLE), "")


class DistinctFilterTests(unittest.TestCase):
    def test_question_about_one_concept_drops_numeric_evidence_of_the_other(self):
        kept, excluded = glossary.filter_by_glossary("병가는 유급이에요? 며칠까지예요?", [SICK_LEAVE, SICK_LEAVE_OF_ABSENCE], TABLE)
        self.assertEqual([e["article"] for e in kept], ["제24조 제1항"])
        self.assertEqual(excluded[0][1], "제28조 제5항")

    def test_question_naming_both_concepts_keeps_both(self):
        kept, excluded = glossary.filter_by_glossary("인병휴가랑 인병휴직 차이가 뭐예요?", [SICK_LEAVE, SICK_LEAVE_OF_ABSENCE], TABLE)
        self.assertEqual(excluded, [])

    def test_entry_without_numbers_is_kept(self):
        other = _e("인사규정", "제28조 제1항", "인병휴직을 신청할 수 있다.")
        kept, excluded = glossary.filter_by_glossary("병가를 쓰고 싶어요", [SICK_LEAVE, other], TABLE)
        self.assertEqual(excluded, [])


class FamilyFilterTests(unittest.TestCase):
    def test_other_deposit_term_drops_numeric_entries_of_unrelated_deposits_but_keeps_the_related_one(self):
        kept, excluded = glossary.filter_by_glossary("계약보증금은 계약금액의 몇 %로 받아요?", [SATAEK, IPCHAL, PROCEDURE], TABLE)
        self.assertEqual([e["article"] for e in kept], ["제7조", "제24조"])  # 입찰보증금(관련 후보)과 수치 없는 절차 조문은 남는다
        self.assertEqual([x[1] for x in excluded], ["제14조 제3항"])

    def test_question_with_member_context_is_untouched(self):
        kept, excluded = glossary.filter_by_glossary("사택 보증금 한도가 계약금액의 몇 %예요?", [SATAEK, IPCHAL], TABLE)
        self.assertEqual(excluded, [])

    def test_question_naming_a_member_is_untouched(self):
        kept, excluded = glossary.filter_by_glossary("임차보증금은 얼마까지 가능해요?", [SATAEK, IPCHAL], TABLE)
        self.assertEqual(excluded, [])

    def test_generic_head_only_question_is_untouched(self):
        kept, excluded = glossary.filter_by_glossary("보증금은 얼마까지 가능해요?", [SATAEK, IPCHAL], TABLE)
        self.assertEqual(excluded, [])

    def test_never_drops_everything(self):
        kept, excluded = glossary.filter_by_glossary("계약보증금은 몇 %예요?", [SATAEK], TABLE)
        self.assertEqual((kept, excluded), ([SATAEK], []))


def _c(doc_id, article, body):
    return {"doc_id": doc_id, "article": article, "body": body, "chunk_id": f"{doc_id}_{article}"}


class CandidateStageTests(unittest.TestCase):
    """후보 단계 제외: 빼면 뒤 후보가 올라와 빈자리를 채운다."""

    CANDS = [_c("sat", "제14조 제3항", "임차보증금은 임차예정주택의 시가의 85%를 초과할 수 없다."),
             _c("sat", "제24조", "임차보증금은 즉시 회수하여 반납하여야 한다."),
             _c("con", "제7조", "제7조(입찰보증금) 경쟁입찰에 참가하려는 자에게 입찰보증금을 내도록 하여야한다."),
             _c("acc", "제51조", "계약금액의 100분의 70을 초과하지 아니하는 범위내에서 선금을 지급할 수 있다.")]

    def test_other_deposit_numeric_candidate_is_removed_and_later_ones_move_up(self):
        kept, excluded = glossary.filter_candidates("계약보증금은 몇 %예요?", self.CANDS, TABLE)
        self.assertEqual([c["article"] for c in kept], ["제24조", "제7조", "제51조"])
        self.assertEqual(excluded[0][:2], ("sat", "제14조 제3항"))
        self.assertEqual(kept[:2][1]["article"], "제7조")  # 입찰보증금 후보가 앞으로 올라온다

    def test_named_member_question_keeps_all_candidates(self):
        for q in ("입찰보증금이랑 임차보증금은 뭐가 달라요?", "임차보증금은 얼마까지예요?", "입찰보증금 면제되는 경우가 있어요?"):
            kept, excluded = glossary.filter_candidates(q, self.CANDS, TABLE)
            self.assertEqual(excluded, [], q)

    def test_procedure_question_about_the_absent_term_still_drops_only_numeric_other_deposit_items(self):
        kept, excluded = glossary.filter_candidates("계약보증금 납부 절차는 어떻게 돼요?", self.CANDS, TABLE)
        self.assertEqual([c["article"] for c in kept], ["제24조", "제7조", "제51조"])

    def test_nothing_removed_when_that_would_leave_no_candidates(self):
        only = [self.CANDS[0]]
        self.assertEqual(glossary.filter_candidates("계약보증금은 몇 %예요?", only, TABLE), (only, []))


if __name__ == "__main__":
    unittest.main()
