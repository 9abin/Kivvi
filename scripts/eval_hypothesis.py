"""가설 카드 시험(읽어 보는 평가): 실제 개정 시나리오와 시험 개정으로 모델이 어떤 우려를 내는지 출력한다.

  uv run --extra db python scripts/eval_hypothesis.py [--model qwen3.5:9b]
기준은 재현율이 아니라 「담당자에게 쓸모 있는 우려를 보였나」다 (보여 주기 위한 기능). 사람이 출력을 읽고 판정한다.
시나리오 S01~S03(eval/scenarios)은 정답 항목이 있어, 가설이 그중 구조 경로가 못 찾은 것을 보탰는지도 센다.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.amend import build_changeset  # noqa: E402
from regcheck.change_check import run as run_check  # noqa: E402
from regcheck.hypothesis import hypotheses  # noqa: E402
from regcheck.nl import _post  # noqa: E402

EXTRA = [  # 시험 개정 (실제 개정 아님): 규정 이름, 개정문
    ("법인카드 사용지침", None),
    ("취업규칙", None),
]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:9b")
    a = ap.parse_args()
    from sentence_transformers import CrossEncoder, SentenceTransformer

    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    rels = [json.loads(l) for l in (ROOT / "out" / "relations" / "relations.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    edges = [json.loads(l) for l in (ROOT / "out" / "edges" / "edges_all.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    d = ROOT / "out" / "vector" / "BAAI__bge-m3"
    index = (np.load(d / "embeddings.npy"), [json.loads(l) for l in (d / "chunks.jsonl").read_text(encoding="utf-8").splitlines()])
    model = SentenceTransformer("BAAI/bge-m3", device="cuda")
    model.half()
    rr = CrossEncoder("BAAI/bge-reranker-v2-m3", device="cuda", model_kwargs={"dtype": "float16"})
    embed = lambda t: model.encode(t, normalize_embeddings=True)  # noqa: E731
    rerank = lambda q, ts: rr.predict([(q, t) for t in ts], batch_size=16, show_progress_bar=False).tolist()  # noqa: E731
    post = lambda msgs: _post(a.model, msgs, timeout=120)  # noqa: E731

    def hypo(reg, old_art, new_art, diff, skip):
        return hypotheses(post, regs, reg, old_art, new_art, diff, index, embed, rerank, skip)

    cases = []
    for p in sorted((ROOT / "eval" / "scenarios").glob("S*.json")):
        sc = json.loads(p.read_text(encoding="utf-8"))
        cases.append((sc["id"] + " " + sc["title"], sc["change"]["target"]["reg_id"], sc["change"]["amendment"], sc["expected"]))
    byname = {r["name"]: r["reg_id"] for r in regs.values()}
    tests = [("T1 법인카드 사용지침 제8조 사용기준: 주말 사용 허용", byname["법인카드 사용지침"], None),
             ("T2 승진지침 제11조 승진심사 결과 공개 범위 변경", byname["승진지침"], None)]
    total = got_extra = 0
    for title, rid, amendment, expected in cases:
        cs, _ = build_changeset(regs[rid], amendment, "H", title)
        t = time.time()
        base = run_check(cs, regs, rels, edges)
        with_h = run_check(cs, regs, rels, edges, hypo=hypo)
        hyp = [f for f in with_h if f["path"].startswith("가설")]
        print(f"\n=== {title} ({time.time() - t:.0f}초) | 구조 경로 카드 {len(base)}장 + 가설 {len(hyp)}장")
        for f in hyp:
            print(f"  · {f['reg_name']} {f['article']}({f['quote']}) 「{f['target']}」 ← {f['reason'][:170]}")
        exp_all = [(c, e) for c, es in expected.items() if isinstance(es, list) for e in es if e.get("article")]
        for c, e in exp_all:
            same = lambda f: f["reg_id"] == (e.get("reg_id") or f["reg_id"]) and f.get("article") == e["article"]  # noqa: E731
            total += 1
            by_struct = any(same(f) for f in base)
            by_hyp = any(same(f) for f in hyp)
            if not by_struct and by_hyp:
                got_extra += 1
                print(f"  ★ 구조 경로가 못 찾은 기대 항목을 가설이 보탬: {c} {e.get('reg_name', '')} {e['article']}")
    print(f"\n시나리오 기대 항목 {total}개 중 구조 경로가 못 찾은 것을 가설이 보탠 수: {got_extra}")


if __name__ == "__main__":
    main()
