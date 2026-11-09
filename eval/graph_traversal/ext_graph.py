"""PR #12의 도메인 온톨로지 DB(domain_ontology.reviewed.db)를 우리 조항 그래프에 합친다. API 호출 없음.
DB: 조항(article) - 개념(concept) 연결(정의/근거/언급), 개념 상하위, (개념, 속성) 단위 근거 연결.
추가하는 관계(가중치는 사전에 정해 두고 평가셋으로 고르지 않았다):
  DOM_ATTR     같은 (개념, 속성)에 '근거'로 걸린 두 조 사이   0.8   (PR #12의 v_candidates 후보와 같은 규칙)
  DOM_정의     조 - 개념(정의)                                 0.5
  DOM_근거     조 - 개념(근거)                                 0.5
  DOM_언급     조 - 개념(언급)                                 0.2
  DOM_BROADER  하위 개념 - 상위 개념                           0.3
사용: traverse 를 import 한 뒤 tlib 를 import 하기 전에 apply(T, db_path) 를 호출한다."""
import sqlite3, collections


def apply(T, db_path):
    c = sqlite3.connect(db_path)
    nodes, rows, adj = T.nodes, T.rows, T.adj
    wts = {"DOM_ATTR": 0.8, "DOM_정의": 0.5, "DOM_근거": 0.5, "DOM_언급": 0.2, "DOM_BROADER": 0.3}
    T.W.update(wts)
    added = collections.Counter()

    def add(u, v, rel):
        rows.append({"src": u, "dst": v, "type": rel})
        adj[u].append((v, rel, "out")); adj[v].append((u, rel, "in")); added[rel] += 1

    for (name,) in c.execute("select name from concept"):
        nodes[f"개념:{name}"] = {"type": "Concept", "title": name}
    for name, parent in c.execute("select name, parent from concept where parent is not null"):
        add(f"개념:{name}", f"개념:{parent}", "DOM_BROADER")
    seen = set()
    for art, con, rel in c.execute("select i.article_id, e.concept, e.rel from edge_item_concept e join item i on i.id = e.item_id"):
        if art in nodes and (art, con, rel) not in seen:
            seen.add((art, con, rel)); add(art, f"개념:{con}", f"DOM_{rel}")
    groups = collections.defaultdict(set)
    for con, at, art in c.execute("select e.concept, x.attr, i.article_id from edge_item_concept e join edge_attr x on x.edge_id = e.id join item i on i.id = e.item_id where e.rel = '근거'"):
        if art in nodes: groups[(con, at)].add(art)
    pairs = set()
    for g in groups.values():
        g = sorted(g)
        for i in range(len(g)):
            for j in range(i + 1, len(g)): pairs.add((g[i], g[j]))
    for u, v in pairs: add(u, v, "DOM_ATTR")
    for u in list(adj): T.deg[u] = len(adj[u])
    # PR #12 방식의 도메인 규칙 후보: 같은 (개념, 속성)을 공유하는 조, 공유한 그룹이 많고 그룹이 작은 순
    share = collections.defaultdict(lambda: collections.defaultdict(float))
    for g in groups.values():
        for a in g:
            for b in g:
                if a != b: share[a][b] += 1.0 / len(g)
    T.DOM_SHARE = share
    print("도메인 그래프 합침:", dict(added), "개념 노드", sum(1 for n in nodes if str(n).startswith("개념:")), flush=True)
    return added
