"""개정안 작성 에이전트(규칙 루프) 실행: 개정문 하나 → 연쇄 수정까지 담은 개정 패키지.

  uv run python scripts/agent_fix.py --change S03                         # out/changes/S03/changeset.json
  uv run python scripts/agent_fix.py --reg 직제규정 --text "…" --id C001  # 새 개정문
출력: out/changes/{id}/package.json, package.html (개정문 최종본·대비표·함께 개정안·부칙 초안·질의서·법령 대조)
참고 초안이다. 원문·규정 DB는 바꾸지 않는다.
"""

import argparse
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck import runinfo  # noqa: E402
from regcheck.agent import build_package  # noqa: E402
from regcheck.amend import AmendError, build_changeset, to_html  # noqa: E402
from regcheck.lawdb import DEFAULT as LAW_DB, LawDB  # noqa: E402

PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>개정 패키지</title><style>
:root{--bg:#fff;--fg:#1d1d1f;--mute:#6b6b70;--line:#c8c8c8;--acc:#2f5bd3;--card:#f6f6f4}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#18181a;--fg:#ececee;--mute:#9a9aa2;--line:#45454b;--acc:#7c9cff;--card:#222226}}
:root[data-theme="dark"]{--bg:#18181a;--fg:#ececee;--mute:#9a9aa2;--line:#45454b;--acc:#7c9cff;--card:#222226}
body{background:var(--bg);color:var(--fg);font:14px/1.7 "Malgun Gothic",sans-serif;margin:0}
.wrap{max-width:1100px;margin:0 auto;padding:20px 16px 60px}h1{font-size:21px;margin:0 0 4px}h2{font-size:16px;margin:26px 0 8px}
.mute{color:var(--mute)}.card{background:var(--card);border-radius:8px;padding:10px 12px;margin:8px 0}
pre{white-space:pre-wrap;background:var(--card);border-radius:8px;padding:10px 12px;font:13px/1.7 "Malgun Gothic",monospace;margin:6px 0}
.cmp{border-collapse:collapse;width:100%;table-layout:fixed}.cmp caption{display:none}.cmp th,.cmp td{border:1px solid var(--line);padding:6px 9px;vertical-align:top;width:50%}
.cmp th{letter-spacing:.3em}u{text-decoration-thickness:2px;text-underline-offset:3px;text-decoration-color:var(--acc)}
.add{color:var(--acc);font-weight:600}table.l{border-collapse:collapse;width:100%}table.l td,table.l th{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}
.tw{overflow-x:auto}
</style></head><body><div class="wrap">__BODY__</div></body></html>"""


def esc(s):
    return html.escape(str(s or ""))


def render(p: dict) -> str:
    b = [f"<h1>「{esc(p['reg_name'])}」 개정 패키지</h1>",
         f"<div class='mute'>{esc(p['request'] or p['id'])} · 참고 초안 — 원문은 바뀌지 않았고, 승인은 개정이 아닙니다.</div>",
         f"<div class='card'>같은 규정 안 깨진 인용 <b>{p['breaks_before']} → {p['breaks_after']}</b> · 추가한 개정문 {len(p['added_sentences'])}문장 · "
         f"함께 개정할 다른 규정 {len(p['companion'])}개 · 판단 필요 {len(p['questions'])}건 · 법령 대조 {len(p['law_check'])}건</div>",
         "<h2>1. 개정문 (최종)</h2><pre>" + esc(p["amendment_input"])
         + "".join(f"\n<span class='add'>{esc(s)}</span>" for s in p["added_sentences"]) + "</pre>"
         + ("<div class='mute'>파란 글씨 = 에이전트가 연쇄 수정으로 추가한 문장</div>" if p["added_sentences"] else ""),
         "<h2>2. 신·구조문대비표</h2><div class='tw'>" + to_html(p["rows"], "") + "</div>"]
    if p["companion"] or p["addendum_draft"]:
        b.append("<h2>3. 다른 규정 — 둘 중 하나를 고릅니다</h2>")
        if p["addendum_draft"]:
            b.append("<b>㉮ 이 개정안 부칙에 관계 조항을 둔다</b><pre>" + esc(p["addendum_draft"]) + "</pre>")
        if p["companion"]:
            b.append("<b>㉯ 인용하는 규정을 함께 개정한다</b>")
            for c in p["companion"]:
                b.append(f"<div class='card'><b>「{esc(c['reg_name'])}」</b> <span class='mute'>{esc(c['status'])}</span>"
                         + (f"<pre>{esc(c['amendment'])}</pre>" if c["amendment"] else "")
                         + ("".join(f"<div class='mute'>수작업: {esc(m['where'])} 「{esc(m['quote'])}」 → 「{esc(m['suggest'])}」</div>" for m in c.get("manual", [])))
                         + (f"<div class='tw'>{to_html(c['rows'], '')}</div>" if c.get("rows") else "") + "</div>")
    if p["questions"]:
        b.append("<h2>4. 판단이 필요한 것 (질의서)</h2><table class='l'><tr><th>위치</th><th>문구</th><th>질문</th></tr>"
                 + "".join(f"<tr><td>{esc(q['where'])}</td><td>{esc(q['quote'])}</td><td>{esc(q['question'])}</td></tr>" for q in p["questions"]) + "</table>")
    if p["law_check"]:
        b.append("<h2>5. 법령 대조</h2><table class='l'><tr><th>법령</th><th>이유</th></tr>"
                 + "".join(f"<tr><td>{esc(x.get('quote'))}</td><td>{esc(x['reason'])}</td></tr>" for x in p["law_check"]) + "</table>")
    if p["review"]:
        b.append(f"<h2>6. 확인 필요·해소 후보 ({len(p['review'])})</h2><table class='l'><tr><th>위치</th><th>문구</th><th>이유</th></tr>"
                 + "".join(f"<tr><td>{esc(x['reg_name'])} {esc(x.get('article'))}</td><td>{esc(x.get('quote'))}</td><td>{esc(x['reason'])}</td></tr>"
                           for x in p["review"][:40]) + "</table>")
    b.append("<h2>진행 기록</h2><pre>" + esc("\n".join(
        f"{r['round']}회차: 깨짐 {r.get('breaks', '-')} · 고칠 수 있음 {r.get('fixable', '-')} · 추가 {len(r.get('added', []))}" + (f" · 오류 {r['error']}" if r.get("error") else "")
        for r in p["rounds"])) + "</pre>")
    return PAGE.replace("__BODY__", "".join(b))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--change")
    ap.add_argument("--reg")
    ap.add_argument("--text")
    ap.add_argument("--id", default="draft")
    args = ap.parse_args()
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    rels = [json.loads(l) for l in (ROOT / "out" / "relations" / "relations.jsonl").read_text(encoding="utf-8").splitlines()]
    edges = [json.loads(l) for l in (ROOT / "out" / "edges" / "edges_all.jsonl").read_text(encoding="utf-8").splitlines()]
    if args.change:
        cid = args.change
        cs = json.loads((ROOT / "out" / "changes" / cid / "changeset.json").read_text(encoding="utf-8"))
    elif args.reg and args.text:
        reg = next((r for r in regs.values() if args.reg in (r["reg_id"], r["name"])), None)
        if not reg:
            sys.exit(f"규정 없음: {args.reg}")
        cid = args.id
        try:
            cs, _ = build_changeset(reg, args.text, cid)
        except AmendError as e:
            sys.exit(f"적용 못함 — {e}")
    else:
        sys.exit(__doc__)
    lawdb = LawDB(LAW_DB) if LAW_DB.exists() else None
    run = runinfo.start("agent", [ROOT / "out" / "relations" / "relations.jsonl", ROOT / "out" / "parsed"],
                        models={"law_db": LAW_DB.name} if lawdb else {}, params={"change": cid})
    pkg = build_package(cs, regs, rels, edges, lawdb=lawdb)
    pkg["run"] = run["run_id"]
    out = ROOT / "out" / "changes" / cid
    out.mkdir(parents=True, exist_ok=True)
    (out / "package.json").write_text(json.dumps(pkg, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "package.html").write_text(render(pkg), encoding="utf-8")
    runinfo.finish(run, [out / "package.json"], {"깨짐": [pkg["breaks_before"], pkg["breaks_after"]], "추가 문장": len(pkg["added_sentences"]),
                                                 "함께 개정": len(pkg["companion"]), "질의": len(pkg["questions"])})
    print(f"「{pkg['reg_name']}」 깨짐 {pkg['breaks_before']} → {pkg['breaks_after']}, 추가 문장 {len(pkg['added_sentences'])}, "
          f"함께 개정 {len(pkg['companion'])}개 규정, 질의 {len(pkg['questions'])}, 법령 대조 {len(pkg['law_check'])}")
    print(f"→ {out / 'package.html'}")


if __name__ == "__main__":
    main()
