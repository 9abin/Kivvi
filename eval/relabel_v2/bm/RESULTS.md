# 개발 100 (bm) 결과

작성 261112. 역할: **개발셋**(261111 결정, val 30은 더 쓰지 않음). 값 · 프롬프트 · 규칙을 이 세트를 보고 정했으므로 아래 숫자는 이 세트에 맞춘 값이다. 일반화 성능은 [시험 100](../test/RESULTS.md).
관련: [리포트 94](../../../docs/reports/261111-2100-94-eval-metrics.md), [리포트 93](../../../docs/reports/261111-1500-93-candidate-state-pipeline.md)

## 설정
커밋 `0905d80`: 판단 모델(kanana_decide) 0.7 · 정의 용어 통과 · luna 입력 RAG 50 + 그래프 위임 · 언급 10 · orders 2 · 답변 luna. 판정 모델 solar-pro4.
정답: `gold.jsonl` id 기준 93개(확신 높음 34 · 중간 59), 정답 있는 문항 55, 정답 0 문항 45.

## 찾기
| 지표 | 값 (95% 구간) |
|---|---|
| 검색 재현율 (문항당 후보 58) | 0.967 |
| **최종 재현율** (문항당 보임 5.2) | **0.934** |
| └ 확신 높음 / 중간 | 34/34 = 1.000 / 53/59 = 0.898 |
| (평가 제외) 확신 낮음 | 25/31 |
| Recall@5 | 0.780 (0.696~0.881) |
| Recall@10 | 0.912 (0.849~0.973), 재실행 0.901 |
| Hit@5 | 0.964 (0.906~1.000) |
| MRR | 0.734 (0.650~0.815) |
| MAP / nDCG@10 | 0.689 / 0.769 |

- 화면을 늘리면: 숨긴 규칙 후보 전부 0.934(9.1개), + luna 입력 상위 10 0.967(17.5개), 검색 후보 전부 0.989(88개).
- 상위 10 밖 9개: 검색 못함 1, 후보에 있었으나 거르기 · luna에서 빠짐 5, 11위 이하 3.

## 정밀도 (solar-pro4 판정 보완)
| 지표 | ① 라벨만 | ② 라벨 + Solar 「예」 |
|---|---|---|
| 목록 전체 | 0.161 | 0.592 |
| Precision@5 | 0.251 | 0.641 |
| Context Precision 식 | 0.430 | 0.750 |

- ragas 라이브러리: LLMContextPrecisionWithReference 0.475, IDBasedContextPrecision 0.224.
- Solar 신뢰도: 라벨 정답에 「예」 72/85(0.847), 라벨에 없는 후보에 「예」 227/442(0.514). 실제 정밀도는 ①과 ② 사이.
- 정답 0 문항 45: 빈 목록 8, 문항당 4.0개.

## 답변
| 답변 | Faithfulness (ragas) | 할루시네이션 비율 |
|---|---|---|
| luna 후보 234 | 0.942 (0.969) | 0.094 |
| 규칙 후보 292 | 0.875 (0.831) | 0.223 |
| 전체 526 | 0.901 (0.890) | 0.165 |

## 라벨 품질
B1 · B2 Cohen's kappa 0.601 (자카드 0.44).

## 파일 (`runs/`)
| 파일 | 내용 |
|---|---|
| `pipe.jsonl` | 파이프라인 실행 결과(문항별 rule · other 목록, 답변) |
| `adj_solar.jsonl` | Solar 판정 보완 |
| `faith_solar.jsonl` | Solar Faithfulness |
| `ragas.json` | ragas 라이브러리 결과 |

다시 재기: `python eval/relabel_v2/metrics.py rank bm eval/relabel_v2/bm/runs/pipe.jsonl`, `python eval/relabel_v2/judge_solar.py report bm eval/relabel_v2/bm/runs/pipe.jsonl eval/relabel_v2/bm/runs/adj_solar.jsonl eval/relabel_v2/bm/runs/faith_solar.jsonl`
