"""개정문을 규정 사본에 적용하고 신·구조문대비표를 만든다 (원문은 바꾸지 않음).

  uv run python scripts/amend.py --scenario S01                       # eval/scenarios/S01.json의 개정문
  uv run python scripts/amend.py --reg 인사규정 --text "제37조를 삭제한다."
  uv run python scripts/amend.py --reg 24024 --file 개정문.txt --id C001
출력: out/changes/{id}/changeset.json (적용 결과·조 번호 대응·대비표 행), compare.html (대비표 한 장)
해석 못 하는 문장이나 없는 문구는 추측하지 않고 이유를 보여 주며 멈춘다.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck import runinfo  # noqa: E402
from regcheck.amend import AmendError, apply, compare_table, label_map, parse_amendment, to_html, to_text  # noqa: E402

PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>신·구조문대비표</title><style>
:root{--bg:#fff;--fg:#1d1d1f;--mute:#6b6b70;--line:#bbb}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#1a1a1c;--fg:#ececee;--mute:#9a9aa2;--line:#555}}
:root[data-theme="dark"]{--bg:#1a1a1c;--fg:#ececee;--mute:#9a9aa2;--line:#555}
body{background:var(--bg);color:var(--fg);font:14px/1.7 "Malgun Gothic",sans-serif;margin:0;padding:20px 16px}
.cmp{border-collapse:collapse;width:100%;max-width:1100px;table-layout:fixed}.cmp caption{font-size:18px;font-weight:700;margin-bottom:10px}
.cmp th,.cmp td{border:1px solid var(--line);padding:8px 10px;vertical-align:top;width:50%}.cmp th{letter-spacing:.3em}
u{text-decoration-thickness:2px;text-underline-offset:3px}.note{color:var(--mute);font-size:13px;max-width:1100px}
</style></head><body>__BODY__</body></html>"""


def find_reg(key: str) -> dict:
    for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"):
        r = json.loads(p.read_text(encoding="utf-8"))
        if key in (r["reg_id"], r["name"]):
            return r
    sys.exit(f"규정 없음: {key}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario")
    ap.add_argument("--reg")
    ap.add_argument("--text")
    ap.add_argument("--file")
    ap.add_argument("--id")
    args = ap.parse_args()
    if args.scenario:
        sc = json.loads((ROOT / "eval" / "scenarios" / f"{args.scenario}.json").read_text(encoding="utf-8"))
        reg, text, cid = find_reg(sc["change"]["target"]["reg_id"]), sc["change"]["amendment"], args.id or sc["id"]
        title = sc["title"]
    else:
        if not args.reg or not (args.text or args.file):
            sys.exit(__doc__)
        reg = find_reg(args.reg)
        text = args.text or Path(args.file).read_text(encoding="utf-8")
        cid, title = args.id or "draft", ""
    run = runinfo.start("amend", [ROOT / "out" / "parsed" / f"{reg['reg_id']}.json"], params={"change": cid})
    try:
        ops = parse_amendment(text)
        new, changes = apply(reg, ops)
    except AmendError as e:
        sys.exit(f"적용 못함 — {e}")
    rows = compare_table(reg, new, changes)
    out = ROOT / "out" / "changes" / cid
    out.mkdir(parents=True, exist_ok=True)
    cs = {"id": cid, "title": title, "reg_id": reg["reg_id"], "reg_name": reg["name"], "amendment": text,
          "sentences": [o.sentence for o in ops], "changes": changes, "label_map": label_map(changes),
          "rows": rows, "run": run["run_id"]}
    (out / "changeset.json").write_text(json.dumps(cs, ensure_ascii=False, indent=1), encoding="utf-8")
    note = ('<p class="note">참고 초안입니다. 원문은 바꾸지 않았고, 승인은 개정이 아닙니다 (공포된 개정본의 시행일에만 규정이 바뀝니다).'
            ' 현행 문안은 개정 표시(&lt;개정 …&gt;)를 뺀 본문입니다.</p>')
    html = PAGE.replace("__BODY__", to_html(rows, f"「{reg['name']}」 신·구조문대비표" + (f" — {title}" if title else "")) + note)
    (out / "compare.html").write_text(html, encoding="utf-8")
    runinfo.finish(run, [out / "changeset.json"], {"조문": len(rows), "번호 변경·삭제": len(cs["label_map"])})
    print(to_text(rows) if len(rows) <= 4 else to_text(rows[:3]) + f"\n  … 외 {len(rows) - 3}개 조문")
    print(f"\n조문 {len(rows)}개 변경 → {out / 'compare.html'}")


if __name__ == "__main__":
    main()
