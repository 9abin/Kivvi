"""규정 폴더 → 메타데이터 + 조문 단위 레코드(JSON).

조문 ID (임시, 김가빈·한석희와 합의 전):
  본문 조      {규정ID}:{조}            예: 24125:3, 24125:8의2
  항·호·목    {조ID}-{항}-{호}-{목}     예: 24125:3-1-1-0  (없는 단계는 0)
  부칙 조      {규정ID}:부칙{순번}:{조}  부칙 순번은 문서 안 등장 순서(1부터)
  별표·별지    {규정ID}:{별표|별지}{번호}  예: 48441:별표4, 40209:별지제3호서식
규정ID는 ALIO 규정 번호(폴더명 끝 숫자)를 쓴다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .extract import extract
from .normalize import amend_marks, compact, index_header, normalize
from .parse import RE_ADDENDUM, RE_ARTICLE, RE_CHAPTER, ParsedRegulation, parse

SOURCE_EXTS = (".hwp", ".hwpx", ".pdf")


def hierarchy(name: str) -> str:
    """내규관리규정 제2조: 취업규칙은 규정, 임직원행동강령·윤리강령은 지침에 포함."""
    if name.endswith("정관"):
        return "정관"
    if name.endswith(("규정", "규칙")):
        return "규정"
    if name.endswith(("지침", "강령")):
        return "지침"
    return "기타"


def revision_date(filename: str) -> str:
    m = re.search(r"(\d{4})\s*년\s*(?:도\s*)?(\d{1,2})\s*월(?:\s*(\d{1,2})\s*일)?", filename)
    if not m:
        m2 = re.search(r"\((\d{2})(\d{2})(\d{2})\)", filename)  # 예: (131011)
        return f"20{m2.group(1)}-{m2.group(2)}-{m2.group(3)}" if m2 else ""
    y, mo, d = m.group(1), int(m.group(2)), m.group(3)
    return f"{y}-{mo:02d}" + (f"-{int(d):02d}" if d else "")


def _sources(root: Path) -> dict[str, str]:
    path = root / "다운로드목록.json"
    if not path.exists():
        return {}
    return {e["filename"]: e["source"] for e in json.loads(path.read_text(encoding="utf-8"))}


def article_id(reg_id: str, no: int, branch: int) -> str:
    return f"{reg_id}:{no}" + (f"의{branch}" if branch else "")


def to_records(reg_id: str, parsed: ParsedRegulation) -> dict:
    def art_rec(a, prefix):
        aid = prefix + f"{a.no}" + (f"의{a.branch}" if a.branch else "")
        return {
            "id": aid,
            "label": a.label,
            "title": a.title,
            "chapter": a.chapter,
            "deleted": a.deleted,
            "amend_notes": a.amend_notes,
            "text": a.text,
            "norm": normalize(a.text),
            "items": [
                {
                    "id": f"{aid}-{p}-{i}-{s}",
                    "path": [p, i, s],
                    "text": it.text,
                    "norm": normalize(it.text),
                    "amend_marks": amend_marks(it.text),
                    "deleted": it.deleted,
                }
                for it in a.items
                for (p, i, s) in [it.path]
            ],
        }

    return {
        "articles": [art_rec(a, f"{reg_id}:") for a in parsed.articles],
        "addenda": [
            {
                "id": f"{reg_id}:부칙{n}",
                "date": ad.date,
                "text": ad.text,
                "articles": [art_rec(a, f"{reg_id}:부칙{n}:") for a in ad.articles],
            }
            for n, ad in enumerate(parsed.addenda, 1)
        ],
        "appendices": [
            {
                "id": f"{reg_id}:{ap.kind}{re.sub(r'\s', '', ap.number)}",
                "kind": ap.kind,
                "number": ap.number,
                "title": ap.title,
                "text": ap.text,
            }
            for ap in parsed.appendices
        ],
        "preamble": parsed.preamble,
    }


def build(folder: Path, sources: dict[str, str] | None = None) -> dict:
    name, reg_id = folder.name.rsplit("_", 1)
    src = next(p for p in sorted(folder.iterdir()) if p.suffix.lower() in SOURCE_EXTS)
    lines = extract(src)
    # 머리말(목차 번호 + 문서명) 줄은 본문이 아니지만, 다른 규정명이 들어간 경우가 있어 원문 확인용으로 남긴다
    body_start = next(
        (i for i, l in enumerate(lines) if RE_CHAPTER.match(l) or RE_ARTICLE.match(l) or RE_ADDENDUM.match(l)),
        len(lines),
    )
    # 본문 중간의 쪽 머리말(「4-3-3. 도서관리지침」)은 앞 항의 이어지는 줄로 붙으므로 뺀다.
    # 목차 번호에 하이픈이 있고 이름이 이 문서와 같을 때, 또는 줄 전체가 이 문서 이름일 때만 (「1. …」 같은 호 줄은 건드리지 않음)
    lines = lines[:body_start] + [l for l in lines[body_start:]
                                  if not (re.match(r"^\s*\d+-\d+", l) and (h := index_header(l)) and compact(h) == compact(name))
                                  and compact(l) != compact(name)]   # 이름만 있는 머리말 (「정보보안관리지침」)
    parsed = parse(lines)
    headers = sorted({h for l in lines[:body_start] if (h := index_header(l))})
    header_mismatch = [h for h in headers if compact(h) != compact(name)]
    return {
        "reg_id": reg_id,
        "name": name,
        "hierarchy": hierarchy(name),
        "revised": revision_date(src.name),
        "file": src.name,
        "source_url": (sources or {}).get(src.name, ""),
        "title_raw": parsed.title,
        "headers": headers,
        "header_mismatch": header_mismatch,
        "history": [h for h in parsed.history if not index_header(h)],
        **to_records(reg_id, parsed),
    }


def build_all(root: Path, out: Path, names: list[str] | None = None) -> list[dict]:
    sources = _sources(root)
    out.mkdir(parents=True, exist_ok=True)
    regs = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if names and folder.name.rsplit("_", 1)[0] not in names:
            continue
        reg = build(folder, sources)
        (out / f"{reg['reg_id']}.json").write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
        regs.append(reg)
    return regs
