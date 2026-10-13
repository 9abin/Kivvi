import json
import tempfile
import unittest
from pathlib import Path

from src.eval.dataset import (
    QUESTION_SUBTYPES,
    is_indexable,
    load_chunk_catalog,
    load_questions,
    non_indexed_ids,
    save_questions,
    validate_questions,
)


class EvalDatasetTests(unittest.TestCase):
    def test_optional_subtype_is_validated_and_preserved(self):
        questions = [
            {"q": "정의는?", "유형": "문서명", "세부유형": "용어정의", "정답": ["100_제2조"]},
            {"q": "세부유형 없는 질문", "유형": "내용", "정답": ["100_제2조"]},
        ]
        self.assertEqual(validate_questions(questions, {"100_제2조"}), [])
        bad = [{"q": "잘못", "유형": "내용", "세부유형": "기타", "정답": ["100_제2조"]}]
        self.assertTrue(
            any("세부유형은" in error for error in validate_questions(bad, {"100_제2조"}))
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "questions.jsonl"
            save_questions(questions, chunk_ids={"100_제2조"}, path=path)
            self.assertEqual(load_questions(path), questions)
            first_line = path.read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(list(json.loads(first_line)), ["q", "유형", "세부유형", "정답"])

    def test_rejects_gold_chunks_that_are_not_indexed(self):
        catalog = {
            "100_제1조": {"id": "100_제1조", "상태": "정상"},
            "100_제2조": {"id": "100_제2조", "상태": "삭제"},
            "100_부칙": {"id": "100_부칙", "상태": "정상"},
        }
        unindexed = non_indexed_ids(catalog)
        self.assertEqual(unindexed, {"100_제2조", "100_부칙"})
        questions = [{"q": "삭제 조문", "유형": "내용", "정답": ["100_제1조", "100_제2조"]}]
        errors = validate_questions(questions, set(catalog), unindexed)
        self.assertTrue(any("색인되지 않는" in error for error in errors))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "questions.jsonl"
            with self.assertRaises(ValueError):
                save_questions(
                    questions, chunk_ids=set(catalog), non_indexed=unindexed, path=path
                )
            self.assertFalse(path.exists())

    def test_indexable_rule_matches_index_builder(self):
        from src.index.build import is_indexed

        for row in load_chunk_catalog().values():
            self.assertEqual(is_indexable(row), is_indexed(row), row["id"])

    def test_committed_dataset_is_valid_and_covers_every_regulation(self):
        catalog = load_chunk_catalog()
        questions = load_questions()
        self.assertEqual(
            validate_questions(questions, set(catalog), non_indexed_ids(catalog)), []
        )
        covered = {chunk_id.split("_", 1)[0] for row in questions for chunk_id in row["정답"]}
        documents = {str(row["고유번호"]) for row in catalog.values()}
        self.assertEqual(documents - covered, set())
        self.assertTrue(
            all(row.get("세부유형", QUESTION_SUBTYPES[0]) in QUESTION_SUBTYPES for row in questions)
        )

    def test_validates_questions_and_gold_chunk_ids(self):
        questions = [
            {"q": "규정 내용은?", "유형": "내용", "정답": ["100_제1조"]},
        ]
        self.assertEqual(validate_questions(questions, {"100_제1조"}), [])
        self.assertTrue(
            any(
                "없는 정답 조문 ID" in error
                for error in validate_questions(questions, set())
            )
        )

    def test_validation_rejects_duplicate_questions_targets_and_unsupported_types(self):
        questions = [
            {"q": "같은 질문", "유형": "내용", "정답": ["100_제1조"]},
            {"q": "같은 질문", "유형": "잘못된 유형", "정답": ["100_제1조", "100_제1조"]},
        ]
        errors = validate_questions(questions, {"100_제1조"})
        self.assertTrue(any("중복된 질문" in error for error in errors))
        self.assertTrue(any("유형은" in error for error in errors))
        self.assertTrue(any("중복되어" in error for error in errors))

    def test_saves_and_reloads_existing_eval_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "questions.jsonl"
            questions = [
                {"q": "무엇인가요?", "유형": "내용", "정답": ["100_제1조"]},
            ]
            save_questions(questions, chunk_ids={"100_제1조"}, path=path)
            self.assertEqual(load_questions(path), questions)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"q": "무엇인가요?", "유형": "내용", "정답": ["100_제1조"]},
            )

    def test_invalid_save_keeps_original_dataset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "questions.jsonl"
            path.write_text('{"q":"기존","유형":"내용","정답":["100_제1조"]}\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                save_questions(
                    [{"q": "신규", "유형": "내용", "정답": ["missing"]}],
                    chunk_ids={"100_제1조"},
                    path=path,
                )
            self.assertIn('"기존"', path.read_text(encoding="utf-8"))
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_loads_original_chunk_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chunks.jsonl"
            path.write_text(
                '{"id":"100_제1조","문서명":"예시","본문":"원문"}\n',
                encoding="utf-8",
            )
            self.assertEqual(
                load_chunk_catalog(path),
                {"100_제1조": {"id": "100_제1조", "문서명": "예시", "본문": "원문"}},
            )

    # 라벨링 화면(app_pages/eval_dataset.py)을 직접 실행해 보는 테스트 2개는 이 저장소에서 뺐다.
    # 화면 자체를 가져오지 않기로 했기 때문이다 (docs/평가_라벨링.md는 기록용으로 남긴다).
    # src.eval.dataset의 불러오기·검증 로직은 위 테스트로 그대로 확인한다.


if __name__ == "__main__":
    unittest.main()
