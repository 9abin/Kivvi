"""한석희의 인사 · 보수 · 휴가 · 휴직 120문항(questions.md, codex/four-domain-ontology-eval-20261106)을 개정 항목으로 파싱한다.
  uv run python scripts/four_domain_parse.py <questions.md> [출력 JSON]
항목: {"type": "replace", "doc", "label", "id"(항목 또는 조 id), "article"(조 id), "old", "new"} 또는 {"type": "move", "doc", "label", "id", "article", "to"}.
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
LINE = re.compile(r'^(?P<n>\d+)\. (?P<doc>.+?) (?P<label>제\d+조(?:의\d+)?|별지제\d+호 ?서식|별표\d+) \((?P<id>[^)]+)\)(?P<rest>.*)$')
REPL = re.compile(r'^의 "(?P<old>.+?)"를 "(?P<new>.+?)"로 바꾼다\.')
MOVE = re.compile(r'^를 (?P<to>제\d+조(?:의\d+)?|별표\d+)(?:로|으로) 이동한다\.')


def parse(path):
    cases, cur = {}, None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^## (Q\d{3})$", line)
        if m:
            cur = cases.setdefault(m.group(1), [])
            continue
        m = LINE.match(line)
        if m and cur is not None:
            rest = m.group("rest")
            art = m.group("id").split("-")[0]
            r = REPL.match(rest)
            if r:
                cur.append({"type": "replace", "doc": m.group("doc"), "label": m.group("label"), "id": m.group("id"), "article": art, "old": r.group("old"), "new": r.group("new")})
                continue
            mv = MOVE.match(rest)
            if mv:
                cur.append({"type": "move", "doc": m.group("doc"), "label": m.group("label"), "id": m.group("id"), "article": art, "to": mv.group("to")})
                continue
            cur.append({"type": "unparsed", "line": line})
    return cases


if __name__ == "__main__":
    c = parse(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("out/four_domain_ops.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    t = Counter(o["type"] for v in c.values() for o in v)
    print("문항", len(c), "| 개정 항목", sum(len(v) for v in c.values()), dict(t))
    print("문항당 항목 수", dict(sorted(Counter(len(v) for v in c.values()).items())))
