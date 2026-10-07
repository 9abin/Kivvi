"""개정 이력 신호: 인용 대상이 바뀐 뒤 인용하는 쪽이 따라가지 않은 곳.

KVIC 확인된 인용 오류 17건 중 15건이 이 패턴이었다 (리포트 261007-1553-21): 인용하는 조는 그대로인데 대상 규정이 나중에 개정됨.
원문에 남은 개정 표시(<개정 …>, [본조신설 …], [전문개정 …], [종전 … 이동], 연혁의 「전부개정 …」)만 쓴다.

신호 (인용하는 쪽 마지막 개정일 이후에 일어난 대상 쪽 사건)
  높음  대상 규정 전부개정                       → 조 번호 전체가 다시 매겨졌을 수 있음 (감사규정 2025.11.28 → 감사지침)
  높음  대상 조 [본조신설]·[전문개정]·이동 표시  → 그 번호에 다른 내용이 들어옴 (급여규정 제21조 2025.03.28 → 성과급지급지침)
  낮음  대상 조 내용 개정(<개정>·<신설>)          → 인용 취지가 여전히 맞는지 확인
인용하는 쪽 마지막 개정일: 그 조의 개정 표시 중 가장 늦은 날. 표시가 없으면 그 규정의 마지막 전부개정일, 없으면 제정일.
판정이 아니라 후보다. 같은 번호가 그대로 맞을 수도 있다 (전부개정에서 번호를 유지한 조).
"""

from __future__ import annotations

import re

RE_DATE = re.compile(r"((?:19|20)\d{2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})")
RE_MARK = re.compile(r"[<\[][^<>\[\]]*?((?:19|20)\d{2}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2})[^<>\[\]]*?[>\]]")
STRONG = ("본조신설", "전문개정", "이동")
BATCH_DAYS = 30   # 인용 쪽 개정 후 이 기간 안의 대상 개정은 같은 개편 묶음일 가능성 (직제지침 2021.03.18 ↔ 직제규정 2021.03.25)


