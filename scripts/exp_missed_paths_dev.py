"""개발 분할에서 후보 상위 20에 못 든 정답이 그래프에서 시작점과 어떤 관계 조합으로 닿는지 센다 (탐색 단계 전이 규칙의 근거).
  uv run python scripts/exp_missed_paths_dev.py [--splits 개발] [--max-len 3]
허브 노드(연결 수 > 60)는 지나가지 않는다. 조합은 관계 이름과 방향(> 따라감, < 거슬러감)으로 적는다.
"""
import argparse
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
from eval_collect_dev import build, jl  # noqa: E402

HUB = 60


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", default="개발"); ap.add_argument("--max-len", type=int, default=3)
    a = ap.parse_args()
    pos, _, _, nodes = build(a.splits)
    adj = collections.defaultdict(list)
    for e in jl(ROOT / "out/kg/edges.jsonl"):
        if e["type"] == "PART_OF":
            continue
        adj[e["src"]].append((e["dst"], e["type"] + ">")); adj[e["dst"]].append((e["src"], e["type"] + "<"))
    combo = collections.Counter(); none = []; tot = 0
    for r in pos:
        top = {c.id for c in r["cands"][:20]}
        for t in r["truth"] - top:
            tot += 1
            best = None
            for s in r["cq"].starts:
                seen = {s: ()}; fr = [s]
                for L in range(a.max_len):
                    nf = []
                    for u in fr:
                        if L and len(adj[u]) > HUB:
                            continue
                        for v, rel in adj[u]:
                            if v not in seen:
                                seen[v] = seen[u] + (rel,); nf.append(v)
                    fr = nf
                    if t in seen:
                        break
                if t in seen and (best is None or len(seen[t]) < len(best)):
                    best = seen[t]
            if best:
                combo[(r["kind"], " ".join(best))] += 1
            else:
                none.append((r["id"], t))
    print(f"[{a.splits}] 상위 20 밖 정답 {tot}건. 닿는 조합 (수정 종류, 관계 열):")
    for (k, c), n in combo.most_common(25):
        print(f"  {n:3d}  [{k}] {c}")
    print(f"  닿지 않음(허브 제외, {a.max_len}단계 이내) {len(none)}건: " + ", ".join(f"{i}:{t}" for i, t in none[:12]))


if __name__ == "__main__":
    main()


def precision_by_combo(splits="개발", max_len=2):
    """시작점에서 max_len단계 이내로 닿는 모든 조합의 후보 수·정답 수 (영향 없음 문항 포함, 허브 제외)."""
    pos, neg, _, _ = build(splits)
    adj = collections.defaultdict(list)
    for e in jl(ROOT / "out/kg/edges.jsonl"):
        if e["type"] == "PART_OF":
            continue
        adj[e["src"]].append((e["dst"], e["type"] + ">")); adj[e["dst"]].append((e["src"], e["type"] + "<"))
    cnt = collections.Counter(); hit = collections.Counter(); newhit = collections.Counter()
    for r in pos + neg:
        top = {c.id for c in r["cands"][:20]}
        got = {}
        for s in r["cq"].starts:
            seen = {s: ()}; fr = [s]
            for L in range(max_len):
                nf = []
                for u in fr:
                    if L and len(adj[u]) > HUB:
                        continue
                    for v, rel in adj[u]:
                        if v not in seen:
                            seen[v] = seen[u] + (rel,); nf.append(v)
                fr = nf
            for v, p in seen.items():
                if v != s and len(p) == max_len and v in r["truth"] | set(x for x in seen if x not in nodes_skip) and not v.startswith(("TERM", "REG", "BODY", "ORG", "LAW")):
                    got.setdefault(" ".join(p), set()).add(v)
        for c, vs in got.items():
            key = (r["kind"], c)
            cnt[key] += len(vs); hit[key] += len(vs & r["truth"]); newhit[key] += len((vs & r["truth"]) - top)
    return cnt, hit, newhit


nodes_skip = set()

if __name__ == "__main__" and "--prec" in sys.argv:
    cnt, hit, newhit = precision_by_combo()
    print("\n[2단계 조합별] 후보 수 / 정답 수 / 상위20 밖 정답 (후보 많은 조합은 제외)")
    for k in sorted(hit, key=lambda k: (-newhit[k], -hit[k])):
        if hit[k]:
            print(f"  [{k[0]}] {k[1]:40} 후보 {cnt[k]:4d} 정답 {hit[k]:3d} ({hit[k]/cnt[k]:.0%}) 새 정답 {newhit[k]}")
