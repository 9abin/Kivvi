#!/bin/bash
# 사용: scripts/run_answer_chain.sh <tiered 평가셋> <태그:k:select_pool> [<태그:k:select_pool> ...]
# 각 설정을 답변 생성 -> 채점 -> 보고(RAGAS 포함) 순서로 차례로 돌린다. PG_* 환경 변수가 설정돼 있어야 한다.
Q="$1"; shift
PY="${PY:-python}"
for spec in "$@"; do
  IFS=: read -r tag k pool <<< "$spec"
  echo "=== $tag (k=$k, select_pool=$pool)"
  "$PY" -m src.eval.run_answer_eval --tag "$tag" --questions "$Q" --k "$k" --no-plan --select-pool "$pool"
  "$PY" -m src.eval.judge_answers "$tag" --questions "$Q"
  "$PY" -m src.eval.report_100 --tag "$tag" --questions "$Q"
done
