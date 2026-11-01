"""법령 층 채우기: 규정이 인용한 법령의 조문 전부를 법령 DB에서 가져와 그래프에 넣는다.

기존 LawProvision은 "규정이 조 번호까지 적어 인용한 조문"(367개)뿐이라, 법령 이름만 적은 인용이나
인용된 조가 다시 맡긴 조(예: 정의 조가 같은 법 다른 조를 가리킴)에는 닿을 수 없었다.
  Law {id: 법령 DB 문서 ID, title}
  LawProvision {id: "법령명 제N조", law_doc, law_article, text}  (기존 id 형식 그대로, MERGE)
  (LawProvision)-[:PART_OF]->(Law)
  (Article)-[:CITES_LAW_DOC]->(Law)          규정이 법령 이름만 적어 인용한 것 (amend.edge 법령 인용 중 조 번호 없는 것)
  (LawProvision)-[:LAW_CITES]->(LawProvision)  같은 법 안에서 "제N조"로 가리킨 조 (다른 법령 이름이 바로 앞에 오면 뺀다)
규칙만 쓴다. 평가셋을 보고 넣은 법령·조문은 없다.

  set -a; . ./.env; set +a
  python scripts/graph_law.py
"""
import collections
import os
import re
import sqlite3
import sys

from neo4j import GraphDatabase

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
sys.stdout.reconfigure(encoding="utf-8")
from graph_struct import NEO4J_URI, pg, resolve_law  # noqa: E402
from src.citations import law_lookup  # noqa: E402

LAW_DB = "data/manual/law_rag.261013/law_rag.261013.sqlite3"
KEY = re.compile(r"^(\d+(?:의\d+)?)")
# "제3조", "제76조의2". 바로 앞에 「다른 법령」이나 "같은 법 시행령" 등이 오면 다른 법을 가리키므로 뺀다
REF = re.compile(r"(「[^」]{1,60}」\s*|(?:같은\s*법|동법)\s*(?:시행령|시행규칙)\s*|(?:시행령|시행규칙)\s*)?제(\d+)조(?:의(\d+))?")


def label(key):
    m = re.fullmatch(r"(\d+)(?:의(\d+))?", key)
    return f"제{m[1]}조" + (f"의{m[2]}" if m[2] else "")


def law_texts():
    """(문서 ID, 조 키) -> 조 전체 본문 (항·호 조각을 순서대로 잇는다), 문서 ID -> 제목."""
    conn = sqlite3.connect(LAW_DB)
    title = {d: (api or req) for d, req, api in conn.execute("select id, requested_title, api_title from documents")}
    parts = collections.defaultdict(list)
    for doc, path, content in conn.execute("select document_id, path, content from chunks order by rowid"):
        m = KEY.match(path or "")
        if m:
            parts[(doc, m.group(1))].append(content or "")
    conn.close()
    return {k: "\n".join(v) for k, v in parts.items()}, title


def main():
    titles, _ = law_lookup.load(LAW_DB)
    texts, title = law_texts()
    with pg("regulations") as c:
        rows = c.execute("select src_article, target from amend.edge where kind='법령 인용'").fetchall()
    cited_docs, doc_refs, unmatched = set(), [], collections.Counter()
    for src, target in rows:
        name = re.sub(r"\s*제\d+조.*$", "", target).strip()
        did, status, _ = resolve_law(name, titles)
        if not did:
            unmatched[name] += 1
            continue
        cited_docs.add(did)
        if not re.search(r"제\d+조", target):
            doc_refs.append({"src": src, "law": did})
    provs = [{"id": f"{title[d]} {label(k)}", "doc": d, "key": k, "text": t[:6000]} for (d, k), t in texts.items() if d in cited_docs]
    known = {(p["doc"], p["key"]) for p in provs}
    inner = set()   # ((문서, 조), (문서, 조))
    for p in provs:
        for m in REF.finditer(p["text"]):
            if m.group(1):
                continue
            k = (p["doc"], m.group(2) + (f"의{m.group(3)}" if m.group(3) else ""))
            if k in known and k != (p["doc"], p["key"]):
                inner.add(((p["doc"], p["key"]), k))
    print(f"법령 인용 {len(rows)}개 -> 법령 DB에 맞춘 법령 {len(cited_docs)}개 (못 맞춤 {sum(unmatched.values())}건: {unmatched.most_common(8)})")
    print(f"그 법령의 조문 {len(provs)}개, 법령 이름만 적은 인용 {len(doc_refs)}개, 같은 법 안 조 인용 {len(inner)}개")

    driver = GraphDatabase.driver(NEO4J_URI, auth=None)
    with driver.session() as s:
        before = s.run("MATCH (n:LawProvision) RETURN count(n)").single()[0]
        for t in ("CITES_LAW_DOC", "LAW_CITES"):
            s.run(f"MATCH ()-[x:{t}]->() DELETE x").consume()
        s.run("UNWIND $r AS r MERGE (l:Law {id: r.id}) SET l.title = r.title",
              r=[{"id": d, "title": title[d]} for d in sorted(cited_docs)]).consume()
        # 기존 노드(규정이 조 번호로 인용한 것)는 (law_doc, law_article)로 찾아 그대로 쓰고, 없는 조문만 새로 만든다
        have = {(d, k): i for i, d, k in s.run("MATCH (n:LawProvision) WHERE n.law_doc IS NOT NULL RETURN n.id, n.law_doc, n.law_article").values()}
        for p in provs:
            p["id"] = have.get((p["doc"], p["key"]), p["id"])
        id_of = {(p["doc"], p["key"]): p["id"] for p in provs}
        inner = [{"src": id_of[a], "dst": id_of[b]} for a, b in inner]
        new = [p for p in provs if (p["doc"], p["key"]) not in have]
        s.run("UNWIND $r AS r MERGE (n:LawProvision {id: r.id}) SET n.law_doc = r.doc, n.law_article = r.key, n.text = r.text, n.law_status = 'law_db'",
              r=new).consume()
        s.run("UNWIND $r AS r MATCH (n:LawProvision {id: r.id}) MATCH (l:Law {id: r.doc}) MERGE (n)-[:PART_OF]->(l)", r=provs).consume()
        s.run("UNWIND $r AS r MATCH (a:Article {id: r.src}) MATCH (l:Law {id: r.law}) MERGE (a)-[:CITES_LAW_DOC]->(l)", r=doc_refs).consume()
        s.run("UNWIND $r AS r MATCH (a:LawProvision {id: r.src}) MATCH (b:LawProvision {id: r.dst}) MERGE (a)-[:LAW_CITES]->(b)", r=inner).consume()
        after = s.run("MATCH (n:LawProvision) RETURN count(n)").single()[0]
        print(f"LawProvision {before} -> {after}")
        print("관계:", s.run("MATCH ()-[x]->() WHERE type(x) IN ['CITES_LAW', 'CITES_LAW_DOC', 'LAW_CITES'] OR (type(x) = 'PART_OF' AND endNode(x):Law) "
                             "RETURN type(x), count(*)").values())
    driver.close()


if __name__ == "__main__":
    main()
