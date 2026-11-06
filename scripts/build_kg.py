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


def load_law_db():
    """법률 DB(data/law_rag.sqlite3)의 법령 제목과 조 제목. 법령 이름 정규화와 법령 조 노드에 쓴다. 없으면 빈 값 (DB는 읽기만 한다)."""
    import re
    import sqlite3
    p = ROOT / "data" / "law_rag.sqlite3"
    if not p.exists():
        return [], {}
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    titles = {r[0]: r[1] for r in con.execute("select id, api_title from documents") if r[1]}
    arts = {}
    for did, path in con.execute("select document_id, path from chunks"):
        m = re.match(r"(\d+)(?:의(\d+))?\s+(.+)", path or "")
        if m and did in titles:
            arts[(titles[did], f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else ""))] = m.group(3)
    con.close()
    return sorted(set(titles.values())), arts


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    regs, edges, inferred = load()
    law_titles, law_art_titles = load_law_db()
    nodes, rows = kg.build(regs, edges, inferred, law_titles=law_titles, law_article_titles=law_art_titles)
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
