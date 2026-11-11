"""4차 실험: 팀 자신의 가상 개정 5·6·7차(AI 라벨 '예')에서 같은 탐색 방법들을 비교. PR12보다 개정 종류가 다양하고 정답이 가까이 있지 않을 수 있다."""
import sys, json, re, pickle, collections, glob
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tlib as L
T = L.T
sys.stdout.reconfigure(encoding="utf-8")
NS = T.NS
nodes = T.nodes
regs = {}
for p in glob.glob("C:/Users/Admin/Downloads/kvic_pr11/out/parsed/[0-9]*.json"):
    r = json.loads(Path(p).read_text(encoding="utf-8")); regs[r["name"]] = r["reg_id"]


def art_id(reg, jo):
    m = re.match(r"제(\d+)조(?:의\s*(\d+))?", jo)
    return f"{regs[reg]}:{m.group(1)}" + (f"의{m.group(2)}" if m.group(2) else "")


cases2 = []
skipped = []
for r in (5, 6, 7):
    for l in open(NS / "teamlab" / f"changes_r{r}.jsonl", encoding="utf-8"):
        c = json.loads(l)
        try:
            L_ = json.load(open(NS / "teamlab" / f"labels_{c['id']}_r{r}.json", encoding="utf-8"))
        except Exception:
            skipped.append(c["id"]); continue
        yes = [x["unit"] for x in L_ if x["label"] == "예"]
        s = c["수정"]
        try:
            a = art_id(s["규정"], s["조"])
        except Exception:
            skipped.append(c["id"] + "(조 해석 실패)"); continue
        if a not in nodes:
            skipped.append(c["id"] + "(노드 없음 " + a + ")"); continue
        wanted = [u for u in yes if u != a and u in nodes]
        if not wanted: continue
        cases2.append({"id": c["id"], "round": r, "kind": s["종류"], "anchors": [(a, s["종류"], None)], "wanted": wanted, "type": s["종류"]})
print("사용 문항", len(cases2), "정답 합", sum(len(c["wanted"]) for c in cases2), "제외", skipped)
print("종류:", dict(collections.Counter(c["kind"] for c in cases2)))
print("정답이 노드에 없어 뺀 수:", sum(1 for r in (5, 6, 7) for l in open(NS / "teamlab" / f"changes_r{r}.jsonl", encoding="utf-8") for _ in [0]) and "")

# 방법: 다른 모듈의 함수들은 앵커 하나짜리 문항이라 단순 적용
def run2(method, **kw):
    out = {}
    for c in cases2:
        a, kind, _ = c["anchors"][0]
        out[c["id"]] = method(a, kind, {a}, **kw)[:100]
    return out


def sc(ranked, ks=(5, 10, 20, 30), cs=None):
    cs = cs or cases2
    tot = sum(len(c["wanted"]) for c in cs)
    cells = []
    for k in ks:
        h = sum(len(set(c["wanted"]) & set(ranked[c["id"]][:k])) for c in cs)
        cells.append(f"{h} ({h/tot:.0%})")
    return cells, tot


ppr_team = L.rank_ppr(L.transition(L.W), 0.3, cases_=cases2)


def rule_then(rank_b, m):
    out = {}
    for c in cases2:
        a, kind, _ = c["anchors"][0]
        first = T.m_rule1(a, kind, {a})[:m]
        out[c["id"]] = first + [x for x in rank_b[c["id"]] if x not in set(first)]
    return out


def union_rules(a, kind, A):
    # 개정 종류를 몰라도 되도록 모든 종류의 규칙표 후보를 합친다 (종류 오분류 영향 확인)
    lists = [T.m_rule1(a, k, A) for k in ("값 변경", "삭제", "번호 이동", "정의 변경", "위임·법령 변경", "내용 변경")]
    return T.pool(lists, 200)


tests = collections.OrderedDict()
tests["규칙표 1홉 (개정 종류를 아는 경우)"] = run2(T.m_rule1)
tests["규칙표, 종류를 몰라 전 종류 합집합"] = run2(union_rules)
tests["collect (PR12 방식)"] = run2(T.m_collect)
tests["전관계 BFS 1홉"] = run2(lambda a, k, A: T.bfs(a, A, 1))
tests["가중 BFS 2홉"] = run2(T.m_wbfs, hops=2)
tests["PageRank 팀 가중치 (0.3)"] = ppr_team
tests["PageRank 되돌아옴 0.15"] = L.rank_ppr(L.transition(L.W), 0.15, cases_=cases2)
tests["규칙 1홉 상위 3개 먼저 + PageRank"] = rule_then(ppr_team, 3)
tests["규칙 1홉 전부 먼저 + PageRank"] = rule_then(ppr_team, 100)
tests["규칙 1홉 + 가지치기 번지기"] = run2(T.m_beam)

print("\n## 5·6·7차 전체")
print("| 방법 | @5 | @10 | @20 | @30 |"); print("|---|---|---|---|---|")
for n, rk in tests.items():
    cells, tot = sc(rk); print(f"| {n} | " + " | ".join(cells) + " |")
print("정답", tot)

print("\n## 개정 종류별 @10 / @20 적중 (규칙표 | 전 종류 합집합 | PageRank)")
for kd in sorted({c["kind"] for c in cases2}):
    cs = [c for c in cases2 if c["kind"] == kd]
    row = []
    for n in ("규칙표 1홉 (개정 종류를 아는 경우)", "규칙표, 종류를 몰라 전 종류 합집합", "PageRank 팀 가중치 (0.3)"):
        cells, tot = sc(tests[n], (10, 20), cs); row.append(" / ".join(cells))
    print(f"- {kd} (문항 {len(cs)}, 정답 {tot}): " + " | ".join(row))

print("\n## 라운드별 @10 (규칙표 | PageRank)")
for r in (5, 6, 7):
    cs = [c for c in cases2 if c["round"] == r]
    a, tot = sc(tests["규칙표 1홉 (개정 종류를 아는 경우)"], (10,), cs); b, _ = sc(tests["PageRank 팀 가중치 (0.3)"], (10,), cs)
    print(f"- {r}차: 정답 {tot}: {a[0]} | {b[0]}")

# 놓친 정답: 어떤 길에도 안 이어진 것인가
miss = []
for c in cases2:
    a = c["anchors"][0][0]
    for w in c["wanted"]:
        if w not in tests["PageRank 팀 가중치 (0.3)"][c["id"]][:20]:
            p = None
            prev = {a: None}; fr = [a]
            for h in range(3):
                nx = []
                for u in fr:
                    for v, rel, d in T.adj.get(u, []):
                        if v in prev: continue
                        prev[v] = (u, rel); nx.append(v)
                fr = nx
            miss.append((c["id"], c["kind"], a, w, w in prev))
print("\nPageRank 상위 20에서 놓친 정답:", len(miss), "그중 3홉 안에 이어진 것:", sum(1 for m in miss if m[4]))
print(collections.Counter(m[1] for m in miss))
pickle.dump({"cases2": cases2, "tests": tests}, open(NS / "tests4.pkl", "wb"))
