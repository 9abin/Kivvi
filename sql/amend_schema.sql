-- amend 스키마: 조항 수정 에이전트(박형건) 작업 데이터. 챗봇 표(public, 김가빈)와 같은 DB, 다른 스키마.
-- 합의안: docs/chatbot-integration-proposal.md (261012 제안, 합의 전 — 공통 키·권한은 합의 결과에 따라 바뀔 수 있음)
--
-- 원칙
--   · public 표에는 쓰지 않는다. 개정 초안은 public.chunks에 넣지 않는다 (챗봇이 승인 전 문안으로 답하면 안 됨).
--   · public 참조는 외래키로 걸지 않는다(chunk_id 칸 + 확인 뷰). 가빈이 덤프를 --clean으로 다시 적재하면
--     public.chunks가 지워졌다 다시 생기는데, 외래키가 있으면 그 적재가 막히거나 우리 표가 같이 지워진다.
--   · 사람 결정(decision_log)은 덧붙이기만. 고치거나 지우면 트리거가 막는다.
-- 다시 실행해도 된다(IF NOT EXISTS). 표 구조를 바꿀 때는 이 파일을 고치고 scripts/amend_load.py --reset.

CREATE SCHEMA IF NOT EXISTS amend;

-- ① 원문 계층 ------------------------------------------------------------
-- 항·호·목 단위 (우리 파서). chunk_id = 같은 내용의 가빈 청크 (항 단위, 호·목은 소속 항의 청크)
CREATE TABLE IF NOT EXISTS amend.chunk_detail (
    item_id      text NOT NULL,                   -- 24024:4-1-0-0 (규정:조[의가지]-항-호-목)
    occ          int  NOT NULL DEFAULT 0,         -- 같은 ID가 다시 나온 순번. 원문 항 번호 중복(⑤⑤)만 1 이상
    reg_id       text NOT NULL,                   -- = public.documents.doc_id
    article_id   text NOT NULL,                   -- 24024:4
    label        text NOT NULL,                   -- 제4조
    title        text,
    hang         int  NOT NULL,
    ho           int  NOT NULL,
    mok          int  NOT NULL,
    text         text NOT NULL,                   -- 원문 표기
    norm         text NOT NULL,                   -- 비교용 정규화
    deleted      boolean NOT NULL DEFAULT false,
    amend_marks  jsonb NOT NULL DEFAULT '[]',     -- 항 끝의 <개정 …>·<신설 …>
    chunk_id     text,                            -- 가빈 public.chunks.chunk_id (대응 못 하면 null)
    chunk_match  text,                            -- 같음 / 단일 항 조 / 가빈 조 단위 / null
    PRIMARY KEY (item_id, occ)
);
CREATE INDEX IF NOT EXISTS chunk_detail_chunk_idx   ON amend.chunk_detail (chunk_id);
CREATE INDEX IF NOT EXISTS chunk_detail_article_idx ON amend.chunk_detail (article_id);

-- 공포 시점별 조문 사본 (가빈 표는 현행본만). 대비표·과거 재현·"개정 검토 중" 표시용
CREATE TABLE IF NOT EXISTS amend.article_version (
    article_id   text NOT NULL,
    occ          int  NOT NULL DEFAULT 0,         -- 원문에 같은 조 번호가 두 번 나오면 1 (경영공시 관리지침 제12조 등)
    valid_from   date NOT NULL,                   -- 이 문안이 실린 개정본의 개정일
    valid_to     date,                            -- 다음 개정본 개정일 (현행이면 null)
    reg_id       text NOT NULL,
    label        text NOT NULL,
    title        text,
    items        jsonb NOT NULL,                  -- [{path, text}] 원문 그대로
    source_file  text,
    PRIMARY KEY (article_id, occ, valid_from)
);

