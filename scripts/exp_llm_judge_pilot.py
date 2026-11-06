"""LLM 판단 시험(소규모): 후보를 ① 단독으로 / ② 후보가 인용하는 조문 본문을 붙여서 읽고 영향 여부를 답하게 한다 (로컬 모델, 규정 원문을 밖으로 보내지 않는다).
  uv run python scripts/exp_llm_judge_pilot.py [--model qwen3.5:9b] [--top 20]
out/rank_ctx_cache.pkl(eval_rank_ctx.py가 만든 후보 · 질의 캐시)를 읽는다. 결과: out/pilot_llm.json
"""
import argparse
import collections
import glob
import json
import pickle
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

INSTR = {
    "값 변경": "바뀌는 값(옛 값 → 새 값)을 이 조문이 같은 사안에 대해 따로 정하거나, 인용하거나, 전제하고 있는가?",
    "정의 변경": "정의가 바뀌는 용어를 이 조문이 쓰고, 바뀌는 부분에 실제로 기대고 있는가?",
    "삭제": "삭제되는 조(항)를 이 조문이 인용하거나 그 내용에 기대고 있는가?",
    "표현 수정": "바뀌는 이름이나 말을 이 조문이 그대로 쓰고 있는가?",
}


def chat(model, prompt, timeout=300):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False, "format": "json", "think": False, "options": {"temperature": 0, "num_ctx": 6000}}).encode()
    req = urllib.request.Request("http://localhost:11434/api/chat", body, {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))["message"]["content"]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--model", default="qwen3.5:9b"); ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()
    items, _, _ = pickle.load(open(ROOT / "out/rank_ctx_cache.pkl", "rb"))
    want = pickle.load(open(ROOT / "out/pilot_want.pkl", "rb"))
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    ut = {x["id"]: x["norm"] for r in regs.values() for x in r["articles"] if not x.get("deleted")}
    name = {x["id"]: f"{r['name']} {x['label']}" for r in regs.values() for x in r["articles"]}
    edges = [json.loads(l) for l in (ROOT / "out/kg/edges.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    cites = collections.defaultdict(list)
    for e in edges:
        if e["type"] == "CITES" and e["dst"] in ut and e["src"] != e["dst"]:
            cites[e["src"]].append(e["dst"])
    out = {}
    cachef = ROOT / "out/pilot_llm.json"
    if cachef.exists():
        out = json.loads(cachef.read_text(encoding="utf-8"))
    for k in want:
        tag, iid, gq, cqs, kind, cands, truth = items[k]
        for ci, (cid, tier, text) in enumerate(cands[: a.top]):
            for mode in ("단독", "보강"):
                key = f"{iid}|{cid}|{mode}"
                if key in out:
                    continue
                extra = ""
                if mode == "보강":
                    cited = list(dict.fromkeys(cites.get(cid, [])))[:2]
                    if cited:
                        extra = "\n[이 조문이 인용하는 다른 조문]\n" + "\n".join(f"- {name.get(c, c)}: {ut[c][:260]}" for c in cited)
                    else:
                        extra = "\n[이 조문이 인용하는 다른 조문] 없음"
                prompt = (f"회사 내부 규정의 한 조문이 개정된다. 확인 대상 조문도 함께 살펴보거나 고쳐야 하는지 판단한다.\n\n[개정 종류] {kind}\n[개정 내용]\n{gq[:700]}\n\n[판단 기준] {INSTR.get(kind, '개정 내용이 이 조문에 닿는가?')}\n\n"
                          f"[확인 대상 조문] {name.get(cid, cid)}\n{text[:600]}{extra}\n\n"
                          "출력은 JSON 한 줄만: {\"영향\": \"예\" 또는 \"불확실\" 또는 \"아니오\", \"근거\": \"확인 대상 조문에서 글자 그대로 복사한 구절(50자 이내)\"}\n"
                          "닿는지 알 수 없으면 「불확실」, 닿지 않는다고 확신할 때만 「아니오」. 낱말이 겹친다는 이유만으로 「예」라고 하지 않는다.")
                t0 = time.time()
                try:
                    raw = chat(a.model, prompt)
                    try:
                        label = json.loads(raw).get("영향", "?")
                    except Exception:
                        label = "?"
                except Exception as e:
                    raw, label = f"오류 {e}", "?"
                out[key] = {"label": label, "raw": raw[:300], "sec": round(time.time() - t0, 1), "truth": cid in truth, "rank": ci + 1, "iid": iid, "kind": kind}
                cachef.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
                print(iid, ci + 1, mode, label, "정답" if cid in truth else "", f"{time.time()-t0:.0f}s", flush=True)
    print("완료", len(out))


if __name__ == "__main__":
    main()
