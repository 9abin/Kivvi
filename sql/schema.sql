-- 규정 검색 DB 스키마 (docs/DB_스키마.md 기준)
-- 실행: docker exec -i db-pg psql -U rag_reg -d regulations < sql/schema.sql
-- 주의: 테이블이 이미 있으면 실패한다. 다시 만들 때는 아래 DROP을 먼저 실행한다.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    doc_id      text PRIMARY KEY,                 -- 고유번호
    title       text NOT NULL,                    -- 문서명
    tier        text NOT NULL,                    -- 위계 (정관/규정/지침/헌장/강령/기타)
    revision    text,                             -- 개정표기
    source_url  text,                             -- 출처URL
    file_name   text                              -- 파일
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id          text PRIMARY KEY,                       -- id
    doc_id            text NOT NULL REFERENCES documents(doc_id),
    article           text,                                   -- 조항
    status            text NOT NULL CHECK (status IN ('정상', '삭제')),
    is_indexed        boolean NOT NULL,                       -- 색인대상
    body              text NOT NULL,                          -- 본문 (원문 표기)
    body_search       text NOT NULL,                          -- 본문_검색 (확정 오탈자 반영)
    body_tokens       text,                                   -- 형태소 토큰 (Kiwi, 공백 구분)
    corrections_note  jsonb,                                  -- 정정안내
    cites             jsonb NOT NULL DEFAULT '[]',            -- 인용 목록
    embedding         vector(1024),                           -- BGE-m3
    embedding_model   text,
    indexed_at        timestamptz,
    CHECK (is_indexed = false OR embedding IS NOT NULL)
);

-- 벡터 검색: 색인 대상만 포함
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
    ON chunks USING hnsw (embedding vector_cosine_ops)
    WHERE is_indexed;

-- 형제 항 확장과 같은 조 조회
CREATE INDEX IF NOT EXISTS chunks_doc_article_idx ON chunks (doc_id, article);

-- 위계 필터
CREATE INDEX IF NOT EXISTS documents_tier_idx ON documents (tier);

-- 키워드 검색: 형태소 토큰 전문 검색
CREATE INDEX IF NOT EXISTS chunks_body_tokens_gin
    ON chunks USING gin (to_tsvector('simple', body_tokens))
    WHERE is_indexed;

-- 다시 만들 때 (순서 중요: chunks를 먼저 지운다)
-- DROP TABLE IF EXISTS chunks;
-- DROP TABLE IF EXISTS documents;