-- ② 관계 계층: 관련 조항 표 (out/relations/relations.jsonl). 그래프 = 이 표 + target 역방향 조회
CREATE TABLE IF NOT EXISTS amend.edge (
    id              text PRIMARY KEY,             -- R00008
    src             text NOT NULL,                -- 인용하는 항 (chunk_detail.item_id 형식)
    src_article     text NOT NULL,
    src_kind        text NOT NULL,                -- 본문·부칙·별표·별지
    target          text NOT NULL,                -- 조 ID·별표 ID·규정 ID 또는 「법령명 제N조」
    target_type     text NOT NULL,                -- article·appendix·regulation·chapter·law
    kind            text NOT NULL,                -- 인용·법령 인용·별표·별지 인용·문서 인용·위임
    evidence        text,
    context         text,
    confidence      text,                         -- 확실·후보
    verification    text,                         -- 자동·의심·확인됨
    check_status    text,                         -- ok·law_pending·missing_article…
    src_version     date,                         -- 인용 쪽 개정일 (개정 이력 신호)
    target_version  date,
    run             text                          -- 만든 실행 ID (역추적)
);
CREATE INDEX IF NOT EXISTS edge_target_idx ON amend.edge (target);   -- "누가 나를 인용하나"
CREATE INDEX IF NOT EXISTS edge_src_idx    ON amend.edge (src_article);

-- ④ 점검 계층 ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS amend.finding (
    fid       text PRIMARY KEY,                   -- 실행마다 바뀜. 사람 결정은 review_key로 잇는다
    review_key text NOT NULL,                     -- B: + 해시(규정·단위·조·항·문구·유형), regcheck.review.key_baseline
    reg_id    text NOT NULL,
    unit      text NOT NULL,
    article   text,
    para      int,
    src_id    text,
    chunk_id  text,
    type      text NOT NULL,
    method    text,                               -- rule·semantic·law_db·history
    quote     text,
    context   text,
    suggest   text,
    note      text,
    run       text
);
CREATE INDEX IF NOT EXISTS finding_chunk_idx ON amend.finding (chunk_id);
CREATE INDEX IF NOT EXISTS finding_key_idx   ON amend.finding (review_key);

-- 변경 감시: 법률 DB 새 판을 반입하고 「탐색 시작」을 누를 때마다 한 줄 (regcheck.watch). 직전 탐색(없으면 amend.finding)과 비교
CREATE TABLE IF NOT EXISTS amend.scan (
    id           text PRIMARY KEY,                -- SC261014-153000
    started_at   timestamptz NOT NULL DEFAULT now(),
    law_snapshot text,                            -- 반입한 법률 DB 파일 (data/law_rag*.sqlite3)
    law_count    int,
    base         text,                            -- 비교 기준: 직전 탐색 id 또는 'finding'(기준선 적재본)
    counts       jsonb NOT NULL,                  -- {"new": n, "resolved": n, "kept": n}
    stats        jsonb NOT NULL DEFAULT '{}',     -- 법령 연결 통계 등
    created_by   text
);
CREATE TABLE IF NOT EXISTS amend.scan_item (
    scan_id    text NOT NULL REFERENCES amend.scan(id) ON DELETE CASCADE,
    review_key text NOT NULL,                     -- amend.finding.review_key와 같은 규칙 (B: + 해시)
    change     text NOT NULL CHECK (change IN ('new', 'resolved', 'kept')),
    reg_id     text NOT NULL,
    reg_name   text,
    unit       text,
    article    text,
    para       int,
    chunk_id   text,
    type       text NOT NULL,
    method     text,
    quote      text,
    note       text,
    PRIMARY KEY (scan_id, review_key)
);

-- 개정안. 상태: 초안 → 검토 → 승인 → 공포 (또는 기각). 승인은 개정이 아니다 (공포는 회사 절차)
CREATE TABLE IF NOT EXISTS amend.changeset (
    id          text PRIMARY KEY,
    reg_id      text NOT NULL,
    title       text,
    amendment   text NOT NULL,                    -- 개정문
    status      text NOT NULL DEFAULT '초안' CHECK (status IN ('초안', '검토', '승인', '공포', '기각')),
    origin_chunk text,                            -- 챗봇 답변에서 「개정안 작성」을 누른 청크
    created_by  text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    payload     jsonb NOT NULL                    -- changeset.json 전체 (changes·label_map·rows)
);

