"""개정문 적용과 신·구조문대비표.

개정문(「제30조제2항 중 "A"를 "B"로 한다.」)을 해석해 규정의 **사본**에 적용하고, 현행·개정안을 나란히 놓은 대비표를 만든다.
원문(out/parsed)은 바꾸지 않는다. 승인 전 초안이며, 규정 DB는 공포된 개정본의 시행일에만 바뀐다 (02-specs).

해석 원칙: 못 읽는 문장, 대상 문구가 없는 치환, 겹치는 번호는 추측하지 않고 AmendError로 멈춘다.
문장은 적힌 순서대로 적용하고, 각 문장의 조 번호는 그 시점의 번호(앞 문장 적용 후)로 읽는다.

지원하는 문장 (v0):
  제N조[제M항[제K호[가목]]] 중 "A"를 "B"로 한다.        (같은 단위 안 모든 "A", 여러 쌍은 「"A"를 "B"로, "C"를 "D"로」)
  제N조[…] 중 "A"를 삭제한다.
  제N조의 제목 중 "A"를 "B"로 한다. / 제N조의 제목 "A"를 "B"로 한다.
  제N조[제M항[제K호]]를 삭제한다.
  제N조[제M항]를 다음과 같이 한다.  + 다음 줄부터 본문
  제N조의 다음에 제X조를 다음과 같이 신설한다. / 제X조를 다음과 같이 신설한다.  + 본문
  제A조부터 제B조까지를 각각 제C조부터 제D조까지로 한다. / 제A조를 제B조로 한다.
「하고,」로 이어진 문장은 나눠서 차례로 적용한다.
"""

from __future__ import annotations

import copy
import difflib
import re
from dataclasses import dataclass, field

from .normalize import normalize

MOK = "가나다라마바사아자차카타파하"
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


class AmendError(ValueError):
    pass


UNIT = r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?(?:\s*제\s*(\d+)\s*항)?(?:\s*제\s*(\d+)\s*호)?(?:\s*([가-하])\s*목)?"
RE_UNIT = re.compile(UNIT)
RE_PAIR = re.compile(r'"([^"]+)"\s*(?:을|를)\s*"([^"]*)"\s*(?:으로|로)')
RE_RANGE = re.compile(r"^(?:종전\s*)?제\s*(\d+)\s*조부터\s*제\s*(\d+)\s*조까지를\s*각각\s*제\s*(\d+)\s*조부터\s*제\s*(\d+)\s*조까지로\s*한다$")
RE_MOVE = re.compile(r"^(?:종전\s*)?제\s*(\d+)\s*조(?:\s*의\s*(\d+))?\s*(?:을|를)\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?\s*(?:으로|로)\s*한다$")
RE_INSERT = re.compile(rf"^(?:(?:같은\s*(?:규정|조)에\s*)?{UNIT}\s*의\s*다음에\s*)?(?:같은\s*(?:규정|조)에\s*)?제\s*(\d+)\s*조(?:\s*의\s*(\d+))?(?:\s*제\s*(\d+)\s*항)?\s*(?:을|를)\s*(?:다음과\s*같이\s*)?신설한다$")
RE_BODY_LINE = re.compile(r"^\s*(?:제\s*\d+\s*조(?:\s*의\s*\d+)?\s*[(（]|[①-⑳]|\d+(?:의\d+)?\.\s|[가-하]\.\s)")


@dataclass
class Unit:
    jo: int
    br: int = 0
    hang: int | None = None
    ho: int | None = None
    mok: int | None = None

    @classmethod
    def from_match(cls, g: tuple) -> "Unit":
        jo, br, hang, ho, mok = g
        return cls(int(jo), int(br or 0), int(hang) if hang else None, int(ho) if ho else None,
                   MOK.index(mok) + 1 if mok else None)

    @property
    def whole(self) -> bool:
        """조 전체를 가리키나 (항·호·목 지정 없음)."""
        return self.hang is None and self.ho is None and self.mok is None

    def label(self) -> str:
        s = f"제{self.jo}조" + (f"의{self.br}" if self.br else "")
        if self.hang:
            s += f"제{self.hang}항"
        if self.ho:
            s += f"제{self.ho}호"
        if self.mok:
            s += f"{MOK[self.mok - 1]}목"
        return s


