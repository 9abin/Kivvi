"""점검 결과를 finding 한 형식으로 모은다.

  uv run python scripts/build_findings.py            # 인용 엣지(90건) + 의미 후보(있는 범위) + 항번호 중복
출력: out/findings/findings.jsonl
입력: out/edges/edges_all.jsonl, out/semantic/stage2.jsonl(있으면), out/parsed/
점검은 원문(파싱 결과)만 읽는다. 정답 원장(eval/)은 읽지 않는다.
"""

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck import runinfo  # noqa: E402
from regcheck.history import merge, stale_citations  # noqa: E402
from regcheck.lawdb import DEFAULT as LAW_DB, LawDB, law_changes  # noqa: E402
from regcheck.citations import Registry  # noqa: E402
from regcheck.findings import duplicate_articles, duplicate_paragraphs, from_edges, from_semantic  # noqa: E402


def main() -> None:
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    edges = [json.loads(l) for l in (ROOT / "out" / "edges" / "edges_all.jsonl").read_text(encoding="utf-8").splitlines()]
    sem_path = ROOT / "out" / "semantic" / "stage2.jsonl"
    sem = [json.loads(l) for l in sem_path.read_text(encoding="utf-8").splitlines()] if sem_path.exists() else []
    sem_runs = sorted({r["run"] for r in sem if r.get("run")})
    run = runinfo.start("findings", [ROOT / "out" / "edges" / "edges_all.jsonl", sem_path, ROOT / "out" / "parsed",
                                     ROOT / "out" / "relations" / "relations.jsonl"],
                        params={"semantic_runs": sem_runs})
    rel_path = ROOT / "out" / "relations" / "relations.jsonl"
    rels = [json.loads(l) for l in rel_path.read_text(encoding="utf-8").splitlines()]
    found = from_edges(edges) + from_semantic(sem) + duplicate_paragraphs(regs) + duplicate_articles(regs)
    found, hist_stats = merge(found, stale_citations(regs, rels))
    law_stats = {}
    if LAW_DB.exists():   # 한석희 법률 DB가 있으면 법령 개정 대조 (필수 ③)
        law_found, law_stats = law_changes(regs, edges, LawDB(LAW_DB), Registry(list(regs.values())).law_aliases)
        found += law_found
    for n, f in enumerate(found):
        f["fid"] = f"F{n:05d}"
        f["reg_name"] = regs[f["reg_id"]]["name"]
        f["run"] = run["run_id"]
    out = ROOT / "out" / "findings"
    out.mkdir(parents=True, exist_ok=True)
    (out / "findings.jsonl").write_text("\n".join(json.dumps(f, ensure_ascii=False) for f in found) + "\n", encoding="utf-8")
    c = collections.Counter((f["method"], f["type"], f["unit"]) for f in found)
    for k, v in sorted(c.items()):
        print(*k, v, sep="\t")
    print("개정 이력 신호:", hist_stats)
    print("법령 DB:", law_stats or "없음 (data/law_rag.sqlite3)")
    runinfo.finish(run, [out / "findings.jsonl"], {"finding": len(found), "개정 이력": hist_stats, "법령 DB": law_stats, "방법별": dict(collections.Counter(f["method"] for f in found))})
    sem_regs = {r["edge"]["src_reg"] for r in sem}
    print(f"finding {len(found)}개 → {out / 'findings.jsonl'}  (의미 검사 범위: 규정 {len(sem_regs)}개)")


if __name__ == "__main__":
    main()
