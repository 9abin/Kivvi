"""기준선 점검 결과에 「옮겨 간 곳 찾기」(regcheck.relocate) 후보를 붙인다 — 도입 시 정비용.

  uv run --no-sync python scripts/build_relocate.py      # GPU (임베딩·reranker). 이어서 scripts/build_findings.py 를 다시 돌리면 합쳐짐
대상: 내규 조를 가리키는 인용의 결과 — 인용_대상없음·인용_대상불일치·인용_제목불일치·개정미반영_후보 (법령·별표·부칙 제외)
출력: out/findings/relocate.json  {review_key: {"found", "candidates": [...]}}
판정이 아니라 후보다. 인용한 규정 안을 먼저 보고, 못 찾으면 다른 규정을 뒤 순위로 붙이며, 끝내 못 찾으면 「근거 소멸 가능성」.
"""

import collections
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.relocate import relocate  # noqa: E402
from regcheck.review import key_baseline  # noqa: E402

TYPES = ("인용_대상없음", "인용_대상불일치", "인용_제목불일치", "개정미반영_후보")
OUT = ROOT / "out" / "findings" / "relocate.json"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    from sentence_transformers import CrossEncoder, SentenceTransformer

    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    edges = {(e["src"], e["raw"]): e for e in (json.loads(l) for l in (ROOT / "out" / "edges" / "edges_all.jsonl").read_text(encoding="utf-8").splitlines() if l.strip())}
    found = [json.loads(l) for l in (ROOT / "out" / "findings" / "findings.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    todo = []
    for f in found:
        if f["type"] not in TYPES or f.get("unit") == "부칙":
            continue
        e = edges.get((f.get("src_id"), f.get("quote")))
        if e and e.get("target_type") == "article" and e.get("target_reg") in regs:
            todo.append((f, e))
    d = ROOT / "out" / "vector" / "BAAI__bge-m3"
    index = (np.load(d / "embeddings.npy"), [json.loads(l) for l in (d / "chunks.jsonl").read_text(encoding="utf-8").splitlines()])
    model = SentenceTransformer("BAAI/bge-m3", device="cuda")
    model.half()
    rr = CrossEncoder("BAAI/bge-reranker-v2-m3", device="cuda", model_kwargs={"dtype": "float16"})
    embed = lambda t: model.encode(t, normalize_embeddings=True)  # noqa: E731
    rerank = lambda q, ts: rr.predict([(q, t) for t in ts], batch_size=16, show_progress_bar=False).tolist()  # noqa: E731
    out, stat = {}, collections.Counter()
    for f, e in todo:
        res = relocate(e, regs, index, embed, rerank)
        top = res["candidates"][0] if res["candidates"] else None
        kind = ("못 찾음" if not res["found"] else "문서 명시" if top["tier"] == "문서 명시"
                else "인용한 그 조가 1위" if top["article_id"] == e.get("target_id") else "다른 조 제안")
        stat[(f["type"], kind + (" · 확실" if res["confident"] else " · 후보만"))] += 1
        out[key_baseline(f)] = {"found": res["found"], "kind": kind, "confident": res["confident"], "gap": res["gap"], "target": regs[e["target_reg"]]["name"], "candidates": res["candidates"]}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"대상 결과 {len(todo)}건 → {OUT}")
    for (t, k), n in sorted(stat.items()):
        print(f"  {t}\t{k}\t{n}")


if __name__ == "__main__":
    main()