-- 변경안 점검·연쇄 수정 결과 (out/changes/{id}/check.json)
CREATE TABLE IF NOT EXISTS amend.impact (
    changeset_id text NOT NULL REFERENCES amend.changeset(id) ON DELETE CASCADE,
    review_key   text NOT NULL,                   -- I:{변경안}: + 해시, regcheck.review.key_impact
    category     text NOT NULL,                   -- breaks·new_conflicts·resolved·review·law_check·undeterminable·info
    reg_id       text NOT NULL,
    unit         text,
    article      text,
    para         int,
    quote        text,
    reason       text,
    handling     text,
    priority     text,
    path         text,                            -- 발견 경로 (인용받음·같은 제목·의미 유사…)
    suggest      text,
    payload      jsonb NOT NULL,
    PRIMARY KEY (changeset_id, review_key)
);

-- 사람 결정 (eval/review_log.jsonl과 같은 형식). 덧붙이기만
CREATE TABLE IF NOT EXISTS amend.decision_log (
    seq       bigserial PRIMARY KEY,
    key       text NOT NULL,                      -- B:/R:/I: 고정 키
    kind      text NOT NULL,
    prev      text,
    state     text NOT NULL,
    reviewer  text NOT NULL,
    at        timestamptz NOT NULL DEFAULT now(),
    comment   text,
    ref       jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS decision_log_key_idx ON amend.decision_log (key, seq);

CREATE OR REPLACE FUNCTION amend.forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'amend.decision_log는 덧붙이기만 합니다 (사람 결정은 고치거나 지우지 않음)';
END $$;
DROP TRIGGER IF EXISTS decision_log_append_only ON amend.decision_log;
CREATE TRIGGER decision_log_append_only BEFORE UPDATE OR DELETE ON amend.decision_log
    FOR EACH ROW EXECUTE FUNCTION amend.forbid_change();
DROP TRIGGER IF EXISTS decision_log_no_truncate ON amend.decision_log;
CREATE TRIGGER decision_log_no_truncate BEFORE TRUNCATE ON amend.decision_log
    FOR EACH STATEMENT EXECUTE FUNCTION amend.forbid_change();

-- 조회용 뷰 ---------------------------------------------------------------
-- 각 결정 열쇠의 현재 상태 (마지막 줄)
DROP VIEW IF EXISTS amend.v_decision_state;
CREATE VIEW amend.v_decision_state AS
SELECT DISTINCT ON (key) key, kind, state, reviewer, at, comment
FROM amend.decision_log ORDER BY key, seq DESC;

-- 이 조를 인용하는 곳 (인용 1건 = 1행). 인용은 조 단위로 적히는 경우가 많아 대상 청크는 그 조의 항 청크 묶음으로 준다.
-- 챗봇 청크에서 조회: WHERE '24184_제7조_1항' = ANY(target_chunks)
DROP VIEW IF EXISTS amend.v_inbound;
CREATE VIEW amend.v_inbound AS
SELECT e.target, e.kind, e.evidence, e.check_status, e.src, s.chunk_id AS src_chunk, s.reg_id AS src_reg, s.label AS src_label,
       e.context,
       ARRAY(SELECT t.chunk_id FROM amend.chunk_detail t
             WHERE t.article_id = e.target AND t.ho = 0 AND t.mok = 0 AND t.occ = 0 AND t.chunk_id IS NOT NULL
             ORDER BY t.hang) AS target_chunks
FROM amend.edge e
LEFT JOIN amend.chunk_detail s ON s.item_id = e.src AND s.occ = 0
WHERE e.target_type = 'article';

-- 챗봇 배지: 진행 중인 개정안이 걸린 청크 (초안·검토·승인)
DROP VIEW IF EXISTS amend.v_chunk_open_change;
CREATE VIEW amend.v_chunk_open_change AS
SELECT DISTINCT d.chunk_id, c.id AS changeset_id, c.status, c.title
FROM amend.changeset c
CROSS JOIN LATERAL jsonb_array_elements(c.payload -> 'changes') ch
JOIN amend.chunk_detail d ON d.article_id = ch ->> 'origin'   -- 바뀌는 조 (신설 조는 origin 없음)
WHERE c.status IN ('초안', '검토', '승인') AND d.chunk_id IS NOT NULL;

-- 가빈 청크와 대응 안 된 우리 항 / 가빈 청크가 사라진 대응 (재적재 후 확인)
DROP VIEW IF EXISTS amend.v_chunk_unmatched;
CREATE VIEW amend.v_chunk_unmatched AS
SELECT d.item_id, d.chunk_id, CASE WHEN d.chunk_id IS NULL THEN '대응 없음' ELSE '가빈 청크 사라짐' END AS problem
FROM amend.chunk_detail d
LEFT JOIN public.chunks c ON c.chunk_id = d.chunk_id
WHERE d.ho = 0 AND d.mok = 0 AND (d.chunk_id IS NULL OR c.chunk_id IS NULL);

-- 권한 (합의안 ④). 로그인 없는 역할 묶음 — 실제 계정에 GRANT 역할 TO 계정
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'chatbot_ro')    THEN CREATE ROLE chatbot_ro NOLOGIN;    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'editor')        THEN CREATE ROLE editor NOLOGIN;        END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'amend_builder') THEN CREATE ROLE amend_builder NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'loader')        THEN CREATE ROLE loader NOLOGIN;        END IF;
END $$;

