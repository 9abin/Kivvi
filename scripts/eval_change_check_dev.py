"""기존 규칙 기반 변경안 점검(`regcheck.change_check`)을 영향 점검 평가셋(68)에서 잰다 — 지식 그래프(72)와 비교할 기준선.

  uv run python scripts/eval_change_check_dev.py                 # 개발 분할, 변형: core · +의미이웃 · +규칙추론
  uv run python scripts/eval_change_check_dev.py --vector        # 의미 유사(벡터 모델)도 켠 변형을 더한다 (모델 로드)
  uv run python scripts/eval_change_check_dev.py --split 봉인 --yes-open-sealed   # 봉인은 한 번만

개정문 -> changeset은 scripts/amend.py와 같은 `build_changeset`을 쓴다. 적용 못 하는 개정문은 「적용 못함」으로 남긴다.
정답은 사람 검토 전 초안이다. 결과는 확인 후보이고 충돌 판정이 아니다.
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

from regcheck import neighbors as nb_mod  # noqa: E402
from regcheck.amend import AmendError, build_changeset  # noqa: E402
from regcheck.change_check import run as run_check  # noqa: E402
from regcheck.impact_eval import Truth  # noqa: E402

K = ROOT / "eval" / "impact_label_kit" / "산출"


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def node_of(it, regs_by_id):
    """점검 카드 하나 -> 노드 ID (src가 있으면 src의 조, 없으면 규정 + 조 라벨)."""
    src = it.get("src") or ""
    if src and re.search(r"-\d+-\d+-\d+$", src):
        return src.rsplit("-", 3)[0]
    if src:
        return src
    rid = it.get("reg_id")
    return Truth.art_id(rid, it.get("article", "")) or (f"{rid}:" + re.sub(r"\s+", "", it.get("article", "")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="개발")
    ap.add_argument("--yes-open-sealed", action="store_true")
    ap.add_argument("--vector", action="store_true")
    a = ap.parse_args()
    if a.split == "봉인" and not a.yes_open_sealed:
        sys.exit("봉인 분할은 한 번만 연다. 규칙 확정 뒤에 --yes-open-sealed로 실행한다.")
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    rels = jl(ROOT / "out/relations/relations.jsonl")
    edges = jl(ROOT / "out/edges/edges_all.jsonl")
    kg_nodes = {r["id"]: {k: v for k, v in r.items() if k != "id"} for r in jl(ROOT / "out/kg/nodes.jsonl")}
    truth = Truth(kg_nodes, regs)
    nbs = nb_mod.load()
    inferred = jl(ROOT / "out/rules/inferred.jsonl") if (ROOT / "out/rules/inferred.jsonl").exists() else None
    variants = {"core(규칙만)": {}, "+의미이웃(미리 계산)": {"neighbors": nbs}, "+규칙추론": {"inferred": inferred}}
    if a.vector:
        from regcheck.vectors import Index, load_model
        vdir = ROOT / "out/vector/BAAI__bge-m3"
        idx = Index(vdir)
        variants["+의미유사(벡터)"] = {"vector": (load_model(idx.model_name), idx)}
    ch = {r["id"]: r for r in jl(K / "changes.jsonl")}
    fin = {r["id"]: r for r in jl(K / "labels_final.jsonl")}
    summary = {v: collections.Counter() for v in variants}
    ctrl = {v: [] for v in variants}
    for i in sorted(ch):
        c = ch[i]
        if c["분할"] != a.split:
            continue
        s = c["수정"]
        rid = truth.by_name.get(s["규정"])
        try:
            cs, _ = build_changeset(regs[rid], s["개정문"], i, "")
        except (AmendError, KeyError, TypeError) as e:
            print(f"\n{i} {s['규정']} {s['조']} [{s['종류']}] 개정문 적용 못함: {str(e)[:70]}")
            for v in variants:
                for e_ in fin[i]["영향"]:
                    summary[v]["적용못함"] += 1
            continue
        t = [(e, truth.resolve(e)) for e in fin[i]["영향"]]
        print(f"\n{i} {s['규정']} {s['조']} [{s['종류']}] 정답 {len(t)}")
        for v, kw in variants.items():
            try:
                found = run_check(cs, regs, rels, edges, **kw)
            except Exception as ex:  # noqa: BLE001
                print(f"   {v}: 실패 {type(ex).__name__} {str(ex)[:60]}")
                continue
            nodes = {node_of(x, regs) for x in found}
            cats = collections.Counter(x["category"] for x in found)
            hit = sum(1 for e, n in t if n and n in nodes)
            unres = sum(1 for e, n in t if not n)
            print(f"   {v:22} 후보 {len(nodes):3d}개 (카드 {len(found)}) | 정답 중 {hit}/{len(t)} (해석불가 {unres}) | {dict(cats)}")
            if not t:
                ctrl[v].append((i, len(nodes)))
            for e, n in t:
                summary[v]["정답"] += 1
                summary[v]["후보에 듦" if (n and n in nodes) else ("해석불가" if not n else "못 찾음")] += 1
    print("\n== 합계 (정답 영향 건수 기준)")
    for v, c in summary.items():
        n = c["정답"] + c["적용못함"]
        print(f"{v:22} 정답 {n} | 후보에 듦 {c['후보에 듦']} ({c['후보에 듦']/n:.0%}) | 못 찾음 {c['못 찾음']} | 해석불가 {c['해석불가']} | 개정문 적용 못함 {c['적용못함']} | 영향 없음 문항 후보 {ctrl[v]}")


if __name__ == "__main__":
    main()
