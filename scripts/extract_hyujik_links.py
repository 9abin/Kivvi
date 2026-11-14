"""팀의 휴직 온톨로지 DB(외부 파일, 읽기 전용)에서 연결 표만 뽑는다. 규정 원문 · 속성 설명 · 메모는 싣지 않는다 (id · 개념 · 관계 · 속성 · 결정자뿐).
  uv run python scripts/extract_hyujik_links.py <DB 경로> [출력 JSONL, 기본 out/hyujik_links.jsonl]
출력 첫 줄은 meta(원본 DB의 sha256 · 버전 · 개념 위계), 이후 한 줄에 연결 하나. 이 파일은 out/(git 제외)에 두고, 저장소에 넣을지는 작성자와 합의한 뒤 정한다.
항목 id(문서:조-항-호-목)의 앞부분이 우리 조 id와 같아 article 칸이 그대로 우리 단위 id다.
"""
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")


def main():
    db = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "out" / "hyujik_links.jsonl"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    meta = {k: v for k, v in con.execute("select key, value from meta") if k in ("name", "version")}
    meta["sha256"] = hashlib.sha256(db.read_bytes()).hexdigest()
    meta["concepts"] = {n: p for n, p in con.execute("select name, parent from concept")}
    attrs = defaultdict(list)
    for eid, a in con.execute("select edge_id, attr from edge_attr order by rowid"):
        attrs[eid].append(a)
    rows = []
    for eid, item, concept, rel, who in con.execute(
            "select e.id, e.item_id, e.concept, e.rel, e.decided_by from edge_item_concept e order by e.id"):
        art = con.execute("select article_id from item where id = ?", (item,)).fetchone()[0]
        rows.append({"item": item, "article": art, "concept": concept, "rel": rel, "attrs": attrs.get(eid, []), "decided_by": who})
    con.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"meta": meta}, ensure_ascii=False) + "\n")
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    from collections import Counter
    print("연결", len(rows), "| 조", len({r["article"] for r in rows}), "| 결정자", dict(Counter(r["decided_by"] for r in rows)), "| 관계", dict(Counter(r["rel"] for r in rows)))
    print("저장", out, "| 원본 sha256", meta["sha256"][:16])


if __name__ == "__main__":
    main()
