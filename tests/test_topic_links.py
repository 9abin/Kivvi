"""주제 링크 확장 (src/search/topics.py, Searcher._related, assemble의 '주제' 역할). 모델·DB 없이 돈다."""
import unittest
from unittest import mock

from src.search import topics
from src.search.search import Searcher

# 2026.10.15부터 기본은 RETURN_ARTICLE 켬(팀 DB 항 단위 → 조 전체). 아래 _related/_related_articles 시험은 DB 없이
# 항 단위 결과를 그대로 돌려받는 경로(끔)를 검사하므로 그 경로로 고정한다 (환경 변수 RETURN_ARTICLE=0과 같은 뜻).
article_off = mock.patch("src.search.search.RETURN_ARTICLE", False)

TABLE = {
    "건강검진": {"terms": ["건강검진", "건강진단", "신체검사"], "except": [], "링크": True, "묶음": "휴가·복무"},
    "보수·연봉": {"terms": ["보수", "연봉"], "except": ["유지보수"], "링크": True, "묶음": "보수"},
    "직제": {"terms": ["직급", "직위"], "except": [], "링크": False, "묶음": "범위어"},
}

ROWS = [
    {"chunk_id": "23482_제3조", "doc_id": "23482", "article": "제3조",
     "body_search": "제3조(지급기준) 건강진단에 소요되는 경비는 예산 범위내에서 회사가 부담한다."},
    {"chunk_id": "23970_제25조", "doc_id": "23970", "article": "제25조",
     "body_search": "제25조(공가) 직원이 다음 각호에 해당하면 공가를 허가한다. 4. 건강진단, 건강검진을 받을 때"},
    {"chunk_id": "24024_제16조", "doc_id": "24024", "article": "제16조",
     "body_search": "제16조(채용방법) 공모전형에 의한 채용은 서류전형, 면접 및 건강검진에 의한다."},
    {"chunk_id": "24010_제31조", "doc_id": "24010", "article": "제31조",
     "body_search": "제31조(건강진단) 회사는 직원의 채용시 및 매년 직원에 대하여 건강 진단을 실시한다."},
    {"chunk_id": "23720_제36조", "doc_id": "23720", "article": "제36조",
     "body_search": "제36조(정보시스템 유지보수) 정보시스템의 유지보수는 계약에 따른다. 직급과 직위를 적는다."},
]


class DetectTests(unittest.TestCase):
    def test_finds_topics_ignoring_spaces(self):
        self.assertEqual(topics.detect("건강 검진 회사에서 비용 대주나요?", TABLE), ["건강검진"])

    def test_except_string_is_removed_before_matching(self):
        self.assertEqual(topics.detect("정보시스템 유지보수 계약", TABLE), [])
        self.assertEqual(topics.detect("보수 지급일", TABLE), ["보수·연봉"])

    def test_unlinked_topics_are_ignored(self):
        self.assertEqual(topics.detect("직급과 직위 기준", TABLE), [])

    def test_hint_only_term_counts(self):
        self.assertEqual(topics.detect("몸 검사 받는 날 " + "신체검사", TABLE), ["건강검진"])


class BuildIndexTests(unittest.TestCase):
    def test_index_marks_title_hits_and_counts_mentions(self):
        topic_chunks, chunk_topics = topics.build_index(ROWS, TABLE)
        self.assertEqual(topic_chunks["건강검진"], ["23482_제3조", "23970_제25조", "24024_제16조", "24010_제31조"])
        self.assertNotIn("직제", topic_chunks)  # 링크=false는 표에 넣지 않는다
        self.assertEqual(chunk_topics["24010_제31조"]["건강검진"], (True, 2))   # 제목이 주제어, 띄어 쓴 '건강 진단'도 셈
        self.assertEqual(chunk_topics["23970_제25조"]["건강검진"], (False, 2))
        self.assertNotIn("23720_제36조", chunk_topics)  # '유지보수'는 '보수'로 세지 않는다

    def test_rank_candidates_prefers_title_hits_then_counts(self):
        _, chunk_topics = topics.build_index(ROWS, TABLE)
        ranked = topics.rank_candidates(["24024_제16조", "23970_제25조", "24010_제31조"], chunk_topics, ["건강검진"], 2)
        self.assertEqual(ranked, ["24010_제31조", "23970_제25조"])


