"""한석희 한 곳 변경 60문항(eval/four_domain/single_change)의 inputs.jsonl에서 개정 항목을 읽어 120문항과 같은 형식(out/four_domain_single_ops.json)으로 바꾼다.
  uv run python scripts/four_domain_ops_from_inputs.py <single_change 폴더>
op 종류: replace / rewrite / replace_text(문구 치환, 띄어쓰기 수정 포함) -> type "replace", renumber(조 번호 이동) -> type "move"(mode "state": 개정 전후 라벨 상태로 판정).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")


def main():
    d = Path(sys.argv[1])
    base = {r["id"]: r for r in (json.loads(l) for l in (d / "base_corpus.jsonl").read_text(encoding="utf-8").splitlines() if l.strip())}
    out = {}
    for line in (d / "inputs.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        x = json.loads(line); q = x["id"]; ops = []
        for o in x["amendment_ops"]:
            if o["op"] == "renumber":
                a = o["logical_article_id"]; b = base.get(a, {})
                ops.append({"type": "move", "mode": "state", "doc": b.get("document", ""), "label": o["old_label"], "id": a, "article": a, "to": o["new_label"]})
            else:
                iid = o.get("logical_item_id") or o.get("logical_unit_id")
                art = iid.split("-")[0]; b = base.get(art, {})
                ops.append({"type": "replace", "doc": b.get("document", ""), "label": b.get("label", ""), "id": iid, "article": art,
                            "old": o.get("old") or o.get("expected_old") or "", "new": o["new"], "op": o["op"]})
        out[q] = ops
    p = ROOT / "out" / "four_domain_single_ops.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print("문항", len(out), "| 개정 항목", sum(len(v) for v in out.values()), dict(Counter(o["type"] for v in out.values() for o in v)))


if __name__ == "__main__":
    main()
