"""Read, validate, and persist retrieval evaluation questions and gold chunk IDs."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from config import OUT_DIR

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval"
QUESTIONS_PATH = EVAL_DIR / "questions.jsonl"
CHUNKS_PATH = Path(OUT_DIR) / "chunks.jsonl"
QUESTION_TYPES = ["내용", "문서명", "항번호"]
# 선택 필드. 정답 근거의 구조(정의 조항·조문 간 인용·문서 간 인용 등)를 나눠 본다.
QUESTION_SUBTYPES = ["단일조문", "복수조문", "용어정의", "조문간인용", "문서간인용", "표·서식"]
QUESTION_FIELDS = {"q", "유형", "세부유형", "정답"}


def is_indexable(row: dict[str, Any]) -> bool:
    """src/index/build.py의 is_indexed와 같은 기준. 색인되지 않은 청크는 검색 평가 정답이 될 수 없다."""
    return row.get("상태") == "정상" and not str(row.get("id", "")).endswith("_부칙")


def non_indexed_ids(catalog: dict[str, dict[str, Any]]) -> set[str]:
    return {chunk_id for chunk_id, row in catalog.items() if not is_indexable(row)}


def load_questions(path: Path = QUESTIONS_PATH) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    questions = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}:{line_number} JSON 오류: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path.name}:{line_number} 각 행은 JSON 객체여야 합니다.")
            questions.append(row)
    return questions


def load_chunk_catalog(path: Path = CHUNKS_PATH) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(
            f"{path}가 없습니다. 먼저 `uv run python run.py`로 원문 조문 청크를 생성하세요."
        )
    catalog = {}
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}:{line_number} JSON 오류: {exc}") from exc
            chunk_id = row.get("id")
            if not isinstance(chunk_id, str) or not chunk_id:
                raise ValueError(f"{path.name}:{line_number} 조문 ID가 없습니다.")
            if chunk_id in catalog:
                raise ValueError(f"{path.name}:{line_number} 중복 조문 ID입니다: {chunk_id}")
            catalog[chunk_id] = row
    if not catalog:
        raise ValueError(f"{path.name}에 평가용 조문이 없습니다.")
    return catalog


def validate_questions(
    questions: list[dict[str, Any]],
    chunk_ids: set[str],
    non_indexed: set[str] | None = None,
) -> list[str]:
    errors = []
    seen_questions: dict[str, int] = {}
    for index, row in enumerate(questions, start=1):
        if not isinstance(row.get("q"), str) or not row["q"].strip():
            errors.append(f"{index}번: 질문(q)이 비어 있습니다.")
        else:
            normalized_question = row["q"].strip().casefold()
            if normalized_question in seen_questions:
                errors.append(
                    f"{index}번: {seen_questions[normalized_question]}번과 중복된 질문입니다."
                )
            else:
                seen_questions[normalized_question] = index

        if row.get("유형") not in QUESTION_TYPES:
            errors.append(f"{index}번: 유형은 {', '.join(QUESTION_TYPES)} 중 하나여야 합니다.")
        if "세부유형" in row and row["세부유형"] not in QUESTION_SUBTYPES:
            errors.append(
                f"{index}번: 세부유형은 {', '.join(QUESTION_SUBTYPES)} 중 하나여야 합니다."
            )

        gold = row.get("정답")
        if not isinstance(gold, list) or not gold:
            errors.append(f"{index}번: 정답은 하나 이상의 조문 ID 목록이어야 합니다.")
            continue
        if any(not isinstance(chunk_id, str) or not chunk_id for chunk_id in gold):
            errors.append(f"{index}번: 정답 조문 ID는 비어 있지 않은 문자열이어야 합니다.")
            continue
        if len(set(gold)) != len(gold):
            errors.append(f"{index}번: 정답 조문 ID가 중복되어 있습니다.")
        missing = [chunk_id for chunk_id in gold if chunk_id not in chunk_ids]
        if missing:
            errors.append(f"{index}번: 청크 파일에 없는 정답 조문 ID: {', '.join(missing)}")
        unindexed = [chunk_id for chunk_id in gold if chunk_id in (non_indexed or set())]
        if unindexed:
            errors.append(
                f"{index}번: 삭제 조문·부칙처럼 색인되지 않는 정답 조문 ID: {', '.join(unindexed)}"
            )

        extra = set(row) - QUESTION_FIELDS
        if extra:
            errors.append(f"{index}번: 평가기에서 사용하지 않는 필드가 있습니다: {', '.join(sorted(extra))}")
    return errors


def save_questions(
    questions: list[dict[str, Any]],
    *,
    chunk_ids: set[str],
    non_indexed: set[str] | None = None,
    path: Path = QUESTIONS_PATH,
) -> None:
    errors = validate_questions(questions, chunk_ids, non_indexed)
    if errors:
        raise ValueError("\n".join(errors))
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as stream:
            for row in questions:
                saved = {"q": row["q"].strip(), "유형": row["유형"]}
                if "세부유형" in row:
                    saved["세부유형"] = row["세부유형"]
                saved["정답"] = row["정답"]
                stream.write(json.dumps(saved, ensure_ascii=False) + "\n")
        os.replace(temporary_name, path)
    except OSError:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
        raise
