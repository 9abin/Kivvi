"""값 불일치 판정을 판단 모델에 맡기는 실험의 1단계: 후보 쌍과 질문 문맥을 만든다 (프로젝트 환경).
  FD_TAG=_single uv run python scripts/judge_decide_build.py <gold.jsonl> <single_change 폴더> <base_corpus.jsonl>
규칙 판정(judge_four_domain.py)과 같은 대상만 뽑는다: 숫자가 바뀌는 치환 개정 항목 x 그 항목의 1단계 후보. 출력 out/fd{TAG}_decide_pairs.json
"""
import collections
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG = os.environ.get("FD_TAG", "")
NUM = r"\d+(?:[.,]\d+)?(?:/\d+)?"
sl = lambda s: re.sub(r"\s+", "", s or "")  # noqa: E731
jl = lambda p: [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]  # noqa: E731


def main():
    gold = {x["id"]: x for x in jl(sys.argv[1])}
    cases = Path(sys.argv[2]); base = {r["id"]: r for r in jl(sys.argv[3])}
    ops = json.loads((ROOT / f"out/four_domain{TAG}_ops.json").read_text(encoding="utf-8"))
    st1 = json.loads((ROOT / f"out/four_domain{TAG}_stage1.json").read_text(encoding="utf-8"))["cache"]
    out = []
    for q, g in gold.items():
        def ov(which):
            p = cases / "cases" / q / f"{which}_overlay.jsonl"
            return {r["id"]: r for r in jl(p)} if p.exists() else {}
        bo, ao = ov("before"), ov("after")
        raw_b = lambda u: (bo.get(u) or base.get(u) or {}).get("text", "")  # noqa: E731
        raw_a = lambda u: (ao.get(u) or bo.get(u) or base.get(u) or {}).get("text", "")  # noqa: E731
        truth = {}
        for state in ("new", "resolved"):
            for iid in g["expected_delta"][state]:
                f = (g["after_facts"] if state == "new" else g["before_facts"])[iid]
                if f["kind"] == "value_mismatch":
                    truth[tuple(sorted(x.split("-")[0] for x in (f["left"]["logical_item_id"], f["right"]["logical_item_id"])))] = state
        for k, op in enumerate(ops[q]):
            if op["type"] != "replace" or re.search(r"제\s*\d+\s*조|별표\s*\d+", op["old"]):
                continue
            an, bn = re.findall(NUM, op["old"]), re.findall(NUM, op["new"])
            if not an or an == bn:
                continue
            U = op["article"]; ut = raw_b(U); i = ut.find(op["old"].strip())
            ctx = ut[max(0, i - 60): i + len(op["old"]) + 60] if i >= 0 else op["old"]
            for P in (st1.get(f"{q}-{k}") or {}).get("all", []):
                if P == U or P not in base:
                    continue
                key = tuple(sorted((U, P)))
                out.append({"q": q, "k": k, "U": U, "P": P, "truth": truth.get(key, "none"), "scenario": g["scenario_type"],
                            "state": f"[개정 항목]\n{base.get(U, {}).get('document', '')} {base.get(U, {}).get('label', '')}: ...{ctx}...\n개정 전 문구: {op['old']}\n개정 후 문구: {op['new']}\n\n[확인 대상 조문]\n{base[P].get('document', '')} {base[P].get('label', '')}\n{raw_b(P)[:1000]}"})
    p = ROOT / f"out/fd{TAG}_decide_pairs.json"
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("후보 쌍", len(out), dict(collections.Counter(x["truth"] for x in out)), "문항", len({x["q"] for x in out}))


if __name__ == "__main__":
    main()
