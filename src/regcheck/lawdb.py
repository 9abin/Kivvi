"""법률 DB(한석희, data/law_rag.sqlite3) 읽기 전용 접근과 법령 개정 신호.

DB: 법제처 현행법령(eflaw) 원본 JSON(documents.raw_json)과 조문 청크. 조문마다 조문시행일자·조문변경여부(이번 개정에서 바뀜)·
조문이동이전/이후가 있고, 법령마다 개정문(「제23조제2항 중 "A"를 "B"로 한다」)과 제개정구분·공포일자·시행일자가 있다.
스냅샷 두 개 없이도 "이 법령 조문은 최근 개정에서 바뀌었다"를 알 수 있다 (필수 ③).

법령 개정 신호 (우리 규정이 인용한 법령 조문 기준)
  높음  인용한 법령 조문이 이동·삭제됨 → 번호가 다른 조문을 가리킬 수 있음
  중간  인용한 법령 조문이 최근 개정에서 바뀌었고(조문변경여부=Y), 그 시행일이 우리 인용 조문의 마지막 개정보다 뒤
  (참고) 인용한 법령은 DB에 있으나 그 조가 없음 → 대상 없음
판정이 아니라 대조 요청이다: 법령이 바뀌어도 규정을 고칠 필요가 없을 수 있다 (AGENTS §3).
"""

from __future__ import annotations

