import sys
import unittest

sys.path.insert(0, "src")
from regcheck.change_query import ChangeQuery  # noqa: E402
from regcheck.collect import Candidate  # noqa: E402
from regcheck.evidence import card  # noqa: E402

NODES = {"A": {"name": "직제규정"}, "B": {"name": "나지침"}, "A:3": {"label": "제3조", "title": "육아휴직", "reg": "A"}, "A:4": {"label": "제4조", "title": "대상", "reg": "A"}, "B:2": {"label": "제2조", "title": "지급", "reg": "B"}}
TEXTS = {"A:4": "대상은 제3조에 따른다. 제30조는 별개이다.", "B:2": "성과급을 지급한다. 지속적 거래 모니터링을 한다."}


def cq(**kw):
    return ChangeQuery(id="T", reg_id="A", kind=kw.pop("kind", "삭제"), starts=["A:3"], **kw)


class EvidenceTest(unittest.TestCase):
    def test_citation_quote_is_the_exact_sentence_and_ignores_longer_numbers(self):
        c = card(Candidate("A:4", 1.0, ["CITES_in"], "확정급"), cq(), NODES, TEXTS)
        self.assertEqual(c["quote"], "대상은 제3조에 따른다.")
        self.assertFalse(c["needs_check"])
        self.assertIn("번호로 인용", c["reasons"][0])

    def test_renumber_makes_replacement_draft(self):
        q = cq(kind="번호 이동", renumber={"A:3": "제4조"})
        c = card(Candidate("A:4", 1.0, ["CITES_in"], "확정급"), q, NODES, TEXTS)
        self.assertEqual(c["replacement"], {"before": "대상은 제3조에 따른다.", "after": "대상은 제4조에 따른다."})

    def test_renumber_replaces_in_one_pass_not_chained(self):
        nodes = {**NODES, "A:4": {"label": "제4조", "title": "대상", "reg": "A"}}
        q = cq(kind="번호 이동", renumber={"A:3": "제4조", "A:4": "제5조"})
        texts = {"A:4": "제3조와 제4조에 따른다."}
        c = card(Candidate("A:4", 1.0, ["CITES_in"], "확정급"), q, nodes, texts)
        self.assertEqual(c["replacement"]["after"], "제4조와 제5조에 따른다.")

    def test_own_heading_is_not_rewritten(self):
        q = cq(kind="번호 이동", renumber={"A:3": "제4조", "A:4": "제5조"})
        texts = {"A:4": "제4조(대상) 이 조는 제3조에 따른다."}
        c = card(Candidate("A:4", 1.0, ["CITES_in"], "확정급"), q, NODES, texts)
        self.assertEqual(c["replacement"]["after"], "제4조(대상) 이 조는 제4조에 따른다.")

    def test_other_regulation_needs_the_regulation_name_in_the_sentence(self):
        q = cq(kind="번호 이동", renumber={"A:3": "제4조"})
        bare = card(Candidate("B:2", 1.0, ["CITES_in"], "확정급"), q, NODES, {"B:2": "제3조에 따른다."})
        named = card(Candidate("B:2", 1.0, ["CITES_in"], "확정급"), q, NODES, {"B:2": "직제규정 제3조에 따른다."})
        self.assertIsNone(bare["replacement"])
        self.assertEqual(named["replacement"]["after"], "직제규정 제4조에 따른다.")

    def test_no_replacement_for_plain_delete(self):
        c = card(Candidate("A:4", 1.0, ["CITES_in"], "확정급"), cq(), NODES, TEXTS)
        self.assertIsNone(c["replacement"])

    def test_phrase_quote_and_check_flag_for_weaker_tier(self):
        c = card(Candidate("B:2", 0.5, ["PHRASE"], "확인 권장"), cq(kind="정의 변경", phrases=["지속적 거래 모니터링"]), NODES, TEXTS)
        self.assertEqual(c["quote"], "지속적 거래 모니터링을 한다.")
        self.assertTrue(c["needs_check"])

    def test_candidate_without_rule_evidence_has_no_quote_and_needs_check(self):
        c = card(Candidate("B:2", 0.2, ["RAG"], "참고"), cq(), NODES, TEXTS, llm={"영향": "불확실"})
        self.assertIsNone(c["quote"])
        self.assertTrue(c["needs_check"])
        self.assertEqual(c["llm"], {"영향": "불확실"})


if __name__ == "__main__":
    unittest.main()


class InternalCitationTest(unittest.TestCase):
    def test_moved_article_citing_another_moved_article_gets_quote_and_replacement(self):
        nodes = {"A": {"name": "직제규정"}, "A:4": {"label": "제4조", "title": "가", "reg": "A"}, "A:5": {"label": "제5조", "title": "나", "reg": "A"}}
        q = ChangeQuery(id="T", reg_id="A", kind="번호 이동", starts=["A:4", "A:5"], renumber={"A:4": "제5조", "A:5": "제6조"})
        texts = {"A:5": "제5조(나) 이 조는 제4조에 따른다."}
        c = card(Candidate("A:5", 1.0, ["CITES_internal"], "확정급"), q, nodes, texts)
        self.assertEqual(c["quote"], "제5조(나) 이 조는 제4조에 따른다.")
        self.assertEqual(c["replacement"]["after"], "제5조(나) 이 조는 제5조에 따른다.")
        self.assertFalse(c["needs_check"])
