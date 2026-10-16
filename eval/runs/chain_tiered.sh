#!/bin/bash
cd "C:/Users/Admin/Downloads/kvic-article-chunking"
PY="C:/Users/Admin/Downloads/한국벤처투자_내부규정_최신개정본/.venv/Scripts/python.exe"; TIER="C:/Users/Admin/Downloads/한국벤처투자_내부규정_최신개정본/eval/answer_eval_100_tiered.jsonl"
while [ ! -f eval/runs/chain_new.done ]; do sleep 20; done
for t in bge8 sel16k8; do
  mkdir -p eval/answer_runs/tiered_${t}_luna_100
  cp eval/answer_runs/new_${t}_luna_100/runs.jsonl eval/answer_runs/tiered_${t}_luna_100/runs.jsonl
  C:/Users/Admin/Downloads/한국벤처투자_내부규정_최신개정본/.venv/Scripts/python.exe -m src.eval.judge_answers tiered_${t}_luna_100 --questions "$TIER" > eval/runs/tiered_${t}.judge.log 2>&1
  C:/Users/Admin/Downloads/한국벤처투자_내부규정_최신개정본/.venv/Scripts/python.exe -m src.eval.report_100 --tag tiered_${t}_luna_100 --questions "$TIER" > eval/runs/tiered_${t}.report.md 2>&1
done
C:/Users/Admin/Downloads/한국벤처투자_내부규정_최신개정본/.venv/Scripts/python.exe scripts/exp_baseline.py --questions "$TIER" --repeat 2 --out exp_baseline_tiered.json > eval/runs/baseline_tiered.txt 2>&1
echo done > eval/runs/chain_tiered.done
