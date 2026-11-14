-- Kivvi DB ERD: 규정 원문 DB, 규정 관계 DB, 법령 DB. ERDCloud 가져오기용 DDL (MySQL 문법)

CREATE TABLE documents (
  doc_id      VARCHAR(20)  NOT NULL COMMENT '고유번호',
  title       VARCHAR(200) NOT NULL COMMENT '문서명',
  tier        VARCHAR(20)  NOT NULL COMMENT '위계(정관/규정/지침/헌장/강령/기타)',
  revision    VARCHAR(100) NULL COMMENT '개정표기',
  source_url  VARCHAR(500) NULL COMMENT '출처URL',
  file_name   VARCHAR(300) NULL COMMENT '원본 파일명',
  PRIMARY KEY (doc_id)
) COMMENT '규정 문서';

CREATE TABLE chunks (
  chunk_id         VARCHAR(100) NOT NULL COMMENT '청크 ID',
  doc_id           VARCHAR(20)  NOT NULL COMMENT '고유번호',
  article          VARCHAR(50)  NULL COMMENT '조',
  section          VARCHAR(200) NULL COMMENT '장·절 경로',
  status           VARCHAR(10)  NOT NULL COMMENT '상태(정상/삭제)',
  is_indexed       BOOLEAN      NOT NULL COMMENT '색인대상',
  body             TEXT         NOT NULL COMMENT '본문(원문 표기)',
  body_search      TEXT         NOT NULL COMMENT '검색용 본문(확정 오탈자 반영)',
  body_tokens      TEXT         NULL COMMENT '형태소 토큰',
  corrections_note JSON         NULL COMMENT '정정안내',
  cites            JSON         NOT NULL COMMENT '인용 목록',
  embedding        TEXT         NULL COMMENT '임베딩 벡터(1024차원)',
  embedding_model  VARCHAR(100) NULL COMMENT '임베딩 모델',
  indexed_at       DATETIME     NULL COMMENT '색인 시각',
  PRIMARY KEY (chunk_id),
  FOREIGN KEY (doc_id) REFERENCES documents (doc_id)
) COMMENT '조 단위 청크';

CREATE TABLE chunk_detail (
  item_id      VARCHAR(100) NOT NULL COMMENT '항·호·목 ID',
  occ          INT          NOT NULL COMMENT '같은 ID의 중복 순번',
  reg_id       VARCHAR(20)  NOT NULL COMMENT '규정 고유번호',
  article_id   VARCHAR(50)  NOT NULL COMMENT '조 ID',
  label        VARCHAR(50)  NOT NULL COMMENT '조 번호(제4조)',
  title        VARCHAR(200) NULL COMMENT '조 제목',
  hang         INT          NOT NULL COMMENT '항',
  ho           INT          NOT NULL COMMENT '호',
  mok          INT          NOT NULL COMMENT '목',
  text         TEXT         NOT NULL COMMENT '원문 표기',
  norm         TEXT         NOT NULL COMMENT '비교용 정규화 문장',
  deleted      BOOLEAN      NOT NULL COMMENT '삭제 여부',
  amend_marks  JSON         NOT NULL COMMENT '개정·신설 표기',
  chunk_id     VARCHAR(100) NULL COMMENT '대응하는 청크',
  chunk_match  VARCHAR(30)  NULL COMMENT '청크 대응 방식',
  PRIMARY KEY (item_id, occ),
  FOREIGN KEY (reg_id) REFERENCES documents (doc_id),
  FOREIGN KEY (chunk_id) REFERENCES chunks (chunk_id)
) COMMENT '항·호·목 단위 원문';

CREATE TABLE article_version (
  article_id   VARCHAR(50)  NOT NULL COMMENT '조 ID',
  occ          INT          NOT NULL COMMENT '같은 조 번호 중복 순번',
  valid_from   DATE         NOT NULL COMMENT '시행 시작일(개정일)',
  valid_to     DATE         NULL COMMENT '다음 개정일(현행이면 비움)',
  reg_id       VARCHAR(20)  NOT NULL COMMENT '규정 고유번호',
  label        VARCHAR(50)  NOT NULL COMMENT '조 번호',
  title        VARCHAR(200) NULL COMMENT '조 제목',
  items        JSON         NOT NULL COMMENT '항·호·목 원문',
  source_file  VARCHAR(300) NULL COMMENT '원본 파일',
  PRIMARY KEY (article_id, occ, valid_from),
  FOREIGN KEY (reg_id) REFERENCES documents (doc_id)
) COMMENT '개정 시점별 조문 사본';

