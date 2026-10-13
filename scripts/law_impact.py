"""법령 개정 → 우리 규정 영향 (PoC 필수 ③). 한석희 법률 DB(data/law_rag.sqlite3)를 쓴다.

  uv run python scripts/law_impact.py --law "개인정보 보호법"     # 그 법령의 최근 개정 조문 → 인용하는 우리 조문
  uv run python scripts/law_impact.py --recent 20260701           # 이 날짜 이후 시행된 개정 전체
  uv run python scripts/law_impact.py --missing                   # 우리가 인용하지만 DB에 없는 법령 (한석희 수집 요청용)
출력: out/law_impact/{이름}.json. 판정이 아니라 대조 요청이다 (법령이 바뀌어도 규정을 고칠 필요가 없을 수 있음).
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.citations import Registry  # noqa: E402
from regcheck.findings import locate  # noqa: E402
from regcheck.history import article_events, fmt, regulation_dates  # noqa: E402
from regcheck.lawdb import DEFAULT, RE_ART, LawDB, _d8, amendment_lines  # noqa: E402

OUT = ROOT / "out" / "law_impact"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--law")
    ap.add_argument("--recent")
    ap.add_argument("--missing", action="store_true")
    args = ap.parse_args()
    if not DEFAULT.exists():
        sys.exit(f"법률 DB 없음: {DEFAULT}")
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    edges = [json.loads(l) for l in (ROOT / "out" / "edges" / "edges_all.jsonl").read_text(encoding="utf-8").splitlines()]
    db, aliases = LawDB(DEFAULT), Registry(list(regs.values())).law_aliases
    law_edges = [e for e in edges if e["status"] in ("law_pending", "external_rule") and e["src_kind"] == "본문"]

    if args.missing:
        miss = collections.Counter(e["target_doc"] for e in law_edges if not db.find(e["target_doc"], aliases))
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / "missing_laws.tsv"
        path.write_text("법령명(규정 표기)\t인용 수\n" + "\n".join(f"{k}\t{v}" for k, v in miss.most_common()) + "\n", encoding="utf-8")
        print(f"DB에 없는 법령 {len(miss)}종 (인용 {sum(miss.values())}개) → {path}")
        for k, v in miss.most_common(15):
            print(f"  {v:3} {k}")
        return

    laws = []
    if args.law:
        law = db.find(args.law, aliases)
        if not law:
            sys.exit(f"DB에 없는 법령: {args.law}")
        laws = [law]
    elif args.recent:
        since = _d8(args.recent)
        laws = [l for l in {v["id"]: v for v in db.laws.values()}.values() if (_d8(l["시행일"]) or (0,)) >= since]
    else:
        sys.exit(__doc__)
    arts = {a["id"]: (r, a) for r in regs.values() for a in r["articles"]}
    report = []
    for law in sorted(laws, key=lambda l: l["title"]):
        changed = {k: u for k, u in law["조문"].items() if u.get("조문변경여부") == "Y"}
        cites = [e for e in law_edges if (l2 := db.find(e["target_doc"], aliases)) and l2["id"] == law["id"]]
        rows = []
        for k, u in changed.items():
            hits = []
            for e in cites:
                m = RE_ART.search(e["raw"])
                if not m or m.group(1) + (f"의{m.group(2)}" if m.group(2) else "") != k:
                    continue
                r, a = arts[e["src"].split("-")[0]]
                ev = [d for d, _ in article_events(a)]
                last = max(ev) if ev else regulation_dates(r)[0]
                hits.append({**locate(e["src"]), "reg_name": r["name"], "quote": e["raw"], "context": e["context"],
                             "last_change": fmt(last), "after": bool(last and _d8(u.get("조문시행일자") or law["시행일"]) > last)})
            if hits:
                rows.append({"article": k, "title": u.get("조문제목"), "effective": u.get("조문시행일자") or law["시행일"],
                             "amendment": amendment_lines(law, k), "text": u.get("조문내용", ""), "cited_by": hits})
        report.append({"law": law["title"], "구분": law["구분"], "시행일": law["시행일"], "공포일": law["공포일"],
                       "changed_articles": len(changed), "cited_changed": rows, "cited_total": len(cites)})
    OUT.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w가-힣]+", "_", args.law or f"recent_{args.recent}")
    (OUT / f"{name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for x in report:
        if not x["cited_changed"] and args.recent:
            continue
        print(f"\n■ 「{x['law']}」 {x['구분']} 시행 {fmt(_d8(x['시행일']))} — 바뀐 조문 {x['changed_articles']}개, 우리 인용 {x['cited_total']}개")
        for row in x["cited_changed"]:
            print(f"  ▸ 제{row['article']}조({row['title']}) 시행 {fmt(_d8(row['effective']))}")
            for line in row["amendment"][:2]:
                print(f"      개정문: {line[:110]}")
            for h in row["cited_by"]:
                flag = "대조 필요" if h["after"] else "이미 반영 시점 이후 개정"
                print(f"      ← {h['reg_name']} {h['article']}{' ' + str(h['para']) + '항' if h['para'] else ''} 「{h['quote']}」 "
                      f"(마지막 개정 {h['last_change']}) {flag}")
    print(f"\n→ {OUT / (name + '.json')}")


if __name__ == "__main__":
    main()
