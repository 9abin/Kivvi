"""챗봇 답변 모델 비교: 가빈 답변 파이프라인(src/answer)을 그대로 두고 LLM 호출만 로컬 Ollama 모델로 바꿔 돌린다.

  uv run python scripts/llm_compare.py --model qwen3:30b-a3b --per-type 3
  uv run python scripts/llm_compare.py --model gpt-4o-mini --per-type 3     # OpenAI 키가 있는 PC에서 (기준선)
출력: out/llm_compare/<모델>.jsonl (이어 실행 가능)
문항: eval/answer_eval.jsonl에서 유형마다 앞에서부터 N개 (모델끼리 같은 문항)
로컬 모델: Ollama 기본 API로 호출 (num_ctx 16384 — 근거 8,000자가 잘리지 않게, think 끔, temperature 0). 프롬프트는 가빈 코드 그대로.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"

OLLAMA = "http://127.0.0.1:11434/api/chat"
THINK = re.compile(r"<think>.*?</think>", re.S)


def pick(per_type: int) -> list[dict]:
    gold = [json.loads(l) for l in (ROOT / "eval" / "answer_eval.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    seen: dict[str, int] = defaultdict(int)
    out = []
    for g in gold:
        if seen[g["유형"]] < per_type:
            seen[g["유형"]] += 1
            out.append(g)
    return out


def ollama_ask(model: str, stats: dict, num_gpu: int | None = None):
    # num_gpu: GPU에 올릴 층 수. 8GB GPU에서는 검색 모델(BGE-m3·재순위)과 함께 올라가 메모리가 넘칠 수 있다 (261013 30B에서 CUDA OOM)
    opts = {"temperature": 0, "num_ctx": 16384, **({"num_gpu": num_gpu} if num_gpu is not None else {})}

    def _ask(self, system, user):
        body = {"model": model, "stream": False, "think": False, "options": opts,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        req = urllib.request.Request(OLLAMA, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=900).read())
        stats["calls"] += 1
        stats["prompt_tokens"] += r.get("prompt_eval_count", 0)
        stats["out_tokens"] += r.get("eval_count", 0)
        stats["gen_sec"] += r.get("eval_duration", 0) / 1e9
        return THINK.sub("", r["message"]["content"]).strip()
    return _ask


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--per-type", type=int, default=3)
    ap.add_argument("--ids", default="", help="쉼표로 문항 지정 (예: Q001,Q002). 주면 --per-type 무시")
    ap.add_argument("--num-gpu", type=int, default=None, help="Ollama가 GPU에 올릴 층 수 (GPU 메모리가 모자랄 때)")
    a = ap.parse_args()

    from src.answer import generate
    stats = defaultdict(float)
    if not a.model.startswith("gpt-"):
        generate.Generator.__init__ = lambda self, model=a.model: setattr(self, "model_name", model)
        generate.Generator._ask = ollama_ask(a.model, stats, a.num_gpu)
    from src.answer.pipeline import Assistant

    dst = ROOT / "out" / "llm_compare" / (a.model.replace(":", "_").replace("/", "_") + ".jsonl")
    dst.parent.mkdir(parents=True, exist_ok=True)
    done = {json.loads(l)["id"] for l in dst.read_text(encoding="utf-8").splitlines()} if dst.exists() else set()
    assistant = Assistant()
    ids = [x for x in a.ids.split(",") if x]
    for g in (pick(99) if ids else pick(a.per_type)):
        if ids and g["id"] not in ids:
            continue
        if g["id"] in done:
            continue
        before = dict(stats)
        t = time.time()
        try:
            r = assistant.ask(g["q"])
            rec = {"id": g["id"], "유형": g["유형"], "mode": r["mode"], "answer": r["answer"], "withheld": r.get("withheld"),
                   "warnings": r.get("warnings", []), "cited": r.get("cited", []),
                   "entries": [f"{e['문서명']} {e['article']}" for e in r["entries"]]}
        except Exception as e:  # 모델 응답 형식이 깨져 파이프라인이 멈춘 경우도 결과로 남긴다
            rec = {"id": g["id"], "유형": g["유형"], "error": repr(e)[:300]}
        rec["sec"] = round(time.time() - t, 1)
        rec["llm"] = {k: round(stats[k] - before.get(k, 0), 1) for k in ("calls", "prompt_tokens", "out_tokens", "gen_sec")}
        with dst.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(g["id"], g["유형"], rec.get("mode", "ERROR"), rec["sec"], "초", flush=True)
    assistant.close()
    print("→", dst.relative_to(ROOT))


if __name__ == "__main__":
    main()
