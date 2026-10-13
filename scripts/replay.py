"""과거 개정 재현 시연: "그날 에이전트가 있었다면?"

  uv run python scripts/replay.py --reg 급여규정 --date 2025.03.28
현행 규정의 개정 표시로 그날 이전 구조를 되돌리고(번호 이동·신설만), 그날 개정문을 재구성해 에이전트(연쇄 수정 루프)에 넣는다.
실제로 일어난 일(다른 규정이 언제까지 그대로였는지)과 정답 원장을 함께 보여 준다.
출력: out/replay/{규정}_{날짜}/replay.html, package.json
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from agent_fix import PAGE, render  # noqa: E402
from regcheck.agent import build_package  # noqa: E402
from regcheck.amend import build_changeset  # noqa: E402
from regcheck.history import article_events, fmt, regulation_dates  # noqa: E402
from regcheck.lawdb import DEFAULT as LAW_DB, LawDB  # noqa: E402
from regcheck.replay import reconstruct  # noqa: E402


def jl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reg", required=True)
    ap.add_argument("--date", required=True)
    args = ap.parse_args()
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    reg = next((r for r in regs.values() if args.reg in (r["reg_id"], r["name"])), None)
    if not reg:
        sys.exit(f"규정 없음: {args.reg}")
    prior, text, note = reconstruct(reg, args.date)
    if not (note["moved"] or note["inserted"]):
        sys.exit(f"{args.date}에 번호 이동·신설 표시가 없음 (내용 개정만: {note['content_not_replayed']})")
    regs_prior = {**regs, reg["reg_id"]: prior}
    rels = jl(ROOT / "out" / "relations" / "relations.jsonl")
    edges = jl(ROOT / "out" / "edges" / "edges_all.jsonl")
    cid = f"replay-{reg['reg_id']}-{re.sub(r'\D', '', args.date)}"
    cs, _ = build_changeset(prior, text, cid, f"{args.date} 개정 재현")
    pkg = build_package(cs, regs_prior, rels, edges, lawdb=LawDB(LAW_DB) if LAW_DB.exists() else None)

    # 신설 조문을 가리키는 현행 서식·문구(예: 자녀수당 신청서가 새 제21조를 인용)는 그 개정과 함께 생긴 것으로 보여 재현 범위 밖
    new_titles = [a["title"] for a in reg["articles"] if a["label"] in note["inserted"] and a["title"]]
    def unit_text(f):   # 그 인용이 들어 있는 별표·별지 전체 (문맥 조각만 보면 「자녀」처럼 제목어가 빠질 수 있음)
        aid = (f.get("src") or "").split("-")[0]
        ap = next((x for x in reg["appendices"] if x["id"] == aid), None)
        return (ap["title"] + " " + ap["text"]) if ap else f.get("context", "")
    out_of_scope = [f for f in pkg["remaining_breaks"] if any(t in unit_text(f) for t in new_titles)]
    pkg["remaining_breaks"] = [f for f in pkg["remaining_breaks"] if f not in out_of_scope]
    pkg["manual_same"] = [m for m in pkg["manual_same"] if not any(m["quote"] == f["quote"] and m["where"].endswith(f["article"]) for f in out_of_scope)]
    pkg["breaks_before"] = (pkg["breaks_before"] or 0) - len(out_of_scope)
    pkg["breaks_after"] = len(pkg["remaining_breaks"])
    note["out_of_scope"] = [f"{f.get('unit')} {f['article']} 「{f['quote']}」 — 신설 조문({', '.join(new_titles)})을 가리킴, 같은 개정에서 생긴 것으로 보임"
                            for f in out_of_scope]

    # 실제로 일어난 일: 함께 고쳤어야 할 다른 규정 조문이 그 뒤로 언제까지 그대로였나
    reality = []
    for c in pkg["companion"]:
        r2 = regs[c["reg_id"]]
        later = [ad.get("date") for ad in r2["addenda"] if (d := re.findall(r"\d+", ad.get("date") or "")) and len(d) >= 3
                 and (int(d[0]), int(d[1]), int(d[2])) > tuple(int(x) for x in re.findall(r"\d+", args.date)[:3])]
        for line in c["amendment"].splitlines():
            m = re.match(r"(제\d+조(?:의\d+)?)", line)
            art = next((a for a in r2["articles"] if m and a["label"] == m.group(1)), None)
            ev = [d for d, _ in article_events(art)] if art else []
            reality.append({"reg": r2["name"], "fix": line, "article_last_change": fmt(max(ev) if ev else regulation_dates(r2)[0]),
                            "reg_amended_after": later})
    gold = [g for f in ("week1", "gabin_doc", "corrections") if (ROOT / "eval" / "ledger" / f"{f}.jsonl").exists()
            for g in jl(ROOT / "eval" / "ledger" / f"{f}.jsonl")]
    matched = []
    for c in pkg["companion"]:
        for g in gold:
            if g["reg_id"] == c["reg_id"] and g.get("answer") and f'"{g["quote"]}"' in c["amendment"].replace(" ", "") \
                    and f'"{g["answer"]}"' in c["amendment"].replace(" ", ""):
                matched.append({"gold": g["id"], "source": g["source"], "answer": g["answer"], "reg": c["reg_name"]})

    out = ROOT / "out" / "replay" / f"{reg['name']}_{re.sub(r'\D', '', args.date)}"
    out.mkdir(parents=True, exist_ok=True)
    pkg.update({"replay_note": note, "reality": reality, "gold_matched": matched})
    (out / "package.json").write_text(json.dumps(pkg, ensure_ascii=False, indent=1), encoding="utf-8")
    e = html.escape
    head = (f"<h1>과거 재현: {e(args.date)} 「{e(reg['name'])}」 개정</h1>"
            "<div class='card'><b>무엇을 했나</b> — 현행 규정의 개정 표시로 그날 이전 구조를 되돌리고(번호 이동 "
            + e(", ".join(f"{v}→{k}" for k, v in note["moved"].items())) + ", 신설 " + e(", ".join(note["inserted"]))
            + ") 그날 개정문을 재구성해 에이전트에 넣었습니다."
            + (f" 내용 개정({e(', '.join(note['content_not_replayed']))})은 옛 문안이 원문에 없어 재현하지 않았습니다." if note["content_not_replayed"] else "")
            + ("<br><span class='mute'>재현 범위 밖: " + e("; ".join(note["out_of_scope"])) + "</span>" if note.get("out_of_scope") else "")
            + "</div><h2>실제로 일어난 일</h2><table class='l'><tr><th>함께 고쳤어야 할 곳 (에이전트 제안)</th><th>그 조의 마지막 개정</th><th>그 뒤 그 규정의 개정</th></tr>"
            + "".join(f"<tr><td>「{e(x['reg'])}」 {e(x['fix'])}</td><td>{e(x['article_last_change'])}</td>"
                      f"<td>{e(', '.join(x['reg_amended_after']) or '없음')}</td></tr>" for x in reality) + "</table>"
            + ("<div class='card'>정답 원장과 일치: " + e("; ".join(f"{m['gold']}({m['source']}) {m['reg']} → {m['answer']}" for m in matched)) + "</div>" if matched else ""))
    page = render(pkg).replace("<div class=\"wrap\">", "<div class=\"wrap\">" + head + "<hr>", 1)
    (out / "replay.html").write_text(page, encoding="utf-8")
    print(f"재현: {note}")
    print(f"에이전트: 같은 규정 깨짐 {pkg['breaks_before']} → {pkg['breaks_after']}, 함께 개정 {len(pkg['companion'])}개 규정, 수작업 {len(pkg['manual_same'])}")
    for x in reality:
        print(f"  실제: 「{x['reg']}」 {x['fix']}  (그 조 마지막 개정 {x['article_last_change']}, 그 뒤 규정 개정 {x['reg_amended_after'] or '없음'})")
    for m in matched:
        print(f"  정답 일치: {m['gold']} ({m['source']}) {m['reg']} → {m['answer']}")
    print(f"→ {out / 'replay.html'}")


if __name__ == "__main__":
    main()
