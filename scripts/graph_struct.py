"""구조형 선 보강 (S4 법령 본문, S5 위임, S6 근접, S7 조문 개정이력). 설계: reports/graph-build-plan-20261028.md §5.1.

graph_load.py로 기본 그래프를 올린 뒤 실행한다. Neo4j를 비우지 않고 이 스크립트가 만드는 선만 지우고 다시 만든다.
전체 규정에 같은 규칙을 적용하는 일괄 절차이며, 평가 케이스를 보고 넣는 선은 없다.

  set -a; . ./.env; set +a
  python scripts/graph_struct.py <emb_*.ids.json 경로>
"""
import collections
import difflib
import json
import os
import re
import sqlite3
import sys

import numpy as np
import psycopg
from neo4j import GraphDatabase

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

NEO4J_URI = "bolt://127.0.0.1:17687"
LAW_DB = "data/manual/law_rag.261012.sqlite3"
DATE = re.compile(r"\d{4}\.\d{2}\.\d{2}")
ART_ID = re.compile(r"^(\d+):(\d+)(?:의(\d+))?$")
EMB_KEY = re.compile(r"^(\d+)_제(\d+)조(?:의(\d+))?(?:_|$)")
DELEG_TOPK, DELEG_MIN_SIM = 2, 0.0   # 임계는 분포를 본 뒤 아래 main에서 정한다
LAW_FUZZY_MIN = 0.85


def pg(dbname):
    return psycopg.connect(host=os.environ.get("PG_HOST", "127.0.0.1"), port=os.environ.get("PG_PORT", "5432"), dbname=dbname,
                           user=os.environ["PG_USER"], password=os.environ["PG_PASSWORD"], connect_timeout=15,
                           options="-c default_transaction_read_only=on")


def order_key(nid):
    m = ART_ID.match(nid)
    return (int(m.group(2)), int(m.group(3) or 0)) if m else None


def read_articles():
    """chunk_detail에서 조 단위로 모은다: 제목, 개정 표시 날짜."""
    arts = {}
    with pg("regulations") as c:
        rows = c.execute("select article_id, reg_id, label, title, amend_marks from amend.chunk_detail").fetchall()
    for aid, reg, label, title, marks in rows:
        if not ART_ID.match(aid or ""):
            continue
        a = arts.setdefault(aid, {"reg": reg, "label": label, "title": title, "dates": set(), "marks": []})
        for m in marks or []:
            a["marks"].append(m)
            a["dates"].update(DATE.findall(m))
    return arts


def amend_props(a):
    dates = sorted(a["dates"])
    kinds = sorted({k for m in a["marks"] for k in ("신설", "개정", "삭제") if k in m})
    return {"label": a["label"], "title": a["title"], "amend_dates": dates, "amend_n": len(dates),
            "amend_first": dates[0] if dates else None, "amend_last": dates[-1] if dates else None, "amend_kinds": kinds}


def near_edges(arts):
    by_reg = collections.defaultdict(list)
    for aid, a in arts.items():
        by_reg[aid.split(":")[0]].append(aid)
    rows = []
    for reg, ids in by_reg.items():
        ids.sort(key=order_key)
        rows += [{"src": x, "dst": y} for x, y in zip(ids, ids[1:])]
    return rows


# 법령 DB에 있는 것을 확인한 약칭만 넣는다 (src/citations/law_lookup.py LAW_ALIASES에 더해서 쓴다).
EXTRA_ALIASES = {"청탁금지법": "부정청탁 및 금품등 수수의 금지에 관한 법률", "여성폭력기본법": "여성폭력방지기본법",
                 # 전체 추출 위임처에서 못 맞춘 약칭 (청탁방지법 시행령 15번 등)
                 "청탁방지법": "부정청탁 및 금품등 수수의 금지에 관한 법률", "정보공개법": "공공기관의 정보공개에 관한 법률",
                 "민원처리법": "민원 처리에 관한 법률", "개인정보보호에 관한 법률": "개인정보 보호법",
                 # 외부 참조 점검 2절: "개보법 대통령령" = 시행령, 행정업무 규정은 이름이 두 번 바뀌었다
                 "개인정보보호법 대통령령": "개인정보 보호법 시행령", "행정업무의 효율적 운영에 관한 규정": "행정업무의 운영 및 혁신에 관한 규정"}
