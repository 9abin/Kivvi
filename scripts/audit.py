"""전체 점검: 우리 파싱 구조와 관련 조항 표(그래프)를 원문·독립 자료와 하나씩 대조한다.

  uv run --extra db python scripts/audit.py
출력: out/audit/structure.tsv, out/audit/graph.tsv, 요약 출력
구조
  원문 대조   조마다 우리 본문(제목+항)과 가빈 원문(public.chunks.body, 사람 보정 없는 칸)을 정규화해 비교.
              다르면 어느 쪽이 무엇을 더/덜 가졌는지 (가빈 보정 칸 body_search·cites는 읽지 않음)
  번호        조 번호 결번·역순·중복, 항 번호 건너뜀·역순, 호 번호 건너뜀
  글자        깨진 글자(제어문자 흔적), 비정상적으로 긴 항(합쳐짐 의심), 빈 항
그래프
  누락        본문·부칙에 적힌 「제N조」·「별표 N」·「별지 제N호」 표기 수 vs 그 항에서 나온 관계 수
  대상        관계 대상이 실제로 있는지, 삭제된 조인지, 자기 자신인지, 상태(ok)와 실제가 맞는지
  역방향      관계의 출발 항이 파싱 결과에 있는지 (고아 관계)
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.citations import RE_APPX, RE_EFFECTIVE_NOTE, RE_HISTORY, RE_JO  # noqa: E402
from regcheck.parse import LOOKALIKE  # noqa: E402

# 깨진 글자: hwp 제어문자가 남은 흔적 (라틴 확장·타밀·사용자 정의 영역·대체 문자). 가운뎃점·한자(監事)는 원문 글자라 제외
GARBLE = re.compile(r"[\u0100-\u024f\u0b80-\u0bff\ue000-\uf8ff\ufffd\x00-\x08\x0b-\x1f]")
NORM = re.compile(r"[\s　·ㆍ․‧“”\"'‘’「」『』()\[\]<>〈〉《》.,:;~\-–—]")
AMEND_NOTE = re.compile(r"[<\[(]\s*(?:개정|신설|삭제|전문개정|본조신설|제목개정|종전)[^>\])]*[>\])]")


def n(s: str) -> str:
    return NORM.sub("", AMEND_NOTE.sub("", (s or "").translate(LOOKALIKE)))


def gabin_side(tag: str, ours: str, theirs: str) -> bool:
    """가빈 원문 쪽 사정으로 설명되는 차이: 장·절 제목·쪽 머리말이 붙음, 쪽 번호, 깨진 글자, 조 도입문 누락(우리에만 있는 「…다음 각 항…」)"""
    if tag == "insert" and (re.match(r"^\d*제\d+[장절]", theirs) or re.fullmatch(r"\d{1,3}", theirs) or GARBLE.search(theirs)
                            or re.search(r"[一-鿿]{2}", theirs) and len(theirs) <= 4 or re.fullmatch(r"[가-힣·\s]{2,30}(?:규정|지침|강령|규칙)", theirs)
                            or re.match(r"^\d*제\d+[장절]", re.sub(r"^\d+", "", theirs))):
        return True
    if tag == "delete" and re.search(r"다음각[항호]|다음과같다|정하는바에따른다", ours):
        return True
    return False


def load_regs() -> dict[str, dict]:
    return {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "out" / "parsed").glob("[0-9]*.json")))}


def gabin_articles() -> dict[tuple[str, str], str]:
    """(규정, 「제N조[의M]」) → 가빈 원문 (그 조의 항 청크를 이어 붙임)."""
    from regcheck.db import connect, safe_select

    rows = safe_select(connect(), "chunks", ["chunk_id", "doc_id", "status", "body"])
    out: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
    for cid, did, st, body in rows:
        m = re.match(r"^\d+_(제\d+조(?:의\d+)?)(?:_(\d+)항)?$", cid)
        if m:
            out[(did, m.group(1))].append((int(m.group(2) or 0), body))
    return {k: " ".join(b for _, b in sorted(v)) for k, v in out.items()}


def structure(regs: dict[str, dict], gabin: dict[tuple[str, str], str] | None) -> list[dict]:
    out = []

    def add(r, label, kind, detail, **kw):
        out.append({"reg_id": r["reg_id"], "reg_name": r["name"], "article": label, "kind": kind, "detail": detail, **kw})

    for r in regs.values():
        nums, prev = [], None
        for a in r["articles"]:
            m = re.match(r"제(\d+)조(?:의(\d+))?", a["label"])
            key = (int(m.group(1)), int(m.group(2) or 0))
            if prev and key <= prev:
                add(r, a["label"], "조 번호 역순·중복", f"앞 조 제{prev[0]}조{'의' + str(prev[1]) if prev[1] else ''}")
            if prev and key[1] == 0 and key[0] > prev[0] + 1:
                add(r, a["label"], "조 결번", f"제{prev[0] + 1}~{key[0] - 1}조 없음")
            prev = key
            last_para, last_ho = 0, 0
            for it in a["items"]:
                h, ho, _ = it["path"]
                if h and h < last_para:
                    add(r, a["label"], "항 번호 역순", f"{last_para} 다음 {h}")
                elif h and h > last_para + 1:
                    add(r, a["label"], "항 번호 건너뜀", f"{last_para} 다음 {h}")
                if h != last_para:
                    last_ho = 0
                if ho and ho > last_ho + 1:
                    add(r, a["label"], "호 번호 건너뜀", f"{h}항 {last_ho} 다음 {ho}")
                if ho:
                    last_ho = ho
                last_para = h or last_para
                if GARBLE.search(it["text"]):
                    add(r, a["label"], "깨진 글자", repr(GARBLE.findall(it["text"])[:5]))
                if len(it["text"]) > 600 and not a.get("deleted"):
                    add(r, a["label"], "긴 항(합쳐짐 의심)", f"{it['path']} {len(it['text'])}자: {it['text'][:60]}")
                if not it["text"].strip() and not a.get("deleted"):
                    add(r, a["label"], "빈 항", str(it["path"]))
            if gabin is not None and not a.get("deleted"):
                g = gabin.get((r["reg_id"], a["label"]))
                if g is None:
                    add(r, a["label"], "가빈에 조 없음", "")
                    continue
                mok = "가나다라마바사아자차카타파하"
                ours = "".join((f"{it['path'][1]}." if it["path"][1] and not it["path"][2] else "")
                               + (f"{mok[it['path'][2] - 1]}." if it["path"][2] else "") + it["text"] for it in a["items"])
                theirs = re.sub(r"^\s*제\s*\d+\s*조(?:\s*의\s*\d+)?\s*(?:\([^)]*\))?", "", g, count=1)
                ours_c, theirs_c = re.sub(r"[①-⑳]", "", n(ours)), re.sub(r"[①-⑳]", "", n(theirs))
                if ours_c != theirs_c:
                    sm = SequenceMatcher(None, ours_c, theirs_c, autojunk=False)
                    diffs = [(t, ours_c[i1:i2], theirs_c[j1:j2]) for t, i1, i2, j1, j2 in sm.get_opcodes() if t != "equal"]
                    size = sum(max(len(x[1]), len(x[2])) for x in diffs)
                    side = "가빈 쪽" if all(gabin_side(t_, o_, g_) for t_, o_, g_ in diffs) else "확인 필요"
                    add(r, a["label"], f"원문 차이({side})", "; ".join(f"{t}:우리「{o[:40]}」/가빈「{g_[:40]}」" for t, o, g_ in diffs[:3]),
                        size=size, ratio=round(sm.ratio(), 3))
        for (rid, label) in ([] if gabin is None else [k for k in gabin if k[0] == r["reg_id"]]):
            if not any(a["label"] == label for a in r["articles"]):
                add(r, label, "우리에 조 없음", "가빈에만 있는 조")
    return out


def graph(regs: dict[str, dict], rels: list[dict]) -> list[dict]:
    out = []
    items = {}
    arts = {}
    apps = {}
    for r in regs.values():
        for a in r["articles"]:
            arts[a["id"]] = (r, a)
            for it in a["items"]:
                items[it["id"]] = (r, a, it, "본문")
        for ad in r.get("addenda", []):
            for a in ad["articles"]:
                for it in a["items"]:
                    items[it["id"]] = (r, a, it, "부칙")
        for ap in r.get("appendices", []):
            apps[ap["id"]] = (r, ap)
    by_src = defaultdict(list)
    for e in rels:
        by_src[e["src"]].append(e)
        if e["src"] not in items and e["src_kind"] in ("본문", "부칙"):
            out.append({"kind": "고아 관계 (출발 항 없음)", "src": e["src"], "detail": e["evidence"], "rel": e["id"]})
        t, tt, st = str(e["target"]), e["target_type"], e["check_status"]
        if tt == "article":
            if t not in arts:
                if st == "ok":
                    out.append({"kind": "대상 없음인데 ok", "src": e["src"], "detail": f"{e['evidence']} → {t}", "rel": e["id"]})
            else:
                ra, aa = arts[t]
                if aa.get("deleted") and st == "ok" and e["src_kind"] != "부칙":
                    out.append({"kind": "삭제된 조를 ok로 인용", "src": e["src"], "detail": f"{e['evidence']} → {ra['name']} {aa['label']}", "rel": e["id"]})
                if t == e["src_article"] and not re.search(r"\d+\s*항|\d+\s*호|부터|까지", (e["evidence"] or "") + (e.get("context") or "")[:0]) and "부터" not in (e.get("context") or ""):
                    out.append({"kind": "자기 조 인용 (항·호 없이)", "src": e["src"], "detail": f"{e['evidence']} | {(e.get('context') or '')[:60]}", "rel": e["id"]})
        elif tt == "appendix" and st == "ok" and t not in apps:
            out.append({"kind": "별표·별지 대상 없음인데 ok", "src": e["src"], "detail": f"{e['evidence']} → {t}", "rel": e["id"]})
    # 표기 수 vs 관계 수 (본문·부칙 항)
    for iid, (r, a, it, unit) in items.items():
        text = it["text"]
        hist = [(m.start(), m.end()) for m in RE_HISTORY.finditer(text)]
        notes = [(m.start(), m.end()) for m in AMEND_NOTE.finditer(text)] + [(m.start(), m.end()) for m in RE_EFFECTIVE_NOTE.finditer(text)]
        # 따옴표 안(부칙 개정문의 옛 문구 「“직제규정 제5조”를」)은 추출도 일부러 해석하지 않는다
        quotes = [(m.start(), m.end()) for m in re.finditer(r'["“][^"”\n]{1,80}["”]', text)]
        inside = lambda p: any(s <= p < e for s, e in hist + notes + quotes)
        jo = [m for m in RE_JO.finditer(text) if not inside(m.start())]
        ap = [m for m in RE_APPX.finditer(text) if not inside(m.start())]
        got = by_src.get(iid, [])
        n_art = sum(1 for e in got if e["target_type"] in ("article", "law", "chapter") and not re.search(r"별\s*[표지]", e.get("evidence") or ""))
        n_app = sum(1 for e in got if e["target_type"] == "appendix" or "별표" in (e.get("evidence") or "") or "별지" in (e.get("evidence") or "")
                    or re.search(r"별\s*[표지]", e.get("evidence") or ""))   # 법령·외부 기준의 별표·별지 관계 포함
        if len(jo) > n_art:
            out.append({"kind": f"{unit} 조 표기 > 관계", "src": iid, "detail": f"표기 {len(jo)} 관계 {n_art}: " + ", ".join(m.group(0) for m in jo)[:120],
                        "context": text[:160]})
        if len(ap) > n_app:
            out.append({"kind": f"{unit} 별표·별지 표기 > 관계", "src": iid, "detail": f"표기 {len(ap)} 관계 {n_app}: " + ", ".join(m.group(0) for m in ap)[:120],
                        "context": text[:160]})
    return out


def main() -> None:
    regs = load_regs()
    rels = [json.loads(l) for l in (ROOT / "out" / "relations" / "relations.jsonl").read_text(encoding="utf-8").splitlines()]
    try:
        gabin = gabin_articles()
    except Exception as e:   # DB가 없으면 원문 대조만 건너뜀
        print("가빈 DB 없음 — 원문 대조 생략:", str(e).splitlines()[0])
        gabin = None
    s = structure(regs, gabin)
    g = graph(regs, rels)
    out = ROOT / "out" / "audit"
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("structure", s), ("graph", g)):
        keys = sorted({k for r in rows for k in r})
        (out / f"{name}.tsv").write_text("\t".join(keys) + "\n" + "\n".join("\t".join(str(r.get(k, "")).replace("\t", " ").replace("\n", " ")
                                                                             for k in keys) for r in rows) + "\n", encoding="utf-8")
    print("구조:", dict(Counter(r["kind"] for r in s)))
    print("그래프:", dict(Counter(r["kind"] for r in g)))
    print(f"→ {out}")


if __name__ == "__main__":
    main()