class FakeReranker:
    """청크별로 정해 둔 점수를 돌려준다. 입력 글자 순서대로 점수를 맞추기 위해 머리말의 조항으로 찾는다."""
    def __init__(self, scores):
        self.scores = scores

    def predict(self, pairs, batch_size=16):
        out = []
        for _, doc in pairs:
            key = next((k for k in self.scores if f"{k}]" in doc or f"{k} " in doc), None)
            out.append(self.scores.get(key, 0.0))
        return out


def bare_searcher(scores):
    s = Searcher.__new__(Searcher)
    s.rerank_header = True
    s.documents = {"23482": {"title": "복지후생 운영지침"}, "23970": {"title": "복무규정"},
                   "24024": {"title": "인사규정"}, "24010": {"title": "취업규칙"}, "23720": {"title": "전산업무지침"}}
    s.row_info = {r["chunk_id"]: {**r, "body": r["body_search"], "cites": [], "corrections_note": None} for r in ROWS}
    s.topics = TABLE
    s.topic_chunks, s.chunk_topics = topics.build_index(ROWS, TABLE)
    s.article_rows = {}
    for r in ROWS:
        s.article_rows.setdefault((r["doc_id"], r["article"].split(" ")[0]), []).append(r["chunk_id"])
    s.reranker = FakeReranker(scores)
    s.last_related = []
    return s


def hit(chunk_id, doc_id, article, cited=(), siblings=()):
    return {"chunk_id": chunk_id, "doc_id": doc_id, "article": article, "body": "…", "문서명": None,
            "cited": list(cited), "siblings": list(siblings)}


@article_off
class RelatedTests(unittest.TestCase):
    def test_links_scattered_articles_excluding_hits_and_cites(self):
        s = bare_searcher({"제25조": 0.9, "제16조": 0.3, "제31조": 0.8})
        results = [hit("23482_제3조", "23482", "제3조", cited=[hit("24010_제31조", "24010", "제31조")])]
        related = s._related("건강검진 회사에서 비용 대주나요?", "건강진단 비용", results)
        ids = [r["chunk_id"] for r in related]
        self.assertEqual(ids, ["23970_제25조", "24024_제16조"])  # 찾은 제3조와 인용으로 온 제31조는 뺀다
        self.assertEqual(related[0]["주제"], ["건강검진"])
        self.assertEqual(related[0]["match"], "주제링크")
        self.assertEqual(related[0]["연결상태"], "주제연결")
        self.assertEqual(related[0]["문서명"], "복무규정")
        self.assertEqual(s.last_related[0][:2], ("복무규정", "제25조"))

    def test_low_rerank_scores_are_dropped(self):
        s = bare_searcher({"제25조": 0.9, "제16조": 0.0005, "제31조": 0.8})
        results = [hit("23482_제3조", "23482", "제3조")]
        ids = [r["chunk_id"] for r in s._related("건강검진 비용", "", results)]
        self.assertEqual(ids, ["23970_제25조", "24010_제31조"])  # 0.0005 < TOPIC_RELATED_MIN_SCORE(0.001)

    def test_same_article_paragraphs_count_once(self):
        s = bare_searcher({"제25조": 0.9, "제16조": 0.3, "제31조": 0.8})
        s.row_info["23970_제25조_2항"] = {**s.row_info["23970_제25조"], "chunk_id": "23970_제25조_2항",
                                          "article": "제25조 제2항"}
        s.topic_chunks["건강검진"].append("23970_제25조_2항")
        s.chunk_topics["23970_제25조_2항"] = {"건강검진": (False, 1)}
        results = [hit("23482_제3조", "23482", "제3조")]
        ids = [r["chunk_id"] for r in s._related("건강검진 비용", "", results)]
        self.assertEqual(ids.count("23970_제25조") + ids.count("23970_제25조_2항"), 1)

    def test_pinned_article_comes_first_regardless_of_score(self):
        s = bare_searcher({"제25조": 0.0001, "제16조": 0.3, "제31조": 0.8})
        s.topics = {**TABLE, "건강검진": {**TABLE["건강검진"],
                                        "함께": [{"대상": "23970_제25조", "이유": "검진 시간은 공가"}]}}
        results = [hit("23482_제3조", "23482", "제3조")]
        related = s._related("건강검진 비용", "", results)
        self.assertEqual([r["chunk_id"] for r in related], ["23970_제25조", "24010_제31조", "24024_제16조"])
        self.assertEqual(related[0]["주제이유"], "검진 시간은 공가")
        self.assertEqual(related[0]["주제"], ["건강검진"])
        self.assertNotIn("주제이유", related[1])
        self.assertEqual(s.last_related[0][-1], "함께")

    def test_pinned_article_already_found_is_skipped(self):
        s = bare_searcher({"제25조": 0.0001, "제16조": 0.3, "제31조": 0.8})
        s.topics = {**TABLE, "건강검진": {**TABLE["건강검진"],
                                        "함께": [{"대상": "23970_제25조", "이유": "검진 시간은 공가"}]}}
        results = [hit("23970_제25조", "23970", "제25조")]
        ids = [r["chunk_id"] for r in s._related("건강검진 비용", "", results)]
        self.assertNotIn("23970_제25조", ids)

    def test_no_topic_in_question_gives_nothing(self):
        s = bare_searcher({"제25조": 0.9})
        self.assertEqual(s._related("연차 며칠이에요", "", [hit("23482_제3조", "23482", "제3조")]), [])
        self.assertEqual(s.last_related, [])


