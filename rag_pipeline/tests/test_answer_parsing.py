"""src/answer/generate.py의 모델 응답 파싱 검사.

목적: "정상적으로 빈 목록을 받음"(ok=True, [])과 "응답을 해석하지 못함"(ok=False)을
확실히 구분하는지 본다. 둘을 구분 못 하면 검사 실패가 사용자에게 보이지 않는다
(PR #1 리뷰 코멘트에서 지적된 문제).
"""
import unittest

from src.answer.generate import assemble_sections, has_bullets, parse_pairs, parse_unsupported


class ParseUnsupportedTests(unittest.TestCase):
    def test_empty_array_is_a_valid_pass(self):
        self.assertEqual(parse_unsupported("[]"), ([], True))

    def test_normal_strings_are_parsed(self):
        self.assertEqual(parse_unsupported('["문장1", "문장2"]'), (["문장1", "문장2"], True))

    def test_blank_strings_are_dropped_but_still_ok(self):
        self.assertEqual(parse_unsupported('["문장1", "  "]'), (["문장1"], True))

    def test_not_json_is_a_failure(self):
        self.assertEqual(parse_unsupported("이건 JSON이 아님"), ([], False))

    def test_json_object_instead_of_array_is_a_failure(self):
        self.assertEqual(parse_unsupported('{"a": 1}'), ([], False))

    def test_non_string_item_is_a_failure_not_silently_dropped(self):
        # 리뷰에서 지적된 사례: 전에는 ([], True)로 "정상 통과"와 구분이 안 됐다.
        self.assertEqual(parse_unsupported("[123]"), ([], False))

    def test_mixed_valid_and_invalid_items_is_a_failure(self):
        self.assertEqual(parse_unsupported('["문장1", 123]'), ([], False))

    def test_code_fence_is_stripped(self):
        self.assertEqual(parse_unsupported('```json\n["문장1"]\n```'), (["문장1"], True))

    def test_missing_closing_bracket_inside_fence_is_repaired(self):
        # 실측: 검사 모델이 ```json\n[\n  "문장"\n``` 처럼 ]를 빠뜨리고 닫은 적이 있다.
        self.assertEqual(parse_unsupported('```json\n[\n  "문장1"\n```'), (["문장1"], True))


class ParsePairsTests(unittest.TestCase):
    def test_empty_array_is_a_valid_pass(self):
        self.assertEqual(parse_pairs("[]", n_entries=5), ([], True))

    def test_normal_pair_is_parsed(self):
        self.assertEqual(parse_pairs("[[1, 3]]", n_entries=5), ([(1, 3)], True))

    def test_out_of_range_pair_is_a_failure(self):
        # 형식은 맞지만 존재하지 않는 근거 번호다. 모델이 근거 목록을 혼동했다는 뜻이라
        # 조용히 걸러내지 않고 실패로 본다 (PR #1 두 번째 재검증에서 지적됨).
        self.assertEqual(parse_pairs("[[1, 99]]", n_entries=5), ([], False))

    def test_self_pair_is_a_failure(self):
        self.assertEqual(parse_pairs("[[2, 2]]", n_entries=5), ([], False))

    def test_one_valid_pair_with_one_out_of_range_pair_is_a_failure(self):
        # 쌍 하나는 정상이어도, 다른 하나가 틀렸으면 응답 전체를 믿지 않는다.
        self.assertEqual(parse_pairs("[[1, 2], [1, 99]]", n_entries=5), ([], False))

    def test_not_json_is_a_failure(self):
        self.assertEqual(parse_pairs("이건 JSON이 아님", n_entries=5), ([], False))

    def test_non_pair_item_is_a_failure_not_silently_dropped(self):
        # 리뷰에서 지적된 사례: 전에는 ([], True)로 "충돌 없음"과 구분이 안 됐다.
        self.assertEqual(parse_pairs('["invalid"]', n_entries=5), ([], False))

    def test_string_numbers_inside_pair_is_a_failure(self):
        self.assertEqual(parse_pairs('[["1", "3"]]', n_entries=5), ([], False))

    def test_pair_with_wrong_length_is_a_failure(self):
        self.assertEqual(parse_pairs("[[1, 2, 3]]", n_entries=5), ([], False))

    def test_mixed_valid_and_invalid_pairs_is_a_failure(self):
        self.assertEqual(parse_pairs('[[1, 2], "invalid"]', n_entries=5), ([], False))

    def test_booleans_are_not_treated_as_integers(self):
        # bool은 파이썬에서 int의 하위 타입이라 isinstance(True, int)가 참이 되는 함정이 있다.
        self.assertEqual(parse_pairs("[[true, false]]", n_entries=5), ([], False))


