import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.citations import law_lookup
from src.citations.refs import resolve_citations


def row(doc, key, text):
    return {"고유번호": doc, "id": f"{doc}_{key}", "본문": text}


class CitationTest(unittest.TestCase):
    def test_internal_short_title_resolves_defined_alias(self):
        rows = [row("1", "제1조", '「자금세탁행위 방지를 위한 업무규정」(이하 “업무규정”이라 한다)'),
                row("1", "제2조", "업무규정 제22조에 따른다."), row("2", "제22조", "보존")]
        resolve_citations(rows, {"1": "업무지침", "2": "한국벤처투자 자금세탁행위 방지를 위한 업무규정"})
        self.assertEqual(rows[1]["cites"][0]["대상"], "2_제22조")
        self.assertEqual(rows[1]["cites"][0]["상태"], "정상연결")

    def test_table_internal_name_can_span_lines(self):
        rows = [row("1", "별표", "분류 근거\n「문서관리\n규정」\n제7조제1항"),
                {**row("2", "제7조", "제7조(분류) ① 본문"), "항번호": [1]}]
        resolve_citations(rows, {"1": "다른지침", "2": "문서관리규정"})
        self.assertEqual(rows[0]["cites"][0]["대상"], "2_제7조")
        self.assertEqual(rows[0]["cites"][0]["항"], 1)

    def test_phone_form_title_variants_are_self_references(self):
        rows = [row("1", "별지", '「전화 녹음 관리 및 운영관리 지침」제8조 제1항\n'
                    '「전화 녹음 시스템 운영지침」제8조 제2항\n「녹음 시스템 운영지침」제8조 제1항'),
                {**row("1", "제8조", "제8조(열람) ① 열람\n② 제공"), "항번호": [1, 2]}]
        resolve_citations(rows, {"1": "전화 녹음관리 및 운영지침"})
        self.assertEqual([c["대상"] for c in rows[0]["cites"]], ["1_제8조"] * 3)
        self.assertEqual([c["항"] for c in rows[0]["cites"]], [1, 2, 1])

    def test_missing_paragraph_needs_review(self):  # 조는 있어도 그 항이 없으면 확인 필요
        rows = [row("1", "제2조", "제5조제3항에 따른다."),
                {**row("1", "제5조", "제5조(a) ① 가\n② 나"), "항번호": [1, 2]}]
        resolve_citations(rows, {"1": "지침"})
        self.assertEqual(rows[0]["cites"][0]["상태"], "확인필요(대상없음)")

    def test_own_article_title_is_not_a_citation(self):  # 조 본문 맨 앞 '제5조(…)'는 인용이 아니다
        rows = [{**row("1", "제5조", "제5조(a) ① 제3조에 따른다."), "항번호": [1]},
                row("1", "제3조", "제3조(b) 본문")]
        resolve_citations(rows, {"1": "지침"})
        self.assertEqual([c["대상"] for c in rows[0]["cites"]], ["1_제3조"])

    def test_bare_law_name_is_captured_and_same_law_decree_inherits(self):
        rows = [row("1", "제1조", "양성평등기본법 제31조 및 동법 시행령 제19조")]
        resolve_citations(rows, {"1": "지침"})
        self.assertEqual([c.get("법령명") for c in rows[0]["cites"]],
                         ["양성평등기본법", "양성평등기본법 시행령"])

    def test_full_bare_title_uses_known_names_without_sentence_prefix(self):
        rows = [row("1", "제1조", "이 지침은 성폭력방지 및 피해자 보호 등에 관한 법률 제5조에 따른다.")]
        resolve_citations(rows, {"1": "지침"}, law_names=["성폭력방지 및 피해자 보호 등에 관한 법률"])
        self.assertEqual(rows[0]["cites"][0]["법령명"], "성폭력방지 및 피해자 보호 등에 관한 법률")

    def test_alias_inside_title_brackets_is_not_part_of_law_name(self):
        rows = [row("1", "제1조", '「국가를\n당사자로 하는 계약에 관한 법률 시행령(이하 “국가계약법 시행령”이라 한다)」제76조')]
        resolve_citations(rows, {"1": "지침"})
        self.assertEqual(rows[0]["cites"][0]["법령명"], "국가를 당사자로 하는 계약에 관한 법률 시행령")

    def test_unknown_spaced_law_keeps_full_name_and_decree(self):
        rows = [row("1", "제1조", "양성평등기본법 제31조 및 동법 시행령 제19조, 20조 및 "
                    "성폭력방지 및 피해자 보호 등에 관한 법률 제5조 및 동법 시행령 제2조")]
        resolve_citations(rows, {"1": "지침"})
        self.assertEqual([c.get("법령명") for c in rows[0]["cites"]][-2:],
                         ["성폭력방지 및 피해자 보호 등에 관한 법률", "성폭력방지 및 피해자 보호 등에 관한 법률 시행령"])

    def test_generic_rule_does_not_get_fabricated_title(self):
        rows = [row("1", "별표", "작업설비 부적절, 고장(규칙 제9조 및 제21조)")]
        resolve_citations(rows, {"1": "지침"})
        self.assertTrue(all("법령명" not in c for c in rows[0]["cites"]))


class LawLookupTest(unittest.TestCase):
    def test_confirmed_aliases_resolve_with_spacing_variants(self):
        pairs = [
            ("특정금융정보법", "특정 금융거래정보의 보고 및 이용 등에 관한 법률"),
            ("특정금융정보법 시행령", "특정 금융거래정보의 보고 및 이용 등에 관한 법률 시행령"),
            ("테러자금금지법", "공중 등 협박목적 및 대량살상무기확산을 위한 자금조달행위의 금지에 관한 법률"),
            ("국가계약법 시행령", "국가를 당사자로 하는 계약에 관한 법률 시행령"),
            ("소프트웨어산업 진흥법", "소프트웨어 진흥법"),
            ("퇴직급여보장법 시행령", "근로자퇴직급여 보장법 시행령"),
            ("공공기록물법 시행령", "공공기록물 관리에 관한 법률 시행령"),
            ("이해충돌방지법 시행령", "공직자의 이해충돌 방지법 시행령"),
        ]
        for alias, title in pairs:
            with self.subTest(alias=alias):
                self.assertEqual(law_lookup.resolve(alias.replace(" ", ""), "제2조", {title.replace(" ", ""): "law"},
                                                    {("law", "2"): "본문"}), ("law", "2", "본문"))

    def test_previous_title_is_loaded_without_overriding_current_title(self):
        with tempfile.NamedTemporaryFile(dir=Path(__file__).parent, suffix=".sqlite3", delete=False) as tmp:
            path = Path(tmp.name)
        try:
            with sqlite3.connect(path) as conn:
                conn.execute("create table documents (id text, requested_title text, api_title text, raw_json text)")
                conn.execute("create table chunks (document_id text, path text, content text)")
                conn.execute("insert into documents values (?, ?, ?, ?)",
                             ("law", "현행법", "현행법", json.dumps({"법령": {"기본정보": {"이전법령명": "구법명"}}})))
                conn.execute("insert into chunks values ('law', '2', '본문')")
            conn.close()
            titles, chunks = law_lookup.load(path)
            self.assertEqual(law_lookup.resolve("구법명", "제2조", titles, chunks), ("law", "2", "본문"))
            self.assertIsNone(law_lookup.resolve("구법명", "제999조", titles, chunks))
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
