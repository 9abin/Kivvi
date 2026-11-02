"""후보 수집(regcheck.collect)을 영향 점검 평가셋에서 잰다: 개정문 -> 변경 분석 -> 그래프 + 문구 + 의미 검색 -> 단계가 붙은 후보.

  uv run python scripts/eval_collect_dev.py [--splits 개발]
수정 종류는 개정문에서 자동 분류한 값을 쓴다 (작성자 라벨을 쓰지 않는다). 후보 상한은 이전 비교와 같다.
"""
import argparse
import collections
import glob
import json
import math
import os
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from exp_query_text_dev import BM25  # noqa: E402
from regcheck.amend import apply, parse_amendment  # noqa: E402
from regcheck.change_query import _art_text, analyze, analyze_law  # noqa: E402
from regcheck.collect import collect  # noqa: E402
from regcheck.impact_eval import Truth  # noqa: E402
from regcheck.kg import KG  # noqa: E402

K = ROOT / "eval/impact_label_kit/산출"
V = ROOT / "out/vector/BAAI__bge-m3"


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def build(splits):
    """개정문 -> 변경 분석 -> 후보 수집. (영향 있는 문항 행, 영향 없는 문항 행, 단위 본문)을 돌려준다."""
    from sentence_transformers import SentenceTransformer
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    nodes = {r["id"]: {k: v for k, v in r.items() if k != "id"} for r in jl(ROOT / "out/kg/nodes.jsonl")}
    kg = KG(nodes, jl(ROOT / "out/kg/edges.jsonl")); T = Truth(nodes, regs)
    emb = np.load(V / "embeddings.npy"); emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    chunks = jl(V / "chunks.jsonl")
    ext = np.load(ROOT / "out/kg/ext_emb.npy"); ext_ids = json.loads((ROOT / "out/kg/ext_ids.json").read_text(encoding="utf-8"))
    ids = [c["article_id"] for c in chunks] + ext_ids; E = np.vstack([emb, ext])
    unit_text = {}
    for r in regs.values():
        for x in r["articles"]:
            if not x.get("deleted"):
                unit_text[x["id"]] = x["norm"]
        for ad in r.get("addenda") or []:
            for x in ad.get("articles") or []:
                unit_text[x["id"]] = x["norm"]
        for ap_ in r.get("appendices") or []:
            unit_text[ap_["id"]] = (ap_.get("title") or "") + "\n" + (ap_.get("text") or "")
    texts = [c["text"] for c in chunks] + [unit_text.get(k, "") for k in ext_ids]
    bm = BM25(texts)
    ntext = {k: re.sub(r"\s+", "", v) for k, v in unit_text.items()}
    enc = SentenceTransformer("BAAI/bge-m3", device="cuda"); enc.max_seq_length = 512

    def find(term):
        t = re.sub(r"\s+", "", term)
        return [k for k, v in ntext.items() if t in v]

    def rag(q, k, exclude=()):
        qa = enc.encode([q[:1200]], normalize_embeddings=True)[0]
        d = np.argsort(-(E @ qa))[:200]; b = np.argsort(-bm.scores(q))[:200]
        rrf = collections.defaultdict(float)
        for r_, j in enumerate(d):
            rrf[j] += 1 / (60 + r_)
        for r_, j in enumerate(b):
            rrf[j] += 1 / (60 + r_)
        seen, out = set(), []
        for j in sorted(rrf, key=lambda j: -rrf[j]):
            x = ids[j]
            if x not in seen and x not in exclude:
                seen.add(x); out.append(x)
            if len(out) >= k:
                break
        return out

    ch = {r["id"]: r for r in jl(K / "changes.jsonl")}; fin = {r["id"]: r for r in jl(K / "labels_final.jsonl")}
    pos_rows, neg_rows = [], []
    for i in sorted(ch):
        c = ch[i]; s = c["수정"]
        if c["분할"] not in splits.split(","):
            continue
        rid = T.by_name.get(s["규정"]); st = T.start_of(s)
        if not rid:   # 법령 개정(규정 밖)
            cq = analyze_law(i, s["규정"], s["개정문"], nodes)
            tx = {"title": s["규정"], "old": "", "new": "", "amend": s["개정문"]}
            cands = collect(kg, cq, find, lambda q, k: rag(q, k, set()), tx)
            truth = {T.resolve(e) for e in fin[i]["영향"]} - {None}
            (pos_rows if truth else neg_rows).append({"id": i, "kind": cq.kind, "truth": truth, "cands": cands, "cq": cq, "tx": tx})
            continue
        if not st:
            continue
        cq = analyze(regs[rid], s["개정문"], i)
        new_reg = apply(regs[rid], parse_amendment(s["개정문"]))[0] if cq.kind != "분석 실패" else regs[rid]
        old_a = next(x for x in regs[rid]["articles"] if x["id"] == st)
        new_a = next((x for x in new_reg["articles"] if x["label"] == old_a["label"]), old_a)
        item_old = next((it["norm"] for it in old_a["items"] if s.get("항") and it["path"][0] == s["항"]), None)
        item_new = next((it.get("norm") or it.get("text") for it in new_a["items"] if s.get("항") and it["path"][0] == s["항"]), None)
        tx = {"title": old_a.get("title") or "", "old": (item_old or _art_text(old_a))[:600], "new": (item_new or _art_text(new_a))[:600], "amend": s["개정문"]}
        ex = set() if cq.kind == "번호 이동" else set(cq.starts or [st])   # 번호 이동은 이동 범위의 조도 후보가 될 수 있다
        cands = collect(kg, cq, find, lambda q, k: rag(q, k, ex), tx)
        truth = {T.resolve(e) for e in fin[i]["영향"]} - {None}
        (pos_rows if truth else neg_rows).append({"id": i, "kind": cq.kind, "truth": truth, "cands": cands, "cq": cq, "tx": tx})
    return pos_rows, neg_rows, unit_text, nodes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", default="개발")
    a = ap.parse_args()
    pos_rows, neg_rows, _, _ = build(a.splits)
    tot = sum(len(r["truth"]) for r in pos_rows)
    print(f"[분할 {a.splits}] 영향 있는 문항 {len(pos_rows)}개, 정답 {tot}건 / 영향 없음 문항 {len(neg_rows)}개")
    for cap in (10, 20):
        h = sum(len(r["truth"] & {c.id for c in r["cands"][:cap]}) for r in pos_rows); n = sum(len(r["cands"][:cap]) for r in pos_rows)
        print(f"  후보 상한 {cap}: 재현율 {h}/{tot} = {h/tot:.0%} | 후보 중 정답 {h/n:.0%}")
    for tier in ("확정급", "확인 권장", "참고"):
        n = sum(1 for r in pos_rows for c in r["cands"] if c.tier == tier)
        h = sum(1 for r in pos_rows for c in r["cands"] if c.tier == tier and c.id in r["truth"])
        print(f"  단계 {tier}: 후보 {n}개 중 정답 {h} ({h/max(n, 1):.0%})")
    strong = sum(len(r["truth"] & {c.id for c in r["cands"] if c.tier == "확정급"}) for r in pos_rows)
    rev = sum(len(r["truth"] & {c.id for c in r["cands"] if c.tier != "참고"}) for r in pos_rows)
    sn = sum(1 for r in pos_rows for c in r["cands"] if c.tier == "확정급")
    rn = sum(1 for r in pos_rows for c in r["cands"] if c.tier != "참고")
    print(f"  읽는 양 대비: 확정급만 {sn}개 읽으면 정답 {strong}/{tot} ({strong/tot:.0%}), 확정급+권장 {rn}개 읽으면 {rev}/{tot} ({rev/tot:.0%})")
    rk = []
    for r in pos_rows:
        pos = {c.id: k + 1 for k, c in enumerate(r["cands"])}
        rk += [pos.get(n, math.inf) for n in r["truth"]]
    fin_ = [x for x in rk if x != math.inf]
    print(f"  도달 재현율 {len(fin_)}/{tot} = {len(fin_)/tot:.0%} | k@100% = {max(fin_)} | 중앙 순위 {sorted(fin_)[len(fin_) // 2]}")
    print("  문항별@20: " + " ".join(f"{r['id']}[{r['kind']}]:{len(r['truth'] & {c.id for c in r['cands'][:20]})}/{len(r['truth'])}" for r in pos_rows))
    print("  영향 없음 문항: " + ", ".join(f"{r['id']}[{r['kind']}] 후보 {len(r['cands'])} (확정급 {sum(c.tier == '확정급' for c in r['cands'])}, 권장 {sum(c.tier == '확인 권장' for c in r['cands'])})" for r in neg_rows))


if __name__ == "__main__":
    main()
