"""pgvector 저장소 어댑터. 검색과 적재 코드는 이 클래스의 메서드만 쓴다.

접속 정보는 프로젝트 루트의 .env (PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD)에서 읽는다.
스키마는 sql/schema.sql, 설명은 docs/DB_스키마.md를 본다.
"""
import os

import numpy as np
import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb

load_dotenv()


def _connect():
    conn = psycopg.connect(
        host=os.environ["PG_HOST"],
        port=os.environ["PG_PORT"],
        dbname=os.environ["PG_DATABASE"],
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"],
        connect_timeout=5,
    )
    register_vector(conn)
    return conn


def _tsquery(tokens):
    """형태소 토큰 목록을 OR 검색식으로 만든다. 작은따옴표는 두 번 써서 이스케이프한다."""
    return " | ".join("'" + t.replace("'", "''") + "'" for t in tokens)


class PgStore:
    def __init__(self):
        self.conn = _connect()

    def close(self):
        self.conn.close()

    # 적재 ---------------------------------------------------------------
    def upsert_documents(self, rows):
        """rows: dict 목록 (doc_id, title, tier, revision, source_url, file_name)"""
        with self.conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO documents (doc_id, title, tier, revision, source_url, file_name)
                VALUES (%(doc_id)s, %(title)s, %(tier)s, %(revision)s, %(source_url)s, %(file_name)s)
                ON CONFLICT (doc_id) DO UPDATE SET
                    title = EXCLUDED.title, tier = EXCLUDED.tier, revision = EXCLUDED.revision,
                    source_url = EXCLUDED.source_url, file_name = EXCLUDED.file_name
                """,
                rows,
            )
        self.conn.commit()

    def upsert_chunks(self, rows):
        """rows: dict 목록. embedding은 numpy 배열 또는 None (색인 대상이 아니면 None)."""
        params = []
        for r in rows:
            params.append({
                "chunk_id": r["chunk_id"],
                "doc_id": r["doc_id"],
                "article": r["article"],
                "status": r["status"],
                "is_indexed": r["is_indexed"],
                "body": r["body"],
                "body_search": r["body_search"],
                "body_tokens": r["body_tokens"],
                "corrections_note": Jsonb(r["corrections_note"]) if r["corrections_note"] else None,
                "cites": Jsonb(r["cites"]),
                "embedding": r["embedding"],
                "embedding_model": r["embedding_model"],
                "indexed_at": r["indexed_at"],
            })
        with self.conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO chunks (chunk_id, doc_id, article, status, is_indexed, body, body_search,
                                    body_tokens, corrections_note, cites, embedding, embedding_model, indexed_at)
                VALUES (%(chunk_id)s, %(doc_id)s, %(article)s, %(status)s, %(is_indexed)s, %(body)s,
                        %(body_search)s, %(body_tokens)s, %(corrections_note)s, %(cites)s,
                        %(embedding)s, %(embedding_model)s, %(indexed_at)s)
                ON CONFLICT (chunk_id) DO UPDATE SET
                    doc_id = EXCLUDED.doc_id, article = EXCLUDED.article, status = EXCLUDED.status,
                    is_indexed = EXCLUDED.is_indexed, body = EXCLUDED.body, body_search = EXCLUDED.body_search,
                    body_tokens = EXCLUDED.body_tokens, corrections_note = EXCLUDED.corrections_note,
                    cites = EXCLUDED.cites, embedding = EXCLUDED.embedding,
                    embedding_model = EXCLUDED.embedding_model, indexed_at = EXCLUDED.indexed_at
                """,
                params,
            )
        self.conn.commit()

    def chunk_texts(self):
        """청크 ID별 (본문, 검색용 본문, 상태). 증분 색인에서 달라진 청크를 가릴 때 쓴다.
        상태도 비교한다: 본문은 그대로이고 정상→삭제로만 바뀐 청크도 다시 적재해야 색인에서 빠진다."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT chunk_id, body, body_search, status FROM chunks")
            return {cid: (b, bs, st) for cid, b, bs, st in cur.fetchall()}

    def delete_chunks(self, chunk_ids):
        """청크 ID 목록을 지운다. 증분 색인에서 더 이상 없는 청크를 정리할 때 쓴다."""
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE chunk_id = ANY(%s)", (list(chunk_ids),))
        self.conn.commit()

    def truncate(self):
        """전체 재적재 전에 비운다. chunks가 documents를 참조하므로 함께 비운다."""
        with self.conn.cursor() as cur:
            cur.execute("TRUNCATE chunks, documents")
        self.conn.commit()

    def count(self, is_indexed=None):
        with self.conn.cursor() as cur:
            if is_indexed is None:
                cur.execute("SELECT count(*) FROM chunks")
            else:
                cur.execute("SELECT count(*) FROM chunks WHERE is_indexed = %s", (is_indexed,))
            return cur.fetchone()[0]

    # 검색 ---------------------------------------------------------------
    def vector_search(self, vector, k=10, doc_ids=None):
        """벡터 검색. 색인 대상 청크만 본다. doc_ids를 주면 그 문서의 청크만 본다 (메타데이터 필터)."""
        vec = np.asarray(vector, dtype=np.float32)
        sql = """
            SELECT c.chunk_id, c.doc_id, c.article, c.body, 1 - (c.embedding <=> %(vec)s) AS score
            FROM chunks c
            WHERE c.is_indexed
        """
        params = {"vec": vec, "k": k}
        if doc_ids is not None:
            sql += " AND c.doc_id = ANY(%(doc_ids)s)"
            params["doc_ids"] = list(doc_ids)
        sql += " ORDER BY c.embedding <=> %(vec)s LIMIT %(k)s"
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return _rows(cur)

    def keyword_search(self, tokens, k=10, tier=None):
        """키워드 검색. tokens는 Kiwi로 나눈 질의 토큰 목록. 하나라도 맞으면 후보가 되고 ts_rank로 정렬한다."""
        if not tokens:
            return []
        sql = """
            SELECT c.chunk_id, c.doc_id, c.article, c.body,
                   ts_rank(to_tsvector('simple', c.body_tokens), to_tsquery('simple', %(q)s)) AS score
            FROM chunks c JOIN documents d ON d.doc_id = c.doc_id
            WHERE c.is_indexed
              AND to_tsvector('simple', c.body_tokens) @@ to_tsquery('simple', %(q)s)
        """
        params = {"q": _tsquery(tokens), "k": k}
        if tier:
            sql += " AND d.tier = %(tier)s"
            params["tier"] = tier
        sql += " ORDER BY score DESC LIMIT %(k)s"
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return _rows(cur)

    def siblings(self, doc_id, article, exclude_chunk_id=None):
        """같은 조(예: '제5조')의 모든 항을 항 순서대로 가져온다. 조 부분은 공백 앞 글자로 비교한다."""
        base = article.split(" ")[0] if article else None
        if not base:
            return []
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT chunk_id, doc_id, article, body, status, corrections_note, NULL::float AS score
                FROM chunks
                WHERE doc_id = %(doc)s AND split_part(article, ' ', 1) = %(base)s
                  AND (%(ex)s::text IS NULL OR chunk_id <> %(ex)s)
                ORDER BY chunk_id
                """,
                {"doc": doc_id, "base": base, "ex": exclude_chunk_id},
            )
            return _rows(cur)

    def index_version(self):
        """색인 버전: 색인 대상 청크 수와 가장 최근 색인 시각. 재색인되면 값이 바뀐다."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*), max(indexed_at)::text FROM chunks WHERE is_indexed")
            return cur.fetchone()

    def load_documents(self):
        """documents 전체(90건)를 읽는다. 메타데이터 필터(문서명·위계·개정일)를 만들 때 쓴다."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT doc_id, title, tier, revision, source_url FROM documents")
            return _rows(cur)

    def load_indexed_rows(self):
        """색인 대상 청크 전체를 읽는다. BM25 역색인을 메모리에 만들 때 쓴다 (search/bm25.py)."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.chunk_id, c.doc_id, c.article, c.body, c.body_search, c.body_tokens, c.cites,
                       c.corrections_note, d.tier
                FROM chunks c JOIN documents d ON d.doc_id = c.doc_id
                WHERE c.is_indexed
                """
            )
            return _rows(cur)

    def find_article(self, doc_id, base, para=None):
        """문서와 조(·항)로 직접 조회한다. para가 없으면 그 조의 모든 항을 항 순서대로 돌려준다."""
        if para:
            label = f"{base} 제{para}항"
            cond = "article = %(label)s"
        else:
            label = base
            cond = "split_part(article, ' ', 1) = %(label)s"
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT chunk_id, doc_id, article, body, status, is_indexed, cites, corrections_note
                FROM chunks
                WHERE doc_id = %(doc)s AND {cond}
                ORDER BY chunk_id
                """,
                {"doc": doc_id, "label": label},
            )
            return _rows(cur)

    def get(self, chunk_ids):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT chunk_id, doc_id, article, body, status, is_indexed, cites, corrections_note FROM chunks WHERE chunk_id = ANY(%s)",
                (list(chunk_ids),),
            )
            return _rows(cur)


def _rows(cur):
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
