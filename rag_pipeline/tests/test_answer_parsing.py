"""src/answer/generate.py의 모델 응답 파싱 검사.

목적: "정상적으로 빈 목록을 받음"(ok=True, [])과 "응답을 해석하지 못함"(ok=False)을
확실히 구분하는지 본다. 둘을 구분 못 하면 검사 실패가 사용자에게 보이지 않는다
(PR #1 리뷰 코멘트에서 지적된 문제).
"""
import unittest

from src.answer.generate import parse_pairs, parse_unsupported


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


if __name__ == "__main__":
    unittest.main()