SUFFIXES = (("시행규칙", "시행규칙"), ("시행령", "시행령"))


def law_index():
    from src.citations import law_lookup
    titles, chunks = law_lookup.load(LAW_DB)
    return titles, chunks


def norm_name(s):
    return re.sub(r"[\s‧ㆍ·・･「」｢｣]", "", s)   # ・･｢｣: 일부 조문이 쓰는 다른 가운뎃점·낫표


def resolve_law(name, titles):
    """법령명을 법령 DB 문서에 맞춘다: 정확/공백 무시 > 약칭표(+시행령·시행규칙) > 비슷한 이름(오기 후보)."""
    from src.citations.law_lookup import LAW_ALIASES
    n = norm_name(name)
    if n in titles:
        return titles[n], "matched", 1.0
    suf = next((x for x, _ in SUFFIXES if n.endswith(x)), "")
    base = n[: -len(suf)] if suf else n
    aliases = {norm_name(k): norm_name(v) for k, v in {**LAW_ALIASES, **EXTRA_ALIASES}.items()}
    if aliases.get(base, base) + suf in titles and (base in aliases or suf):
        return titles[aliases.get(base, base) + suf], "alias", 1.0
    best = difflib.get_close_matches(n, list(titles), n=1, cutoff=LAW_FUZZY_MIN)
    if best:
        return titles[best[0]], "fuzzy", round(difflib.SequenceMatcher(None, n, best[0]).ratio(), 3)
    return None, "unmatched", 0.0


def context_name(name, sibling_names):
    """"(약칭 미해석) 법/규칙/법 시행령" 같은 이름을 같은 조가 인용한 다른 법령 하나로 읽는다: 후보가 정확히 하나일 때만."""
    m = re.match(r"^\(약칭 미해석\)\s*(법|동법|규칙|법 시행령|동법 시행령|시행령|시행규칙)$", name)
    if not m:
        return None
    kind = m.group(1)
    bases = {re.sub(r"\s*(시행령|시행규칙)$", "", n) for n in sibling_names if not n.startswith("(약칭")}
    if len(bases) != 1:
        return None
    base = bases.pop()
    if "시행령" in kind:
        return base + " 시행령"
    return base + " 시행규칙" if "규칙" in kind else base


def law_props(lp_id, titles, chunks, siblings):
    m = re.match(r"^(.*?) 제(\d+)조(?:의(\d+))?", lp_id)
    name = lp_id if not m else m.group(1)
    inferred = context_name(name, siblings)
    did, status, sim = resolve_law(inferred or name, titles)
    if inferred and did:
        status = "inferred"
    props = {"law_status": status, "law_sim": sim}
    if did:
        props["law_doc"] = did
        if m:
            key = m.group(2) + (f"의{m.group(3)}" if m.group(3) else "")
            txt = chunks.get((did, key))
            if txt:
                props["text"] = txt[:6000]
                props["law_article"] = key
            else:
                props["law_status"] = status + "_no_article"
    return props


def article_vectors(ids_path):
    ids = json.load(open(ids_path, encoding="utf-8"))
    mat = np.load(os.path.join(os.path.dirname(ids_path), os.path.basename(ids_path).replace(".ids.json", ".npy")))
    groups = collections.defaultdict(list)
    for i, k in enumerate(ids):
        m = EMB_KEY.match(k)
        if m:
            groups[f"{m.group(1)}:{m.group(2)}" + (f"의{m.group(3)}" if m.group(3) else "")].append(i)
    vec = {}
    for a, ix in groups.items():
        v = mat[ix].mean(axis=0)
        vec[a] = v / (np.linalg.norm(v) or 1)
    return vec


