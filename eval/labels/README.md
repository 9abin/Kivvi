# 영향 후보 점검 평가 라벨 (AI 초안, 사람 검수 전)

스크립트(`scripts/exp_*`, `eval_*`)는 라벨을 `out/`(git 제외) 아래 경로에서 읽는다. 이 폴더는 그 라벨을 저장소에 보존한 사본이다. 재현하려면 아래 대응대로 `out/`에 복사한다.

| 이 폴더 | 복사할 곳 | 내용 |
|---|---|---|
| `leave_items/` | `out/leave_items/` | 휴직 시험 28문항(`eval/leave_items`)의 후보 풀 라벨 |
| `pay_items/` | `out/pay_items/` | 보수 · 수당 8문항(`eval/pay_items`), `_v2`는 후보 전체를 직접 읽게 한 재라벨 |
| `new_items/` | `out/new_items/` | 새 봉인 20문항(`eval/new_items`, N01~N20) |
| `disc_items/` | `out/disc_items/` | 징계 8문항(`eval/disc_items`) |
| `pool_extra/` | `out/pool_extra/` | RAG · BM25 상위로 모은 풀 밖 후보 보강 라벨 |
| `impact_r5_pool/` | `out/fresh_label_kit/산출/` | 새 가상 개정 5차(`eval/impact_label_kit_r5`, I61~I80) 풀 라벨 |
| `impact_r6_pool/` | `out/fresh6_label_kit/산출/` | 6차(I81~I100, I84는 형식 불량으로 제외) |
| `impact_r7_pool/` | `out/fresh7_label_kit/산출/` | 7차 휴직 20문항(I101~I120) |
| `coamend/` | `out/coamend_kit/산출/`, `out/coamend_none_key.json` | 동반 개정 「연결 없음」 282쌍의 AI 판정(`labels_a/b.json`)과 쌍 id 대응(`none_key.json`) |
| `four_domain_ops.json` | `out/four_domain_ops.json` | 한석희 120문항(`questions.md`)에서 파싱한 개정 항목(`scripts/four_domain_parse.py`) |

## 읽는 법과 주의

- 모든 라벨은 Claude 서브에이전트가 만든 **AI 초안**이다. 현업 확인이 없다. 라벨러는 시스템을 모르는 채 후보 풀의 조를 하나씩 읽고 `예 · 불확실 · 아니오`를 정했다.
- 형식: `[{"unit": "23234:20", "label": "예", "quote": "원문에서 복사한 근거 문장", "why": "한 문장"}]`. `quote`에 규정 원문의 짧은 발췌(80자 안팎)가 들어 있다. 원문 전체와 후보 풀 본문(`pool_*.md`)은 저장소에 올리지 않았다.
- `eval/impact_label_kit*/`: 개정문(`산출/changes.jsonl`), 라벨러 산출(`labels_*.jsonl`), 지시서, 검사기. 키트 안의 규정 원문 사본(`규정/`)은 `.gitignore`로 제외했다. 같은 규정 txt를 `규정목록.tsv`와 ALIO 링크로 복원한다.
- 라벨 파일에 있는 5차~7차 개정문(`eval/impact_label_kit_r5~r7/산출/changes.jsonl`)은 동결 후 실행한 봉인 문항이었으나 평가 뒤 코드를 고쳤으므로 이제 개발용이다. 결과와 한계는 `docs/reports/`의 83 · 85 · 86 · 87 · 88.
- 한석희 브랜치의 휴직 온톨로지 연결 표(`out/hyujik_links.jsonl`)는 작성자와 합의 전이라 포함하지 않았다.