class RetrieverRoundTripTests(unittest.TestCase):
    def test_related_survives_langchain_document_conversion(self):
        from src.search.langchain_retriever import document_to_entry, entry_to_document
        entry = {"chunk_id": "a", "doc_id": "1", "article": "제1조", "body": "본문", "문서명": "x",
                 "cited": [], "siblings": [], "related": [{"chunk_id": "b", "주제": ["건강검진"]}]}
        back = document_to_entry(entry_to_document(entry))
        self.assertEqual(back["related"], entry["related"])
        # related가 없는 항목은 None으로 돌아온다. assemble이 그래도 돌아야 한다
        plain = document_to_entry(entry_to_document({k: v for k, v in entry.items() if k != "related"}))
        self.assertIsNone(plain["related"])
        plain.update({"조문상태": "현행"})
        s = Searcher.__new__(Searcher)
        self.assertEqual(len(s.assemble([plain])["groups"]), 1)


@article_off
class RelatedArticlesTests(unittest.TestCase):
    """연관 조항 표 방식 (TOPIC_MODE=article): 찾은 조마다 표를 조회해 전부 붙이고 겹치는 것을 뺀다."""
    TABLE_REL = {
        "23482_제3조": [{"대상": "23970_제25조", "주제": "건강검진", "등급": "핵심", "이유": "검진 시간은 공가"},
                      {"대상": "24010_제31조", "주제": "건강검진", "등급": "핵심"},
                      {"대상": "24024_제16조", "주제": "건강검진", "등급": "참고"}],
        "24010_제31조": [{"대상": "23482_제3조", "주제": "건강검진", "등급": "참고"},
                       {"대상": "23970_제25조", "주제": "건강검진", "등급": "핵심"}],
    }

    def searcher(self):
        s = bare_searcher({})
        s.related_table = self.TABLE_REL
        return s

    def test_links_from_each_hit_deduped_against_hits_and_cites(self):
        s = self.searcher()
        results = [hit("23482_제3조", "23482", "제3조", cited=[hit("24024_제16조", "24024", "제16조")]),
                   hit("24010_제31조", "24010", "제31조")]
        related = s._related_articles("건강검진 비용", "", results)
        ids = [r["chunk_id"] for r in related]
        self.assertEqual(ids, ["23970_제25조"])  # 제31조는 찾은 조, 제16조는 인용으로 옴, 제25조는 두 표에서 한 번만
        self.assertEqual(related[0]["match"], "연관조항")
        self.assertEqual(related[0]["주제이유"], "검진 시간은 공가")
        self.assertEqual(related[0]["연관출발"], "복지후생 운영지침 제3조")
        self.assertEqual(s.last_related[0][-1], "핵심")

    def test_core_links_come_before_reference_links_per_hit(self):
        s = self.searcher()
        s.related_table = {"23482_제3조": [{"대상": "24024_제16조", "주제": "건강검진", "등급": "참고"},
                                         {"대상": "24010_제31조", "주제": "건강검진", "등급": "핵심"}]}
        ids = [r["chunk_id"] for r in s._related_articles("건강검진 비용", "", [hit("23482_제3조", "23482", "제3조")])]
        self.assertEqual(ids, ["24010_제31조", "24024_제16조"])

    def test_empty_table_gives_nothing(self):
        s = self.searcher()
        s.related_table = {}
        self.assertEqual(s._related_articles("건강검진 비용", "", [hit("23482_제3조", "23482", "제3조")]), [])

    def test_case_links_follow_same_case_articles_without_topic_filter(self):
        # 사례 링크 (CASE_LINKS): 질문 주제어와 무관하게 찾은 조의 사례 조문을 붙인다. 주제 링크로 이미 온 조는 뺀다
        s = self.searcher()
        s.case_table = {"23482_제3조": [{"대상": "23970_제25조", "주제": "건강검진", "주제들": ["건강검진"], "등급": "핵심", "이유": "환수 대상", "사례": ["D11"]},
                                      {"대상": "24024_제16조", "주제": "건강검진", "주제들": ["건강검진"], "등급": "핵심", "이유": "제재", "사례": ["D11"]},
                                      {"대상": "24010_제31조", "주제": "보수", "주제들": ["보수"], "등급": "핵심", "이유": "다른 사례", "사례": ["D12"]}]}
        results = [hit("23482_제3조", "23482", "제3조")]
        already = [hit("24024_제16조", "24024", "제16조")]
        got = s._case_links("건강검진 비용", "", results, already=already)
        self.assertEqual([r["chunk_id"] for r in got], ["23970_제25조"])  # 제16조는 이미 붙음, 제31조는 질문 주제(보수) 아님
        self.assertEqual(got[0]["match"], "사례링크")
        self.assertEqual(got[0]["주제"], ["건강검진"])
        self.assertEqual(got[0]["주제이유"], "환수 대상")
        self.assertEqual(s.last_related[0][-1], "핵심")

    def test_case_links_empty_table_gives_nothing(self):
        s = self.searcher()
        s.case_table = {}
        self.assertEqual(s._case_links("건강검진", "", [hit("23482_제3조", "23482", "제3조")]), [])

    def test_case_link_without_topic_always_follows(self):
        s = self.searcher()
        s.case_table = {"23482_제3조": [{"대상": "23970_제25조", "주제": "", "주제들": [], "등급": "핵심", "이유": "대결", "사례": ["D26"]}]}
        got = s._case_links("아무 주제 없는 질문", "", [hit("23482_제3조", "23482", "제3조")])
        self.assertEqual([r["chunk_id"] for r in got], ["23970_제25조"])
        self.assertEqual(got[0]["주제"], ["사례"])