-- chatbot_ro: 챗봇. public 읽기 + 배지 뷰만
GRANT USAGE ON SCHEMA public, amend TO chatbot_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO chatbot_ro;
GRANT SELECT ON amend.v_chunk_open_change TO chatbot_ro;

-- editor: 내규담당부서·준법감시인. public 읽기, amend 읽기, 개정안·영향 쓰기, 결정은 INSERT만
GRANT USAGE ON SCHEMA public, amend TO editor;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO editor;
GRANT SELECT ON ALL TABLES IN SCHEMA amend TO editor;
GRANT INSERT, UPDATE ON amend.changeset, amend.impact TO editor;
GRANT INSERT ON amend.decision_log TO editor;
GRANT USAGE ON SEQUENCE amend.decision_log_seq_seq TO editor;
GRANT INSERT ON amend.scan, amend.scan_item TO editor;   -- 「탐색 시작」 결과 기록 (점검 결과 표 자체는 못 바꿈)

-- amend_builder: 파이프라인(파싱·관계·점검 결과 적재). public은 읽기만
GRANT USAGE ON SCHEMA public, amend TO amend_builder;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO amend_builder;
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON amend.chunk_detail, amend.article_version, amend.edge, amend.finding TO amend_builder;
GRANT SELECT, INSERT, UPDATE, DELETE ON amend.changeset, amend.impact TO amend_builder;
GRANT SELECT, INSERT ON amend.decision_log TO amend_builder;
GRANT USAGE ON SEQUENCE amend.decision_log_seq_seq TO amend_builder;

-- loader: 공포본 적재 (public 쓰기). amend는 손대지 않음
GRANT USAGE ON SCHEMA public TO loader;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO loader;

-- team_ro: 팀 공유 조회 (DBeaver). 가빈 표·우리 표 모두 읽기만. 로그인 계정은 scripts/db_team_account.py가 만든다
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'team_ro') THEN CREATE ROLE team_ro NOLOGIN; END IF;
END $$;
GRANT USAGE ON SCHEMA public, amend TO team_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO team_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA amend TO team_ro;

