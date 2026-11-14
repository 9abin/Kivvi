"""법령 대조 카드: 관련 법조문 찾기 · 숫자 나란히 보기 · 법제처 해석례 붙이기 (화면 서버가 부른다).

  related(law, text)            바뀐 내규 문장과 글자 구성이 가까운 법조문 항 (제목이 같은 조만 보면 기준을 놓친다:
                                육아휴직 분할 횟수의 기준은 제19조가 아니라 제19조의4①에 있음)
  compare(law_items, old, new)  단위별 숫자 — 법령 / 현행 / 개정안. 같아짐·달라짐만 표시하고 법적 판단은 하지 않는다 (AGENTS §3)
  interpretations(law, key)     해석례 DB(data/expc*.sqlite3, 한석희 261013)에서 그 조문 해석례 + 「해석 뒤 조문이 바뀌었는지」 판정
해석례의 「내규 인용 여부」 표시(cited_by_naegyu)는 실제 인용과 맞지 않아(261013 점검) 쓰지 않는다. 연결은 법령명·조 키로만 한다.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPC = sorted((ROOT / "data").glob("expc*.sqlite3"))
UNIT = r"(회|년|개월|월|주|일|시간|분|세|명|%|퍼센트|만원|원|배|단계|등급|호봉)"
NUM = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*" + UNIT)
_REQ: dict[str, str] = {}     # 법령 id → requested_title (해석례 DB의 law_title과 같은 표기)


def init(db) -> None:
    """서버 시작 때 (법률 DB를 만든 스레드에서) 한 번. 요청 스레드는 SQLite 연결을 쓰지 않는다."""
    if db is not None and not _REQ:
        _REQ.update(dict(db.con.execute("select id, requested_title from documents").fetchall()))


def label(key: str) -> str:
    """조 키 → 표기: 19의4 → 제19조의4"""
    n, _, m = key.partition("의")
    return f"제{n}조" + (f"의{m}" if m else "")


def _bigrams(s: str) -> Counter:
    s = re.sub(r"[^가-힣0-9]", "", s or "")
    return Counter(s[i:i + 2] for i in range(len(s) - 1))


def _cos(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    return dot / (math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values())))


def _paras(u: dict) -> list[str]:
    hang = u.get("항") or []
    hang = hang if isinstance(hang, list) else [hang]
    out = [h.get("항내용", "") for h in hang if isinstance(h, dict) and h.get("항내용")]
    return out or [u.get("조문내용") or ""]


def focus_of(old: str, new: str, width: int = 14) -> tuple[str, set[str]]:
    """바뀐 곳 앞뒤 구절과 바뀐 숫자의 단위 — 관련 법조문을 고를 때 문장 전체보다 이쪽을 무겁게 본다"""
    import difflib
    sm = difflib.SequenceMatcher(a=old or "", b=new or "", autojunk=False)
    spans = [(i1, i2) for t, i1, i2, _, _ in sm.get_opcodes() if t != "equal"]
    if not spans:
        return "", set()
    lo, hi = max(0, spans[0][0] - width), min(len(old), spans[-1][1] + width)
    units = {u for (o, u), (n, _) in zip(_ordered(old), _ordered(new)) if o != n} if len(_ordered(old)) == len(_ordered(new)) else set()
    return old[lo:hi], units


def related(law: dict, text: str, k: int = 2, prefer: str | None = None, focus: str = "", units: set[str] | None = None) -> list[dict]:
    """법령의 항들 중 내규 문장과 가까운 것 k개 (같은 조의 항은 하나만). 점수 = 문장 전체 유사도 + 바뀐 구절 유사도×2
    + 바뀐 숫자와 같은 단위의 숫자가 있으면 0.2. prefer: 함께 보여 줄 조 키(제목이 같은 조 등)"""
    q, f, scored = _bigrams(text), _bigrams(focus), []
    for key, u in law["조문"].items():
        for p in _paras(u):
            b = _bigrams(p)
            bonus = 0.2 if units and any(x in numbers(p) for x in units) else 0.0
            scored.append((_cos(q, b) + (2 * _cos(f, b) if f else 0.0) + bonus, key, u.get("조문제목") or "", p))
    scored.sort(key=lambda x: -x[0])
    out, seen = [], set()
    for s, key, title, p in scored:
        if key in seen or s <= 0:
            continue
        seen.add(key)
        out.append({"key": key, "article": label(key), "title": title, "text": p.strip(), "score": round(s, 3)})
        if len(out) == k:
            break
    if prefer and prefer not in seen and prefer in law["조문"]:
        u = law["조문"][prefer]
        out.append({"key": prefer, "article": label(prefer), "title": u.get("조문제목") or "", "text": _paras(u)[0].strip(), "score": None})
    return out


def numbers(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for n, u in NUM.findall(text or ""):
        u = "%" if u == "퍼센트" else u
        out.setdefault(u, [])
        if n not in out[u]:
            out[u].append(n)
    return out


def _ordered(text: str) -> list[tuple[str, str]]:
    return [(n, "%" if u == "퍼센트" else u) for n, u in NUM.findall(text or "")]


def compare(law_texts: list[str], old: str, new: str) -> list[dict]:
    """개정으로 바뀐 값만: 문장 안 순서대로 짝지어(최대 2회→3회, 총 3회→4회) 법령 조문의 같은 단위 값과 나란히.
    같아짐·달라짐 사실만 적는다 (법적 판단 아님). 순서로 짝지을 수 없으면(숫자 개수가 다름) 단위별 목록으로."""
    lw = numbers(" ".join(law_texts))
    a, b = _ordered(old), _ordered(new)
    sents = [s for t in law_texts for s in re.split(r"(?<=다)\.\s*", t) if s.strip()]
    rows = []
    if len(a) == len(b) and all(x[1] == y[1] for x, y in zip(a, b)):
        pos = 0
        for (o, unit), (n, _) in zip(a, b):
            m = re.compile(re.escape(o) + r"\s*" + re.escape(unit)).search(old, pos)
            ctx = old[max(0, m.start() - 16): m.end() + 16] if m else ""
            pos = m.end() if m else pos
            if o == n:
                continue
            law_vals = lw.get(unit, [])

            def same_thing(val):   # 그 값이 나오는 법조문 문장이 이 숫자 주변 구절과 같은 것을 말하는가 (같은 단위라도 세는 대상이 다를 수 있음)
                # 기준 0.15: 261013 실측 — 「분할 횟수 최대 2회」↔법 「3회에 한정하여 나누어」 0.208(같은 것), 「휴직 단위 총 3회」↔같은 법 문장 0.091(다른 것)
                return any(_cos(_bigrams(ctx), _bigrams(s)) >= 0.15 for s in sents if re.search(re.escape(val) + r"\s*" + re.escape(unit), s))
            note = ("개정안 값이 법령 조문의 값과 같아짐" if n in law_vals and o not in law_vals and same_thing(n) else
                    "현행 값은 법령 조문에 있으나 개정안 값은 다름" if o in law_vals and n not in law_vals and same_thing(o) else "")
            rows.append({"unit": unit, "law": law_vals, "old": [o], "new": [n], "note": note, "context": ctx.strip()})
        return rows
    lo, no = numbers(old), numbers(new)
    for unit in sorted(set(lo) | set(no)):
        if lo.get(unit, []) != no.get(unit, []):
            rows.append({"unit": unit, "law": lw.get(unit, []), "old": lo.get(unit, []), "new": no.get(unit, []), "note": ""})
    return rows


def interpretations(law_id: str, key: str, limit: int = 5) -> list[dict]:
    if not EXPC or not law_id or not key:
        return []
    title = _REQ.get(law_id)
    if not title:
        return []
    con = sqlite3.connect(f"file:{EXPC[-1].as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select i.case_no, i.interpreted_at, i.title, l.paragraph, l.version_status, l.change_size, l.changed_after, i.answer, i.source_url "
            "from interpretation_links l join interpretations i using (expc_id) where l.law_title = ? and l.article_key = ? "
            "order by i.interpreted_at desc limit ?", (title, key, limit)).fetchall()
    finally:
        con.close()
    out = []
    for case_no, at, ttl, para, status, size, changed, answer, url in rows:
        out.append({"case_no": case_no, "date": f"{at[:4]}.{at[4:6]}.{at[6:8]}" if at and len(at) >= 8 else at,
                    "title": re.sub(r"\s+", " ", ttl or "")[:120], "para": para, "status": status, "size": size,
                    "changed_after": (changed or "").split()[-1:] and (changed or "").split()[-1],
                    "answer": re.sub(r"\s+", " ", answer or "")[:220], "url": url})
    return out


def article_ref(db, text: str) -> tuple[dict | None, str]:
    """「개인정보 보호법 제18조제2항」 → (법령, 조 키 '18')"""
    m = re.match(r"^(.*?)\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?", text or "")
    if not m or db is None:
        return None, ""
    law = db.find(m.group(1).strip())
    key = m.group(2) + (f"의{m.group(3)}" if m.group(3) else "")
    return law, key if law and key in law["조문"] else ""
