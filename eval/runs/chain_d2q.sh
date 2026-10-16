#!/bin/bash
cd "C:/Users/Admin/Downloads/kvic-article-chunking"
while ! grep -q "^완료:" eval/runs/gen_doc2query.txt; do sleep 15; done
C:/Users/Admin/Downloads/한국벤처투자_내부규정_최신개정본/.venv/Scripts/python.exe scripts/exp_d2q.py --questions "C:/Users/Admin/Downloads/한국벤처투자_내부규정_최신개정본/eval/answer_eval_100_tiered.jsonl" --repeat 2 > eval/runs/d2q.txt 2>&1
echo done > eval/runs/chain_d2q.done
