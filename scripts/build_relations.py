"""관련 조항 표 생성 + 정확도 측정용 표본 추출.

  uv run python scripts/build_relations.py                 # 표 생성
  uv run python scripts/build_relations.py --sample 50     # 표본 추출 → eval/relation_audit.jsonl (라벨 칸 비움)
입력: out/edges/edges_all.jsonl (extract_edges.py --all), out/parsed/
사람 결정: eval/relation_decisions.json (있으면 반영)
"""

import argparse
import collections
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck import runinfo  # noqa: E402
from regcheck.relations import apply_decisions, from_edges, save  # noqa: E402

OUT = ROOT / "out" / "relations"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--seed", type=int, default=20261006)
    args = ap.parse_args()
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    edges = [json.loads(l) for l in (ROOT / "out" / "edges" / "edges_all.jsonl").read_text(encoding="utf-8").splitlines()]
    dec_path = ROOT / "eval" / "relation_decisions.json"
    run = runinfo.start("relations", [ROOT / "out" / "edges" / "edges_all.jsonl", ROOT / "out" / "parsed", dec_path])
    rels = from_edges(edges, regs)
    for r in rels:
        r.run = run["run_id"]
    if dec_path.exists():
        apply_decisions(rels, json.loads(dec_path.read_text(encoding="utf-8")))
    save(rels, OUT)
    c = collections.Counter((r.kind, r.confidence, r.verification) for r in rels)
    for k, v in sorted(c.items()):
        print("\t".join(k), v, sep="\t")
    runinfo.finish(run, [OUT / "relations.jsonl"], {"관계": len(rels)})
    print(f"관계 {len(rels)}개 → {OUT / 'relations.jsonl'}  (실행 {run['run_id']})")

    if args.sample:
        # 종류별 층화 표본: 본문·별표·별지 출발만 (부칙은 '후보'라 제외)
        pool = [r for r in rels if r.confidence == "확실"]
        quota = {"인용": 0.55, "문서 인용·위임": 0.15, "법령 인용": 0.2, "별표·별지 인용": 0.1}
        rnd = random.Random(args.seed)
        picked = []
        for kind, share in quota.items():
            group = [r for r in pool if r.kind == kind]
            picked += rnd.sample(group, min(len(group), round(args.sample * share)))
        audit = []
        for r in picked:
            reg = regs[r.src.split(":")[0]]
            tgt_text = ""
            if r.target_type == "article" and ":" in r.target:
                treg = regs.get(r.target.split(":")[0], {})
                art = next((a for a in treg.get("articles", []) if a["id"] == r.target), None)
                tgt_text = f"{treg.get('name', '')} {art['label']}({art['title']}) {art['norm'][:120]}" if art else ""
            elif r.target_type == "regulation":
                tgt_text = regs.get(r.target, {}).get("name", r.target)
            audit.append({**r.to_dict(), "src_reg_name": reg["name"], "target_text": tgt_text,
                          "label": "", "label_note": ""})
        path = ROOT / "eval" / "relation_audit.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for a in audit:
                f.write(json.dumps(a, ensure_ascii=False) + "\n")
        print(f"표본 {len(audit)}개 → {path} (label: 정확 | 오류 | 애매)")


if __name__ == "__main__":
    main()
