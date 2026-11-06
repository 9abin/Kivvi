"""동반 개정 벤치마크를 쌍의 연결 유형으로 쪼갠다 (실제 개정 이력이 정답, AI 라벨 아님).
  uv run python scripts/eval_coamend_split.py
유형: 직접 인용(CITES) / 그래프 1홉(다른 관계) / 그래프 2~3홉 / 글자만 공유(희귀 낱말) / 연결 없음.
각 유형의 비중과, 의미 검색 상위 K + 그래프 3홉 상위 K로 찾은 비율. 연결 없음에서 무작위로 몇 쌍을 읽어 볼 수 있게 출력한다.
"""
import collections
import random
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
import eval_coamend as ec  # noqa: E402

K = 50
JOSA = re.compile(r"(으로|에서|에게|까지|부터|이다|하는|하여|한다|에는|에도|의|를|을|은|는|이|가|에|로|과|와|도|만)$")


def toks(t):
    out = set()
    for w in re.findall(r"[가-힣]{3,}", t or ""):
        w = JOSA.sub("", w)
        if len(w) >= 3:
            out.add(w)
    return out


def main():
    vec, regs, nodes, edges = ec.load()
    cl = ec.clusters(regs)
    nb = ec.neighbors(edges)
    cites = collections.defaultdict(set)
    for e in edges:
        if e["type"] == "CITES":
            cites[e["src"]].add(e["dst"]); cites[e["dst"]].add(e["src"])
    text, title, rname = {}, {}, {r["reg_id"]: r["name"] for r in regs.values()}
    for r in regs.values():
        for a in r["articles"]:
            text[a["id"]] = a["norm"]; title[a["id"]] = a.get("title") or ""
    T = {u: toks(t) for u, t in text.items()}
    df = collections.Counter(w for s in T.values() for w in s)
    keys = [k for k in vec if ":" in k]; M = np.stack([vec[k] for k in keys])
    rows = []
    for d, v in cl.items():
        for s in v:
            truth = {x for x in v if ec.reg_of(x) != ec.reg_of(s)}
            if not truth or s not in vec:
                continue
            order = [keys[i] for i in np.argsort(-(M @ vec[s])) if keys[i] != s]
            R = {h: ec.reach(nb, nodes, s, h) for h in (1, 2, 3)}
            g3 = sorted(R[3], key=lambda k: (R[3][k], -float(vec[k] @ vec[s])) if k in vec else (R[3][k], 0))
            sem, gr = set(order[:K]), set(g3[:K])
            for t in truth:
                hop = R[3].get(t)
                direct = t in cites[s]
                shared = {w for w in T.get(s, set()) & T.get(t, set()) if df[w] <= 12}
                cat = "직접 인용" if direct else ("그래프 1홉" if hop == 1 else ("그래프 2~3홉" if hop else ("글자만 공유" if shared else "연결 없음")))
                rows.append(dict(date=d, s=s, t=t, cat=cat, size=len(v), sem=t in sem, gr=t in gr, either=(t in sem) or (t in gr), cos=float(vec[s] @ vec[t]) if t in vec else 0.0))
    n = len(rows)
    print(f"쌍 {n} (묶음 {len(cl)}개) | 후보 상한 K={K}: 의미 상위 K + 그래프 3홉 상위 K")
    print(f"전체 재현율 {sum(r['either'] for r in rows)/n:.1%} (의미만 {sum(r['sem'] for r in rows)/n:.1%}, 그래프만 {sum(r['gr'] for r in rows)/n:.1%})\n")
    print(f"{'유형':10s} {'쌍':>5s} {'비중':>6s} | {'찾음':>6s} {'의미만':>6s} {'그래프만':>7s} | 평균 코사인")
    for cat in ("직접 인용", "그래프 1홉", "그래프 2~3홉", "글자만 공유", "연결 없음"):
        v = [r for r in rows if r["cat"] == cat]
        if v:
            print(f"{cat:10s} {len(v):5d} {len(v)/n:6.0%} | {sum(r['either'] for r in v)/len(v):6.0%} {sum(r['sem'] for r in v)/len(v):6.0%} {sum(r['gr'] for r in v)/len(v):7.0%} | {np.mean([r['cos'] for r in v]):.2f}")
    print("\n[묶음 크기별 (조 개수)]")
    for lo, hi in ((2, 2), (3, 3), (4, 5), (6, 8)):
        v = [r for r in rows if lo <= r["size"] <= hi]
        if v:
            print(f"  {lo}~{hi}개 {len(v):4d}쌍 | 찾음 {sum(r['either'] for r in v)/len(v):.0%} | 연결 없음 비중 {sum(r['cat']=='연결 없음' for r in v)/len(v):.0%}")
    print("\n[날짜별 묶음 크기 상위 — 일괄 개정 의심]")
    cnt = collections.Counter((r["date"], r["size"]) for r in rows)
    byd = collections.Counter(r["date"] for r in rows)
    for dt, c in byd.most_common(8):
        v = [r for r in rows if r["date"] == dt]
        print(f"  {dt} 쌍 {c:3d} | 찾음 {sum(r['either'] for r in v)/len(v):.0%} | 연결 없음 {sum(r['cat']=='연결 없음' for r in v)/len(v):.0%}")
    rnd = random.Random(7)
    none = [r for r in rows if r["cat"] == "연결 없음"]
    print(f"\n[연결 없음 {len(none)}쌍 중 무작위 표본: 질의 조 → 짝, 후보에 든 여부]")
    for r in rnd.sample(none, min(14, len(none))):
        s, t = r["s"], r["t"]
        print(f"  {r['date']} [{'찾음' if r['either'] else '놓침'} cos {r['cos']:.2f}] {rname[ec.reg_of(s)]} {title[s]} ({text[s][:40].replace(chr(10),' ')}) → {rname[ec.reg_of(t)]} {title[t]} ({text[t][:40].replace(chr(10),' ')})")


if __name__ == "__main__":
    main()
