"""이동 표지 파싱: 원문에 남은 「[종전 제28조는 제29조로 이동 <2025.05.30.>]」에서 옛 번호 → 새 번호 표를 만들고,
옛 번호를 그대로 인용하는 곳을 후보로 올린다. 이력이 필요한 점검이다 (현재 원문만으로는 못 한다).

  parse_moves(regs)                              [{reg_id, old, new, kind, date, where, mark}]
  stale_by_moves(regs, moves, rels)              옛 번호를 인용하고, 그 뒤로 인용하는 조가 개정되지 않은 곳 → 새 번호를 제안

history.stale_citations와 겹친다: 그쪽은 「대상 조에 이동 표시가 있다」는 신호만 보고, 여기는 옛 → 새 번호 표로 **고칠 번호까지** 제시한다.
같은 번호에 새 내용이 들어온 경우(대상 조가 남아 있음)도 잡는다. 날짜가 없는 표지는 건너뛴다 (인용 쪽이 이후에 고쳐졌는지 알 수 없음).
판정이 아니라 후보다 (AGENTS §3).
"""

from __future__ import annotations

import re

from .history import RE_DATE, _d, article_events

_NUM = r"제\s*(\d+)\s*조(?:의\s*(\d+))?"
RE_MARK = re.compile(r"\[[^\[\]]*?(?:이동|번호\s*변경|삭제)[^\[\]]*?\]", re.S)
RE_FROM = re.compile(rf"(?:종전\s*)?{_NUM}(?!\s*(?:제\s*\d+\s*항|[②③④⑤])|\s*는)\s*에서\s*(?:이?\s*)?이동")      # 제N조에서 이동 (이 조가 옛 N조)
RE_TO = re.compile(rf"종전\s*{_NUM}\s*는\s*{_NUM}(?!\s*제\s*\d+\s*항)\s*(?:로|으로)\s*(?:조문)?\s*이동")                    # 종전 제N조는 제M조로 이동
RE_DEL = re.compile(rf"종전\s*{_NUM}\s*는\s*삭제")
RE_LIST = re.compile(r"종전\s*(.+?)\s*를\s*(.+?)\s*로\s*조문번호\s*변경")                                                  # 종전 제2조의1, 제3조 … 를 제4조 … 로 조문번호 변경


def key(m: str | None, sub: str | None) -> str:
    """조 번호 → article_id 접미사 (제22조의3 → 22의3)."""
    return f"{int(m)}의{int(sub)}" if sub else f"{int(m)}"


def label(k: str) -> str:
    return "제" + k.replace("의", "조의") if "의" in k else f"제{k}조"


def _nums(s: str) -> list[str]:
    return [key(m.group(1), m.group(2)) for m in re.finditer(_NUM, s)]


def _marks(a: dict) -> list[str]:
    texts = list(a.get("amend_notes", [])) + [m for it in a["items"] for m in it.get("amend_marks", [])]
    texts.append(a.get("text", ""))
    seen, out = set(), []
    for t in texts:
        for m in RE_MARK.finditer(t):
            s = re.sub(r"\s+", " ", m.group(0))
            if s not in seen:
                seen.add(s)
                out.append(s)
    return out


def parse_moves(regs: dict[str, dict]) -> list[dict]:
    rows = []
    for reg in regs.values():
        for a in reg["articles"]:
            here = a["id"].split(":", 1)[1]
            for s in _marks(a):
                d = _d(s)
                date = f"{d[0]}.{d[1]:02d}.{d[2]:02d}" if d else ""
                base = {"reg_id": reg["reg_id"], "reg_name": reg["name"], "date": date, "where": a["label"], "mark": s}
                handled = False
                m = RE_LIST.search(s)
                if m:
                    olds, news = _nums(m.group(1)), _nums(m.group(2))
                    if len(olds) == len(news):
                        rows += [{**base, "old": o, "new": n, "kind": "번호변경"} for o, n in zip(olds, news) if o != n]
                        handled = True
                if handled:
                    continue
                for m in RE_TO.finditer(s):
                    rows.append({**base, "old": key(m.group(1), m.group(2)), "new": key(m.group(3), m.group(4)), "kind": "이동"})
                for m in RE_DEL.finditer(s):
                    rows.append({**base, "old": key(m.group(1), m.group(2)), "new": "", "kind": "삭제"})
                tail = RE_TO.sub("", RE_DEL.sub("", s))
                for m in RE_FROM.finditer(tail):
                    o = key(m.group(1), m.group(2))
                    if o != here:
                        rows.append({**base, "old": o, "new": here, "kind": "이동"})
    # 같은 이동이 조·항 표지에 여러 번 나옴 → 한 줄로
    uniq = {}
    for r in rows:
        uniq.setdefault((r["reg_id"], r["old"], r["new"], r["kind"], r["date"]), r)
    return list(uniq.values())


