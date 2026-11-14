# 전후 상태 비교 지시 수정 실험

기존 결과는 변경하지 않는다. 목표는 도메인 그래프+RAG의 누락을 줄이는 것이며, 89/120을 얻었다고 미리 가정하지 않는다.

첫 프롬프트가 ‘새로 생긴 문제’로 시작하고, 해소 판정은 뒤쪽 규칙에서만 설명하는 불균형을 수정한다. 개정 후가 정상이더라도 개정 전 양쪽 원문을 확인하도록 같은 지시를 세 방식에 적용한다. 특정 문항·조문·숫자·정답 유형은 지시에 넣지 않는다.

- 데이터·정답·채점기·모델·reasoning effort·검색 후보와 순서·문맥은 고정한다.
- 후보 프롬프트는 `run_state_compare.py`의 v2 한 개다. 모든 방식·문항에 같은 프롬프트를 적용한다.
- Q029/Q038은 원인 확인용 개발 사례다. 확인 후 전체 120문항×3방식을 실행한다. 실패한 답만 바꿔 붙이거나 문항마다 이전/새 답 중 정답을 선택하지 않는다.
- 맞힌 문항의 퇴행, 새로 맞힌 문항, 추가 발견 검토 대기를 모두 기록한다. 89점에서 중단하지 않는다.
- 기존 평가 문항을 보고 수정했으므로 결과는 같은 개발 집합의 재평가다. 별도 미사용 문제에 대한 일반화 성능은 검증하지 않는다.

OpenAI Docs의 [평가 지침](https://developers.openai.com/api/docs/guides/evaluation-best-practices)을 참고했다.

```powershell
uv run --no-project --with numpy python eval/four_domain/run_state_compare.py --env YOUR_LOCAL_ENV --phase all
```

출력은 `runs/state-compare-v2/`이며 API 키와 캐시는 게시하지 않는다. 결과 Markdown에는 전체 성능만 표시한다.

JSON 스키마 검사에 실패해 실행이 멈추면 `retry_invalid_inference.py --env YOUR_LOCAL_ENV`로 형식 오류만 재요청한 뒤 원 명령을 재실행한다. 유효한 응답은 그대로 보존한다. 재요청은 동일한 입력·모델·설정으로 최대 두 번이며, 원 응답은 로컬 캐시와 `format-validation-retries.jsonl`에 보존한다. 정답 여부는 재요청 조건이 아니다.