@dataclass
class Op:
    kind: str                 # replace | delete | rewrite | insert | renumber
    sentence: str
    unit: Unit | None = None
    pairs: list[tuple[str, str]] = field(default_factory=list)
    title: bool = False
    after: Unit | None = None
    body: list[str] = field(default_factory=list)
    moves: list[tuple[tuple[int, int], tuple[int, int]]] = field(default_factory=list)


# ---------------------------------------------------------------- 해석

def _clean(s: str) -> str:
    s = normalize(s).replace("“", '"').replace("”", '"').strip()
    return re.sub(r"\s+", " ", s)


def _split_sentences(line: str) -> list[str]:
    line = _clean(line)
    # 「하고,」 「하며,」로 이어진 문장 나누기 (따옴표 안은 건드리지 않게 바깥에서만)
    parts, buf, inq = [], "", False
    i = 0
    while i < len(line):
        c = line[i]
        if c == '"':
            inq = not inq
        if not inq:
            m = re.match(r"(하고|하며|신설하고|삭제하고)\s*,\s*", line[i:])
            if m:
                verb = {"하고": "한다", "하며": "한다", "신설하고": "신설한다", "삭제하고": "삭제한다"}[m.group(1)]
                parts.append(buf + verb)
                buf, i = "", i + m.end()
                continue
        buf += c
        i += 1
    parts.append(buf)
    out = []
    for p in parts:
        out += [x.strip() for x in re.split(r'(?<=다)\.\s*(?=(?:[^"]*"[^"]*")*[^"]*$)', p) if x.strip()]
    return [s.rstrip(".").strip() for s in out]


def _parse_sentence(s: str) -> Op:
    if m := RE_RANGE.match(s):
        a, b, c, d = map(int, m.groups())
        if b - a != d - c:
            raise AmendError(f"번호 범위 길이가 다름: {s}")
        return Op("renumber", s, moves=[((n, 0), (n + c - a, 0)) for n in range(a, b + 1)])
    if m := RE_MOVE.match(s):
        a, ab, c, cb = m.groups()
        return Op("renumber", s, moves=[((int(a), int(ab or 0)), (int(c), int(cb or 0)))])
    if m := RE_INSERT.match(s):
        g = m.groups()
        after = Unit.from_match(g[0:5]) if g[0] else None
        unit = Unit(int(g[5]), int(g[6] or 0), int(g[7]) if g[7] else None)
        return Op("insert", s, unit=unit, after=after)
    m = RE_UNIT.match(s)
    if not m:
        raise AmendError(f"해석 못함 (대상 조문이 없음): {s}")
    unit, rest = Unit.from_match(m.groups()), s[m.end():].strip()
    title = False
    if rest.startswith("의 제목"):
        title, rest = True, rest[len("의 제목"):].strip()
    if rest.startswith("중 ") or (title and rest.startswith('"')):
        rest = rest[2:].strip() if rest.startswith("중 ") else rest
        if m2 := re.fullmatch(r'"([^"]+)"\s*(?:을|를)\s*삭제한다', rest):
            return Op("replace", s, unit=unit, pairs=[(m2.group(1), "")], title=title)
        pairs = RE_PAIR.findall(rest)
        tail = RE_PAIR.sub("", rest).replace(",", "").replace("각각", "").strip()
        if not pairs or tail != "한다":
            raise AmendError(f"해석 못함 (치환 문구): {s}")
        return Op("replace", s, unit=unit, pairs=pairs, title=title)
    if re.fullmatch(r"(?:을|를)\s*삭제한다", rest):
        return Op("delete", s, unit=unit)
    if re.fullmatch(r"(?:을|를)\s*다음과\s*같이\s*한다", rest):
        return Op("rewrite", s, unit=unit)
    raise AmendError(f"해석 못함: {s}")


