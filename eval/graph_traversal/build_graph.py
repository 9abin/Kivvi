import sys, glob, json, re, sqlite3, dataclasses, pickle, collections
from pathlib import Path
NS = Path(sys.argv[1])
sys.path.insert(0, str(NS / "kgcode" / "src"))
sys.stdout.reconfigure(encoding="utf-8")
from regcheck.citations import Registry, extract_edges
from regcheck import kg
regs = {}
for p in glob.glob("C:/Users/Admin/Downloads/kvic_pr11/out/parsed/[0-9]*.json"):
    r = json.loads(Path(p).read_text(encoding="utf-8")); regs[r["reg_id"]] = r
R = Registry(list(regs.values()))
edges = [dataclasses.asdict(e) for r in regs.values() for e in extract_edges(r, R)]
# 법령 DB 제목과 조 제목
con = sqlite3.connect("file:data/manual/law_rag.261013/law_rag.261013.sqlite3?mode=ro", uri=True)
titles = {r[0]: r[1] for r in con.execute("select id, api_title from documents") if r[1]}
arts = {}
for did, path in con.execute("select document_id, path from chunks"):
    m = re.match(r"(\d+)(?:의(\d+))?\s+(.+)", path or "")
    if m and did in titles:
        arts[(titles[did], f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else ""))] = m.group(3)
nodes, rows = kg.build(regs, edges, [], law_titles=sorted(set(titles.values())), law_article_titles=arts)
pickle.dump({"nodes": nodes, "rows": rows}, open(NS / "kg.pkl", "wb"))
print(len(edges), len(nodes), len(rows))
print(collections.Counter(v["type"] for v in nodes.values()))
print(collections.Counter(r["type"] for r in rows))
