"""LLM 인용 검토: 본문의 조 인용마다 「[인용] 자리에 가장 알맞은 조」를 LLM이 대상 규정의 조 목록에서 고르게 하고,
실제로 인용한 조와 다르면 후보로 낸다 (리포트 57, 선택형). 규칙이 못 잡는 「있는 조인데 엉뚱한 조를 가리킴」을 찾는다.

  uv run python scripts/llm_cite_review.py --model api:gpt-5.6-luna [--workers 4]
  uv run python scripts/llm_cite_review.py --model qwen3-30b-a3b-2507-q4km-ctx12k --workers 1
결과: out/llm_cite_review/{모델}.jsonl (인용마다 고른 조 · 둘째 후보 · 이유 · 후보 여부), 화면에 정답 원장 대조
정답 원장: eval/ledger/*.jsonl 의 「인용_대상불일치」(확정된 실제 오류). 재현율은 원장 기준, 정밀도는 원장에 없는 후보를 사람이 봐야 안다.
API는 공개 규정(ALIO) 문장만 보낸다.
"""

import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_inject_llm import PROMPT_CHOOSE, ask_api, ask_local  # noqa: E402
from regcheck.relocate import citing_text  # noqa: E402
from semantic_check import article_text, load_regs  # noqa: E402

LABEL = re.compile(r"제\s*\d+\s*조(?:\s*의\s*\d+)?")


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="api:gpt-5.6-luna")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    from regcheck.db import load_env
    os.environ.setdefault("OPENAI_API_KEY", load_env().get("OPENAI_API_KEY", "").strip('"'))
    regs = load_regs()
    edges = [json.loads(l) for l in (ROOT / "out" / "edges" / "edges_all.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    pool = [e for e in edges if e["status"] == "ok" and e["target_type"] == "article" and e["src_kind"] == "본문" and e.get("target_reg") in regs]
    if a.limit:
        pool = pool[:a.limit]
    arts = {x["id"]: (r, x) for r in regs.values() for x in r["articles"]}
    api = a.model.startswith("api:")
    name = a.model.split(":", 1)[1] if api else a.model

    def one(e):
        sent = citing_text(regs, e)
        if "[인용]" not in sent:
            return {"src": e["src"], "skip": "인용 표현을 문장에서 못 찾음"}
        tgt = regs[e["target_reg"]]
        src_article = e["src"].rsplit("-", 3)[0]
        lines = [f"{x['label']}({x['title']}) {article_text(x, 90)[len(x['label']) + len(x['title']) + 3:]}"
                 for x in tgt["articles"] if not x["deleted"] and x["id"] != src_article]
        user = f"문장: {sent}\n\n「{tgt['name']}」 조문 목록:\n" + "\n".join(lines)
        t = time.time()
        try:
            raw = (ask_api if api else ask_local)(name, user, PROMPT_CHOOSE)
            js = json.loads(raw)
        except Exception as ex:
            return {"src": e["src"], "error": repr(ex)[:120]}
        first = [norm(m.group(0)) for m in LABEL.finditer(str(js.get("고른 조", "")))]
        second = [norm(m.group(0)) for m in LABEL.finditer(str(js.get("둘째 후보", "")))]
        cited = norm(arts[e["target_id"]][1]["label"])
        return {"src": e["src"], "src_reg": e["src_reg"], "src_name": e["src_name"], "raw": e["raw"], "target_reg": e["target_reg"],
                "target_doc": tgt["name"], "cited": cited, "cited_title": arts[e["target_id"]][1]["title"], "sent": sent,
                "pick": first[:1], "second": second[:1], "why": str(js.get("이유", ""))[:200],
                "flag": bool(first) and cited not in set(first[:1] + second[:1]), "sec": round(time.time() - t, 2)}

    t0 = time.time()
    with cf.ThreadPoolExecutor(a.workers if api else 1) as ex:
        rows = list(ex.map(one, pool))
    out = ROOT / "out" / "llm_cite_review"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{re.sub(r'[^\w.-]', '_', a.model)}.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    # 정답 원장 대조: 「인용_대상불일치」 (있는 조인데 엉뚱한 조) — 출발 조 · 인용 표현으로 맞춘다
    ledger = {}
    for f in (ROOT / "eval" / "ledger").glob("*.jsonl"):
        for l in f.read_text(encoding="utf-8").splitlines():
            if l.strip() and (g := json.loads(l))["type"] == "인용_대상불일치" and g.get("unit") == "본문":
                ledger.setdefault((g["reg_id"], norm(g["article"]), norm(g["quote"])), g)
    names = {aid: x["label"] for aid, (_, x) in arts.items()}
    ok = [r for r in rows if "flag" in r]
    flagged = [r for r in ok if r["flag"]]
    def key(r):
        art = names.get(r["src"].rsplit("-", 3)[0], "")
        return (r["src_reg"], norm(art), norm(r["raw"]))
    in_pool = {k for k in ledger if any(key(r)[:2] == k[:2] and k[2] in key(r)[2] for r in ok)}
    hit = {k for k in in_pool if any(key(r)[:2] == k[:2] and k[2] in key(r)[2] and r["flag"] for r in ok)}
    print(f"모델 {a.model} · 인용 {len(pool)}개 · 판정 {len(ok)} · 오류 {sum('error' in r for r in rows)} · 건너뜀 {sum('skip' in r for r in rows)} · {time.time() - t0:.0f}초")
    print(f"후보(인용한 조 ≠ 고른 조): {len(flagged)}개 ({len(flagged) / max(len(ok), 1):.0%})")
    print(f"정답 원장 「인용_대상불일치」(본문) {len(ledger)}건 중 이 검토 대상에 든 것 {len(in_pool)} → 후보로 잡음 {len(hit)}")
    for k in sorted(in_pool - hit):
        print(f"  놓침: {ledger[k]['id']} {k}")
    print(f"→ {path}")


if __name__ == "__main__":
    main()