def delegation_edges(vec, topk, min_sim):
    """문서 인용·위임 선(조 -> 규정)을 조 -> 조 위임 선으로 풀어쓴다: 도착 규정 안에서 출발 조와 의미가 가장 가까운 조를 고른다."""
    with pg("regulations") as c:
        rows = c.execute("select src_article, target, evidence, confidence from amend.edge where kind='문서 인용·위임'").fetchall()
    by_reg = collections.defaultdict(list)
    for a in vec:
        by_reg[a.split(":")[0]].append(a)
    out, sims = [], []
    for src, target_reg, evidence, conf in rows:
        if src not in vec or target_reg not in by_reg or src.split(":")[0] == target_reg:
            continue
        cand = sorted(((float(vec[src] @ vec[t]), t) for t in by_reg[target_reg]), reverse=True)[:topk]
        for rank, (sim, t) in enumerate(cand, 1):
            sims.append(sim)
            if sim >= min_sim:
                out.append({"src": src, "dst": t, "props": {"sim": round(sim, 4), "rank": rank, "evidence": evidence, "confidence": conf,
                                                            "status": "후보", "source": "S5 인용·위임+임베딩"}})
    return out, sims


def main(ids_path):
    arts = read_articles()
    print("조(개정 표시 포함):", len(arts), "개정 날짜가 있는 조:", sum(1 for a in arts.values() if a["dates"]))
    titles, chunks = law_index()
    vec = article_vectors(ids_path)
    sims_probe = delegation_edges(vec, DELEG_TOPK, -1)[1]
    min_sim = float(np.percentile(sims_probe, 50)) if sims_probe else 0.0
    deleg, _ = delegation_edges(vec, DELEG_TOPK, min_sim)
    print(f"위임 선 후보 {len(sims_probe)}개 -> 유사도 중앙값 {min_sim:.3f} 이상 {len(deleg)}개")
    near = near_edges(arts)

    driver = GraphDatabase.driver(NEO4J_URI, auth=None)
    with driver.session() as s:
        for t in ("SAME_REG_NEAR", "DELEGATES_TO"):
            s.run(f"MATCH ()-[x:{t}]->() DELETE x").consume()
        rows = [{"id": aid, "props": amend_props(a), "reg": a["reg"]} for aid, a in arts.items()]
        s.run("UNWIND $rows AS r MERGE (n:Article {id: r.id}) SET n += r.props", rows=rows).consume()
        s.run("UNWIND $rows AS r MATCH (a:Article {id: r.id}) MATCH (b:Regulation {id: r.reg}) MERGE (a)-[:PART_OF]->(b)", rows=rows).consume()
        s.run("UNWIND $rows AS r MATCH (a:Article {id: r.src}) MATCH (b:Article {id: r.dst}) CREATE (a)-[:SAME_REG_NEAR]->(b)", rows=near).consume()
        s.run("UNWIND $rows AS r MATCH (a:Article {id: r.src}) MATCH (b:Article {id: r.dst}) CREATE (a)-[x:DELEGATES_TO]->(b) SET x += r.props",
              rows=deleg).consume()
        lp = [r[0] for r in s.run("MATCH (n:LawProvision) RETURN n.id").values()]
        sib = collections.defaultdict(set)   # 인용하는 조 -> 그 조가 인용한 법령명들
        for src, nid in s.run("MATCH (a)-[:CITES_LAW]->(l:LawProvision) RETURN a.id, l.id").values():
            sib[src].add(re.sub(r" 제\d+조.*$", "", nid))
        owners = collections.defaultdict(set)
        for src, nid in s.run("MATCH (a)-[:CITES_LAW]->(l:LawProvision) RETURN a.id, l.id").values():
            owners[nid].add(src)
        lrows = [{"id": i, "props": law_props(i, titles, chunks, set().union(*(sib[o] for o in owners[i])) if owners[i] else set())} for i in lp]
        s.run("UNWIND $rows AS r MATCH (n:LawProvision {id: r.id}) SET n += r.props", rows=lrows).consume()
        print("법령 조문 노드:", len(lp), dict(collections.Counter(r["props"]["law_status"] for r in lrows)))
        print("노드:", s.run("MATCH (n) RETURN labels(n)[0] AS l, count(*) AS c ORDER BY c DESC").values())
        print("관계:", s.run("MATCH ()-[x]->() RETURN type(x) AS t, count(*) AS c ORDER BY c DESC").values())
    driver.close()


if __name__ == "__main__":
    main(sys.argv[1])