-- 설명 (DBeaver 표·칸 목록에 보인다) ------------------------------------------
COMMENT ON SCHEMA amend IS '조항 수정 에이전트(박형건) 작업 데이터. 가빈 챗봇 표(public)는 읽기만 한다. 정의: sql/amend_schema.sql';
COMMENT ON TABLE amend.chunk_detail IS '항·호·목 단위 원문 (우리 파서). chunk_id = 같은 내용의 가빈 청크';
COMMENT ON COLUMN amend.chunk_detail.item_id IS '규정:조[의가지]-항-호-목 (예 24024:4-1-0-0)';
COMMENT ON COLUMN amend.chunk_detail.occ IS '같은 ID가 다시 나온 순번. 원문 항 번호 중복(⑤⑤)만 1 이상';
COMMENT ON COLUMN amend.chunk_detail.chunk_id IS '가빈 public.chunks.chunk_id (대응 못 하면 비어 있음)';
COMMENT ON COLUMN amend.chunk_detail.chunk_match IS '같음 / 단일 항 조 / 가빈 조 단위';
COMMENT ON TABLE amend.article_version IS '공포 시점별 조문 사본 (지금은 현행본 1벌). 대비표·과거 재현용';
COMMENT ON TABLE amend.edge IS '관련 조항 표 = 그래프 간선. src(인용하는 항) → target(조·별표·규정·법령). "누가 나를 인용하나"는 target으로 조회';
COMMENT ON COLUMN amend.edge.check_status IS 'ok 정상 / missing_article 대상 조 없음 / law_pending 법령(법률 DB 대조) / external_rule 외부 기준 / deleted_target 삭제된 조 …';
COMMENT ON COLUMN amend.edge.src_version IS '인용 쪽 규정의 개정일 (개정 미반영 신호)';
COMMENT ON TABLE amend.finding IS '기준선 점검 결과 (오류 후보). 판정이 아니라 담당자 검토용';
COMMENT ON COLUMN amend.finding.review_key IS '사람 결정과 잇는 고정 키 (fid는 실행마다 바뀜)';
COMMENT ON COLUMN amend.finding.method IS 'rule 규칙 / semantic 의미 비교 / law_db 법률 DB / history 개정 이력';
COMMENT ON TABLE amend.scan IS '변경 감시 탐색 기록. 법률 DB 새 판 반입 후 점검을 다시 돌린 결과 요약';
COMMENT ON TABLE amend.scan_item IS '탐색 결과 항목. change = new 새로 생김 / resolved 해결됨 / kept 그대로';
COMMENT ON TABLE amend.changeset IS '개정안. 상태 초안→검토→승인→공포(또는 기각). 승인은 개정이 아님';
COMMENT ON TABLE amend.impact IS '개정안의 영향 (변경안 점검·연쇄 수정 결과)';
COMMENT ON COLUMN amend.impact.category IS 'breaks 깨짐 / new_conflicts 새 충돌 / resolved 해소 / review 확인 필요 / law_check 법령 대조 / undeterminable 판단 불가 / info 참고';
COMMENT ON TABLE amend.decision_log IS '사람 결정 기록. 덧붙이기만 (고치거나 지우면 트리거가 거부)';
COMMENT ON VIEW amend.v_inbound IS '이 조를 인용하는 곳 (인용 1건 = 1행). 챗봇 청크로: WHERE ''24184_제7조_1항'' = ANY(target_chunks)';
COMMENT ON VIEW amend.v_chunk_open_change IS '진행 중 개정안이 걸린 가빈 청크 (챗봇 "개정 검토 중" 배지)';
COMMENT ON VIEW amend.v_chunk_unmatched IS '가빈 청크와 대응 안 된 우리 항 (가빈 재적재 후 확인)';
COMMENT ON VIEW amend.v_decision_state IS '결정 열쇠별 현재 상태 (마지막 기록)';