def _is_instruction(line: str) -> bool:
    """개정 지시 문장인가. 「제N조(제목) …」처럼 괄호 제목이 붙은 줄과 ①·1.·가. 로 시작하는 줄은 본문이다."""
    if RE_BODY_LINE.match(line):
        return False
    return bool(re.match(r"^(?:종전\s*)?(?:같은\s*\S+에\s*)?제\s*\d+\s*조", line))


def parse_amendment(text: str) -> list[Op]:
    """개정문 → Op 목록. 「다음과 같이」 문장 뒤의 본문 줄은 그 문장에 붙인다."""
    ops: list[Op] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        line = _clean(raw)
        needs_body = ops and ops[-1].kind in ("rewrite", "insert") and "다음과 같이" in ops[-1].sentence
        if needs_body and not _is_instruction(line):
            ops[-1].body.append(line)
            continue
        for s in _split_sentences(line):
            ops.append(_parse_sentence(s))
    for op in ops:
        if op.kind in ("rewrite", "insert") and "다음과 같이" in op.sentence and not op.body:
            raise AmendError(f"본문 없음: {op.sentence}")
    return ops


# ---------------------------------------------------------------- 적용

def _key(a: dict) -> tuple[int, int]:
    m = re.match(r"제\s*(\d+)\s*조(?:의\s*(\d+))?", a["label"])
    return int(m.group(1)), int(m.group(2) or 0)


def _label(no: int, br: int) -> str:
    return f"제{no}조" + (f"의{br}" if br else "")


def _find(arts: list[dict], jo: int, br: int) -> dict:
    for a in arts:
        if _key(a) == (jo, br) and not a.get("_gone"):
            return a
    raise AmendError(f"대상 조문 없음: {_label(jo, br)}")


def _items_in(a: dict, u: Unit) -> list[dict]:
    # 「제3조제2호」처럼 항 없이 호·목을 적으면 항 구분이 없는 조(항 0)의 호로 읽는다
    hang = u.hang if u.hang is not None else (0 if (u.ho or u.mok) else None)
    want = [hang, u.ho, u.mok]
    out = []
    for it in a["items"]:
        p = it["path"]
        ok = True
        for k, w in enumerate(want):
            if w is None:
                continue
            # 항 구분이 없는 조(항 0)에서 제1항은 본문 전체로 본다
            if k == 0 and p[0] == 0 and w == 1:
                continue
            if p[k] != w:
                ok = False
                break
        # 호만 지정하면 그 호의 목까지, 목을 지정하지 않으면 하위 전체
        if ok and u.ho and not u.mok and p[1] != u.ho:
            ok = False
        if ok:
            out.append(it)
    if not out:
        raise AmendError(f"대상 단위 없음: {a['label']} → {u.label()}")
    return out


def _parse_body(body: list[str], jo: int, br: int) -> dict:
    """본문 줄을 기존 파서로 읽어 조문 레코드로 만든다."""
    from .build import to_records
    from .parse import parse

    lines = list(body)
    if not re.match(r"^\s*제\s*\d+\s*조", lines[0]):
        lines[0] = f"{_label(jo, br)}(임시) " + lines[0]
    rec = to_records("new", parse(lines))
    if len(rec["articles"]) != 1:
        raise AmendError(f"본문을 조문 하나로 읽지 못함: {body[0][:40]}")
    return rec["articles"][0]


def _one_pattern(old: str) -> str:
    """조문 인용처럼 보이는 문구(제N조…)는 숫자·「의N」이 이어지는 자리에서 잘못 맞지 않게 경계를 둔다 (「제7조」가 「제7조의2」에 맞지 않게)."""
    p = re.escape(old)
    if re.match(r"^제\s*\d", old):
        p = r"(?<!\d)" + p + (r"(?!\d|\s*의\s*\d)" if re.search(r"(조|항|호)$", old) else r"(?!\d)")
    return p


