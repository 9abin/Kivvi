"""같은 지식 그래프에서 탐색 방법만 바꿔 비교한다 (영향 점검 평가셋 68, 개발 분할, 후보 상한 같음).

  uv run python scripts/exp_traversal_dev.py [--cap 20]
탐색 방법 (사전에 정한 목록; 결과를 보고 추가하지 않는다):
  T1 모든 선 1단계 (방향 무시, 수정 종류 무시)
  T2 모든 선 2단계 (방향 무시)
  T3 인용받음만 1단계 (이전 기준)
  T4 인용받음만 2단계
  T5 개인화 PageRank (시작 조에서 출발, 방향 무시, 모든 선)
  T6 수정 종류별 경로 (kg.impact) — 이름 순서
  T7 수정 종류별 경로 + 경로 점수순 정렬 (지금 쓰는 방식)
  T8 가중 최단 경로 (선 종류별 신뢰도 가중치, 방향 무시, 수정 종류 무시, 3단계까지; 경로 점수 = 가중치의 곱)
  T9 빔 서치 (폭 10, 3단계, 같은 가중치. 단계마다 상위 10개 경로만 유지)
BFS류는 정렬 기준이 없어 (단계, 연결 경로 수) 순으로 자른다. PART_OF(소속)는 모든 조를 규정 허브로 이어서 뺀다.
"""
import argparse
import collections
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from regcheck.impact_eval import Truth  # noqa: E402
from regcheck.kg import KG, moved_range  # noqa: E402

