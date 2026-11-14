"""최종 화면용 판단 모델 점수: 후보마다 「이 개정이 이 조문에 얼마나 닿는가」 0~3 기댓값(lv)과 수정 종류별 예/아니오 확률(p) (판단 모델 환경).
  <kd_venv python> scripts/decide_score_pairs.py <모델 폴더> <pairs.json> <scores.json>
pairs.json = {"pairs": [{"key", "kind", "state"}]} (scripts/eval_final_view.py build가 만든다). 이미 점수가 있는 key는 건너뛴다.
"""
import json
import sys
import time
from pathlib import Path

import torch
from transformers import AutoModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(encoding="utf-8")
from regcheck.final_view import INSTR, LEVEL_Q, LEVELS  # noqa: E402


def main():
    model = AutoModel.from_pretrained(sys.argv[1], trust_remote_code=True, dtype=torch.bfloat16).to("cuda").eval()
    pairs = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))["pairs"]; outf = Path(sys.argv[3])
    S = json.loads(outf.read_text(encoding="utf-8")) if outf.exists() else {}
    todo = [x for x in pairs if x["key"] not in S]
    print("점수 필요", len(todo), "/", len(pairs), flush=True)
    t0 = time.time()
    for n, x in enumerate(todo):
        r = model.system_one(state=x["state"], questions={
            "affected": {"type": "noul", "instructions": INSTR.get(x["kind"], "개정 내용이 이 조문에 닿는가?")},
            "level": {"type": "score", "instructions": LEVEL_Q, "criteria": LEVELS}})
        S[x["key"]] = {"p": r["answers"]["affected"]["noul"], "lv": r["answers"]["level"]["score"]}
        if n % 100 == 99:
            outf.write_text(json.dumps(S, ensure_ascii=False), encoding="utf-8"); print(n + 1, f"{time.time() - t0:.0f}초", flush=True)
    outf.write_text(json.dumps(S, ensure_ascii=False), encoding="utf-8"); print("완료", len(S), f"{time.time() - t0:.0f}초")


main()