def stale_by_moves(regs: dict[str, dict], moves: list[dict], rels: list[dict]) -> list[dict]:
    by_target = {}
    for mv in moves:
        if mv["date"]:
            by_target.setdefault(f"{mv['reg_id']}:{mv['old']}", []).append(mv)
    arts = {a["id"]: (reg, a) for reg in regs.values() for a in reg["articles"]}
    out = []
    for r in rels:
        if r.get("kind") != "인용" or r.get("target_type") != "article" or r.get("src_kind") != "본문":
            continue
        mvs = by_target.get(r["target"])
        if not mvs or r["src_article"] not in arts:
            continue
        reg, a = arts[r["src_article"]]
        last = max((d for d, _ in article_events(a)), default=None)
        for mv in mvs:
            md = _d(mv["date"])
            if last and last >= md:    # 인용하는 조가 이동 뒤에 고쳐졌으면 이미 따라갔을 수 있음
                continue
            if mv["new"] == mv["old"]:
                continue
            fix = f"제{mv['new']}조로 이동" if mv["new"] else "삭제됨"
            tgt = f"{mv['reg_name']} {label(mv['old'])}"
            out.append({"category": "review", "reg_id": reg["reg_id"], "reg_name": reg["name"], "unit": "본문", "article": a["label"],
                        "para": int(r["src"].split(":")[1].split("-")[1]) if "-" in r["src"] else 0, "quote": r.get("evidence", ""),
                        "context": r.get("context", ""), "path": "이동 표지", "rule": "moved_citation", "target": tgt, "handling": "판단 가능",
                        "priority": "높음" if mv["kind"] == "삭제" else "중간", "suggest": mv["new"],
                        "reason": f"{tgt}는 {mv['date']}에 {fix}({mv['kind']}). 이 조는 그 뒤 개정 표시가 없음 → 옛 번호를 그대로 인용하는지 확인"
                        + (f" (새 번호 {label(mv['new'])})" if mv["new"] else ""),
                        "move": {k: mv[k] for k in ("old", "new", "kind", "date", "mark")},
                        "src": r["src"], "target_id": r["target"], "new_id": f"{mv['reg_id']}:{mv['new']}" if mv["new"] else ""})
    return out


def move_conflicts(moves: list[dict]) -> list[dict]:
    """이동표 자체의 모순: 서로 다른 옛 번호가 같은 새 번호로 가거나, 한 옛 번호가 두 곳으로 감 (원문 표기 오류 후보)."""
    out, seen = [], set()
    for side, other, msg in (("new", "old", "옛 {a}, {b}가 모두 {k}로 이동"), ("old", "new", "옛 {k}이 {a}, {b} 두 곳으로 이동")):
        groups: dict[tuple, list[dict]] = {}
        for mv in moves:
            if mv[side] and mv["kind"] != "삭제":
                groups.setdefault((mv["reg_id"], mv["date"], mv[side]), []).append(mv)
        for (rid, date, k), g in groups.items():
            vals = sorted({x[other] for x in g}, key=lambda v: (len(v), v))
            if len(vals) > 1 and (rid, date, side, k) not in seen:
                seen.add((rid, date, side, k))
                lab = [label(v) for v in vals]
                out.append({"category": "review", "reg_id": rid, "reg_name": g[0]["reg_name"], "unit": "본문", "article": g[0]["where"], "para": 0,
                            "quote": g[0]["mark"], "context": " | ".join(x["mark"] for x in g)[:300], "path": "이동 표지", "rule": "move_conflict",
                            "target": label(k), "priority": "중간", "handling": "판단 가능",
                            "reason": f"{date} 이동표가 서로 맞지 않음: " + msg.format(a=lab[0], b=", ".join(lab[1:]), k=label(k)) + ". 원문 표기 오류인지 확인"})
    return out