import json
import re
import sqlite3
from functools import cached_property
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "data" / "law_rag.sqlite3"
RE_ART = re.compile(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?")
# 규정 원문에서 자주 쓰는 약칭 (규정 안에 「(이하 "…"이라 한다)」 정의가 없는 경우용)
ABBR = {
    "공공기록물법": "공공기록물 관리에 관한 법률",
    "특정금융정보법": "특정 금융거래정보의 보고 및 이용 등에 관한 법률",
    "청탁금지법": "부정청탁 및 금품등 수수의 금지에 관한 법률",
    "개인정보보호법": "개인정보 보호법",
    "벤처투자법": "벤처투자 촉진에 관한 법률",
    "이해충돌방지법": "공직자의 이해충돌 방지법",
    "테러자금금지법": "공중 등 협박목적 및 대량살상무기확산을 위한 자금조달행위의 금지에 관한 법률",
    "국가계약법": "국가를 당사자로 하는 계약에 관한 법률",
    "지방계약법": "지방자치단체를 당사자로 하는 계약에 관한 법률",
    "퇴직급여보장법": "근로자퇴직급여 보장법",
}
# 내규가 법령 이름을 틀리게 쓴 경우 (정식 이름 → 연결은 하되 「법령명_오기」로 알림). 글자 차이가 커서 close_title로 못 잡는 것만
MISNAMED = {
    "여성폭력기본법": "여성폭력방지기본법",   # 성희롱·성폭력 예방 운영지침 제3조
}
# 법령 이름이 바뀐 경우 (옛 이름 → 지금 이름). 바로 전 이름은 법률 DB 기본정보의 「이전법령명」에서 자동으로 읽고,
# 그보다 앞 이름(두 번 이상 바뀜)만 여기 적는다. 규정이 옛 이름을 쓰면 「법령명_변경」 신호 (261012 점검)
RENAMED = {
    "행정업무의 효율적 운영에 관한 규정": "행정업무의 운영 및 혁신에 관한 규정",   # 2023.10 개정
    "소프트웨어산업 진흥법": "소프트웨어 진흥법",                                   # 2020.12 시행
    "민원사무처리에 관한 법률": "민원 처리에 관한 법률",                             # 2015 전부개정 (DB에 이전법령명 기록 없음)
    "전자거래기본법": "전자문서 및 전자거래 기본법",                                 # 2012 (DB에 이전법령명 기록 없음)
}


def norm(s: str) -> str:
    return re.sub(r"[\s·ㆍ‧・]", "", s or "")


def _d8(s: str | None) -> tuple | None:
    return (int(s[:4]), int(s[4:6]), int(s[6:8])) if s and len(s) >= 8 and s[:8].isdigit() else None


class LawDB:
    def __init__(self, path: Path = DEFAULT):
        self.path = Path(path)
        self.con = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)

    @cached_property
    def laws(self) -> dict[str, dict]:
        """정규화 이름 → 법령 정보 {id, title, 시행일, 공포일, 구분, 개정문, 조문{key: unit}}"""
        out = {}
        for did, req, api, raw in self.con.execute("select id, requested_title, api_title, raw_json from documents"):
            j = json.loads(raw)["법령"]
            info = j.get("기본정보", {})
            units = {}
            for u in (j.get("조문") or {}).get("조문단위", []) or []:
                if u.get("조문여부") != "조문":
                    continue
                units[u["조문번호"] + (f"의{u['조문가지번호']}" if u.get("조문가지번호") else "")] = u
            amend = j.get("개정문") or {}
            text = amend.get("개정문내용") if isinstance(amend, dict) else None
            flat = "\n".join(x for row in (text or []) for x in (row if isinstance(row, list) else [row]) if isinstance(x, str))
            law = {"id": did, "title": api or req, "시행일": info.get("시행일자"), "공포일": info.get("공포일자"),
                   "구분": info.get("제개정구분"), "개정문": flat, "조문": units, "이전법령명": info.get("이전법령명") or ""}
            for t in (req, api):
                if t:
                    out.setdefault(norm(t), law)
        return out

    @cached_property
    def previous_names(self) -> dict[str, str]:
        """옛 법령명(정규화) → 지금 법령명. 법률 DB 「이전법령명」 + RENAMED"""
        out = {norm(k): v for k, v in RENAMED.items()}
        for law in self.laws.values():
            if law["이전법령명"] and norm(law["이전법령명"]) not in self.laws:
                out.setdefault(norm(law["이전법령명"]), law["title"])
        return out

    def find(self, name: str, aliases: dict[str, str] | None = None) -> dict | None:
        """이름 → 법령. 그대로, 규정 약칭 사전, 일반 약칭, 「약칭 + 시행령·시행규칙」 순서로 찾는다."""
        aliases = aliases or {}
        renamed = self.previous_names
        name = re.sub(r"\s*\(이하[^)]*\)\s*$", "", name or "").strip()   # 「…시행령(이하 "국가계약법 시행령"이라 한다)」
        misnamed = {norm(k): v for k, v in MISNAMED.items()}
        for cand in (name, aliases.get(name, ""), ABBR.get(norm(name), ""), renamed.get(norm(name), ""), misnamed.get(norm(name), "")):
            if cand and norm(cand) in self.laws:
                return self.laws[norm(cand)]
        # 앞이 잘린 이름(「금품 등 수수의 금지에 관한 법률」): 정확히 한 법령의 끝부분과 같을 때만 (12자 이상)
        key = norm(name)
        if len(key) >= 12:
            tails = {law["id"]: law for k, law in self.laws.items() if k.endswith(key) and k != key}
            if len(tails) == 1:
                return next(iter(tails.values()))
        m = re.match(r"^(.*?)\s*(시행령|시행규칙)$", name or "")
        if m and m.group(1):
            base = aliases.get(m.group(1)) or ABBR.get(norm(m.group(1))) or m.group(1)
            if norm(base + m.group(2)) in self.laws:
                return self.laws[norm(base + m.group(2))]
        return None

    def close_title(self, name: str, cutoff: float = 0.95) -> str | None:
        """찾지 못한 이름과 거의 같은 법령명 (오기 의심용, 글자 한두 개 차이). 연결에는 쓰지 않는다."""
        from difflib import SequenceMatcher

        key = norm(re.sub(r"\s*\(이하[^)]*\)\s*$", "", name or ""))
        if len(key) < 8:
            return None
        best, score, edits = None, 0.0, 99
        for k, law in self.laws.items():
            sm = SequenceMatcher(None, key, k)
            sc = sm.ratio()
            if sc > score:
                best, score = law["title"], sc
                edits = sum(max(i2 - i1, j2 - j1) for t, i1, i2, j1, j2 in sm.get_opcodes() if t != "equal")
        # 글자 하나 차이는 짧은 이름도 오기로 본다 — 0.95만 보면 10자 이름의 한 글자 누락(0.947)을 놓쳤다 (261014 오류 주입 평가)
        return best if score >= cutoff or edits <= 1 else None

    def same_title(self, title: str, limit: int = 5) -> list[tuple[dict, str, dict]]:
        """조문 제목이 같은 법령 조문 [(법령, 조 키, 조문)] — 변경안 점검의 법령 대조 후보 (예: 육아휴직 → 남녀고용평등법 제19조)"""
        if not title:
            return []
        out, seen = [], set()
        for law in self.laws.values():
            if law["id"] in seen:
                continue
            seen.add(law["id"])
            for key, u in law["조문"].items():
                if (u.get("조문제목") or "") == title:
                    out.append((law, key, u))
        # 법률 → 시행령 → 시행규칙 순, 이름 짧은 순
        out.sort(key=lambda x: (("시행령" in x[0]["title"]) + 2 * ("시행규칙" in x[0]["title"]), len(x[0]["title"])))
        return out[:limit]

    def article(self, law: dict, ref: str) -> tuple[str, dict | None]:
        m = RE_ART.search(ref or "")
        if not m:
            return "", None
        key = m.group(1) + (f"의{m.group(2)}" if m.group(2) else "")
        return key, law["조문"].get(key)