K = ROOT / "eval/impact_label_kit/산출"
UNITS = ("Article", "Supplement", "Annex", "Form")


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=20)
    a = ap.parse_args()
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    nodes = {r["id"]: {k: v for k, v in r.items() if k != "id"} for r in jl(ROOT / "out/kg/nodes.jsonl")}
    rows = jl(ROOT / "out/kg/edges.jsonl")
    kg = KG(nodes, rows)
    T = Truth(nodes, regs)
    item_text = {it["id"]: it["norm"] for r in regs.values() for x in r["articles"] for it in x["items"]}
    W = {"CITES": 1.0, "SAME_TITLE": 0.9, "USES_TERM": 0.8, "DEFINES": 0.8, "CITES_LAW": 0.5, "SUPPLEMENTS": 0.4, "CITES_REG": 0.3, "MENTIONS_BODY": 0.2}
    wadj = collections.defaultdict(list)   # (이웃, 가중치) 방향 무시, PART_OF 제외. 구조 검사에서 걸린 인용은 절반
    und = collections.defaultdict(set)   # 방향 무시 이웃 (PART_OF 제외)
    cites_in = collections.defaultdict(set)
    for r in rows:
        if r["type"] == "PART_OF":
            continue
        und[r["src"]].add(r["dst"]); und[r["dst"]].add(r["src"])
        w = W.get(r["type"], 0.1) * (0.5 if r.get("suspect") else 1.0)
        wadj[r["src"]].append((r["dst"], w)); wadj[r["dst"]].append((r["src"], w))
        if r["type"] == "CITES":
            cites_in[r["dst"]].add(r["src"])

    def bfs(starts, hops, nbr):
        dist = {s: 0 for s in starts}; paths = collections.Counter(); fr = set(starts)
        for h in range(1, hops + 1):
            nxt = set()
            for u in fr:
                for v in nbr.get(u, ()):
                    if v not in dist:
                        dist[v] = h; nxt.add(v)
                    if dist[v] == h:
                        paths[v] += 1
            fr = nxt
        order = sorted((n for n in dist if n not in starts and nodes.get(n, {}).get("type") in UNITS), key=lambda n: (dist[n], -paths[n], n))
        return order

    def best_paths(starts, depth, beam=None):
        """경로 점수 = 선 가중치의 곱. beam이 있으면 단계마다 상위 beam개 경로만 유지한다."""
        best = {s_: 1.0 for s_ in starts}; frontier = {s_: 1.0 for s_ in starts}
        for _ in range(depth):
            cand = {}
            for u, sc in frontier.items():
                for v, w in wadj.get(u, ()):
                    ns = sc * w
                    if v not in starts and ns > cand.get(v, 0):
                        cand[v] = ns
            if beam:
                cand = dict(sorted(cand.items(), key=lambda t: -t[1])[:beam])
            frontier = {v: sc for v, sc in cand.items() if sc > best.get(v, 0)}
            for v, sc in frontier.items():
                best[v] = max(best.get(v, 0), sc)
        return [n for n, _ in sorted(best.items(), key=lambda t: -t[1]) if n not in starts and nodes.get(n, {}).get("type") in UNITS]

    def ppr(starts, alpha=0.15, iters=30):
        p = {s: 1 / len(starts) for s in starts}; r = dict(p)
        for _ in range(iters):
            nr = collections.defaultdict(float)
            for u, mass in r.items():
                nb = und.get(u)
                if not nb:
                    continue
                share = (1 - alpha) * mass / len(nb)
                for v in nb:
                    nr[v] += share
            for s, m in p.items():
                nr[s] += alpha * m
            r = nr
        return [n for n, _ in sorted(r.items(), key=lambda t: -t[1]) if n not in starts and nodes.get(n, {}).get("type") in UNITS]

    ch = {r["id"]: r for r in jl(K / "changes.jsonl")}; fin = {r["id"]: r for r in jl(K / "labels_final.jsonl")}
    names = ["T1 모든 선 1단계", "T2 모든 선 2단계", "T3 인용받음만 1단계", "T4 인용받음만 2단계", "T5 개인화 PageRank", "T6 수정 종류별 경로", "T7 + 경로 점수순 (현재)", "T8 가중 최단 경로", "T9 빔 서치(폭 10)"]
    hit = collections.defaultdict(lambda: collections.Counter()); size = collections.defaultdict(collections.Counter)
    tot = collections.Counter(); per = collections.defaultdict(dict); ctrl = collections.defaultdict(list)
    for i in sorted(ch):
        c = ch[i]; s = c["수정"]
        if c["분할"] != "개발":
            continue
        rid = T.by_name[s["규정"]]; st = T.start_of(s); kind = s["종류"]
        starts = moved_range(s["개정문"], rid, nodes) if kind == "번호 이동" else [st]
        starts = starts or [st]
        hint = None
        if kind == "정의 변경":
            q = re.search(r'"([^"]+)"', s["개정문"]); want = re.sub(r"\s+", "", q.group(1))[:12] if q else ""
            hint = [t for t in kg.terms_defined_in(st) if want and want in re.sub(r"\s+", "", item_text.get(nodes[t]["item"], ""))] or kg.terms_defined_in(st)
        res = kg.impact_many(starts, kind) if len(starts) > 1 else kg.impact(st, kind, hint)
        lists = {
            names[0]: bfs(set(starts), 1, und), names[1]: bfs(set(starts), 2, und),
            names[2]: sorted(n for s_ in starts for n in cites_in.get(s_, ()) if n not in starts),
            names[3]: bfs(set(starts), 2, cites_in),
            names[4]: ppr(set(starts)),
            names[5]: sorted(res["candidates"]),
            names[6]: [n for n, _ in sorted(res["candidates"].items(), key=lambda t: -t[1]["score"])],
            names[7]: best_paths(set(starts), 3), names[8]: best_paths(set(starts), 3, beam=10),
        }
        truth = {T.resolve(e) for e in fin[i]["영향"]} - {None}
        if not truth:
            for n, l in lists.items():
                ctrl[n].append(len(l[:a.cap]))
            continue
        tot["정답"] += len(truth)
        for n, l in lists.items():
            top = l[:a.cap]
            hit[n]["적중"] += len(truth & set(top)); size[n]["후보"] += len(top); size[n]["전체후보"] += len(l)
            per[n][i] = f"{len(truth & set(top))}/{len(truth)}"
    print(f"[후보 상한 {a.cap}개/문항, 개발 5문항, 정답 {tot['정답']}건]")
    print(f"{'탐색 방법':26} {'재현율':>10} {'후보 중 정답':>12} {'상한 전 후보수':>14}  문항별(I01~I05)  | 영향 없음 문항 후보수(I06~I08)")
    for n in names:
        h = hit[n]["적중"]; l = size[n]["후보"]
        print(f"{n:26} {h:>4}/{tot['정답']} {h/tot['정답']:>4.0%} {h/l:>10.0%} {size[n]['전체후보']:>12}  {' '.join(per[n].get(k, '-') for k in sorted(per[n]))}  | {ctrl[n]}")


if __name__ == "__main__":
    main()
