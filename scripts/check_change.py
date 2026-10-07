"""변경안 점검: 개정 초안 하나가 다른 조항에 주는 영향 (깨짐·충돌·해소·확인 필요).

  uv run python scripts/amend.py --scenario S03          # 먼저 대비표·changeset 생성
  uv run python scripts/check_change.py --change S03      # 점검 (시나리오면 기대 결과와 채점)
  uv run python scripts/check_change.py --all             # out/changes/* 전부
  --no-vector: 의미 유사 후보 끄기 (모델 로드 없이 규칙·관련 조항 표만)
입력: out/changes/{id}/changeset.json, out/parsed, out/relations, out/edges/edges_all.jsonl, (out/vector)
출력: out/changes/{id}/check.json (점검 결과 + 시나리오 채점)
"""

import argparse
import collections
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck import runinfo  # noqa: E402
from regcheck.amend import apply, parse_amendment  # noqa: E402
from regcheck.change_check import check, score  # noqa: E402
from regcheck.citations import Registry  # noqa: E402

CAT = {"breaks": "깨짐", "new_conflicts": "새 충돌 후보", "resolved": "해소 후보", "review": "확인 필요",
       "law_check": "법령 대조", "undeterminable": "판단 불가", "info": "참고"}


def jl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--change", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-vector", action="store_true")
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()
    ids = sorted(p.name for p in (ROOT / "out" / "changes").iterdir() if (p / "changeset.json").exists()) if args.all else [args.change]
    if not ids or not ids[0]:
        sys.exit(__doc__)
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    rels = jl(ROOT / "out" / "relations" / "relations.jsonl")
    edges = jl(ROOT / "out" / "edges" / "edges_all.jsonl")
    vector, models = None, {}
    vdir = ROOT / "out" / "vector" / "BAAI__bge-m3"
    if not args.no_vector and vdir.exists():
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        from regcheck.vectors import Index, load_model
        index = Index(vdir)
        vector, models = (load_model(index.model_name), index), {"embedding": index.model_name}

    for cid in ids:
        cdir = ROOT / "out" / "changes" / cid
        cs = json.loads((cdir / "changeset.json").read_text(encoding="utf-8"))
        run = runinfo.start("check_change", [cdir / "changeset.json", ROOT / "out" / "relations" / "relations.jsonl",
                                             ROOT / "out" / "parsed"], models=models, params={"change": cid, "k": args.k})
        new_reg, _ = apply(regs[cs["reg_id"]], parse_amendment(cs["amendment"]))
        factory = lambda nr: Registry([nr if r["reg_id"] == nr["reg_id"] else r for r in regs.values()])  # noqa: E731
        found = check(cs, regs, rels, baseline_edges=edges, new_reg=new_reg, registry_factory=factory, vector=vector, k=args.k)
        sc_path = ROOT / "eval" / "scenarios" / f"{cid}.json"
        result = score(found, json.loads(sc_path.read_text(encoding="utf-8"))["expected"]) if sc_path.exists() else None
        for f in found:
            f["run"] = run["run_id"]
        (cdir / "check.json").write_text(json.dumps({"id": cid, "found": found, "score": result}, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        summ = dict(collections.Counter(CAT[f["category"]] for f in found))
        print(f"\n■ {cid} 「{cs['reg_name']}」 {cs.get('title', '')}\n  결과: {summ}")
        for f in found:
            if f["category"] == "info":
                continue
            print(f"   [{CAT[f['category']]}·{f.get('priority', '')}] {f['reg_name']} {f.get('unit', '')} {f.get('article', '')}"
                  f"{' ' + str(f['para']) + '항' if f.get('para') else ''} 「{(f.get('quote') or '')[:30]}」 ({f['path']})"
                  + (f" → 제안 「{f['suggest']}」" if f.get("suggest") else ""))
        sumsc = {}
        if result:
            print("  시나리오 채점:")
            for cat, rows in result.items():
                if not rows:
                    continue
                ok = sum(r["result"] in ("탐지", "통과") for r in rows)
                sumsc[cat] = [ok, len(rows)]
                miss = [f"{r['expected'].get('reg_name', '')} {r['expected'].get('article', '')} → {r['result']}{'(' + CAT.get(r['as'], '') + ')' if r['as'] and r['result'] != '탐지' else ''}"
                        for r in rows if r["result"] not in ("탐지", "통과")]
                print(f"   {CAT.get(cat, '영향 없음')}: {ok}/{len(rows)}" + (f"  — {'; '.join(miss)}" if miss else ""))
        runinfo.finish(run, [cdir / "check.json"], {"결과": summ, "채점": sumsc})


if __name__ == "__main__":
    main()