class RerankWindowTests(unittest.TestCase):
    """리랭커 앞에서 조로 합치기 (MERGE_BEFORE_RERANK). 창을 '서로 다른 조' 기준으로 센다."""
    FUSED = [{"chunk_id": "a_1", "doc_id": "A", "article": "제1조 제1항"},
             {"chunk_id": "a_2", "doc_id": "A", "article": "제1조 제2항"},
             {"chunk_id": "b_1", "doc_id": "B", "article": "제5조"},
             {"chunk_id": "a_3", "doc_id": "A", "article": "제2조"},
             {"chunk_id": "c_1", "doc_id": "C", "article": "제9조 제1항"},
             {"chunk_id": "a_4", "doc_id": "A", "article": "제1조 제3항"}]

    def test_off_keeps_top_n_paragraphs(self):
        s = Searcher.__new__(Searcher)
        self.assertEqual([h["chunk_id"] for h in s.rerank_window(self.FUSED, top=3, mode="off")], ["a_1", "a_2", "b_1"])

    def test_all_counts_distinct_articles_and_keeps_their_paragraphs(self):
        s = Searcher.__new__(Searcher)
        ids = [h["chunk_id"] for h in s.rerank_window(self.FUSED, top=3, mode="all", doc_cap=0)]
        self.assertEqual(ids, ["a_1", "a_2", "b_1", "a_3", "a_4"])  # 조 3개(A제1조, B제5조, A제2조)의 항 전부, RRF 순서

    def test_best_keeps_one_paragraph_per_article(self):
        s = Searcher.__new__(Searcher)
        ids = [h["chunk_id"] for h in s.rerank_window(self.FUSED, top=3, mode="best", doc_cap=0)]
        self.assertEqual(ids, ["a_1", "b_1", "a_3"])

    def test_doc_cap_defers_extra_articles_of_same_regulation(self):
        s = Searcher.__new__(Searcher)
        ids = [h["chunk_id"] for h in s.rerank_window(self.FUSED, top=3, mode="best", doc_cap=1)]
        self.assertEqual(ids, ["a_1", "b_1", "c_1"])  # A제2조는 규정당 1개 상한에 걸려 C제9조가 들어온다


