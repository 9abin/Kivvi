"""영향 점검 평가셋(68)에서 RAG · 지식 그래프 · 결합을 한 표로 잰다 (개발 · 봉인 · 전체).

  uv run python scripts/eval_all.py                      # 개발 분할만
  uv run python scripts/eval_all.py --splits 개발,봉인 --yes-open-sealed    # 봉인은 코드 동결 뒤 한 번만
비교 (모두 같은 후보 상한 k):
  RAG만       수정한 조와 임베딩이 가까운 단위 (본문 조 + 부칙 · 별표 · 서식 임베딩)
  그래프만    수정 종류별 경로 (kg.impact), 경로 점수순
  그래프 먼저 + RAG 채움   (제안 구성)
지표: 재현율@k(10, 20), 후보 중 정답 비율, 도달 재현율(후보 풀 전체), k@100%(정답을 모두 포함하는 최소 k), 영향 없음 문항의 후보 수.
법령 개정처럼 시작점이 조가 아닌 문항은 RAG 기준을 만들 수 없어 표에서 빼고 그래프 결과만 따로 적는다.
정답은 사람 검토 전 초안이다. 결과는 확인 후보이고 충돌 판정이 아니다.
"""
import argparse
import collections
import glob
import json
import math
import random
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from regcheck.impact_eval import Truth  # noqa: E402
from regcheck.kg import KG, moved_range  # noqa: E402
from src.combo import metrics as M  # noqa: E402