def amendment_lines(law: dict, key: str, limit: int = 5) -> list[str]:
    """개정문에서 그 조를 고친 문장 (예: 「제23조제2항 중 "훼손되지"를 …로 한다.」).
    타법개정이면 개정문은 다른 법의 부칙이므로 「{이 법} 일부를 다음과 같이 개정한다」 이후 부분만 본다."""
    text = law["개정문"]
    head = re.search(re.escape(law["title"]) + r"\s*(?:일부|전부)를\s*다음과\s*같이\s*개정한다", text)
    if head:
        text = text[head.end():]
        nxt = re.search(r"\n\s*[①-⑳]\s|\n\s*제\d+조\(다른", text)
        text = text[: nxt.start()] if nxt else text[:1500]
    no, _, br = key.partition("의")
    pat = re.compile(rf"(?:^|\s)제{no}조" + (rf"의{br}" if br else r"(?!의\d)") + r"(?:제\d+항|제\d+호|\s|를|중|의|부터)")
    verb = re.compile(r"(로 한다|삭제한다|신설한다|같이 한다)$")
    return [x.strip().lstrip(".").strip() for x in re.split(r"(?<=다)\.\s*", text) if pat.search(x) and verb.search(x.strip())][:limit]


def touches_cited(lines: list[str], key: str, raw: str) -> bool:
    """개정문이 우리가 인용한 항·호를 건드리나. 판단할 수 없으면 True (알림을 남긴다).
    예: 인용 「제2조제6호」, 개정문 「제2조제3호마목 중 … 같은 호 아목부터 …」 → 제3호만 바뀜 → False (261015 정밀도 표본)"""
    no, _, br = key.partition("의")
    art = rf"제{no}조" + (rf"의{br}" if br else r"(?!의\d)")
    m = re.search(art + r"\s*(?:제\s*(\d+)\s*항)?\s*(?:제\s*(\d+)\s*호)?", re.sub(r"\s", "", raw))
    if not lines or not m or not (m.group(1) or m.group(2)):
        return True                       # 개정문을 모르거나 조 전체를 인용
    my_hang, my_ho = m.group(1), m.group(2)
    units = []
    for x in lines:
        x = re.sub(r"\s", "", x)
        for u in re.finditer(rf"(?:{art}|같은조)(?:제(\d+)항)?(?:제(\d+)호)?", x):
            units.append((u.group(1), u.group(2)))
    if not units or any(h is None and o is None for h, o in units):
        return True                       # 조 전체를 바꿈 (「제N조를 다음과 같이 한다」, 「제N조 중 …」)
    for h, o in units:
        if my_hang and h and h != my_hang:
            continue
        if my_ho and o and o != my_ho and (not my_hang or h == my_hang or h is None):
            continue
        return True
    return False


