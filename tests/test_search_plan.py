"""질문 나누기: 모델 응답 해석(parse_plan)과 하위 질문 결과 합치기(interleave). 모델·DB 없이 돈다."""
import unittest

from src.answer.generate import MAX_SUBQUERIES, parse_plan
from src.search.search import interleave


class ParsePlanTests(unittest.TestCase):
    def test_two_asks(self):
        raw = ('[{"질문": "출장여비는 어떻게 지급하나요?", "검색어": "출장 여비 지급"},'
               ' {"질문": "법인카드 한도는?", "검색어": ""}]')
        plan = parse_plan(raw, "원래 질문")
        self.assertEqual([p["질문"] for p in plan], ["출장여비는 어떻게 지급하나요?", "법인카드 한도는?"])
        self.assertEqual(plan[0]["검색어"], "출장 여비 지급")
        self.assertIsNone(plan[1]["검색어"])

    def test_single_ask_keeps_original_question(self):
        # 나누지 않는 질문은 리랭킹까지 지금과 똑같이 원래 질문 글자로 검색해야 한다
        plan = parse_plan('[{"질문": "모델이 바꾼 문장", "검색어": "인병휴가 유급"}]', "인병휴가는 유급인가요?")
        self.assertEqual(plan, [{"질문": "인병휴가는 유급인가요?", "검색어": "인병휴가 유급"}])

    def test_fenced_json(self):
        plan = parse_plan('```json\n[{"질문": "a", "검색어": "b"}, {"질문": "c", "검색어": "d"}]\n```', "q")
        self.assertEqual(len(plan), 2)

    def test_unreadable_falls_back_to_question(self):
        for raw in ["", "검색어: 출장", "[1, 2]", '[{"검색어": "질문 없음"}]']:
            self.assertEqual(parse_plan(raw, "원래 질문"), [{"질문": "원래 질문", "검색어": None}], raw)

    def test_caps_subqueries(self):
        raw = "[" + ",".join(f'{{"질문": "q{i}"}}' for i in range(5)) + "]"
        self.assertEqual(len(parse_plan(raw, "q")), MAX_SUBQUERIES)


class InterleaveTests(unittest.TestCase):
    @staticmethod
    def ids(hits):
        return [h["chunk_id"] for h in hits]

    @staticmethod
    def hits(*ids):
        return [{"chunk_id": i} for i in ids]

    def test_alternates_by_rank(self):
        out = interleave([self.hits("a1", "a2", "a3"), self.hits("b1", "b2", "b3")], 4)
        self.assertEqual(self.ids(out), ["a1", "b1", "a2", "b2"])

    def test_dedup_keeps_first(self):
        out = interleave([self.hits("x", "a2"), self.hits("x", "b2")], 10)
        self.assertEqual(self.ids(out), ["x", "a2", "b2"])

    def test_short_list_lets_others_fill(self):
        out = interleave([self.hits("a1"), self.hits("b1", "b2", "b3")], 4)
        self.assertEqual(self.ids(out), ["a1", "b1", "b2", "b3"])

    def test_single_list_is_unchanged(self):
        self.assertEqual(self.ids(interleave([self.hits("a", "b", "c")], 2)), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