def _pairs_pattern(pairs: list[tuple[str, str]]):
    olds = sorted({o for o, _ in pairs}, key=len, reverse=True)   # 긴 문구 먼저 (제7조제1항 > 제7조)
    pat = re.compile("|".join(f"(?:{_one_pattern(o)})" for o in olds))
    table = {}
    for o, r in pairs:
        table[o] = r

    class _T(dict):
        def __missing__(self, key):   # 경계 패턴이라 매칭 문자열이 원문과 같다
            return table[key]
    return pat, _T(table)


def apply(reg: dict, ops: list[Op]) -> tuple[dict, list[dict]]:
    """규정 사본에 Op를 적용. 반환: (새 규정, 조문별 변경 기록)."""
    new = copy.deepcopy(reg)
    arts = new["articles"]
    for a in arts:
        a["_origin"] = a["id"]
        a["_old_label"] = a["label"]
        a["_changes"] = []
    log: list[dict] = []
    for op in ops:
        if op.kind == "replace":
            a = _find(arts, op.unit.jo, op.unit.br)
            # 여러 쌍은 동시에 바꾼다: 「"제7조"를 "제8조"로, "제8조"를 "제9조"로」에서 새로 생긴 제8조를 다시 바꾸지 않게
            pat, table = _pairs_pattern(op.pairs)
            texts = [a["title"]] if op.title else [it["norm"] for it in _items_in(a, op.unit)]
            counts = {old: sum(len(re.findall(_one_pattern(old), t)) for t in texts) for old, _ in op.pairs}
            for old, n in counts.items():
                if not n:
                    if op.title:
                        raise AmendError(f'제목에 "{old}" 없음: {a["label"]}({a["title"]})')
                    raise AmendError(f'"{old}" 없음: {a["label"]} {op.unit.label()} — ' + " / ".join(t[:40] for t in texts[:3]))
            if op.title:
                a["title"] = pat.sub(lambda m: table[m.group(0)], a["title"])
            else:
                for it in _items_in(a, op.unit):
                    it["norm"] = pat.sub(lambda m: table[m.group(0)], it["norm"])
            for old, rep in op.pairs:
                a["_changes"].append({"op": "replace", "unit": op.unit.label(), "old": old, "new": rep, "count": counts[old],
                                      "title": op.title})
        elif op.kind == "delete":
            a = _find(arts, op.unit.jo, op.unit.br)
            if op.unit.whole:
                a["deleted"] = True
                a["_changes"].append({"op": "delete", "unit": op.unit.label(), "whole": True})
            else:
                for it in _items_in(a, op.unit):
                    it["deleted"], it["norm"] = True, "삭제"
                a["_changes"].append({"op": "delete", "unit": op.unit.label()})
        elif op.kind == "rewrite":
            a = _find(arts, op.unit.jo, op.unit.br)
            if op.unit.whole:
                body = _parse_body(op.body, op.unit.jo, op.unit.br)
                if not body["title"].startswith("임시"):
                    a["title"] = body["title"]
                a["items"], a["deleted"] = body["items"], False
            else:
                if op.unit.hang is None:
                    raise AmendError(f"호·목 단위 「다음과 같이 한다」는 v0에서 미지원 — 「중 \"A\"를 \"B\"로」로 적어 주세요: {op.sentence}")
                body = _parse_body(op.body, op.unit.jo, op.unit.br)
                olds = _items_in(a, Unit(op.unit.jo, op.unit.br, op.unit.hang))
                keep = [it for it in a["items"] if it not in olds]
                a["items"] = sorted(keep + body["items"], key=lambda it: it["path"])
            a["_changes"].append({"op": "rewrite", "unit": op.unit.label()})
        elif op.kind == "insert":
            u = op.unit
            if u.hang:
                raise AmendError(f"항 신설은 v0에서 미지원 — 「제N조를 다음과 같이 한다」로 적어 주세요: {op.sentence}")
            if any(_key(a) == (u.jo, u.br) and not a.get("_gone") for a in arts):
                raise AmendError(f"이미 있는 번호에 신설: {_label(u.jo, u.br)} (번호 이동 문장을 먼저 적어야 함)")
            if op.after and not any(_key(a) == (op.after.jo, op.after.br) for a in arts):
                raise AmendError(f"기준 조문 없음: {op.after.label()}")
            body = _parse_body(op.body, u.jo, u.br) if op.body else {"title": "", "items": [], "text": "", "norm": ""}
            arts.append({"id": f"{reg['reg_id']}:new{len(log)}", "label": _label(u.jo, u.br),
                         "title": "" if body["title"].startswith("임시") else body["title"],
                         "chapter": "", "deleted": False, "amend_notes": [], "items": body["items"],
                         "_origin": None, "_old_label": None, "_changes": [{"op": "insert", "unit": u.label()}]})
        elif op.kind == "renumber":
            targets = []
            for (a_no, a_br), (c_no, c_br) in op.moves:
                if any(_key(x)[0] == a_no and _key(x)[1] and not a_br for x in arts) and len(op.moves) > 1:
                    raise AmendError(f"범위 안에 가지 조문(제{a_no}조의N)이 있음 — 따로 적어 주세요: {op.sentence}")
                targets.append((_find(arts, a_no, a_br), (c_no, c_br)))
            moved = {id(a) for a, _ in targets}
            for a, (c_no, c_br) in targets:
                clash = [x for x in arts if _key(x) == (c_no, c_br) and id(x) not in moved and not x.get("deleted")]
                if clash:
                    raise AmendError(f"옮길 번호가 이미 있음: {_label(c_no, c_br)}")
            for a, (c_no, c_br) in targets:
                a["_changes"].append({"op": "renumber", "from": a["label"], "to": _label(c_no, c_br)})
                a["label"] = _label(c_no, c_br)
        log.append({"op": op.kind, "sentence": op.sentence})
    arts.sort(key=_key)
    changes = [{
        "origin": a["_origin"], "old_label": a["_old_label"], "new_label": a["label"],
        "ops": a["_changes"],
    } for a in arts if a["_changes"]]
    return new, changes


