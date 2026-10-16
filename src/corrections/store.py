"""보정 목록(data/manual/corrections.json) 읽기와 검증.

상태 값
  확정(…)            : 검색용 ID 정정처럼 바로 적용 가능한 항목
  후보(…)            : 원본 확인 후 확정할 항목
  원본 확인 필요(…)   : 아직 판단 보류
"""
import io
import json

from config import CORRECTIONS
from src.chunk.articles import article_id


def load(path=CORRECTIONS):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def validate(corrections, chunk_ids):
    """대상 ID가 청크에 있는지 확인. 반환: 문제가 있는 항목 목록"""
    problems = []
    for c in corrections:
        target = c.get("대상ID") and article_id(c["대상ID"])
        if target and target not in chunk_ids and not any(i.startswith(target + "_") for i in chunk_ids):
            problems.append((c["id"], target))
    return problems


def check_pairs(corrections, doc_texts):
    """치환전 글자가 해당 문서 본문에 없으면 경고 목록으로 돌려준다.
    doc_texts: {고유번호: 본문 전체 문자열}"""
    warnings = []
    for c in corrections:
        before = c.get("치환전")
        if not before or not str(c.get("상태", "")).startswith("확정"):
            continue
        if before not in doc_texts.get(c.get("고유번호"), ""):
            warnings.append(f"치환전 없음: {c['id']} '{before[:30]}' ({c.get('고유번호')})")
    return warnings
