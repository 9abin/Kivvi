"""내규 불일치 후보에 붙이는 힌트: 내규 위계 · 시간 우선(내규관리규정 제7조)과 법령 기준(법률 DB).

판정이 아니라 사람이 볼 단서다 (AGENTS §3). 근거
  내규관리규정 제7조: ① 법령 · 정관에 저촉되는 부분은 효력 상실 ② 규정이 지침보다 우선 ③ 같은 순위끼리는 나중에 제정 · 개정된 것이 우선
  내규관리규정 제2조: 취업규칙은 규정에 포함, 임직원행동강령 · 윤리강령은 지침에 포함
한계: 개정일은 문서 단위 최종 개정일이다 (조문 단위가 아님). 법은 최저 기준일 수 있어 「좁다 · 넓다」의 뜻은 법무가 본다.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date

from .consistency import RE_COUNT, count_limits

RANK_NAME = {3: "정관", 2: "규정", 1: "지침"}


def reg_rank(name: str) -> int | None:
    n = re.sub(r"\s+", "", name or "")
    if n.endswith("정관"):
        return 3
    if n.endswith("지침") or n.endswith("강령"):
        return 1
    if n.endswith("규정") or n.endswith("규칙"):
        return 2
    return None


def parse_date(s: str | None) -> date | None:
    m = re.match(r"(\d{4})[-.]?(\d{2})(?:[-.]?(\d{2}))?", (s or "").replace(" ", ""))
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)) if m else None


def precedence_winner(a_name: str, a_revised: str | None, b_name: str, b_revised: str | None) -> str | None:
    """제7조로 우선하는 쪽의 이름 (가릴 수 없으면 None)."""
    ra, rb = reg_rank(a_name), reg_rank(b_name)
    if ra and rb and ra != rb:
        return a_name if ra > rb else b_name
    da, db = parse_date(a_revised), parse_date(b_revised)
    if ra and ra == rb and da and db and da != db:
        return a_name if da > db else b_name
    return None


def precedence_hint(a_name: str, a_revised: str | None, b_name: str, b_revised: str | None) -> str:
    ra, rb = reg_rank(a_name), reg_rank(b_name)
    if ra and rb and ra != rb:
        hi, lo = (a_name, b_name) if ra > rb else (b_name, a_name)
        return f"내규관리규정 제7조②: {RANK_NAME[max(ra, rb)]}인 「{hi}」가 {RANK_NAME[min(ra, rb)]}인 「{lo}」보다 우선"
    da, db = parse_date(a_revised), parse_date(b_revised)
    if ra and ra == rb and da and db and da != db:
        later = a_name if da > db else b_name
        return f"내규관리규정 제7조③: 같은 순위({RANK_NAME[ra]})라 나중에 개정된 「{later}」({max(da, db).isoformat()})가 우선하는 것으로 읽힘 (문서 단위 개정일, 조문 단위 아님)"
    return "내규관리규정 제7조로 우선순위를 가리지 못함 (순위가 불분명하거나 개정일이 같음)"


def first_count(sentence: str) -> int | None:
    m = RE_COUNT.search(sentence or "")
    return int(m.group(1)) if m else None


def law_counts(con: sqlite3.Connection, term: str, context: str = "", limit: int = 2) -> list[dict]:
    """법률 DB에서 같은 말(term)에 대해 횟수 한도를 정한 법령 조문: [{law, path, count, sentence}]. 법률을 시행령 · 시행규칙보다 앞에 둔다.
    context(내규의 한도 문장)에 분할 · 나누어가 있으면 법령 문장에도 있어야 같은 종류의 한도로 본다 (육아휴직 「주 단위 1회」 같은 다른 한도를 거른다)."""
    must = [w for w in ("나누어", "분할") if w in (context or "")]
    out, seen = [], set()
    rows = con.execute("select d.api_title, c.path, c.content from chunks c join documents d on d.id = c.document_id where c.content like ? limit 400", (f"%{term}%",)).fetchall()
    for title, path, content in sorted(rows, key=lambda r: bool(re.search(r"시행령|시행규칙", r[0] or ""))):
        for n, sent in count_limits(content).items():
            if term in sent and (not must or any(w in sent for w in must)) and (title, path) not in seen:
                seen.add((title, path))
                out.append({"law": title, "path": path, "count": first_count(sent) or n, "sentence": sent})
                break
        if len(out) >= limit:
            break
    return out


def article_label(path: str) -> str:
    m = re.match(r"(\d+)(?:의(\d+))?", (path or "").split()[0] if path else "")
    return f"제{m.group(1)}조" + (f"의{m.group(2)}" if m and m.group(2) else "") if m else path


def floor_hint(internal_name: str, internal_count: int | None, laws: list[dict]) -> str:
    if not laws or internal_count is None:
        return ""
    lw = laws[0]
    rel = "같음" if internal_count == lw["count"] else ("법령보다 적게 정함" if internal_count < lw["count"] else "법령보다 많게 정함")
    return f"「{internal_name}」 {internal_count}회는 {lw['law']} {article_label(lw['path'])}의 {lw['count']}회와 {rel} (법령 문구: {lw['sentence'][:70]}…)"


def agrees_with_law(internal_count: int | None, laws: list[dict]) -> bool | None:
    return None if not laws or internal_count is None else internal_count == laws[0]["count"]


def apply_article7(a: dict, b: dict, laws: list[dict]) -> dict:
    """내규관리규정 제7조를 ① 법령 · 정관 → ② 규정 · 지침 → ③ 시간 순서대로 적용해 결과를 제안한다 (먼저 결정되는 단계에서 멈춘다).
    a, b = {name, revised, count}. ①은 법령 문장이 「할 수 있다」 꼴의 사용 한도(권리의 최저 기준)일 때만 「내규가 법령보다 적으면 저촉 가능」으로 본다 (그 가정을 결과에 적는다).
    반환 {steps: [...], decided: 단계, keep: 유지될 쪽 이름 | None, change: 고칠 쪽 | None, text}. 확정이 아니라 내규담당부서 · 준법감시인 확인 전의 제안이다."""
    steps: list[str] = []
    if laws and a.get("count") is not None and b.get("count") is not None:
        lw = laws[0]
        permission = "수 있" in lw["sentence"]
        below = [x for x in (a, b) if x["count"] < lw["count"]]
        if permission and len(below) == 1:
            low, high = below[0], a if below[0] is b else b
            steps.append(f"① 법령: {lw['law']} {article_label(lw['path'])}는 {lw['count']}회(사용 한도 → 최저 기준으로 가정). 「{low['name']}」 {low['count']}회는 법령보다 적어 저촉 가능 → 그 부분 효력 상실 가능")
            return {"steps": steps, "decided": 1, "keep": high["name"], "change": low["name"], "text": " / ".join(steps) + f" ⇒ 「{high['name']}」 유지, 「{low['name']}」 개정 검토 (②③은 적용 안 함)"}
        steps.append("① 법령: 법령 문구와 어느 쪽도 가려지지 않음" if laws else "① 법령: 비교할 법령 문구 없음")
    else:
        steps.append("① 법령: 비교할 법령 문구 없음")
    w = precedence_winner(a["name"], None, b["name"], None) if reg_rank(a["name"]) != reg_rank(b["name"]) else None
    if w:
        loser = b["name"] if w == a["name"] else a["name"]
        steps.append(f"② 종류: {RANK_NAME[reg_rank(w)]}인 「{w}」가 우선")
        return {"steps": steps, "decided": 2, "keep": w, "change": loser, "text": " / ".join(steps) + f" ⇒ 「{w}」 우선"}
    steps.append("② 종류: 같은 순위이거나 불분명")
    w = precedence_winner(a["name"], a.get("revised"), b["name"], b.get("revised"))
    if w:
        loser = b["name"] if w == a["name"] else a["name"]
        steps.append(f"③ 시간: 나중에 개정된 「{w}」 우선 (문서 단위 개정일)")
        return {"steps": steps, "decided": 3, "keep": w, "change": loser, "text": " / ".join(steps) + f" ⇒ 「{w}」 우선"}
    steps.append("③ 시간: 개정일이 같거나 알 수 없음")
    return {"steps": steps, "decided": None, "keep": None, "change": None, "text": " / ".join(steps) + " ⇒ 제7조로 가릴 수 없음"}
