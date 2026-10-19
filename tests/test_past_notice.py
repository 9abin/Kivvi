"""src/answer/pipeline.py asks_past: 과거 기준·시행일·개정 질문이면 현행 기준 안내를 붙인다."""
import unittest

from src.answer.pipeline import asks_past


class PastQuestionTests(unittest.TestCase):
    def test_past_and_effective_date_questions(self):
        for q in ("예전엔 배우자 출산휴가가 5일이었잖아요. 지금은 며칠 쓸 수 있어요?",
                  "시차출퇴근제 새로 도입된다고 들었는데 언제부터 쓸 수 있나요?",
                  "2026년부터 연차를 2시간 단위로도 쪼개 쓸 수 있게 된다던데, 맞아요?",
                  "사적인 이해관계 있으면 따로 신고해야 하는 게 새로 생겼다던데, 그거 언제부터 해야 되는 거예요?"):
            self.assertTrue(asks_past(q), q)

    def test_ordinary_questions(self):
        for q in ("육아휴직은 언제부터 쓸 수 있어요?", "출장 다녀오면 복명서는 언제까지 내야 해요?"):
            self.assertFalse(asks_past(q), q)


if __name__ == "__main__":
    unittest.main()
