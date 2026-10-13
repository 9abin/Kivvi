"""가빈 챗봇 청크 전수 점검: public.chunks의 청크를 하나씩 원문·우리 파싱과 대조해 청크 결함을 찾는다.

  uv run --extra db python scripts/chunk_audit.py
출력: out/audit/chunks.tsv (결함 한 줄씩), 요약 출력
읽는 칸: chunk_id, doc_id, article, status, body 만 (보정 칸 body_search·corrections_note·cites·body_tokens는 읽지 않음)

청크 하나씩
  번호        chunk_id의 조·항과 본문 첫머리(항 기호)가 맞는지, article 칸이 chunk_id와 맞는지
  경계        다른 조의 머리(「제N조(」)가 줄 머리에 섞임, 장·절 제목 붙음, 쪽 머리말·쪽 번호, 부칙·별표가 본문 청크에 섞임
  글자        깨진 글자, 비슷한 모양 원문자(ⓛ ➀), 짝 안 맞는 꺾쇠(「<2012.2.23.>」이 「>」만 남음 등)
  상태        status 「삭제」인데 본문이 있음 / 「정상」인데 본문이 삭제 표시뿐
  번호 순서   한 청크 안 호(1. 2. …)·목(가. 나. …) 건너뜀·역순
  중복        같은 본문이 다른 청크에 또 있음
  내용        우리 파싱의 같은 조·항과 정규화 비교 → 가빈에만 있는 글(8자 이상) / 우리에만 있는 글(빠진 글)
문서 전체
  짝          우리 조·항에 대응 청크가 없음 / 청크에 대응하는 우리 조·항이 없음
  원문 누락   가빈 원문 텍스트(out/text/ID.txt)의 줄이 어느 청크에도 없음 (제1조 앞 머리 부분은 따로 표시)
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.parse import CIRCLED, LOOKALIKE  # noqa: E402

GARBLE = re.compile(r"[Ā-ɏ஀-௿-�\x00-\x08\x0b-\x1f]")
LOOK = re.compile("[ⓛ" + "".join(chr(0x2780 + i) for i in range(20)) + "]")
ART_HEAD = re.compile(r"^\s*(제\d+조(?:의\d+)?)\s*\(")
CHAPTER = re.compile(r"^\s*제\s*\d+\s*[장절관]\s*\S")
PAGE_NO = re.compile(r"^\s*[-–]\s*\d{1,3}\s*[-–]\s*$")
HO = re.compile(r"^\s*(\d+)(?:의(\d+))?\.(?!\d)\s*\S")
HO_GLUED = re.compile(r"(?:[가-힣)」』]|(?<!\d)\.)[ \t]*(\d{1,2})\.[ \t]*[가-힣「『“]")
HANG_GLUED = re.compile(r"[다음함요]\.?[ \t]*([①-⑳])[ \t]*[가-힣「제]")
MIDBREAK = re.compile(r"[가-힣]\n(?!\s*(?:\d+(?:의\d+)?\.|[가-하]\.|[①-⑳]|\[|<|【|제\s*\d+\s*[조장절관]|\(|※|-|·|○|◦|□|■|▶|부\s*칙|별\s*[표지]))[가-힣]")
MOK = re.compile(r"^\s*([가-하])\.\s*(?=\S)(?![다니요])")
GANADA = "가나다라마바사아자차카타파하"
AMEND_NOTE = re.compile(r"[<\[(]\s*(?:개정|신설|삭제|전문개정|본조신설|제목개정|종전|시행일)[^>\])]*[>\])]")
DELETED_ONLY = re.compile(r"^\s*(?:[①-⑳]\s*)?(?:\d+\.\s*)?(?:삭\s*제|\[?삭제\]?)\s*(?:[<\[(][^>\])]*[>\])])?\s*$")
KEEP = re.compile(r"[가-힣A-Za-z0-9]")


def alnum(s: str) -> str:
    return "".join(KEEP.findall(AMEND_NOTE.sub("", (s or "").translate(LOOKALIKE))))


def load_chunks() -> list[dict]:
    from regcheck.db import connect, safe_select

    rows = safe_select(connect(), "chunks", ["chunk_id", "doc_id", "article", "status", "body"])
    return [dict(zip(("chunk_id", "doc_id", "article", "status", "body"), r)) for r in rows]


def load_regs() -> dict[str, dict]:
    return {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "out" / "parsed").glob("[0-9]*.json")))}


def kind_of(cid: str) -> tuple[str, str | None, int]:
    """(종류, 조 표기, 항). 종류: 본문 · 부칙 · 별표 · 별지 · 기타"""
    m = re.match(r"^\d+_(제\d+조(?:의\d+)?)(?:_(\d+)항)?$", cid)
    if m:
        return "본문", m.group(1), int(m.group(2) or 0)
    for k in ("부칙", "별표", "별지"):
        if f"_{k}" in cid:
            return k, None, 0
    return "기타", None, 0


def our_units(regs: dict[str, dict]) -> dict[tuple[str, str, int], dict]:
    """(규정, 조, 항) → 우리 본문. 항이 없거나 하나뿐인 조는 항 0과 1 둘 다로 찾을 수 있게."""
    out = {}
    for r in regs.values():
        for a in r["articles"]:
            by_h: dict[int, list[str]] = defaultdict(list)
            for it in a["items"]:
                by_h[it["path"][0]].append(it["text"])
            hs = sorted(by_h) or [0]
            for h in hs:
                u = {"text": " ".join(by_h.get(h, [])), "deleted": bool(a.get("deleted")) or all(
                    it.get("deleted") for it in a["items"] if it["path"][0] == h) and bool(by_h.get(h)), "title": a.get("title", ""), "all": a["text"]}
                out[(r["reg_id"], a["label"], h)] = u
            if hs in ([0], [1]):
                whole = {"text": " ".join(" ".join(v) for v in by_h.values()), "deleted": bool(a.get("deleted")), "title": a.get("title", ""), "all": a["text"]}
                out[(r["reg_id"], a["label"], 0)] = whole
                out[(r["reg_id"], a["label"], 1)] = whole
    return out


def per_chunk(c: dict, regs: dict[str, dict], add) -> None:
    cid, body, st = c["chunk_id"], c["body"] or "", c["status"]
    kind, label, h = kind_of(cid)
    lines = body.splitlines()
    title = regs.get(c["doc_id"], {}).get("name", "")
    # 번호
    if kind == "본문":
        art = c["article"] or ""
        if not art.startswith(label) or (h and f"제{h}항" not in art):
            add(c, "번호", "article 칸 불일치", f"article「{art}」")
        if h:
            first = body.lstrip().translate(LOOKALIKE)[:1]
            if first in CIRCLED and CIRCLED.index(first) + 1 != h:
                add(c, "번호", "항 기호와 chunk_id 불일치", f"본문 첫 기호 {first}, id {h}항")
            elif first not in CIRCLED and not DELETED_ONLY.match(body):
                add(c, "번호", "항 청크인데 항 기호로 시작 안 함", body[:40])
    # 경계
    for i, ln in enumerate(lines):
        m = ART_HEAD.match(ln)
        if kind == "본문" and m and m.group(1) != label and not (i == 0 and h in (0, 1)):
            add(c, "경계", "다른 조 머리가 섞임", ln.strip()[:60])
        if CHAPTER.match(ln):
            add(c, "경계", "장·절 제목 붙음", ln.strip()[:40])
        if PAGE_NO.match(ln) or (title and ln.strip().replace(" ", "") == title.replace(" ", "") and kind == "본문"):
            add(c, "경계", "쪽 번호·머리말 섞임", ln.strip()[:40])
        if kind == "본문" and re.match(r"^\s*(?:부\s*칙|\[?별\s*[표지])", ln):
            add(c, "경계", "본문 청크에 부칙·별표·별지 섞임", ln.strip()[:40])
    if kind == "본문" and h == 0 and label and not ART_HEAD.match(lines[0] if lines else "") and not DELETED_ONLY.match(body):
        add(c, "경계", "조 청크가 조 머리로 시작 안 함", body[:40])
    # 글자
    if GARBLE.search(body):
        add(c, "글자", "깨진 글자", repr(GARBLE.findall(body)[:5]))
    if LOOK.search(body):
        add(c, "글자", "비슷한 모양 원문자", "".join(sorted(set(LOOK.findall(body)))))
    for ln in lines:
        s = ln.strip()
        if s in (">", "<", "]", "[") or (s.count("<") != s.count(">") and re.search(r"\d{4}\.\s*\d", s)):
            add(c, "글자", "꺾쇠 짝 안 맞음(날짜 표시 깨짐)", s[:50])
            break
    # 상태
    deleted_body = bool(DELETED_ONLY.match(body)) or len(alnum(body)) <= 12 and "삭제" in body
    if st == "삭제" and not deleted_body and len(alnum(body)) > 30:
        add(c, "상태", "삭제 상태인데 본문 있음", body[:50])
    if st == "정상" and deleted_body:
        add(c, "상태", "정상 상태인데 본문이 삭제 표시뿐", body[:50])
    if len(alnum(body)) < 4 and not deleted_body:
        add(c, "글자", "본문이 거의 없음", repr(body[:30]))
    if kind == "본문":
        mids = MIDBREAK.findall(body)
        if mids:
            i = MIDBREAK.search(body).start()
            add(c, "글자", "문장 중간 줄바꿈", f"{len(mids)}곳 · 예「{body[max(0, i - 12):i + 14]!r}」")
        hg = [m.group(1) for m in HANG_GLUED.finditer(body.translate(LOOKALIKE)) if m.start() > 0]
        if hg:
            add(c, "경계", "다른 항이 한 청크에 섞임", "".join(hg) + " · " + body[:30].replace("\n", " "))
        glued = [m.group(1) for m in HO_GLUED.finditer(body) if int(m.group(1)) > 1]
        if glued and any(int(g) - 1 == int(h_.group(1)) for g in glued for h_ in HO.finditer(body)) or len(glued) >= 2:
            add(c, "경계", "호가 앞 줄에 붙음", ", ".join(glued) + ".")
    # 번호 순서 (본문 청크만: 별표·부칙은 표·여러 부칙이라 제외)
    if kind == "본문":
        prev_ho, prev_mok = 0, -1
        for ln in lines:
            m = HO.match(ln)
            if m and not m.group(2):
                n = int(m.group(1))
                if prev_ho and n != prev_ho + 1:
                    add(c, "번호 순서", "호 번호 건너뜀·역순", f"{prev_ho}. 다음 {n}.")
                prev_ho, prev_mok = n, -1
                continue
            m = MOK.match(ln)
            if m:
                k = GANADA.find(m.group(1))
                if k != prev_mok + 1:
                    add(c, "번호 순서", "목 기호 건너뜀·역순", f"{GANADA[prev_mok] if prev_mok >= 0 else '(처음)'} 다음 {m.group(1)}")
                prev_mok = k


def content(c: dict, ours: dict, add) -> None:
    kind, label, h = kind_of(c["chunk_id"])
    if kind != "본문":
        return
    u = ours.get((c["doc_id"], label, h))
    if u is None:
        return
    g = c["body"] or ""
    g = re.sub(r"^\s*" + re.escape(label) + r"\s*\([^)\n]*(?:\([^)\n]*\)[^)\n]*)*\)", "", g)  # 조 머리 제거 (우리 항 본문에는 없음)
    a, b = alnum(u["text"]), alnum(g)
    if not a and not b:
        return
    sm = SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        mine, theirs = a[i1:i2], b[j1:j2]
        if len(theirs) >= 8 and tag in ("insert", "replace"):
            add(c, "내용", "가빈에만 있는 글", f"가빈「{theirs[:50]}」" + (f" / 우리「{mine[:30]}」" if mine else ""))
        if len(mine) >= 8 and tag in ("delete", "replace"):
            add(c, "내용", "가빈에 빠진 글", f"우리「{mine[:50]}」" + (f" / 가빈「{theirs[:30]}」" if theirs else ""))


def coverage(chunks: list[dict], regs: dict[str, dict], ours: dict, add, add_doc) -> None:
    have = {(c["doc_id"], *kind_of(c["chunk_id"])[1:]) for c in chunks if kind_of(c["chunk_id"])[0] == "본문"}
    for r in regs.values():
        for a in r["articles"]:
            hs = sorted({it["path"][0] for it in a["items"]}) or [0]
            for hh in hs:
                if (r["reg_id"], a["label"], hh) in have or (hs in ([0], [1]) and {(r["reg_id"], a["label"], 0), (r["reg_id"], a["label"], 1)} & have):
                    continue
                add_doc(r["reg_id"], "짝", "우리 조·항에 대응 청크 없음", f"{a['label']}" + (f" {hh}항" if hh else "") + ("(삭제된 조)" if a.get("deleted") else ""))
    for c in chunks:
        kind, label, h = kind_of(c["chunk_id"])
        if kind == "본문" and (c["doc_id"], label, h) not in ours:
            add(c, "짝", "청크에 대응하는 우리 조·항 없음", (c["body"] or "")[:40])
    # 가빈 원문 텍스트의 줄이 어느 청크에도 없는지. 줄을 조 머리·항 기호에서 나눠 조각마다 본다
    by_doc: dict[str, str] = defaultdict(str)
    titled: set[tuple[str, str]] = set()
    for c in chunks:
        by_doc[c["doc_id"]] += alnum(c["body"])
        m = ART_HEAD.match(c["body"] or "")
        if m:
            titled.add((c["doc_id"], m.group(1)))
    no_title = [(r["reg_id"], a["label"]) for r in regs.values() for a in r["articles"]
                if not a.get("deleted") and (r["reg_id"], a["label"]) not in titled]
    for rid, label in no_title:
        add_doc(rid, "경계", "조 제목이 어느 청크 본문에도 없음", label)
    for p in sorted((ROOT / "out" / "text").glob("[0-9]*.txt")):
        if "_" in p.stem or p.stem not in by_doc:
            continue
        allc = by_doc[p.stem]
        started = False
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if re.match(r"^\s*제1조\s*\(", ln):
                started = True
            body = re.sub(r"^\s*제\d+조(?:의\d+)?\s*\([^)]*(?:\([^)]*\)[^)]*)*\)", "", ln)
            for piece in re.split(r"[①-⑳]", body.translate(LOOKALIKE)):
                s = alnum(piece)
                if len(s) >= 12 and s not in allc:
                    add_doc(p.stem, "원문 누락", "원문 줄이 어느 청크에도 없음" + ("" if started else "(제1조 앞 머리)"), piece.strip()[:70])


def main() -> None:
    regs = load_regs()
    chunks = load_chunks()
    ours = our_units(regs)
    out: list[dict] = []
    names = {k: v["name"] for k, v in regs.items()}

    def add(c, cat, kind, detail):
        out.append({"reg_id": c["doc_id"], "reg_name": names.get(c["doc_id"], ""), "chunk_id": c["chunk_id"], "category": cat, "kind": kind, "detail": detail})

    def add_doc(rid, cat, kind, detail):
        out.append({"reg_id": rid, "reg_name": names.get(rid, ""), "chunk_id": "", "category": cat, "kind": kind, "detail": detail})

    for c in chunks:
        per_chunk(c, regs, add)
        content(c, ours, add)
    norm_bodies = defaultdict(list)
    for c in chunks:
        k = alnum(c["body"])
        if len(k) > 30:
            norm_bodies[k].append(c["chunk_id"])
    for ids in norm_bodies.values():
        if len(ids) > 1:
            for cid in ids:
                add(next(c for c in chunks if c["chunk_id"] == cid), "중복", "같은 본문이 다른 청크에도 있음", ", ".join(i for i in ids if i != cid)[:80])
    coverage(chunks, regs, ours, add, add_doc)

    dst = ROOT / "out" / "audit" / "chunks.tsv"
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]) if out else ["reg_id"], delimiter="\t")
        w.writeheader()
        w.writerows(out)
    print(f"청크 {len(chunks)} · 결함 후보 {len(out)} · 청크 {len({o['chunk_id'] for o in out if o['chunk_id']})}개 · 규정 {len({o['reg_id'] for o in out})}개")
    for (cat, kind), n in sorted(Counter((o["category"], o["kind"]) for o in out).items()):
        print(f"  {cat:6s} {kind:28s} {n}")
    print("→", dst.relative_to(ROOT))


if __name__ == "__main__":
    main()