def label_map(changes: list[dict]) -> dict[str, str | None]:
    """원래 조문ID → 새 조 번호 (삭제는 None). 변경안 점검에서 깨지는 인용을 찾을 때 쓴다."""
    out = {}
    for c in changes:
        if not c["origin"]:
            continue
        if any(o["op"] == "delete" and o.get("whole") for o in c["ops"]):
            out[c["origin"]] = None
        elif c["old_label"] != c["new_label"]:
            out[c["origin"]] = c["new_label"]
    return out


# ---------------------------------------------------------------- 대비표

def marker(path: list[int]) -> str:
    p, i, s = path
    if s:
        return f"{MOK[s - 1]}. "
    if i:
        return f"{i}. "
    if p:
        return (CIRCLED[p - 1] if p <= len(CIRCLED) else f"({p})") + " "
    return ""


def _segments(old: str, new: str) -> tuple[list, list]:
    """글자 단위 비교 → [(문자열, 바뀜 여부)] 두 줄. 대비표의 밑줄 부분."""
    sm = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    o, n = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            o.append((old[i1:i2], False))
            n.append((new[j1:j2], False))
        else:
            if i2 > i1:
                o.append((old[i1:i2], True))
            if j2 > j1:
                n.append((new[j1:j2], True))
    return o, n


def _head(label: str, title: str) -> str:
    return label + (f"({title})" if title else "")


