"""팀의 휴직 온톨로지(한석희, codex/four-domain-ontology-eval-20261106의 hyujik-user-original.db)의 연결 표를 후보 수집의 주제 층으로 꽂아
우리 키워드 규칙 휴직 층과 비교한다. 연결 표는 scripts/extract_hyujik_links.py가 외부 DB에서 뽑은 JSONL(out/hyujik_links.jsonl, 원문 없음)이고,
층은 regcheck.linked_layer.LinkLayer다. 후보 수집 코드는 건드리지 않는다.

  uv run python scripts/exp_leave_db.py <연결 표 JSONL> [--topic=pay] [--no-rag]
후보 연결: 바뀐 항(개정문이 따옴표로 짚은 항)이 DB에서 어떤 개념의 어떤 속성을 근거로 하는지 보고, 같은 개념에서 같은 속성을 근거로 하는 다른 조를 낸다
(DB 자신의 질의 v_candidates와 같은 발상). 라벨은 AI 초안(out/leave_items 풀 라벨 + out/pool_extra)이고, 풀 밖 후보는 정답 여부를 모른다(정답으로 세지 않음).
"""

import glob
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")

norm = lambda s: re.sub(r"\s+", "", s or "")  # noqa: E731


TOPIC = "pay" if "--topic=pay" in sys.argv else "leave"   # pay: 보수 시험 항목(eval/pay_items, 전수 재라벨 _v2) — 검토본 DB(domain_ontology.reviewed.db)로 비교


def _opt(name):
    return next((a.split("=", 1)[1] for a in sys.argv if a.startswith(f"--{name}=")), None)


ITEMS_ARG, LABELS_ARG = _opt("items"), _opt("labels")   # --items=<changes.jsonl> --labels=<labels_<id>.json 폴더>: 새 문항으로 비교할 때


def load_labels(i):
    units, truth = set(), set()
    if LABELS_ARG:
        for x in json.loads((Path(LABELS_ARG) / f"labels_{i}.json").read_text(encoding="utf-8")):
            u = x["unit"].strip("[] ")
            units.add(u)
            if x["label"] == "예":
                truth.add(u)
        return units, truth
    d = "pay_items" if TOPIC == "pay" else "leave_items"
    first = ROOT / "out" / d / (f"labels_{i}_v2.json" if (ROOT / "out" / d / f"labels_{i}_v2.json").exists() else f"labels_{i}.json")
    for p in (first, ROOT / "out/pool_extra" / f"labels_{i}.json"):
        if p.exists():
            for x in json.loads(p.read_text(encoding="utf-8")):
                u = x["unit"].strip("[] ")
                units.add(u)
                if x["label"] == "예":
                    truth.add(u)
    return units, truth


def main():
    from regcheck.impact_view import load_context, run
    from regcheck.leave_layer import LeaveLayer
    import eval_recall_all as era
    from regcheck.linked_layer import LinkLayer, item_texts
    db = sys.argv[1]
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    ctx = load_context(ROOT, regs); by = {r["name"]: k for k, r in regs.items()}; rag = None if "--no-rag" in sys.argv else era.build_rag(ctx)   # --no-rag: 층 자체의 정보량만 본다
    if TOPIC == "pay":
        from regcheck.pay_layer import PayLayer
        ours = PayLayer(ctx.unit_text)
    else:
        ours = LeaveLayer(ctx.unit_text)
    txt = item_texts(regs)
    L = lambda **kw: LinkLayer(db, item_text=txt, **kw)   # noqa: E731
    variants = {
        "층 끔 (RAG+그래프)": False,
        "우리 키워드 층": ours,
        "그들 DB (같은 개념 · 같은 속성)": L(),
        "그들 DB + 개념 위계": L(hierarchy=True),
        "그들 DB 확정 연결만 + 위계": L(hierarchy=True, trusted_only=True),
        "그들 DB + 같은 개념 전부": L(hierarchy=True, same_concept_any_attr=True),
        "우리 + 그들 DB(위계)": [ours, L(hierarchy=True)],
        "우리 + 그들 DB(확정만, 위계)": [ours, L(hierarchy=True, trusted_only=True)],
    }
    items = [json.loads(l) for l in (Path(ITEMS_ARG) if ITEMS_ARG else ROOT / "eval" / ("pay_items" if TOPIC == "pay" else "leave_items") / "changes.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"{'보수 · 수당' if TOPIC == 'pay' else '휴직'} 시험 항목 {len(items)}개")
    res = {k: dict(T=0, A=0, D=0, C=0, CD=0, unl=0, add=0, items=0, gained=[]) for k in variants}
    base_hits = {}
    for it in items:
        i, m = it["id"], it["수정"]
        units, truth = load_labels(i)
        for name, layer in variants.items():
            r = run(ctx, regs, by[m["규정"]], m["개정문"], i, rag=rag, leave=layer)
            if r["cq"].kind == "분석 실패":
                continue
            allc = {c.id for c in r["view"]["all"]}; dflt = {c.id for c in r["view"]["default"]}
            R = res[name]
            R["T"] += len(truth); R["A"] += len(truth & allc); R["D"] += len(truth & dflt); R["C"] += len(allc); R["CD"] += len(dflt)
            R["unl"] += len({c for c in allc if c not in units})
            if name == "층 끔 (RAG+그래프)":
                base_hits[i] = (allc, truth)
            else:
                R["add"] += len(allc - base_hits[i][0])
                for u in (truth & allc) - base_hits[i][0]:   # 층을 껐을 때 후보에 없던 정답
                    R["gained"].append((i, u))
    print(f"\n{'변형':36s} {'정답':>4s} {'전체후보 안':>10s} {'기본화면 안':>10s} {'후보 합':>7s} {'기본화면 합':>9s} {'층이 더한 후보':>12s} {'층이 더한 정답':>11s} {'풀 밖 후보':>8s}")
    for name, R in res.items():
        print(f"{name:36s} {R['T']:4d} {R['A']:10d} {R['D']:10d} {R['C']:7d} {R['CD']:9d} {R['add']:12d} {len(R['gained']):11d} {R['unl']:8d}")
    for name, R in res.items():
        if R["gained"]:
            print(f"\n[{name}] 층이 더한 정답:", R["gained"])


if __name__ == "__main__":
    main()
