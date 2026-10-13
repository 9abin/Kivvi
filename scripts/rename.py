"""직제 개편 시연: 명칭 변경표 → 90개 규정의 개정문·대비표 한 번에.

  uv run python scripts/rename.py --map 부기관장=전무이사 --map 지역투자센터=지역성장센터 --id reorg-demo
출력: out/changes/{id}/rename.json, rename.html. 자동(개정문), 확인 필요(다른 낱말 속 출현), 수작업(별표·별지·부칙)으로 나눈다.
참고 초안이다. 원문·규정 DB는 바꾸지 않는다.
"""

import argparse
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from agent_fix import PAGE  # noqa: E402
from regcheck import runinfo  # noqa: E402
from regcheck.amend import to_html  # noqa: E402
from regcheck.rename import build  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", action="append", required=True, help="옛명칭=새명칭 (여러 번)")
    ap.add_argument("--id", default="rename")
    args = ap.parse_args()
    mapping = dict(m.split("=", 1) for m in args.map)
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    run = runinfo.start("rename", [ROOT / "out" / "parsed"], params={"mapping": mapping})
    p = build(regs, mapping)
    out = ROOT / "out" / "changes" / args.id
    out.mkdir(parents=True, exist_ok=True)
    (out / "rename.json").write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    e = html.escape
    b = [f"<h1>명칭 일괄 변경 패키지</h1><div class='mute'>{e(', '.join(f'{k} → {v}' for k, v in mapping.items()))} · 참고 초안</div>",
         f"<div class='card'>출현 <b>{p['total']}</b>곳 · 규정 {p['regs_touched']}개 · 자동 개정문 {p['auto']}곳({len(p['packages'])}개 규정) · "
         f"확인 필요 {len(p['check'])} · 수작업 {len(p['manual'])}</div>"]
    if p["check"]:
        b.append("<h2>확인 필요 — 다른 낱말 속 출현 (자동으로 바꾸지 않음)</h2><table class='l'><tr><th>위치</th><th>낱말</th><th>문맥</th></tr>"
                 + "".join(f"<tr><td>{e(c['reg_name'])} {e(c['article'])}{' ' + str(c['para']) + '항' if c['para'] else ''}</td>"
                           f"<td>{e(c['token'])}</td><td>{e(c['context'])}</td></tr>" for c in p["check"]) + "</table>")
    if p["manual"]:
        b.append("<h2>수작업 — 별표·별지·부칙</h2><table class='l'><tr><th>위치</th><th>문맥</th></tr>"
                 + "".join(f"<tr><td>{e(m['reg_name'])} {e(m['unit'])} {e(m['article'])}</td><td>{e(m['context'])}</td></tr>" for m in p["manual"]) + "</table>")
    b.append("<h2>규정별 개정문과 대비표</h2>")
    for x in p["packages"]:
        b.append(f"<div class='card'><b>「{e(x['reg_name'])}」</b> {x['count']}곳 · <span class='mute'>{e(x['status'])}"
                 + (f", 적용 후 남은 옛 명칭 {x['left_after']}" if 'left_after' in x else "") + "</span>"
                 + f"<pre>{e(x['amendment'])}</pre>" + (f"<details><summary>대비표</summary><div class='tw'>{to_html(x['rows'], '')}</div></details>" if x.get("rows") else "")
                 + "</div>")
    (out / "rename.html").write_text(PAGE.replace("__BODY__", "".join(b)), encoding="utf-8")
    runinfo.finish(run, [out / "rename.json"], {k: p[k] for k in ("total", "auto", "regs_touched")})
    print(f"출현 {p['total']}곳(규정 {p['regs_touched']}개): 자동 {p['auto']}곳 → {len(p['packages'])}개 규정 개정문, "
          f"확인 필요 {len(p['check'])}, 수작업 {len(p['manual'])}, 적용 실패 {sum(x['status'] != 'ok' for x in p['packages'])}")
    print(f"→ {out / 'rename.html'}")


if __name__ == "__main__":
    main()