def law_changes(regs: dict[str, dict], edges: list[dict], db: LawDB, aliases: dict[str, str] | None = None) -> tuple[list[dict], dict]:
    """우리 규정의 법령 조문 인용 × 법령 최근 개정 → 대조 요청 결과. (결과, 연결 통계)"""
    from .findings import locate
    from .history import article_events, fmt, regulation_dates

    arts = {a["id"]: (r, a) for r in regs.values() for a in r["articles"]}
    stats = {"법령 인용": 0, "법령 연결": 0, "조문 연결": 0, "법령 없음": 0, "조 없음": 0}
    out, seen = [], set()
    def written(e: dict) -> bool:
        """그 법령 이름이 이 인용 문맥에 실제로 적혀 있나. 「…법(이하 "법")」 뒤의 「법 제5조」는 약칭으로 이어진 것이라
        이름 오기·옛 이름은 이름이 적힌 곳에서 한 번만 알린다 (같은 오기를 33번 알리던 문제, 261014 오류 주입 평가)"""
        return norm(e["target_doc"]) in norm(e.get("context") or "")

    for e in edges:
        # 본문과 별표·별지 (표 안 「…법률 제7조제1항제1호」도 법령 인용, 261012). 부칙은 옛 문구라 제외
        if e["status"] not in ("law_pending", "external_rule") or e["src_kind"] not in ("본문", "별표", "별지"):
            continue
        stats["법령 인용"] += 1
        law = db.find(e["target_doc"], aliases)
        if not law:
            stats["법령 없음"] += 1
            close = db.close_title(e["target_doc"])
            if close and written(e) and (e["src"], "typo", close) not in seen:
                seen.add((e["src"], "typo", close))
                stats["법령명 오기 의심"] = stats.get("법령명 오기 의심", 0) + 1
                out.append({**locate(e["src"]), "quote": e["target_doc"], "context": e["context"], "method": "law_db", "src_id": e["src"],
                            "type": "법령명_오기", "priority": "중간", "suggest": close,
                            "note": f"법률 DB에 「{e['target_doc']}」은 없고 「{close}」가 거의 같음 — 오기인지 확인"})
            continue
        stats["법령 연결"] += 1
        wrong = {norm(k): v for k, v in MISNAMED.items()}.get(norm(e["target_doc"]))
        if wrong and written(e) and (e["src"], "typo", wrong) not in seen:
            seen.add((e["src"], "typo", wrong))
            stats["법령명 오기 의심"] = stats.get("법령명 오기 의심", 0) + 1
            out.append({**locate(e["src"]), "quote": e["target_doc"], "context": e["context"], "method": "law_db", "src_id": e["src"],
                        "type": "법령명_오기", "priority": "중간", "suggest": law["title"],
                        "note": f"정식 이름은 「{law['title']}」 — 법령명 확인"})
        new_name = db.previous_names.get(norm(e["target_doc"]))
        if new_name and written(e) and (e["src"], "renamed", new_name) not in seen:
            seen.add((e["src"], "renamed", new_name))
            stats["법령명 변경"] = stats.get("법령명 변경", 0) + 1
            out.append({**locate(e["src"]), "quote": e["target_doc"], "context": e["context"], "method": "law_db", "src_id": e["src"],
                        "type": "법령명_변경", "priority": "높음", "suggest": new_name, "law": law["title"],
                        "note": f"「{e['target_doc']}」은 지금 「{law['title']}」(시행 {fmt(_d8(law['시행일']))})로 이름이 바뀜 — 법령명 정비 필요"})
        key, u = db.article(law, e["raw"])
        if not key:
            continue
        aid = e["src"].split("-")[0]
        if e["src_kind"] == "본문":
            if aid not in arts:
                continue
            r, a = arts[aid]
        else:   # 별표·별지: 조가 없으니 그 별표·별지의 개정 표시(<신설 2025.04.17.>), 없으면 규정 마지막 개정일과 비교
            r, a = regs.get(e["src"].split(":")[0]), None
            if not r:
                continue
            ap = next((x for x in r.get("appendices", []) if x["id"] == e["src"]), None)
            marks = [(int(y), int(m), int(d)) for y, m, d in re.findall(r"(\d{4})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})", (ap or {}).get("title") or "")]
            rev = re.match(r"(\d{4})-(\d{2})(?:-(\d{2}))?", r.get("revised") or "")
            appx_last = max(marks) if marks else ((int(rev[1]), int(rev[2]), int(rev[3] or 1)) if rev else None)
        base = {**locate(e["src"]), "quote": e["raw"], "context": e["context"], "method": "law_db", "suggest": "",
                "src_id": e["src"], "law": law["title"], "law_article": f"제{key.replace('의', '조의') if '의' in key else key + '조'}"}
        if not u:
            stats["조 없음"] += 1
            out.append({**base, "type": "법령_조문없음", "priority": "높음",
                        "note": f"「{law['title']}」(시행 {fmt(_d8(law['시행일']))})에 {base['law_article']}가 없음 — 삭제·이동 또는 오기"})
            continue
        stats["조문 연결"] += 1
        ev = [d for d, _ in article_events(a)] if a else []
        rb, _ = regulation_dates(r)
        last = (max(ev) if ev else rb) if a else appx_last
        moved = u.get("조문이동이전", "000000") not in ("000000", "", None) or u.get("조문이동이후", "000000") not in ("000000", "", None)
        changed = u.get("조문변경여부") == "Y"
        when = _d8(u.get("조문시행일자")) or _d8(law["시행일"])
        if not (moved or changed) or not when or (last and when <= last):
            continue
        k = (e["src"], law["id"], key)
        if k in seen:
            continue
        seen.add(k)
        lines = amendment_lines(law, key)
        if not moved and not touches_cited(amendment_lines(law, key, limit=100), key, e["raw"]):   # 판단은 개정문 전체로 (보여 줄 때만 5문장)
            stats["개정이 인용한 항·호와 무관"] = stats.get("개정이 인용한 항·호와 무관", 0) + 1
            continue
        out.append({**base, "type": "법령_개정확인", "priority": "높음" if moved else "중간",
                    "note": f"「{law['title']}」 {base['law_article']}({u.get('조문제목') or ''}) "
                            + ("이동 " if moved else "") + f"{law['구분'] or '개정'} 시행 {fmt(when)}"
                            + f" — 인용 쪽 마지막 개정 {fmt(last)}. 규정 문안이 개정 법령과 맞는지 대조"
                            + (f" / 개정문: {' '.join(lines)[:300]}" if lines else ""),
                    "law_text": u.get("조문내용", "")})
    return out, stats
