# 최종 벤치마크 100건 - 사람 검토 전 AI 초안

- 형식은 val셋과 같다: `산출/changes.jsonl`(개정 B001~B100, 분할=봉인), `산출/labels_final.jsonl`(영향 조, 근거 문장은 원문 발췌). `python validate.py labels 산출/labels_final.jsonl 산출/changes.jsonl` 통과.
- 조 뽑기: `kit_sample.py`(시드 20261120). 도메인 25건(도메인 DB에서 정의/근거로 연결된 조), 그 밖 75건(전체 규정, 도메인 후보 조 제외), 규정별 최대 3건. 제외: 메모의 목록 + val셋 30건 + val 교체분(`excluded_articles.json`, `used_articles.json`, `replaced_round1.json`).
- 개정문 작성(A)은 서브에이전트 4개가 25건씩, 뽑힌 조 본문만 보고 썼다. B1/B2는 서로 못 보는 격리 폴더에서 구간별(25건)로, C는 B1/B2 산출물만 보고 구간별(50건)로 했다. A는 후보 찾기 코드, 평가 결과, 그래프를 보지 않았다.
- 일치도: `산출/agreement_B.md`. 사람이 검토하기 전의 AI 초안이다.
- 이 폴더의 `지시서.md`는 라벨러용 원문(30건 키트 버전)이고, 라벨러에게는 건수 문구만 100건으로 고친 사본을 줬다. 원본 키트 `eval/impact_label_kit_dom/`은 작업 트리에 없어 커밋 d2680e9에서 꺼냈다.
