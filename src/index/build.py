"""검색 색인 적재: 청크를 BGE-m3로 임베딩하고 Kiwi 형태소 토큰을 만든 뒤 PostgreSQL(pgvector)에 넣는다.

실행 (프로젝트 루트에서):
  uv run python -m src.index.build               # 전체 (조 단위 청크, 부칙·별지·삭제 제외하고 색인)
  uv run python -m src.index.build --limit 200   # 샘플 (적재 전체를 비우지 않고 샘플만 덮어씀)

전체 실행은 documents와 chunks를 비운 뒤 다시 넣는다.
"""
import argparse
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import numpy as np
from kiwipiepy import Kiwi

from config import EMBED_BATCH, EMBED_DIM, EMBED_MODEL, OUT_DIR
from src.store.pgstore import PgStore

sys.stdout.reconfigure(encoding="utf-8")

CHUNKS = os.path.join(OUT_DIR, "chunks.jsonl")


def load_chunks():
    """전체 청크를 읽는다. 색인 대상은 정상 조·항 청크(부칙 제외)다."""
    rows = []
    with io.open(CHUNKS, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def is_indexed(r):
    return r["상태"] == "정상" and not re.search(r"_(부칙|별지)(_중복\d+)?$", r["id"])


def embed_text(r):
    """임베딩 입력. 장·절 경로를 머리말로 붙인다 (예: "[제2장 감사의 종류 > 제1절 정기감사]\n제5조(정기감사) …").
    조 제목은 본문 첫 줄("제N조(제목)")에 이미 있다. 문서명은 붙이지 않는다: 청크가 많은 문서가 상위를
    독점할 수 있어서, 문서명은 리랭커 입력에만 붙인다 (search.py rerank_text). BM25 토큰에도 붙이지 않는다."""
    section = r.get("장")
    return f"[{section}]\n{r['본문_검색']}" if section else r["본문_검색"]


def documents_from(rows):
    """청크에 반복된 문서 정보를 고유번호 기준으로 한 번씩만 모은다."""
    docs = {}
    for r in rows:
        docs.setdefault(r["고유번호"], {
            "doc_id": r["고유번호"],
            "title": r["문서명"],
            "tier": r["위계"],
            "revision": r["개정표기"] or None,
            "source_url": r["출처URL"] or None,
            "file_name": r["파일"] or None,
        })
    return list(docs.values())


def build(limit=None, full=True, incremental=False):
    from sentence_transformers import SentenceTransformer  # 무거운 import는 실행 때만

    rows = load_chunks()
    store = PgStore()
    if incremental:
        # DB에 없거나 본문이 달라진 청크만 다시 넣는다 (나머지는 임베딩을 다시 계산하지 않는다).
        existing = store.chunk_texts()
        stale = sorted(set(existing) - {r["id"] for r in rows})
        if stale:
            store.delete_chunks(stale)
            print(f"더 이상 없는 청크 삭제: {len(stale)}개 {stale[:5]}")
        rows = [r for r in rows
                if existing.get(r["id"]) != (r["본문"], r["본문_검색"], r["상태"], r.get("장") or "")]
    indexed = [r for r in rows if is_indexed(r)]
    if limit:
        # 샘플: 색인 대상 중 앞부분만 쓴다. 나머지 청크는 적재하지 않는다.
        indexed = indexed[:limit]
        rows = [r for r in rows if not is_indexed(r)] + indexed
    print(f"적재 대상: 청크 {len(rows)}개 (색인 {len(indexed)}개, 부칙·별지·삭제 {len(rows) - len(indexed)}개)")

    if full and not limit and not incremental:
        store.truncate()
    store.upsert_documents(documents_from(rows))
    print(f"documents 적재: {len({r['고유번호'] for r in rows})}개")

    kiwi = Kiwi()
    t0 = time.time()
    model = SentenceTransformer(EMBED_MODEL, device="cuda")
    model.half()  # 8GB GPU에서 메모리를 줄이기 위해 fp16 사용
    assert model.get_sentence_embedding_dimension() == EMBED_DIM
    print(f"모델 로드: {time.time() - t0:.1f}초")

    now = datetime.now(timezone.utc)
    indexed_ids = {r["id"] for r in indexed}
    t0 = time.time()
    for start in range(0, len(rows), EMBED_BATCH):
        batch = rows[start:start + EMBED_BATCH]
        ind = [x for x in batch if x["id"] in indexed_ids]
        vecs = {}
        if ind:
            # 검색에는 정정 반영 글자(본문_검색)를 쓴다. 답변 인용은 원문(본문)을 가져온다.
            enc = model.encode([embed_text(x) for x in ind], batch_size=EMBED_BATCH,
                               normalize_embeddings=True, convert_to_numpy=True)
            vecs = {x["id"]: enc[i].astype(np.float32) for i, x in enumerate(ind)}
        store.upsert_chunks([to_row(x, vecs.get(x["id"]), kiwi, now) for x in batch])
        done = start + len(batch)
        if done % (EMBED_BATCH * 20) == 0 or done == len(rows):
            print(f"  {done}/{len(rows)} ({time.time() - t0:.0f}초)")

    print(f"chunks 적재 완료: 전체 {store.count()}, 색인 {store.count(is_indexed=True)}")
    store.close()


def to_row(r, vec, kiwi, now):
    indexed = vec is not None
    tokens = " ".join(t.form for t in kiwi.tokenize(r["본문_검색"])) if indexed else None
    return {
        "chunk_id": r["id"],
        "doc_id": r["고유번호"],
        "article": r["조항"],
        "section": r.get("장") or None,
        "status": r["상태"],
        "is_indexed": indexed,
        "body": r["본문"],
        "body_search": r["본문_검색"],
        "body_tokens": tokens,
        "corrections_note": r.get("정정안내") or None,
        "cites": r.get("cites", []),
        "embedding": vec,
        "embedding_model": EMBED_MODEL if indexed else None,
        "indexed_at": now if indexed else None,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--incremental", action="store_true", help="DB에 없거나 달라진 청크만 반영 (전체를 비우지 않음)")
    args = ap.parse_args()
    build(limit=args.limit, incremental=args.incremental)
