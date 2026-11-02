"""규정 지식 그래프 만들기 (규칙만, LLM 없음). 설계: docs/reports 72.

  uv run python scripts/build_kg.py            # out/kg/{nodes,edges}.jsonl, schema.json, stats.json
입력: out/parsed/*.json, out/edges/edges_all.jsonl
"""
import collections
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck import kg  # noqa: E402


def load():
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out" / "parsed" / "[0-9]*.json")))}
    edges = [json.loads(l) for l in (ROOT / "out" / "edges" / "edges_all.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    inf = ROOT / "out" / "rules" / "inferred.jsonl"
    inferred = [json.loads(l) for l in inf.read_text(encoding="utf-8").splitlines() if l.strip()] if inf.exists() else []
    return regs, edges, inferred


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    regs, edges, inferred = load()
    nodes, rows = kg.build(regs, edges, inferred)
    out = ROOT / "out" / "kg"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "nodes.jsonl").open("w", encoding="utf-8") as f:
        for k, v in nodes.items():
            f.write(json.dumps({"id": k, **v}, ensure_ascii=False) + "\n")
    with (out / "edges.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out / "schema.json").write_text(json.dumps(kg.ONTOLOGY, ensure_ascii=False, indent=1), encoding="utf-8")
    stats = {"노드": dict(collections.Counter(v["type"] for v in nodes.values())), "선": dict(collections.Counter(r["type"] for r in rows)),
             "CITES 종류": dict(collections.Counter(r.get("sub") for r in rows if r["type"] == "CITES"))}
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