class TopicNoteTests(unittest.TestCase):
    def test_pinned_entry_gets_a_note_unless_already_cited(self):
        from src.answer.generate import topic_notes
        entries = [{"문서명": "복지후생 운영지침", "article": "제3조", "역할": "검색"},
                   {"문서명": "복무규정", "article": "제25조", "역할": "주제", "주제이유": "검진 시간은 공가로 처리된다"},
                   {"문서명": "여비규정", "article": "제24조", "역할": "주제"}]
        self.assertEqual(topic_notes(entries, [1]), ["※ 함께 확인할 조문 [2] 복무규정 제25조: 검진 시간은 공가로 처리된다"])
        self.assertEqual(topic_notes(entries, [1, 2]), [])


class CaseNoteTests(unittest.TestCase):
    def test_case_link_note_reads_as_caution(self):
        from src.answer.generate import topic_notes
        entries = [{"역할": "검색", "문서명": "A", "article": "제1조"},
                   {"역할": "주제", "match": "사례링크", "문서명": "징계지침", "article": "제17조", "주제이유": "배상 및 환수"}]
        self.assertEqual(topic_notes(entries, cited=set()), ["※ 유의할 조문 [2] 징계지침 제17조: 배상 및 환수"])
        self.assertEqual(topic_notes(entries, cited={2}), [])


class AssembleTests(unittest.TestCase):
    def test_related_entries_get_topic_role_once(self):
        s = Searcher.__new__(Searcher)
        base = {"body": "본문", "문서명": "x", "조문상태": "현행"}
        found = {**base, "chunk_id": "23482_제3조", "doc_id": "23482", "article": "제3조", "match": "벡터"}
        linked = {**base, "chunk_id": "23970_제25조", "doc_id": "23970", "article": "제25조", "match": "주제링크"}
        found["cited"] = [dict(linked)]  # 인용으로도 왔다면 인용이 먼저 들어가고 주제 쪽은 건너뛴다
        found["related"] = [dict(linked)]
        groups = s.assemble([found])["groups"]
        roles = [(it["chunk_id"], it["역할"]) for g in groups for it in g["items"]]
        self.assertEqual(roles, [("23482_제3조", "검색"), ("23970_제25조", "인용")])

        found["cited"] = []
        second = {**base, "chunk_id": "23492_제10조", "doc_id": "23492", "article": "제10조", "match": "벡터"}
        roles = [(it["chunk_id"], it["역할"]) for g in s.assemble([found, second])["groups"] for it in g["items"]]
        # 같은 주제 조문은 검색으로 찾은 조문들 뒤에 온다
        self.assertEqual(roles, [("23482_제3조", "검색"), ("23492_제10조", "검색"), ("23970_제25조", "주제")])


if __name__ == "__main__":
    unittest.main()