CREATE TABLE edge (
  id              VARCHAR(20)  NOT NULL COMMENT '관계 ID',
  src             VARCHAR(100) NOT NULL COMMENT '인용하는 항(item_id)',
  src_article     VARCHAR(50)  NOT NULL COMMENT '인용하는 조',
  src_kind        VARCHAR(20)  NOT NULL COMMENT '본문/부칙/별표/별지',
  target          VARCHAR(200) NOT NULL COMMENT '인용 대상(조·별표·규정·법령)',
  target_type     VARCHAR(20)  NOT NULL COMMENT 'article/appendix/regulation/chapter/law',
  kind            VARCHAR(30)  NOT NULL COMMENT '인용/법령 인용/별표/별지 인용/문서 인용/위임',
  evidence        TEXT         NULL COMMENT '근거 문구',
  context         TEXT         NULL COMMENT '앞뒤 문맥',
  confidence      VARCHAR(10)  NULL COMMENT '확실/후보',
  verification    VARCHAR(10)  NULL COMMENT '자동/의심/확인됨',
  check_status    VARCHAR(30)  NULL COMMENT '점검 상태',
  src_version     DATE         NULL COMMENT '인용 쪽 개정일',
  target_version  DATE         NULL COMMENT '인용 대상 개정일',
  run             VARCHAR(50)  NULL COMMENT '만든 실행 ID',
  PRIMARY KEY (id)
) COMMENT '조항 간 인용 관계';

CREATE TABLE finding (
  fid         VARCHAR(50)  NOT NULL COMMENT '점검 결과 ID',
  review_key  VARCHAR(200) NOT NULL COMMENT '사람 결정과 잇는 키',
  reg_id      VARCHAR(20)  NOT NULL COMMENT '규정 고유번호',
  unit        VARCHAR(20)  NOT NULL COMMENT '단위',
  article     VARCHAR(50)  NULL COMMENT '조',
  para        INT          NULL COMMENT '항',
  src_id      VARCHAR(100) NULL COMMENT '출처 항 ID',
  chunk_id    VARCHAR(100) NULL COMMENT '청크',
  type        VARCHAR(50)  NOT NULL COMMENT '유형',
  method      VARCHAR(20)  NULL COMMENT '발견 방법(rule/semantic/law_db/history)',
  quote       TEXT         NULL COMMENT '근거 문장',
  context     TEXT         NULL COMMENT '문맥',
  suggest     TEXT         NULL COMMENT '제안',
  note        TEXT         NULL COMMENT '메모',
  run         VARCHAR(50)  NULL COMMENT '실행 ID',
  PRIMARY KEY (fid),
  FOREIGN KEY (reg_id) REFERENCES documents (doc_id),
  FOREIGN KEY (chunk_id) REFERENCES chunks (chunk_id)
) COMMENT '기준선 점검 결과';

CREATE TABLE scan (
  id            VARCHAR(50)  NOT NULL COMMENT '탐색 ID',
  started_at    DATETIME     NOT NULL COMMENT '시작 시각',
  law_snapshot  VARCHAR(300) NULL COMMENT '반입한 법령 DB 파일',
  law_count     INT          NULL COMMENT '법령 수',
  base          VARCHAR(50)  NULL COMMENT '비교 기준',
  counts        JSON         NOT NULL COMMENT '신규/해소/유지 건수',
  stats         JSON         NOT NULL COMMENT '통계',
  created_by    VARCHAR(50)  NULL COMMENT '실행자',
  PRIMARY KEY (id)
) COMMENT '법령 변경 탐색';

CREATE TABLE scan_item (
  scan_id     VARCHAR(50)  NOT NULL COMMENT '탐색 ID',
  review_key  VARCHAR(200) NOT NULL COMMENT '사람 결정과 잇는 키',
  change      VARCHAR(10)  NOT NULL COMMENT 'new/resolved/kept',
  reg_id      VARCHAR(20)  NOT NULL COMMENT '규정 고유번호',
  reg_name    VARCHAR(200) NULL COMMENT '규정명',
  unit        VARCHAR(20)  NULL COMMENT '단위',
  article     VARCHAR(50)  NULL COMMENT '조',
  para        INT          NULL COMMENT '항',
  chunk_id    VARCHAR(100) NULL COMMENT '청크',
  type        VARCHAR(50)  NOT NULL COMMENT '유형',
  method      VARCHAR(20)  NULL COMMENT '발견 방법',
  quote       TEXT         NULL COMMENT '근거 문장',
  note        TEXT         NULL COMMENT '메모',
  PRIMARY KEY (scan_id, review_key),
  FOREIGN KEY (scan_id) REFERENCES scan (id),
  FOREIGN KEY (reg_id) REFERENCES documents (doc_id)
) COMMENT '탐색 결과 항목';

