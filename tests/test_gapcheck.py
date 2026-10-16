"""답변 뒤 누락 검사 (src/answer/gapcheck.py). 모델·DB 없이 가짜 검색기·생성기로 돈다."""
import unittest

from src.answer import gapcheck
from src.answer.gapcheck import GapChecker, gap_notes, proviso_sentences


class FakeGenerator:
    def __init__(self, probe_raw, judge_raw):
        self.probe_raw, self.judge_raw, self.calls = probe_raw, judge_raw, []

    def _ask(self, system, user):
        self.calls.append((system, user))
        return self.probe_raw if system == gapcheck.PROBE_PROMPT else self.judge_raw


class FakeSearcher:
    documents = {"23970": {"title": "복무규정"}, "23482": {"title": "복지후생 운영지침"}, "23753": {"title": "정보보안관리지침"}}

    def __init__(self):
        self.last_reranked = [
            {"chunk_id": "23482_제3조_1항", "doc_id": "23482", "article": "제3조 제1항", "body": "건강진단 경비는 회사가 부담한다."},
            {"chunk_id": "23753_제12조_1항", "doc_id": "23753", "article": "제12조 제1항",
             "body": "업무파일은 반출할 수 있다. 다만 기밀 사항인 경우에는 보안담당관의 승인을 받아야 한다."},
        ]
        self.searched = []

    def _query_tokens(self, text):
        return ["업무파일", "반출", "기밀"]

    def search(self, text, k=3, **kw):
        self.searched.append(text)
        return [{"chunk_id": "23970_제25조", "doc_id": "23970", "article": "제25조", "문서명": "복무규정",
                 "body": "제25조(공가) 건강검진을 받을 때 공가를 허가한다."}]


ENTRIES = [{"chunk_id": "23482_제3조", "doc_id": "23482", "article": "제3조", "문서명": "복지후생 운영지침",
            "body": "건강진단 경비는 예산 범위에서 회사가 부담한다."}]


class PipelineImportTests(unittest.TestCase):
    def test_pipeline_module_parses_and_imports(self):
        """pipeline.py는 다른 테스트가 import하지 않아 문법 오류가 평가 실행 때야 드러났다 (2026.10.15)."""
        import importlib
        self.assertTrue(hasattr(importlib.import_module("src.answer.pipeline"), "Assistant"))


class ProvisoTests(unittest.TestCase):
    def test_picks_conditional_sentences_sharing_question_nouns(self):
        body = "업무파일은 반출할 수 있다. 다만 기밀 사항인 경우에는 승인을 받아야 한다. 점심시간은 12시다."
        self.assertEqual(proviso_sentences(body, ["업무파일", "기밀"]), ["다만 기밀 사항인 경우에는 승인을 받아야 한다."])
        self.assertEqual(proviso_sentences(body, ["점심"]), [])


class GapCheckerTests(unittest.TestCase):
    def test_follow_up_search_and_judge_produce_notes(self):
        s, g = FakeSearcher(), FakeGenerator('{"질문": ["건강검진을 받는 시간은 공가로 처리되는가"]}',
                                             '{"보완": [{"번호": 1, "문장": "법정 건강검진 시간은 공가로 처리된다"}]}')
        r = GapChecker(s, g).run("건강검진 비용 대주나요?", "회사가 부담합니다.", ENTRIES, asker="사원")
        self.assertEqual(r["되묻기"], ["건강검진을 받는 시간은 공가로 처리되는가"])
        self.assertEqual(s.searched, ["건강검진을 받는 시간은 공가로 처리되는가"])
        self.assertEqual(r["보완"][0]["규정"], "복무규정")
        self.assertEqual(r["보완"][0]["출처"], "되묻기")
        self.assertEqual(gap_notes(r), ["※ 확인 필요: 복무규정 제25조 — 법정 건강검진 시간은 공가로 처리된다"])
        self.assertIn("질문자: 사원", g.calls[0][1])

    def test_discarded_proviso_is_offered_even_without_follow_ups(self):
        s, g = FakeSearcher(), FakeGenerator('{"질문": []}', '{"보완": [{"번호": 1, "문장": "기밀 사항은 승인이 필요하다"}]}')
        r = GapChecker(s, g).run("업무파일 반출 돼요?", "됩니다.", ENTRIES)
        self.assertEqual(s.searched, [])
        self.assertEqual(r["후보"], ["23753_제12조_1항"])  # 근거에 있는 복지후생 제3조는 빠지고 단서 있는 보안지침만
        self.assertEqual(r["보완"][0]["출처"], "버린후보")

    def test_unparseable_model_output_gives_no_notes(self):
        s, g = FakeSearcher(), FakeGenerator("음...", "모르겠다")
        r = GapChecker(s, g).run("업무파일 반출 돼요?", "됩니다.", ENTRIES)
        self.assertEqual(r["보완"], [])
        self.assertEqual(gap_notes(r), [])


if __name__ == "__main__":
    unittest.main()