def compare_table(old_reg: dict, new_reg: dict, changes: list[dict]) -> list[dict]:
    """대비표 행: 조문 하나당 한 행. 각 칸은 줄 목록, 줄은 [(문자열, 바뀜)] 조각 목록.
    바뀌지 않은 항·호는 현행 「(생 략)」, 개정안 「(현행과 같음)」으로 줄인다 (법제처 대비표 관례)."""
    olds = {a["id"]: a for a in old_reg["articles"]}
    news = {a.get("_origin") or a["id"]: a for a in new_reg["articles"]}
    rows = []
    for c in changes:
        na = news[c["origin"]] if c["origin"] else next(a for a in new_reg["articles"] if a["label"] == c["new_label"] and not a.get("_origin"))
        oa = olds.get(c["origin"]) if c["origin"] else None
        L, R = [], []
        if oa is None:                                   # 신설
            L.append([("<신 설>", False)])
            R.append([(_head(na["label"], na["title"]), True)])
            R += [[(marker(it["path"]) + it["norm"], True)] for it in na["items"]]
        elif na.get("deleted") and not oa.get("deleted"):  # 조 삭제
            L.append([(_head(oa["label"], oa["title"]), True)])
            L += [[(marker(it["path"]) + it["norm"], True)] for it in oa["items"]]
            R.append([(f"{na['label']} ", False), ("삭제", True)])
        else:
            # 조 번호가 바뀌면 번호 전체를 표시 (글자 비교는 「제1[0]조」처럼 숫자 일부만 잡음)
            to, tn = _segments(f"({oa['title']})" if oa["title"] else "", f"({na['title']})" if na["title"] else "")
            moved = oa["label"] != na["label"]
            L.append([(oa["label"], moved)] + to)
            R.append([(na["label"], moved)] + tn)
            oi = {tuple(it["path"]): it for it in oa["items"]}
            ni = {tuple(it["path"]): it for it in na["items"]}
            skipped = False
            for path in sorted(set(oi) | set(ni)):
                a, b = oi.get(path), ni.get(path)
                if a and b and a["norm"] == b["norm"]:
                    if not skipped:
                        L.append([("(생 략)", False)])
                        R.append([("(현행과 같음)", False)])
                        skipped = True
                    continue
                skipped = False
                mk = marker(list(path))
                if a and b and b.get("deleted") and not a.get("deleted"):   # 항·호 삭제: 글자 비교 대신 통째로
                    L.append([(mk, False), (a["norm"], True)])
                    R.append([(mk, False), ("삭제", True)])
                elif a and b:
                    so, sn = _segments(a["norm"], b["norm"])
                    L.append([(mk, False)] + so)
                    R.append([(mk, False)] + sn)
                elif a:
                    L.append([(mk + a["norm"], True)])
                    R.append([(mk, False), ("삭제", True)])
                else:
                    L.append([("<신 설>", False)])
                    R.append([(mk + b["norm"], True)])
        rows.append({"origin": c["origin"], "old_label": c["old_label"], "new_label": c["new_label"],
                     "ops": c["ops"], "old": L, "new": R})
    return rows


def to_html(rows: list[dict], title: str) -> str:
    import html as H

    def cell(lines):
        return "".join("<div>" + "".join(f"<u>{H.escape(t)}</u>" if ch else H.escape(t) for t, ch in ln) + "</div>"
                       for ln in lines)
    body = "".join(f"<tr><td>{cell(r['old'])}</td><td>{cell(r['new'])}</td></tr>" for r in rows)
    return (f'<table class="cmp"><caption>{H.escape(title)}</caption>'
            f"<tr><th>현 행</th><th>개 정 안</th></tr>{body}</table>")


def to_text(rows: list[dict]) -> str:
    """터미널·테스트용. 바뀐 부분은 [[ ]]로 감싼다."""
    def line(ln):
        return "".join(f"[[{t}]]" if ch else t for t, ch in ln)
    out = []
    for r in rows:
        out.append(f"■ {r['old_label'] or '(신설)'} → {r['new_label']}")
        for k in range(max(len(r["old"]), len(r["new"]))):
            a = line(r["old"][k]) if k < len(r["old"]) else ""
            b = line(r["new"][k]) if k < len(r["new"]) else ""
            out.append(f"  현행  | {a}\n  개정안| {b}")
    return "\n".join(out)


def build_changeset(reg: dict, text: str, cid: str, title: str = "") -> tuple[dict, dict]:
    """개정문 → (changeset, 개정안 사본). scripts/amend.py와 검토 화면이 같이 쓴다."""
    ops = parse_amendment(text)
    new, changes = apply(reg, ops)
    rows = compare_table(reg, new, changes)
    cs = {"id": cid, "title": title, "reg_id": reg["reg_id"], "reg_name": reg["name"], "amendment": text,
          "sentences": [o.sentence for o in ops], "changes": changes, "label_map": label_map(changes), "rows": rows}
    return cs, new
