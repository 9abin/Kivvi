"""값 불일치 판정 실험 2단계: 판단 모델이 후보 쌍마다 「새로 어긋남 / 어긋남이 해소됨」 확률을 낸다 (판단 모델 환경).
  <kd_venv python> scripts/judge_decide_score.py <모델 폴더> out/fd_single_decide_pairs.json out/fd_single_decide_scores.json
"""
import json
import sys
import time
from pathlib import Path

import torch
from transformers import AutoModel

sys.stdout.reconfigure(encoding="utf-8")
Q_NEW = "이 개정으로, 개정 전에는 확인 대상 조문과 같은 사안의 값(기간 · 비율 · 금액 · 날짜 등)이 일치했는데 개정 후에는 서로 어긋나게 되는가?"
Q_RES = "이 개정으로, 개정 전에는 확인 대상 조문과 같은 사안의 값이 서로 어긋나 있었는데 개정 후에는 일치하게 되는가?"


def main():
    model = AutoModel.from_pretrained(sys.argv[1], trust_remote_code=True, dtype=torch.bfloat16).to("cuda").eval()
    pairs = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")); outf = Path(sys.argv[3])
    S = json.loads(outf.read_text(encoding="utf-8")) if outf.exists() else {}
    t0 = time.time()
    for n, x in enumerate(pairs):
        key = f"{x['q']}|{x['k']}|{x['P']}"
        if key in S:
            continue
        r = model.system_one(state=x["state"], questions={"new": {"type": "noul", "instructions": Q_NEW}, "res": {"type": "noul", "instructions": Q_RES}})
        S[key] = {"new": r["answers"]["new"]["noul"], "res": r["answers"]["res"]["noul"]}
        if n % 100 == 99:
            outf.write_text(json.dumps(S), encoding="utf-8"); print(n + 1, f"{time.time()-t0:.0f}초", flush=True)
    outf.write_text(json.dumps(S), encoding="utf-8"); print("완료", len(S))


main()
