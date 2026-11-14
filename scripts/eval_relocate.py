"""끊긴 인용의 대상 재탐색(regcheck.relocate) 평가: 정답 원장의 실제 정정 사례로 옮겨 간 곳을 맞히는지 본다.

  uv run --no-sync python scripts/eval_relocate.py
정답: eval/ledger/*.jsonl 의 인용 오류(대상불일치·대상없음·제목불일치) 중
  · 「고칠 수 있음」 + answer(정답 조, 예: 제36조) → 정답 조가 후보 1위·3위 안에 드는가 (같은 대상 규정 기준)
  · 「판단 불가」(대상 소멸 등) → 「찾지 못함」으로 나오는가 (1위 점수)
비교: scope=target(기존처럼 인용한 규정 안에서만) / corpus(전체 벡터 검색만) / union(둘 다, 제안)
정답 원장은 사람이 확인한 실제 사례라 재탐색 규칙과 독립적이다 (재탐색은 원장을 읽지 않음).
"""

import collections
import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.findings import locate  # noqa: E402
from regcheck.relocate import relocate  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    from sentence_transformers import CrossEncoder, SentenceTransformer

    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    edges = [json.loads(l) for l in (ROOT / "out" / "edges" / "edges_all.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    d = ROOT / "out" / "vector" / "BAAI__bge-m3"
    index = (np.load(d / "embeddings.npy"), [json.loads(l) for l in (d / "chunks.jsonl").read_text(encoding="utf-8").splitlines()])
    model = SentenceTransformer("BAAI/bge-m3", device="cuda")
    model.half()
    rr = CrossEncoder("BAAI/bge-reranker-v2-m3", device="cuda", model_kwargs={"dtype": "float16"})
    embed = lambda t: model.encode(t, normalize_embeddings=True)  # noqa: E731
    rerank = lambda q, ts: rr.predict([(q, t) for t in ts], batch_size=16, show_progress_bar=False).tolist()  # noqa: E731

    ledger = []
    for f in ("week1", "gabin_doc", "corrections"):
        for l in (ROOT / "eval" / "ledger" / f"{f}.jsonl").read_text(encoding="utf-8").splitlines():
            if l.strip():
                ledger.append({**json.loads(l), "_src": f})
    cases, unmatched = [], []
    for x in ledger:
        if x["type"] not in ("인용_대상불일치", "인용_대상없음", "인용_제목불일치"):
            continue
        cand = [e for e in edges if e["src_reg"] == x["reg_id"] and locate(e["src"])["article"] == x["article"]
                and (e["raw"] == x["quote"] or x["quote"] in e["raw"] or e["raw"] in x["quote"])]
        if x.get("para") not in (None, "", "0", 0):
            cand = [e for e in cand if str(locate(e["src"])["para"]) == str(x["para"])] or cand
        if not cand:
            unmatched.append((x["_src"], x["reg_id"], x["article"], x["quote"]))
            continue
        m = re.match(r"제\s*\d+\s*조(?:의\s*\d+)?", str(x.get("answer") or ""))
        cases.append({"x": x, "edge": cand[0], "answer": re.sub(r"\s", "", m.group(0)) if m else ""})
    print(f"인용 오류 정답 {sum(1 for x in ledger if x['type'] in ('인용_대상불일치', '인용_대상없음', '인용_제목불일치'))}건 · 엣지와 맞춘 것 {len(cases)} · 못 맞춤 {len(unmatched)}")
    fixable = [c for c in cases if c["answer"]]
    gone = [c for c in cases if not c["answer"] and c["x"].get("handling") == "판단 불가"]
    print(f"정답 조가 있는 사례 {len(fixable)} · 판단 불가(대상 소멸 등) {len(gone)}")

    for scope in ("target", "cascade"):
        h1 = h3 = 0
        other_reg = 0
        rows = []
        for c in fixable:
            res = relocate(c["edge"], regs, index, embed, rerank, scope=scope)
            labs = [(x["reg_id"], x["label"]) for x in res["candidates"]]
            want = (c["edge"]["target_reg"], c["answer"])
            h1 += bool(labs) and labs[0] == want
            h3 += want in labs
            other_reg += bool(res["candidates"]) and res["candidates"][0]["reg_id"] != c["edge"]["target_reg"]
            rows.append((c, res))
        top_gone = [relocate(c["edge"], regs, index, embed, rerank, scope=scope)["candidates"] for c in gone]
        g_scores = [t[0]["score"] if t else 0.0 for t in top_gone]
        f_scores = [r["candidates"][0]["score"] if r["candidates"] else 0.0 for _, r in rows]
        print(f"\n[{scope}] 1위 정답 {h1}/{len(fixable)} · 3위 안 {h3}/{len(fixable)} · 1위가 다른 규정 {other_reg}"
              f" | 1위 점수 중앙값: 정답 있는 사례 {np.median(f_scores):.2f} · 판단 불가 사례 {np.median(g_scores) if g_scores else 0:.2f}")
        # 「찾지 못함」 판단 문턱: 인용한 규정 안 1위 점수 기준 (정답 있는 사례는 찾음, 판단 불가 사례는 못 찾음이 맞음)
        def in_tgt(res, c):
            xs = [x for x in res["candidates"] if x["reg_id"] == c["edge"]["target_reg"]]
            return 1.0 if xs and xs[0]["tier"] == "문서 명시" else (xs[0]["score"] if xs else 0.0)
        ts = [in_tgt(r, c) for c, r in rows]
        gs = [in_tgt({"candidates": t}, c) for c, t in zip(gone, top_gone)]
        print("   문턱별 [정답 있는 사례 중 찾음 / 판단 불가 중 못 찾음]:",
              {t: (sum(x >= t for x in ts), sum(x < t for x in gs)) for t in (0.2, 0.3, 0.4, 0.5, 0.6)})
        if scope == "cascade":
            for c, r in rows:
                lab = r["candidates"][0] if r["candidates"] else {}
                ok = "O" if lab and (lab.get("reg_id"), lab.get("label")) == (c["edge"]["target_reg"], c["answer"]) else "X"
                print(f"   {ok} {regs[c['x']['reg_id']]['name'][:12]} {c['x']['article']} 「{c['x']['quote']}」 정답 {c['edge']['target_doc'] or ''} {c['answer']}"
                      f" → 1위 {lab.get('reg_name', '')[:12]} {lab.get('label', '')}({lab.get('title', '')}) {lab.get('score', 0):.2f} [{lab.get('tier', '')}]")
            for c, t in zip(gone, top_gone):
                lab = t[0] if t else {}
                print(f"   · 판단 불가 {regs[c['x']['reg_id']]['name'][:12]} {c['x']['article']} 「{c['x']['quote']}」 → 1위 {lab.get('reg_name', '')[:12]} {lab.get('label', '')} {lab.get('score', 0):.2f}")
    if unmatched:
        print("\n엣지와 못 맞춘 정답:", unmatched[:10])


if __name__ == "__main__":
    main()