class PromptTemplateTests(unittest.TestCase):
    def test_every_prompt_only_expects_the_input_variable(self):
        """프롬프트 안의 중괄호가 LangChain 템플릿 변수로 읽히면 모든 질문이 오류가 난다."""
        from langchain_core.prompts import ChatPromptTemplate
        from src.answer import generate as g
        for name in ("SYSTEM_PROMPT", "CONFLICT_PROMPT", "VERIFY_PROMPT", "CONSISTENCY_PROMPT",
                     "REWRITE_PROMPT"):
            tpl = ChatPromptTemplate.from_messages([("system", getattr(g, name)), ("human", "{input}")])
            self.assertEqual(sorted(tpl.input_variables), ["input"], name)


class AssembleSectionsTests(unittest.TestCase):
    RAW = (
        "전제: 수습기간에는 연차가 안 생긴다\n"
        "세부:\n"
        "- 수습기간은 계속근로기간에 산입된다 [1]\n"
        "- 1년 미만은 1개월 개근 시 1일이 생긴다 [2]\n"
        "결론: 맞지 않습니다. 수습 중에도 연차가 생깁니다 [1][2]."
    )

    def test_conclusion_is_moved_to_the_top_and_premise_is_dropped(self):
        out = assemble_sections(self.RAW)
        self.assertTrue(out.startswith("맞지 않습니다."))
        self.assertIn("- 수습기간은 계속근로기간에 산입된다 [1]", out)
        self.assertNotIn("전제:", out)
        self.assertLess(out.index("맞지 않습니다."), out.index("- 수습기간"))

    def test_no_conclusion_section_returns_text_unchanged(self):
        raw = "맞습니다. [1]\n- 세부 내용 [1]"
        self.assertEqual(assemble_sections(raw), raw)

    def test_empty_detail_is_omitted_and_related_articles_are_kept(self):
        raw = (
            "전제: 없음\n"
            "세부: 없음\n"
            "결론: 제공된 규정에서 확인되지 않습니다. 담당 부서에 확인해 주세요.\n"
            "관련 조문: [2] 복무규정 제32조"
        )
        out = assemble_sections(raw)
        self.assertTrue(out.startswith("제공된 규정에서 확인되지 않습니다."))
        self.assertTrue(out.endswith("관련 조문: [2] 복무규정 제32조"))
        self.assertFalse(has_bullets(out))
        self.assertNotIn("세부", out)

    def test_generic_fallback_sentence_is_trimmed_when_conclusion_has_an_answer(self):
        raw = (
            "세부:\n"
            "- 야근식대는 9,000원 이내이다 [1]\n"
            "결론: 야근식대는 9,000원 이내입니다 [1]. 제공된 규정에서 확인되지 않습니다. 담당 부서에 확인해 주세요."
        )
        out = assemble_sections(raw)
        self.assertNotIn("확인되지 않습니다", out)
        self.assertTrue(out.startswith("야근식대는 9,000원 이내입니다 [1]."))

    def test_generic_fallback_is_kept_when_it_is_the_whole_conclusion(self):
        raw = (
            "세부:\n"
            "- 우편 발송 절차 [1]\n"
            "결론: 제공된 규정에서 확인되지 않습니다. 담당 부서에 확인해 주세요."
        )
        self.assertIn("확인되지 않습니다", assemble_sections(raw))

    def test_has_bullets(self):
        self.assertTrue(has_bullets("결론\n\n- 불릿 [1]"))
        self.assertFalse(has_bullets("불릿 없는 답변"))


if __name__ == "__main__":
    unittest.main()