CREATE TABLE changeset (
  id            VARCHAR(50)  NOT NULL COMMENT '개정안 ID',
  reg_id        VARCHAR(20)  NOT NULL COMMENT '규정 고유번호',
  title         VARCHAR(200) NULL COMMENT '제목',
  amendment     TEXT         NOT NULL COMMENT '개정문',
  status        VARCHAR(10)  NOT NULL COMMENT '초안/검토/승인/공포/기각',
  origin_chunk  VARCHAR(100) NULL COMMENT '개정안 작성을 시작한 청크',
  created_by    VARCHAR(50)  NULL COMMENT '작성자',
  created_at    DATETIME     NOT NULL COMMENT '작성 시각',
  payload       JSON         NOT NULL COMMENT '개정안 전체',
  PRIMARY KEY (id),
  FOREIGN KEY (reg_id) REFERENCES documents (doc_id)
) COMMENT '개정안';

CREATE TABLE impact (
  changeset_id  VARCHAR(50)  NOT NULL COMMENT '개정안 ID',
  review_key    VARCHAR(200) NOT NULL COMMENT '사람 결정과 잇는 키',
  category      VARCHAR(30)  NOT NULL COMMENT '영향 분류',
  reg_id        VARCHAR(20)  NOT NULL COMMENT '영향받는 규정',
  unit          VARCHAR(20)  NULL COMMENT '단위',
  article       VARCHAR(50)  NULL COMMENT '조',
  para          INT          NULL COMMENT '항',
  quote         TEXT         NULL COMMENT '근거 문장',
  reason        TEXT         NULL COMMENT '이유',
  handling      TEXT         NULL COMMENT '처리 방법',
  priority      VARCHAR(20)  NULL COMMENT '우선순위',
  path          VARCHAR(100) NULL COMMENT '발견 경로(인용받음/같은 제목/의미 유사)',
  suggest       TEXT         NULL COMMENT '제안',
  payload       JSON         NOT NULL COMMENT '원본 결과',
  PRIMARY KEY (changeset_id, review_key),
  FOREIGN KEY (changeset_id) REFERENCES changeset (id),
  FOREIGN KEY (reg_id) REFERENCES documents (doc_id)
) COMMENT '개정 영향 후보';

CREATE TABLE decision_log (
  seq         BIGINT       NOT NULL COMMENT '순번',
  review_key  VARCHAR(200) NOT NULL COMMENT '결정 대상 키',
  kind        VARCHAR(30)  NOT NULL COMMENT '종류',
  prev        VARCHAR(30)  NULL COMMENT '이전 상태',
  state       VARCHAR(30)  NOT NULL COMMENT '결정 상태',
  reviewer    VARCHAR(50)  NOT NULL COMMENT '검토자',
  at          DATETIME     NOT NULL COMMENT '결정 시각',
  comment     TEXT         NULL COMMENT '의견',
  ref         JSON         NOT NULL COMMENT '참고 정보',
  PRIMARY KEY (seq)
) COMMENT '사람 결정 기록(덧붙이기만)';

-- ===== 규정 관계 DB: 지식 그래프 (out/kg 의 nodes.jsonl, edges.jsonl 을 표로 표현) =====

CREATE TABLE kg_node (
  id        VARCHAR(100) NOT NULL COMMENT '노드 ID',
  type      VARCHAR(20)  NOT NULL COMMENT '노드 종류(Regulation/Article/Supplement/Annex/Form/Law/LawArticle/Term/Body/Org/Alias)',
  name      VARCHAR(200) NULL COMMENT '이름(규정명·용어 등)',
  reg       VARCHAR(20)  NULL COMMENT '소속 규정 고유번호',
  label     VARCHAR(50)  NULL COMMENT '조 번호',
  title     VARCHAR(200) NULL COMMENT '조 제목',
  tier      VARCHAR(20)  NULL COMMENT '위계',
  revised   DATE         NULL COMMENT '개정일',
  deleted   BOOLEAN      NULL COMMENT '삭제 여부',
  PRIMARY KEY (id),
  FOREIGN KEY (reg) REFERENCES documents (doc_id)
) COMMENT '지식 그래프 노드';

CREATE TABLE kg_edge (
  src       VARCHAR(100) NOT NULL COMMENT '시작 노드',
  dst       VARCHAR(100) NOT NULL COMMENT '도착 노드',
  type      VARCHAR(30)  NOT NULL COMMENT '관계 종류(CITES/SAME_TITLE/USES_TERM 등 22종)',
  method    VARCHAR(30)  NOT NULL COMMENT '만든 규칙',
  evidence  TEXT         NULL COMMENT '근거 문구',
  context   TEXT         NULL COMMENT '문맥',
  suspect   BOOLEAN      NULL COMMENT '의심 여부',
  status    VARCHAR(30)  NULL COMMENT '점검 상태',
  PRIMARY KEY (src, dst, type, method),
  FOREIGN KEY (src) REFERENCES kg_node (id),
  FOREIGN KEY (dst) REFERENCES kg_node (id)
) COMMENT '지식 그래프 관계';

