"""개정 위치 찾기: 리랭커 상위 N개 후보를 로컬 LLM이 읽고 순서를 다시 매기면 좋아지는가 (시험용).

  uv run python scripts/eval_target_llm_rerank.py --pool out/eval/target_rag_q4b_800_pool.json --model qwen3-30b-a3b-2507-q4km-ctx12k --top 10
입력: eval_target_rag.py 가 저장한 후보 목록(pool: 후보마다 규정·조, 리랭커 점수, 임베딩 순위). 평가셋 질문은 --file.
LLM은 질문과 후보 N개(규정명 · 조 제목 · 본문 앞부분)를 보고 「고칠 가능성이 높은 순서로 번호 3개」를 낸다 → 그 3개를 앞에, 나머지는 리랭커 순서.
출력: 조 @1 · @3 · @5, 리랭커만 대비 짝지은 차이, 질문당 시간. 결과 out/eval/target_llm_rerank_{model}.json
"""

import argparse
import json
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PROMPT = ("너는 회사 내부 규정 담당자다. 직원의 문장을 보고, 이 문장과 관련해 확인하거나 고쳐야 할 규정 조문을 후보 중에서 고른다.\n"
          "후보는 [번호] 규정명 조(제목): 본문 앞부분 이다. 관련이 높은 순서로 후보 번호 3개만 쉼표로 적는다. 설명은 쓰지 않는다. 예: 4, 1, 7")


def ask(model: str, user: str) -> str:
    body = {"model": model, "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": user}],
            "stream": False, "think": False, "options": {"temperature": 0, "num_predict": 30}}
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())["message"]["content"]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="out/eval/target_rag_q4b_800_pool.json")
    ap.add_argument("--file", default="../MINI_CNS_rag/eval/answer_eval_100_tiered_v2.jsonl")
    ap.add_argument("--model", default="qwen3-30b-a3b-2507-q4km-ctx12k")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--chars", type=int, default=200)
    a = ap.parse_args()
    from regcheck.relocate import article_text
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    arts = {(r["reg_id"], re.sub(r"\s+", "", x["label"])): (r, x) for r in regs.values() for x in r["articles"]}
    qs = {json.loads(l)["id"]: json.loads(l)["q"] for l in Path(a.file).read_text(encoding="utf-8").splitlines() if l.strip()}
    R = json.loads((ROOT / a.pool).read_text(encoding="utf-8"))
    out, secs = [], []
    for n, r in enumerate(R, 1):
        pool = [tuple(p["c"]) for p in sorted(r["pool"], key=lambda p: -p["s"])]
        top = pool[:a.top]
        lines = []
        for i, c in enumerate(top, 1):
            reg, art = arts[c]
            lines.append(f"[{i}] {reg['name']} {article_text(art, a.chars)}")
        t = time.time()
        try:
            raw = ask(a.model, f"문장: {qs[r['id']]}\n\n후보:\n" + "\n".join(lines))
        except Exception as e:   # 연결 실패면 리랭커 순서 그대로
            raw = f"오류 {e}"
        secs.append(time.time() - t)
        picks = []
        for x in re.findall(r"\d+", raw):
            i = int(x)
            if 1 <= i <= len(top) and top[i - 1] not in picks:
                picks.append(top[i - 1])
        order = picks[:3] + [c for c in pool if c not in picks[:3]]
        out.append({"id": r["id"], "need": r["need"], "rerank": pool[:5], "llm": order[:5], "raw": raw[:60]})
        if n % 15 == 0:
            print(f"  {n}/{len(R)} ({sum(secs) / len(secs):.1f}초/질문)", flush=True)

    def hit(o, need, k):
        nd = {tuple(x) for x in need}
        return int(any(tuple(c) in nd for c in o[:k]))
    print(f"모델 {a.model} · 후보 상위 {a.top}개 · 질문 {len(out)}개 · 질문당 {sum(secs) / len(secs):.1f}초")
    for name, key in (("리랭커만", "rerank"), ("LLM 재정렬", "llm")):
        print(f"  {name}: " + " · ".join(f"조@{k} {sum(hit(o[key], o['need'], k) for o in out)}/{len(out)}" for k in (1, 3, 5)))
    random.seed(0)
    for k in (1, 3):
        d = [hit(o["llm"], o["need"], k) - hit(o["rerank"], o["need"], k) for o in out]
        bs = sorted(sum(random.choice(d) for _ in d) / len(d) for _ in range(4000))
        print(f"  조@{k} 차이 {sum(d) / len(d):+.3f} [{bs[100]:+.3f} ~ {bs[3900]:+.3f}] 좋아짐 {sum(x > 0 for x in d)} 나빠짐 {sum(x < 0 for x in d)}")
    p = ROOT / "out" / "eval" / f"target_llm_rerank_{re.sub(r'[^\w.-]', '_', a.model)}_top{a.top}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
