"""src/search/synonyms.py: 구어체 말 -> 규정 용어 확장, 그리고 사전 파일의 품질 검사."""
import json
import os
import unittest

from config import OUT_DIR
from src.search import synonyms


class ExpandTests(unittest.TestCase):
    TABLE = {"법카": ["법인카드"], "사표": ["사직", "퇴직"], "ktx": ["철도"]}

    def test_colloquial_word_adds_the_regulation_term(self):
        self.assertEqual(synonyms.expand("법카 영수증 잃어버렸어요", self.TABLE), "법인카드")

    def test_several_targets_are_joined(self):
        self.assertEqual(synonyms.expand("사표 내면 바로 수리돼요?", self.TABLE), "사직 퇴직")

    def test_match_is_case_insensitive(self):
        self.assertEqual(synonyms.expand("KTX 특실 타도 돼요?", self.TABLE), "철도")

    def test_no_colloquial_word_gives_empty_string(self):
        self.assertEqual(synonyms.expand("법인카드는 언제 쓰나요", self.TABLE), "")

    def test_term_already_in_the_question_is_not_repeated(self):
        self.assertEqual(synonyms.expand("법카로 철도 사표", self.TABLE), "법인카드 사직 퇴직")


class DictionaryFileTests(unittest.TestCase):
    """사전의 목표어는 모두 규정 원문에 있어야 한다. 없는 말을 검색어에 더하면 검색만 흐려진다."""

    def test_every_target_term_appears_in_the_regulations(self):
        table = synonyms.load()
        self.assertTrue(table)
        path = os.path.join(OUT_DIR, "chunks.jsonl")
        if not os.path.exists(path):
            self.skipTest("out/chunks.jsonl 없음")
        with open(path, encoding="utf-8") as f:
            text = "\n".join(json.loads(line)["본문"] for line in f)
        missing = sorted({t for targets in table.values() for t in targets if t not in text})
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
