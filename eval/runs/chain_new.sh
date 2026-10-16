#!/bin/bash
cd "C:/Users/Admin/Downloads/kvic-article-chunking"
PY="C:/Users/Admin/Downloads/한국벤처투자_내부규정_최신개정본/.venv/Scripts/python.exe"; NEW="C:/Users/Admin/Downloads/한국벤처투자_내부규정_최신개정본/eval/answer_eval_100_new.jsonl"
C:/Users/Admin/Downloads/한국벤처투자_내부규정_최신개정본/.venv/Scripts/python.exe scripts/exp_baseline.py --questions "$NEW" --repeat 2 > eval/runs/baseline_new2.txt 2>&1
for cfg in "new_bge8_luna_100:" "new_sel16k8_luna_100:--select-pool 16"; do
  TAG=${cfg%%:*}; EXTRA=${cfg#*:}
  QUERY_PLAN=0 C:/Users/Admin/Downloads/한국벤처투자_내부규정_최신개정본/.venv/Scripts/python.exe -m src.eval.run_answer_eval --tag $TAG --questions "$NEW" --k 8 --no-plan --serial $EXTRA > eval/runs/$TAG.log 2>&1
  C:/Users/Admin/Downloads/한국벤처투자_내부규정_최신개정본/.venv/Scripts/python.exe -m src.eval.judge_answers $TAG --questions "$NEW" > eval/runs/$TAG.judge.log 2>&1
  C:/Users/Admin/Downloads/한국벤처투자_내부규정_최신개정본/.venv/Scripts/python.exe -m src.eval.report_100 --tag $TAG --questions "$NEW" > eval/runs/$TAG.report.md 2>&1
done
echo done > eval/runs/chain_new.done
