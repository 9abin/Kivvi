# 온톨로지 검색 핵심 파일

기존 저장소에서 검색 핵심만 추린 독립 실행 폴더입니다. 원본 파일은 변경하지 않았습니다.
Python 3.12 이상에서 이 폴더를 통째로 복사해 실행할 수 있습니다.
기본 그래프 검색에는 외부 패키지, 모델 다운로드, API 키가 필요하지 않습니다.

## 실행

이 폴더에서 실행합니다.

```powershell
python run.py --anchor 24024:30 --limit 5
python run.py --anchor 24024:30 --method scoped --kind "값 변경" --amendment '제30조 중 "1/2"을 "2/3"로 한다.' --limit 5
python -m unittest discover -s tests -t . -v
```

`--seed`로 항·호 ID를 지정할 수 있습니다. ID는 `data/corpus.jsonl`의 `items`에서 확인합니다.
`--query`는 선택적인 변경 주제 입력입니다. 두 검색 방식 모두 그래프 검색이며 자연어 챗봇이 아닙니다.
`--lexical`은 BM25 점수로 그래프의 동점을 정렬하는 선택 옵션입니다. 사용하려면 `python -m pip install "numpy>=1.26"`로 설치합니다.
벡터 검색, RRF 병합, 모델의 오류 판정은 이 실행기에 포함하지 않았습니다.

## 구성

| 경로 | 용도 |
|---|---|
| `run.py` | 공통 실행 진입점, 입력 확인, 결과 JSON 출력 |
| `ontology_search/domain_key_graph.py` | 도메인 키 검색 |
| `ontology_search/validated_graph.py` | SQLite 로더·개념 계층 및 기존 기준 검색 |
| `ontology_search/propagating_graph.py` | 전파 이벤트·예산 제한 및 기본 연결 탐색 |
| `ontology_search/scoped_propagating_graph.py` | 항·호 범위를 유지하는 전파 검색 |
| `ontology_search/bm25.py` | 선택적인 어휘 점수 계산 |
| `data/ontology.db` | 읽기 전용으로 사용하는 온톨로지 DB 스냅샷 |
| `data/corpus.jsonl` | 현재 39문항 평가의 코퍼스 스냅샷 3,171개 |
| `data/references.jsonl` | 기존 chunks에서 검색에 필요한 내부 인용 필드만 추출 |
| `data/schema.sql` | DB 구조 |
| `tests/` | 기존 핵심 회귀 검사 14개 |
| `manifest.json` | 원본 경로, SHA256, 복사·가공 여부 |

실험 실행기, 평가 정답, 이전 결과, 다운로드 사본, 웹 배포물, 원본 HWP, 개인 PC의 모델 경로와 비밀 설정은 제외했습니다.
`pyproject.toml`은 코드 패키지의 메타데이터입니다. 데이터까지 쓰려면 이 폴더 전체를 보관하고 `run.py`로 실행합니다.

## 기존 코드에서 바꾼 부분

모듈 import를 패키지 상대 import로 바꾸고 DB 기본 경로를 `data/ontology.db`로 통일했습니다.
기존 도메인 키 검사 네 개는 표준 `unittest`에서 실행되도록 감쌌습니다.
내부 인용 데이터는 전파 검색이 실제로 읽는 필드만 남겼습니다.
검색 알고리즘, DB 내용 및 코퍼스 내용은 바꾸지 않았습니다.

## 알려진 제한

- 명시한 항에 연결이 없을 때 도메인 키 검색이 같은 조의 다른 항으로 대체하는 기존 동작이 남아 있습니다.
- 전파 검색에서 종착 경로가 먼저 도착하면 나중의 확장 가능 경로가 중복으로 처리되는 문제가 남아 있습니다.
- 속성·주제어 게이트는 규칙 기반 휴리스틱입니다. 후보는 실제 충돌 확정 결과가 아닙니다.
- 코퍼스는 39문항 평가의 추출본, 인용은 기존 `out/chunks.jsonl`에서 가져왔습니다. 이 정리는 두 원천의 의미적 일치나 전체 인용의 완전성을 새로 보증하지 않습니다.
- 14개 회귀 검사는 기존 동작과 패키징 확인용이며, 위 알려진 문제를 해결했다는 뜻이 아닙니다.

## 검증 문서와 선택적 LLM 리랭킹 프롬프트

- [검증 및 전체 흐름](검증_및_전체_플로우.md)
- [기존 개정 영향 리랭킹 프롬프트](prompts/README.md)

프롬프트는 참조용으로 포함했으며, `run.py`에 LLM 호출을 연결하지 않았습니다. 재검증은 `python -B verify.py`로 실행합니다. 현재 item ID 중복으로 종료 코드 1이 반환되는 사유는 검증 문서에 기록했습니다.
