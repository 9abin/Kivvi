"""그래프 확장: 검색으로 찾은 조에서 Neo4j의 인용 선을 따라가 같이 읽어야 하는 조를 더한다.

선 종류마다 방향을 따로 정한다: "fwd"(내가 인용하는 조), "bwd"(나를 인용하는 조), "both", "off".
색인에 있는 조·별표만 더한다 (법령·규정 단위 선은 대상이 아니다).
"""
import re

from neo4j import GraphDatabase

# ONTO_DELEG: scripts/onto_deleg.py가 만든 파일의 선. CITES_LAW·LAW_CITES·LAW_PICK: scripts/graph_law_link.py가 만든 파일의 법령 선
# ONTO_SAME: scripts/onto_same_edges.py가 만든 파일의 선 (같은 개념의 규정문을 가진 다른 문서의 조끼리)
# DEF_OF: scripts/onto_def_edges.py가 만든 파일의 선 (규정의 각 조 -> 그 규정의 정의·용어 조)
KINDS = ("CITES", "CITES_ANNEX", "DELEGATES_TO", "SAME_REG_NEAR", "ONTO_DELEG", "CITES_LAW", "LAW_CITES", "LAW_PICK", "ONTO_SAME", "DEF_OF")
LAW = "LAW:"  # 법령 조 노드 키 접두사. 검색 색인에 없지만 확장 대상으로 받는다
NEAR_KIND = "SAME_REG_NEAR"  # 시작 조를 리랭크 상위로 좁혀서 쓴다 (expand의 near_seeds)
DEFAULT_URI = "bolt://127.0.0.1:17687"


def node_key(x):
    """적재 ID(24024:30, 24137:5의1)를 평가 노드 키(24024:제30조, 24137:제5조의1)로 바꾼다."""
    m = re.fullmatch(r"(\d+):(\d+)(?:의(\d+))?", x or "")
    return f"{m[1]}:제{m[2]}조" + (f"의{m[3]}" if m[3] else "") if m else x


def load_edges(uri=DEFAULT_URI):
    q = "MATCH (a)-[r]->(b) WHERE type(r) IN $kinds RETURN a.id AS s, b.id AS t, type(r) AS k"
    driver = GraphDatabase.driver(uri, auth=None)
    try:
        with driver.session() as s:
            return [(node_key(r["s"]), node_key(r["t"]), r["k"]) for r in s.run(q, kinds=list(KINDS))]
    finally:
        driver.close()


class Expander:
    def __init__(self, edges, indexed, policy):
        self.fwd, self.bwd = {}, {}
        self.policy = {k: policy.get(k, "off") for k in KINDS}
        for s, t, k in edges:
            if k in self.policy and s != t and (s in indexed or s.startswith(LAW)) and (t in indexed or t.startswith(LAW)):
                self.fwd.setdefault((k, s), set()).add(t)
                self.bwd.setdefault((k, t), set()).add(s)

    def expand_detail(self, seeds, near_seeds=None):
        """{더해진 조: 따라온 선 종류 집합}. SAME_REG_NEAR만 near_seeds(없으면 seeds)에서 출발한다."""
        out = {}
        for n in seeds:
            for k, d in self.policy.items():
                if k == NEAR_KIND and near_seeds is not None and n not in near_seeds:
                    continue
                if d in ("fwd", "both"):
                    for t in self.fwd.get((k, n), ()):
                        out.setdefault(t, set()).add(k)
                if d in ("bwd", "both"):
                    for t in self.bwd.get((k, n), ()):
                        out.setdefault(t, set()).add(k)
        for n in seeds:
            out.pop(n, None)
        return out

    def expand_support(self, seeds, near_seeds=None):
        """{더해진 조: 그 조를 가리킨 시작 조 집합}. expand_detail과 같은 규칙으로 걷되 선 종류 대신 어느 조에서 왔는지를 모은다.
        여러 조가 함께 가리키는 후보를 앞에 세우는 데 쓴다 (budget order = "links")."""
        out = {}
        for n in seeds:
            for k, d in self.policy.items():
                if k == NEAR_KIND and near_seeds is not None and n not in near_seeds:
                    continue
                if d in ("fwd", "both"):
                    for t in self.fwd.get((k, n), ()):
                        out.setdefault(t, set()).add(n)
                if d in ("bwd", "both"):
                    for t in self.bwd.get((k, n), ()):
                        out.setdefault(t, set()).add(n)
        for n in seeds:
            out.pop(n, None)
        return out

    def expand(self, seeds, near_seeds=None):
        """seeds의 이웃 조 집합 (seeds 자신은 뺀다)."""
        return set(self.expand_detail(seeds, near_seeds))
