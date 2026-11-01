"""규정 관계 그래프 적재 (설계: reports/ontology-design-20261021.md §2, §4).

공유 PostgreSQL은 읽기 전용 세션으로만 읽고, 이 PC의 Neo4j(127.0.0.1:17687)에 적재한다.
다시 돌리면 Neo4j를 비우고 처음부터 다시 만든다.

  set -a; . ./.env; set +a
  .venv/Scripts/python.exe scripts/graph_load.py
"""
import collections
import os
import sys

import psycopg
from neo4j import GraphDatabase

sys.stdout.reconfigure(encoding="utf-8")

# .env의 NEO4J_URI는 읽지 않는다. 적재 전에 전체를 지우므로 이 PC의 Neo4j(127.0.0.1)로만 연결한다.
NEO4J_URI = "bolt://127.0.0.1:17687"
REL_TYPE = {"인용": "CITES", "별표·별지 인용": "CITES_ANNEX", "문서 인용·위임": "REFERS_REG", "법령 인용": "CITES_LAW"}
TARGET_LABEL = {"regulation": "Regulation", "law": "LawProvision", "chapter": "Chapter"}


def pg(dbname):
    return psycopg.connect(host=os.environ.get("PG_HOST", "127.0.0.1"), port=os.environ.get("PG_PORT", "5432"), dbname=dbname,
                           user=os.environ["PG_USER"], password=os.environ["PG_PASSWORD"], connect_timeout=15,
                           options="-c default_transaction_read_only=on")


def unit_label(nid):
    """조·별표·부칙은 아이디 모양으로 가른다 (예: 24024:30, 24125:별표1, 24184:부칙2:2)."""
    if ":별표" in nid or ":별지" in nid:
        return "Annex"
    if ":부칙" in nid:
        return "Supplement"
    return "Article"


def read_source():
    with pg("regulations_para") as c:
        docs = c.execute("select doc_id, title, tier, revision from documents").fetchall()
    with pg("regulations") as c:
        edges = c.execute("select id, src, src_article, src_kind, target, target_type, kind, evidence, context, confidence, "
                          "verification, check_status, src_version, target_version from amend.edge").fetchall()
    return docs, edges


def build(docs, edges):
    nodes = collections.defaultdict(dict)  # (label, id) -> props
    for doc_id, title, tier, revision in docs:
        nodes[("Regulation", doc_id)] = {"title": title, "tier": tier, "revision": revision, "known": True}
    rels = collections.defaultdict(list)   # (src_label, dst_label, type) -> rows
    skipped = 0
    for eid, src_path, src, src_kind, target, ttype, kind, evidence, context, conf, verif, check, sv, tv in edges:
        if not src or not target or kind not in REL_TYPE:
            skipped += 1
            continue
        sl = unit_label(src)
        dl = {"article": unit_label(target), "appendix": "Annex"}.get(ttype) or TARGET_LABEL.get(ttype, "Article")
        nodes[(sl, src)] = nodes.get((sl, src), {})
        if (dl, target) not in nodes:
            nodes[(dl, target)] = {"known": dl != "Regulation"} if dl == "Regulation" else {}
        if dl == "LawProvision":
            nodes[(dl, target)]["law_name"] = target.rsplit(" 제", 1)[0]
        rels[(sl, dl, REL_TYPE[kind])].append({
            "src": src, "dst": target, "props": {"edge_id": eid, "src_path": src_path, "evidence": evidence, "context": context, "confidence": conf, "verification": verif,
                                                 "check_status": check, "src_kind": src_kind, "src_version": sv, "target_version": tv}})
    part_of = collections.defaultdict(list)
    for (label, nid) in list(nodes):
        if label in ("Article", "Annex", "Supplement") and ":" in nid:
            reg = nid.split(":", 1)[0]
            if ("Regulation", reg) not in nodes:
                nodes[("Regulation", reg)] = {"known": False}
            part_of[label].append({"src": nid, "dst": reg, "props": {}})
    return nodes, rels, part_of, skipped


def load(nodes, rels, part_of):
    driver = GraphDatabase.driver(NEO4J_URI, auth=None)
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n").consume()
        for label in sorted({l for l, _ in nodes}):
            s.run(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE").consume()
        by_label = collections.defaultdict(list)
        for (label, nid), props in nodes.items():
            by_label[label].append({"id": nid, "props": props})
        for label, rows in by_label.items():
            s.run(f"UNWIND $rows AS r MERGE (n:{label} {{id: r.id}}) SET n += r.props", rows=rows).consume()
        for (sl, dl, rtype), rows in rels.items():
            s.run(f"UNWIND $rows AS r MATCH (a:{sl} {{id: r.src}}) MATCH (b:{dl} {{id: r.dst}}) "
                  f"CREATE (a)-[x:{rtype}]->(b) SET x += r.props", rows=rows).consume()
        for label, rows in part_of.items():
            s.run(f"UNWIND $rows AS r MATCH (a:{label} {{id: r.src}}) MATCH (b:Regulation {{id: r.dst}}) CREATE (a)-[:PART_OF]->(b)",
                  rows=rows).consume()
        print("노드:", s.run("MATCH (n) RETURN labels(n)[0] AS l, count(*) AS c ORDER BY c DESC").values())
        print("관계:", s.run("MATCH ()-[x]->() RETURN type(x) AS t, count(*) AS c ORDER BY c DESC").values())
    driver.close()


if __name__ == "__main__":
    docs, edges = read_source()
    nodes, rels, part_of, skipped = build(docs, edges)
    print(f"읽음: 규정 {len(docs)}건, 관계 {len(edges)}개 (건너뜀 {skipped})")
    load(nodes, rels, part_of)