K = ROOT / "eval/impact_label_kit/산출"
V = ROOT / "out/vector/BAAI__bge-m3"
CAPS = (10, 20)


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", default="개발")
    ap.add_argument("--yes-open-sealed", action="store_true")
    a = ap.parse_args()
    splits = a.splits.split(",")
    if "봉인" in splits and not a.yes_open_sealed:
        sys.exit("봉인 분할은 코드 동결 뒤 한 번만 연다. --yes-open-sealed")
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    nodes = {r["id"]: {k: v for k, v in r.items() if k != "id"} for r in jl(ROOT / "out/kg/nodes.jsonl")}
    kg = KG(nodes, jl(ROOT / "out/kg/edges.jsonl"))
    T = Truth(nodes, regs)
    item_text = {it["id"]: it["norm"] for r in regs.values() for x in r["articles"] for it in x["items"]}
    emb = np.load(V / "embeddings.npy"); emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    art = np.array([c["article_id"] for c in jl(V / "chunks.jsonl")]); byart = collections.defaultdict(list)
    for j, x in enumerate(art):
        byart[x].append(j)
    ext = np.load(ROOT / "out/kg/ext_emb.npy"); ext_ids = json.loads((ROOT / "out/kg/ext_ids.json").read_text(encoding="utf-8"))

    def rag_rank(x):
        q = emb[byart[x]]; best = {}
        for j, sc in enumerate((emb @ q.T).max(axis=1)):
            b = art[j]
            if b != x and sc > best.get(b, -1):
                best[b] = sc
        for k_, sc in zip(ext_ids, (ext @ q.T).max(axis=1)):
            best[k_] = sc
        return [y for y, _ in sorted(best.items(), key=lambda t: -t[1])]

    ch = {r["id"]: r for r in jl(K / "changes.jsonl")}; fin = {r["id"]: r for r in jl(K / "labels_final.jsonl")}
    rows = []   # 문항별 결과
    for i in sorted(ch):
        c = ch[i]; s = c["수정"]
        if c["분할"] not in splits:
            continue
        kind = s["종류"]; rid = T.by_name.get(s["규정"]); st = T.start_of(s)
        truth = {T.resolve(e) for e in fin[i]["영향"]} - {None}
        if not st:   # 법령 개정 등
            law = f"LAW:{s['규정']} {s['조']}"
            res = kg.impact(law, kind) if law in nodes else {"candidates": {}}
            hit = len(truth & set(res["candidates"]))
            rows.append({"id": i, "split": c["분할"], "kind": kind, "skip": True, "truth": len(truth), "kg_hit": hit, "kg_n": len(res["candidates"])})
            continue
        starts = moved_range(s["개정문"], rid, nodes) if kind == "번호 이동" else []
        hint = None
        if kind == "정의 변경":
            q = re.search(r'"([^"]+)"', s["개정문"]); want = re.sub(r"\s+", "", q.group(1))[:12] if q else ""
            hint = [t for t in kg.terms_defined_in(st) if want and want in re.sub(r"\s+", "", item_text.get(nodes[t]["item"], ""))] or kg.terms_defined_in(st)
        res = kg.impact_many(starts, kind) if len(starts) > 1 else kg.impact(st, kind, hint)
        kgr = [n for n, _ in sorted(res["candidates"].items(), key=lambda t: -t[1]["score"])]
        regl = [x for x in res["reg_level"] if not x.startswith("REG:")]
        nr = rag_rank(st)
        lists = {"RAG만": nr, "그래프만": kgr + [x for x in regl if x not in kgr], "그래프 먼저 + RAG": list(dict.fromkeys(kgr + regl + nr))}
        rows.append({"id": i, "split": c["분할"], "kind": kind, "skip": False, "truth": truth, "lists": lists, "kg_n": len(kgr)})

    def summarize(sel, title):
        sel = [r for r in sel if not r["skip"]]
        pos = [r for r in sel if r["truth"]]; neg = [r for r in sel if not r["truth"]]
        tot = sum(len(r["truth"]) for r in pos)
        print(f"\n===== {title}: 영향 있는 문항 {len(pos)}개(정답 {tot}건), 영향 없음 문항 {len(neg)}개 =====")
        if not pos:
            return
        for cap in CAPS:
            print(f"[후보 상한 {cap}]")
            for name in ("RAG만", "그래프만", "그래프 먼저 + RAG"):
                h = sum(len(r["truth"] & set(r["lists"][name][:cap])) for r in pos); n = sum(len(r["lists"][name][:cap]) for r in pos)
                print(f"  {name:20} 재현율 {h}/{tot} = {h/tot:.0%} | 후보 중 정답 {h/n:.0%}")
            pairs = [(len(r["truth"] & set(r["lists"]["그래프 먼저 + RAG"][:cap])), len(r["truth"]), len(r["truth"] & set(r["lists"]["RAG만"][:cap])), len(r["truth"])) for r in pos]
            d = M.paired_diff_cluster(pairs, n=2000)
            w = sum(1 for p in pairs if p[0] > p[2]); l = sum(1 for p in pairs if p[0] < p[2])
            print(f"  (그래프 먼저 + RAG) − (RAG만) = {d[0]:+.3f} 문항 단위 구간 [{d[1]:+.3f}, {d[2]:+.3f}] | 이긴 문항 {w}, 진 문항 {l}, 같음 {len(pairs)-w-l}")
        for name in ("RAG만", "그래프만", "그래프 먼저 + RAG"):
            rk = []
            for r in pos:
                pos_ = {n: k + 1 for k, n in enumerate(r["lists"][name])}
                rk += [pos_.get(n, math.inf) for n in r["truth"]]
            fin_ = [x for x in rk if x != math.inf]
            print(f"  {name:20} 도달 재현율 {len(fin_)}/{tot} = {len(fin_)/tot:.0%} | k@100% = {max(fin_) if fin_ else '-'} | 중앙 순위 {sorted(fin_)[len(fin_)//2] if fin_ else '-'}")
        print("  문항별(상한 20, 그래프 먼저 + RAG): " + " ".join(f"{r['id']}:{len(r['truth'] & set(r['lists']['그래프 먼저 + RAG'][:20]))}/{len(r['truth'])}" for r in pos))
        byk = collections.defaultdict(lambda: [0, 0, 0])
        for r in pos:
            byk[r["kind"]][0] += len(r["truth"]); byk[r["kind"]][1] += len(r["truth"] & set(r["lists"]["그래프 먼저 + RAG"][:20])); byk[r["kind"]][2] += len(r["truth"] & set(r["lists"]["RAG만"][:20]))
        print("  종류별(정답, 그래프+RAG@20, RAG@20): " + "; ".join(f"{k} {v[0]}, {v[1]}, {v[2]}" for k, v in byk.items()))
        if neg:
            print("  영향 없음 문항의 그래프 후보 수: " + ", ".join(f"{r['id']}={r['kg_n']}" for r in neg))

    for sp in splits:
        summarize([r for r in rows if r["split"] == sp], sp)
    if len(splits) > 1:
        summarize(rows, "전체")
    for r in rows:
        if r["skip"]:
            print(f"\n[표에서 뺌] {r['id']} ({r['split']}, {r['kind']}): 시작점이 조가 아님. 그래프 후보 {r['kg_n']}개 중 정답 {r['kg_hit']}/{r['truth']}")


if __name__ == "__main__":
    main()