-- ===== 법령 DB (SQLite: data/manual/law_rag.*.sqlite3) 표 이름 앞에 law_ 를 붙임 =====

CREATE TABLE law_documents (
  id               VARCHAR(50)  NOT NULL COMMENT '법령 ID',
  requested_title  VARCHAR(200) NOT NULL COMMENT '요청한 법령명',
  api_title        VARCHAR(200) NULL COMMENT 'API 법령명',
  target           VARCHAR(50)  NOT NULL COMMENT '대상 종류',
  api_id           VARCHAR(50)  NOT NULL COMMENT 'API 고유 ID',
  relation_type    VARCHAR(30)  NOT NULL COMMENT '수집 관계(seed 등)',
  relation_from    VARCHAR(50)  NULL COMMENT '수집한 출처 법령',
  relation_evidence TEXT        NULL COMMENT '수집 근거',
  effective_date   VARCHAR(20)  NULL COMMENT '시행일',
  current_status   VARCHAR(30)  NULL COMMENT '현행 여부',
  source_url       VARCHAR(500) NULL COMMENT '출처 URL',
  fetched_at       DATETIME     NOT NULL COMMENT '수집 시각',
  raw_json         JSON         NOT NULL COMMENT '원본 응답',
  PRIMARY KEY (id)
) COMMENT '법령';

CREATE TABLE law_chunks (
  id           INT          NOT NULL COMMENT '조문 청크 ID',
  document_id  VARCHAR(50)  NOT NULL COMMENT '법령 ID',
  path         VARCHAR(200) NOT NULL COMMENT '조·항 위치',
  content      TEXT         NOT NULL COMMENT '조문 본문',
  PRIMARY KEY (id),
  FOREIGN KEY (document_id) REFERENCES law_documents (id)
) COMMENT '법령 조문 청크';

CREATE TABLE law_relations (
  source_document_id  VARCHAR(50) NOT NULL COMMENT '법령 ID',
  target_document_id  VARCHAR(50) NOT NULL COMMENT '관련 법령 ID',
  relation_type       VARCHAR(30) NOT NULL COMMENT '관계 종류',
  evidence            TEXT        NULL COMMENT '근거',
  PRIMARY KEY (source_document_id, target_document_id, relation_type),
  FOREIGN KEY (source_document_id) REFERENCES law_documents (id),
  FOREIGN KEY (target_document_id) REFERENCES law_documents (id)
) COMMENT '법령 간 관계';

CREATE TABLE law_references (
  id                  INT          NOT NULL COMMENT '인용 ID',
  source_document_id  VARCHAR(50)  NOT NULL COMMENT '인용하는 법령',
  source_chunk_id     INT          NOT NULL COMMENT '인용하는 조문 청크',
  target_title        VARCHAR(200) NOT NULL COMMENT '인용 대상 법령명',
  target_article      VARCHAR(50)  NOT NULL COMMENT '인용 대상 조',
  target_document_id  VARCHAR(50)  NULL COMMENT '연결된 법령(없으면 비움)',
  evidence            TEXT         NOT NULL COMMENT '근거 문구',
  resolution_status   VARCHAR(30)  NOT NULL COMMENT '연결 상태',
  ref_scope           VARCHAR(20)  NOT NULL COMMENT '인용 범위(external 등)',
  hop                 INT          NOT NULL COMMENT '인용 단계',
  parent_reference_id INT          NULL COMMENT '상위 인용',
  PRIMARY KEY (id),
  FOREIGN KEY (source_document_id) REFERENCES law_documents (id),
  FOREIGN KEY (source_chunk_id) REFERENCES law_chunks (id),
  FOREIGN KEY (target_document_id) REFERENCES law_documents (id),
  FOREIGN KEY (parent_reference_id) REFERENCES law_references (id)
) COMMENT '법령 조문 간 인용';

CREATE TABLE law_appendix_texts (
  document_id  VARCHAR(50)  NOT NULL COMMENT '법령 ID',
  number       VARCHAR(30)  NOT NULL COMMENT '별표 번호',
  title        VARCHAR(200) NULL COMMENT '별표 제목',
  content      TEXT         NOT NULL COMMENT '별표 본문',
  source       VARCHAR(100) NULL COMMENT '출처',
  updated_at   DATETIME     NOT NULL COMMENT '갱신 시각',
  PRIMARY KEY (document_id, number),
  FOREIGN KEY (document_id) REFERENCES law_documents (id)
) COMMENT '법령 별표';

-- ===== 규정 DB와 법령 DB 연결: 규정이 법령을 인용 (edge.target_type = law) =====
