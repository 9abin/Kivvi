#!/usr/bin/env bash
# 임베딩 튜닝 평가 자동 실행: 학습이 끝나면(model_ft_v1_s05가 생기면) 개발용·시험용을 차례로 평가한다.
#   기준선(BAAI/bge-m3) / LoRA 효과 1.0 / LoRA 효과 0.5, 문서 모양 v0(현행), BM25는 현행 DB 값
#   결과: out/eval/embed_ft_dev.json·embed_ft_holdout.json, 로그 data/embedding/eval_ft_{dev,holdout}.txt
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
until [ -f data/embedding/model_ft_v1_s05/modules.json ]; do sleep 20; done
sleep 10
M="BAAI/bge-m3 $PWD/data/embedding/model_ft_v1 $PWD/data/embedding/model_ft_v1_s05"
uv run --no-sync --extra db python scripts/exp_embed_baseline.py --models $M --forms v0 \
    --gold data/embedding/answer_eval_100.jsonl --rewrite data/embedding/rewrite-cache.json > data/embedding/eval_ft_dev.txt 2>&1
cp out/eval/embed_baseline.json out/eval/embed_ft_dev.json
cp data/embedding/cands_BAAI_bge_m3_v0.json data/embedding/cands_base_dev.json 2>/dev/null
uv run --no-sync --extra db python scripts/exp_embed_baseline.py --models $M --forms v0 \
    --gold data/embedding/answer_eval_holdout.jsonl --rewrite data/embedding/rewrite-cache-holdout.json > data/embedding/eval_ft_holdout.txt 2>&1
cp out/eval/embed_baseline.json out/eval/embed_ft_holdout.json
echo DONE > data/embedding/eval_ft_done.txt