def _d(s: str) -> tuple[int, int, int] | None:
    m = RE_DATE.search(s or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _days(a: tuple, b: tuple) -> int:
    from datetime import date
    try:
        return (date(*b) - date(*a)).days
    except ValueError:   # 일자 0 등 잘못 적힌 날짜
        return (b[0] - a[0]) * 365 + (b[1] - a[1]) * 30


def fmt(d: tuple | None) -> str:
    return f"{d[0]}.{d[1]:02d}.{d[2]:02d}" if d else "?"


def article_events(a: dict) -> list[tuple[tuple, str]]:
    """조의 개정 사건 [(날짜, 종류)]. 종류: strong(본조신설·전문개정·이동) | amend(개정·신설·삭제 등)"""
    texts = list(a.get("amend_notes", [])) + [m for it in a["items"] for m in it.get("amend_marks", [])]
    texts += re.findall(r"[<\[][^<>\[\]]*(?:이동|본조신설|전문개정)[^<>\[\]]*[>\]]", a.get("text", ""))
    out = []
    for t in texts:
        for m in RE_MARK.finditer(t if t.startswith(("<", "[")) else f"[{t}]"):
            d = _d(m.group(1))
            if d:
                out.append((d, "strong" if any(k in m.group(0) for k in STRONG) else "amend"))
        if not RE_MARK.search(t) and (d := _d(t)):
            out.append((d, "strong" if any(k in t for k in STRONG) else "amend"))
    return out


def merge(found: list[dict], hist: list[dict]) -> tuple[list[dict], dict]:
    """이력 신호를 기존 결과에 합친다.
    같은 위치(규정·단위·조·항·인용) 결과가 있으면 근거로 덧붙이고, 강한 신호면 우선순위를 높음으로.
    없으면 강한 신호만 단독 결과로 남긴다 (내용 개정·같은 개편 묶음은 근거로만 — 단독으로는 잡음이 큼)."""
    def key(f):
        return (f["reg_id"], f["unit"], f["article"], f["para"], re.sub(r"\s", "", f["quote"]))
    by = {}
    for f in found:
        by.setdefault(key(f), []).append(f)
    added, stats = [], {"덧붙임": 0, "단독": 0, "버림(약한 신호)": 0}
    for h in hist:
        hits = by.get(key(h))
        if hits:
            for f in hits:
                f["note"] = (f["note"] + " / " if f["note"] else "") + "개정 이력: " + h["note"]
                if h["signal"] == "strong":
                    f["priority"] = "높음"
                f.setdefault("signals", []).append("history")
            stats["덧붙임"] += 1
        elif h["signal"] == "strong":
            added.append(h)
            stats["단독"] += 1
        else:
            stats["버림(약한 신호)"] += 1
    return found + added, stats


def regulation_dates(r: dict) -> tuple[tuple | None, list[tuple]]:
    """(기준일 = 마지막 전부개정 또는 제정일, 전부개정일 목록)"""
    hist = r.get("history") or []
    full = [d for h in hist if ("전부개정" in h or "전문개정" in h) and (d := _d(h))]
    dated = [d for h in hist if (d := _d(h))]
    if not dated:
        dated = [d for ad in r.get("addenda", []) if (d := _d(ad.get("date") or ""))]
    base = max(full) if full else (min(dated) if dated else None)
    return base, full


def stale_citations(regs: dict[str, dict], rels: list[dict]) -> list[dict]:
    from .findings import locate

    arts = {a["id"]: (r, a) for r in regs.values() for a in r["articles"]}
    reg_info = {rid: regulation_dates(r) for rid, r in regs.items()}
    out, seen = [], set()
    for rel in rels:
        if rel["target_type"] != "article" or rel["src_kind"] != "본문" or rel.get("check_status") != "ok":
            continue   # 대상 없음 등은 이미 다른 점검이 표시. 부칙의 옛 조문은 오류 제외 원칙
        src_aid, tgt = rel["src_article"], rel.get("target") or ""
        if src_aid == tgt or src_aid not in arts or tgt not in arts:
            continue
        sreg, sart = arts[src_aid]
        treg, tart = arts[tgt]
        s_events = [d for d, _ in article_events(sart)]
        s_base, _ = reg_info[sreg["reg_id"]]
        s_last = max(s_events) if s_events else s_base
        if not s_last:
            continue
        _, t_full = reg_info[treg["reg_id"]]
        events = []
        events += [(d, "full") for d in t_full if d > s_last and treg["reg_id"] != sreg["reg_id"]]
        events += [(d, k) for d, k in article_events(tart) if d > s_last]
        if not events:
            continue
        strong = [e for e in events if e[1] in ("full", "strong") and _days(s_last, e[0]) > BATCH_DAYS]
        batch = [e for e in events if e[1] in ("full", "strong") and _days(s_last, e[0]) <= BATCH_DAYS]
        key = (rel["src"], tgt, rel["evidence"])
        if key in seen:
            continue
        seen.add(key)
        top = max(strong or batch or events)
        what = {"full": f"「{treg['name']}」 전부개정", "strong": f"{treg['name']} {tart['label']}에 다른 조문이 들어옴(본조신설·전문개정·이동)",
                "amend": f"{treg['name']} {tart['label']} 내용 개정"}[top[1]]
        out.append({
            **locate(rel["src"]), "quote": rel["evidence"], "context": rel["context"],
            "type": "개정미반영_후보", "method": "history", "suggest": "",
            "priority": "높음" if strong else "낮음",
            "signal": "strong" if strong else ("batch" if batch else "amend"),
            "note": f"{what} {fmt(top[0])} — 인용 쪽 마지막 개정 {fmt(s_last)}"
                    + ("" if s_events else " (조 개정 표시 없음: 규정 제정·전부개정일 기준)")
                    + (f" (인용 쪽 개정 {BATCH_DAYS}일 안의 대상 개정 — 같은 개편에서 함께 정비됐을 가능성)" if not strong and batch else "")
                    + f". 현재 {tart['label']}({tart['title']})",
            "src_id": rel["src"],
        })
    return out
