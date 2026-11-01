"""법령 층 2단계: 규정 조 -> 법령 조 선을 검색 확장용 파일로 만든다.

법령 조 노드 키는 "LAW:<법령 DB 문서 ID>:<조 키>" (예: LAW:eflaw:001872:76의2). 검색 색인에는 법령 조가 없어서 규정 조 키와 섞이지 않게 따로 쓴다.
  CITES_LAW  규정 조가 법령 조를 조 번호까지 적어 인용 (graph_struct, Neo4j)
  LAW_CITES  법령 조가 같은 법의 다른 조를 가리킴 (graph_law.py, Neo4j)
  LAW_PICK   규정 조가 법령 이름만 적었을 때 (Neo4j CITES_LAW_DOC, 온톨로지 위임처가 법령인 것) 그 법령에서 고른 조:
             규정 조의 개념(규정문이 있으면 그 개념, 없으면 조 제목)이 본문에 나오는 법령 조 중 임베딩이 가까운 3개,
             이름이 안 나오면 법령 전체에서 가까운 2개. 정의 조가 따로 있으면 같이 걸리도록 3개까지 본다.
결과: <out>  [{"src", "dst", "kind", "근거"}]. 규칙과 임베딩만 쓴다. 평가셋을 보고 넣은 선은 없다.

  set -a; . ./.env; set +a
  python scripts/graph_law_link.py eval/onto_leave2 eval/law_links.json
"""
import collections
import json
import os
import re
import sys

import numpy as np
from neo4j import GraphDatabase

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
sys.stdout.reconfigure(encoding="utf-8")
from graph_law import LAW_DB, law_texts  # noqa: E402
from graph_struct import NEO4J_URI, resolve_law  # noqa: E402
from onto_build import norm, read_items  # noqa: E402
from onto_expand_eval import node  # noqa: E402
from src.citations import law_lookup  # noqa: E402
from src.combo.onto_search import embed  # noqa: E402


DEF_LAW = re.compile(r"[「｢]([^」｣]+)[」｣][^「｢」｣]{0,40}?\(이하\s*[\"“”‘’']?법[\"“”‘’']?\s*(?:이라|라)")
GENERIC_LAW = re.compile(r"(관련|관계|유관|다른|그 밖의|국내외)")
# 일반명이라 별칭으로 걸면 다른 인용까지 잡히는 것은 그 조 하나에만 잇는다 (외부 참조 점검 2절)
# 복무규정 제30조 "미숙아의 범위는 고용노동부령으로" = 근로기준법 시행규칙 제12조의2(미숙아의 범위 등)
LOCAL = {("23970:30", "고용노동부령"): ("근로기준법 시행규칙", "12의2")}


def law_key(doc, key):
    return f"LAW:{doc}:{key}"


def main(onto, out_path):
    arts = read_items()
    ex = json.load(open(os.path.join(onto, "extract.json"), encoding="utf-8"))
    titles, _ = law_lookup.load(LAW_DB)
    texts, _ = law_texts()
    rows = []
    drv = GraphDatabase.driver(NEO4J_URI, auth=None)
    with drv.session() as s:
        for a, d, k in s.run("MATCH (a:Article)-[:CITES_LAW]->(n:LawProvision) WHERE n.law_doc IS NOT NULL AND n.law_article IS NOT NULL "
                             "RETURN a.id, n.law_doc, n.law_article").values():
            rows.append({"src": node(a), "dst": law_key(d, k), "kind": "CITES_LAW"})
        for d1, k1, d2, k2 in s.run("MATCH (a:LawProvision)-[:LAW_CITES]->(b:LawProvision) RETURN a.law_doc, a.law_article, b.law_doc, b.law_article").values():
            rows.append({"src": law_key(d1, k1), "dst": law_key(d2, k2), "kind": "LAW_CITES"})
        doc_refs = s.run("MATCH (a:Article)-[:CITES_LAW_DOC]->(l:Law) RETURN a.id, l.id").values()
    drv.close()
    # 법령 이름만 적은 인용: (규정 조, 법령 문서, 개념 후보들, 근거)
    picks = []
    concept_of = collections.defaultdict(list)
    for aid, v in ex.items():
        for st in v["규정문"]:
            concept_of[aid].append(st["개념"])
    for aid, did in doc_refs:
        if aid in arts:
            picks.append((aid, did, list(dict.fromkeys(concept_of.get(aid) or [arts[aid]["title"] or ""])), "법령 이름 인용"))
    # 위임처가 그냥 "법"·"시행령"이면 그 규정이 정의한 법을 쓴다: 「공익신고자 보호법」(이하 "법"이라 한다)
    defined = {}
    for aid, a in arts.items():
        for it in a["items"]:
            for m in DEF_LAW.finditer(it["text"]):
                defined.setdefault(aid.split(":")[0], m[1])
    for aid, v in ex.items():
        for st in v["규정문"]:
            for t in st.get("위임처") or []:
                name = t["이름"].strip()
                if (aid, name) in LOCAL:
                    law, key = LOCAL[aid, name]
                    did, _, _ = resolve_law(law, titles)
                    rows.append({"src": node(aid), "dst": law_key(did, key), "kind": "LAW_PICK", "근거": "조 하나에만 거는 별칭"})
                    continue
                if GENERIC_LAW.match(name):   # "관련세법"이 비슷한 이름으로 관세법에 붙는 것을 막는다
                    continue
                if name in ("법", "시행령", "법 시행령") and aid.split(":")[0] in defined:
                    name = defined[aid.split(":")[0]] + ("" if name == "법" else " 시행령")
                did, status, _ = resolve_law(name, titles)
                if did:
                    picks.append((aid, did, [st["개념"]], "온톨로지 위임처"))
    by_law = collections.defaultdict(list)
    for (d, k) in texts:
        by_law[d].append(k)
    need = sorted({d for _, d, _, _ in picks})
    cache = os.path.join(os.path.dirname(out_path) or ".", "law_prov_emb.npz")
    old = dict(np.load(cache, allow_pickle=True)) if os.path.exists(cache) else {}
    vec = {}
    for d in need:
        ks = by_law[d]
        key = d.replace(":", "_")
        if key in old and len(old[key]) == len(ks):
            vec[d] = old[key]
        else:
            vec[d] = embed([texts[(d, k)][:800] for k in ks])
            old[key] = vec[d]
    np.savez(cache, **old)
    Q = embed([f"{arts[a]['title'] or ''} {' '.join(cs)}: " + " ".join(i["text"] for i in arts[a]["items"])[:400] for a, _, cs, _ in picks]) if picks else []
    for (aid, did, cs, why), q in zip(picks, Q):
        ks = by_law[did]
        names = [norm(c) for c in cs if len(norm(c)) >= 2]
        named = [i for i, k in enumerate(ks) if any(n in norm(texts[(did, k)]) for n in names)]
        pool, n_ = (named, 3) if named else (range(len(ks)), 2)
        best = sorted(((float(q @ vec[did][i]), ks[i]) for i in pool), reverse=True)[:n_]
        rows += [{"src": node(aid), "dst": law_key(did, k), "kind": "LAW_PICK", "근거": why, "sim": round(sim, 3)} for sim, k in best]
    seen, out = set(), []
    for r in rows:
        k = (r["src"], r["dst"], r["kind"])
        if k not in seen and r["src"] != r["dst"]:
            seen.add(k)
            out.append(r)
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"법령 선 {len(out)}개 {dict(collections.Counter(r['kind'] for r in out))}, 이름만 인용 {len(picks)}건 "
          f"{dict(collections.Counter(p[3] for p in picks))}, 임베딩한 법령 {len(need)}개")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
