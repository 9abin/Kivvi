"""규정 사이 일관성 점검: 인용이 없어도 어긋나는 곳 — 현재 원문만 있으면 된다 (이력 불필요).

  vocab_findings(regs, vocab)      기관·표기 사전 — 정식명과 다르게 쓴 곳 (사장·이사장, 지체없이 …)
  usage_matrix(regs, vocab)        규정 × 구성원 호칭 사용 표 (직원·임직원·근로자 …) — 표로만 보고 판정하지 않음
  definition_diffs(regs)           같은 용어를 둘 이상의 규정이 다르게 정의 (terms.definitions 재사용)
  deadlines(regs)                  기한 표: 규정·조·항·절차 종류·N·단위(일·영업일·근무일·역일·월·년)·이내/전/후
  deadline_findings(rows)          같은 절차 종류인데 일수·단위가 다른 규정, 한 규정 안 단위 혼용

절차 종류는 문장 속 낱말로 정한다 (이의·재심 / 통지·회신 / 신고 / 제출 / 지급 / 연장 / 보존 / 처리 / 기타).
날짜(2020년 5월 3일)·나이(만 20세)는 기한이 아니므로 뺀다. 본문 조만 본다 (부칙·별표 제외).
판정이 아니라 후보다. 의도된 차이일 수 있다 (AGENTS §3).
"""

from __future__ import annotations

import collections
import difflib
import itertools
import re

from . import terms

UNITS = ("영업일", "근무일", "역일", "일", "개월", "월", "년", "주")
RE_DEADLINE = re.compile(r"(?<![\d.])(\d{1,3})\s*(영업일|근무일|역일|일|개월|월|년|주)\s*(?:간)?\s*(이내|이상|이하|전|후|이전|이후|까지|동안)?")
RE_DATE_BEFORE = re.compile(r"(?:\d{4}\s*년\s*(?:\d{1,2}\s*월\s*)?|\d{1,2}\s*월\s*)$")   # 「2020년 5월 3일」의 3일
RE_AGE = re.compile(r"만\s*$")
KINDS = (("이의·재심", r"이의|소원|재심|불복"), ("신고", r"신고"), ("통지·회신", r"통보|통지|회신|답변|알려|고지"),
         ("제출", r"제출"), ("지급", r"지급"), ("연장", r"연장"), ("보존", r"보존|보관"), ("처리", r"처리"))
RE_EXTERNAL_BEFORE = re.compile(r"(?:공단|공사|공단의|협회|은행|연합회|재단|진흥원|위원회|정보원|부|처|청|원)\s*$")   # 「중소벤처기업진흥공단 이사장」: 다른 기관의 직함
RE_REQUEST = re.compile(r"청구|신청|제기|신고|제출")      # 기한이 당사자가 낼 때의 것
RE_HANDLE = re.compile(r"심사|재심의|심의한다|통지|통보|거부사유|회신|조치")   # 받은 쪽이 처리·통지할 때의 기한 → 청구 기한과 따로 본다
UNIFORM_KINDS = {"이의·재심"}   # 규정이 달라도 같은 기한이 자연스러운 종류 → 어긋나면 후보로 올림


def _body_items(reg: dict):
    for a in reg["articles"]:
        if a.get("deleted"):
            continue
        for it in a["items"]:
            if not it.get("deleted"):
                yield a, it


def _finding(reg: dict, a: dict, it: dict, **kw) -> dict:
    return {"category": "review", "reg_id": reg["reg_id"], "reg_name": reg["name"], "unit": "본문", "article": a["label"],
            "para": it["path"][0], "handling": "판단 가능", **kw}


# ── 기관·표기 사전 ─────────────────────────────────────────────────────────────

def vocab_findings(regs: dict[str, dict], vocab: dict) -> list[dict]:
    out, seen = [], set()   # seen: 같은 조에서 같은 변형은 한 장
    for canon, spec in vocab.get("기관", {}).items():
        if canon.startswith("_"):
            continue
        for var in spec["변형"]:
            pat = terms._pattern(var)
            for reg in regs.values():
                for a, it in _body_items(reg):
                    m = next((x for x in pat.finditer(it["norm"]) if not RE_EXTERNAL_BEFORE.search(it["norm"][: x.start()])), None)
                    if m and (reg["reg_id"], a["id"], var) not in seen:
                        seen.add((reg["reg_id"], a["id"], var))
                        out.append(_finding(reg, a, it, quote=var, context=_ctx(it["norm"], m), path="기관명 사전", rule="vocab_org",
                                            target=canon, priority=spec.get("우선순위", "낮음"),
                                            reason=f"정식명은 「{canon}」인데 「{var}」로 적음. 같은 기관인지, 옛 명칭인지 확인"))
    for canon, variants in vocab.get("표기", {}).items():
        if canon.startswith("_"):
            continue
        for var in variants:
            for reg in regs.values():
                for a, it in _body_items(reg):
                    m = re.search(re.escape(var), it["norm"])
                    if m:
                        out.append(_finding(reg, a, it, quote=var, context=_ctx(it["norm"], m), path="표기 사전", rule="vocab_style",
                                            target=canon, priority="낮음", reason=f"표기는 「{canon}」(띄어쓰기)인데 「{var}」로 적음"))
    return out


def _ctx(s: str, m: re.Match, w: int = 40) -> str:
    return s[max(0, m.start() - w): m.end() + w].replace("\n", " ")


def usage_matrix(regs: dict[str, dict], vocab: dict) -> list[dict]:
    """규정마다 구성원 호칭별 사용 횟수 (조 단위). 같은 규정이 직원·근로자·임직원을 섞어 쓰면 적용 대상이 헷갈린다."""
    words = vocab.get("구성원", {}).get("호칭", [])
    pats = {w: terms._pattern(w) for w in words}
    rows = []
    for reg in regs.values():
        cnt = collections.Counter()
        for a, it in _body_items(reg):
            for w, p in pats.items():
                if p.search(it["norm"]):
                    cnt[w] += 1
        if cnt:
            rows.append({"reg_id": reg["reg_id"], "reg_name": reg["name"], **{w: cnt.get(w, 0) for w in words}})
    return rows


def _flat(text: str) -> str:
    return re.sub(r"[\s\"“”'「」]|이란|이라\s*함은|라\s*함은", "", text)   # 띄어쓰기·따옴표·「이란/이라 함은」 차이는 뺌


def def_similarity(a: str, b: str) -> float:
    """정의 두 개의 문구 유사도 (띄어쓰기·따옴표 차이 제외)."""
    return difflib.SequenceMatcher(None, _flat(a), _flat(b)).ratio()


def definition_diffs(regs: dict[str, dict], min_regs: int = 2) -> list[dict]:
    """같은 용어를 여러 규정이 정의하는 경우 (정의 조 기준). 뜻이 같은지는 사람이 본다."""
    by_term: dict[str, list[tuple[dict, dict]]] = collections.defaultdict(list)
    for reg in regs.values():
        for d in terms.definitions(reg):
            by_term[d["term"]].append((reg, d))
    out = []
    for t, lst in sorted(by_term.items()):
        if len({r["reg_id"] for r, _ in lst}) < min_regs:
            continue
        texts = {d["text"] for _, d in lst}
        flat = [_flat(d["text"]) for _, d in lst]
        sim = min(difflib.SequenceMatcher(None, x, y).ratio() for x, y in itertools.combinations(flat, 2))
        out.append({"term": t, "same_text": len(texts) == 1, "similarity": round(sim, 2),
                    "defs": [{"reg_id": r["reg_id"], "reg_name": r["name"], "article_id": d["article_id"], "label": d["label"], "text": d["text"]} for r, d in lst]})
    return sorted(out, key=lambda d: d["similarity"])   # 비슷한 정도가 낮은 것(뜻이 다를 가능성)부터


# ── 기한 표 ───────────────────────────────────────────────────────────────────

def _kind(sentence: str) -> str:
    for k, p in KINDS:
        if re.search(p, sentence):
            return k
    return "기타"


def deadlines(regs: dict[str, dict]) -> list[dict]:
    rows = []
    for reg in regs.values():
        for a, it in _body_items(reg):
            s = it["norm"]
            for m in RE_DEADLINE.finditer(s):
                before = s[: m.start()]
                if RE_DATE_BEFORE.search(before) or RE_AGE.search(before):
                    continue
                n, unit, rel = int(m.group(1)), m.group(2), m.group(3) or ""
                if unit in ("월", "년") and not rel:   # 「3월」「5년」처럼 기간 관계어가 없으면 달력·연도일 수 있음
                    continue
                sent = _sentence(s, m.start())
                rows.append({"reg_id": reg["reg_id"], "reg_name": reg["name"], "article": a["label"], "title": a.get("title", ""),
                             "para": it["path"][0], "n": n, "unit": unit, "rel": rel, "kind": _kind(sent), "context": sent})
    return rows


def _sentence(s: str, pos: int) -> str:
    start = max(s.rfind(".", 0, pos), s.rfind("\n", 0, pos)) + 1
    end = min([i for i in (s.find(".", pos), s.find("\n", pos)) if i >= 0] or [len(s)])
    return s[start:end].strip()


def _request_side(r: dict) -> bool:
    """기한 뒤에 오는 말이 청구·신청·제기(당사자가 냄)이면 True, 심사·통지·조치(받은 쪽이 처리)이면 False.
    기산점의 「통지일로부터」 같은 앞 문구는 보지 않는다."""
    m = re.search(rf"{r['n']}\s*(?:영업일|근무일|역일|일)", r["context"])
    tail = r["context"][m.end():] if m else r["context"]
    tail = re.sub(r"재심의?\s*(?=청구)", "", tail)   # 「재심의 청구」는 청구 쪽
    return bool(RE_REQUEST.search(tail)) and not RE_HANDLE.search(tail[: RE_REQUEST.search(tail).start()])


def deadline_outliers(rows: list[dict]) -> list[tuple[dict, tuple, int, list[dict]]]:
    """같은 종류(이의·재심 …)의 청구 기한 중 다수와 다른 행: [(행, 다수 값 (일수, 단위), 다수 행 수, 다수 행들)]."""
    out = []
    for kind in UNIFORM_KINDS:
        sub = [r for r in rows if r["kind"] == kind and r["unit"] in ("일", "영업일", "근무일", "역일") and r["rel"] in ("이내", "")
               and _request_side(r)]
        cnt = collections.Counter((r["n"], r["unit"]) for r in sub)
        if len(cnt) < 2:
            continue
        mode, mode_n = cnt.most_common(1)[0]
        same = [r for r in sub if (r["n"], r["unit"]) == mode]
        out += [(r, mode, mode_n, same) for r in sub if (r["n"], r["unit"]) != mode]
    return out


def deadline_findings(rows: list[dict]) -> list[dict]:
    out = []
    # 1) 같은 종류(이의·재심 …)인데 일수·단위가 다름
    for r, mode, mode_n, _ in deadline_outliers(rows):
        kind = r["kind"]
        out.append({"category": "review", "reg_id": r["reg_id"], "reg_name": r["reg_name"], "unit": "본문", "article": r["article"],
                    "para": r["para"], "quote": f"{r['n']}{r['unit']}", "context": r["context"], "path": "기한 표", "rule": "deadline_outlier",
                    "target": f"{kind} 기한 {mode[0]}{mode[1]}", "priority": "중간", "handling": "판단 가능",
                    "reason": f"{kind} 기한이 다른 {mode_n}곳은 {mode[0]}{mode[1]}인데 여기는 {r['n']}{r['unit']}. 의도된 차이인지 확인"})
    # 2) 한 규정 안 단위 혼용 (일 ↔ 영업일·근무일)
    by_reg = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        if r["unit"] in ("일", "영업일", "근무일", "역일"):
            by_reg[r["reg_id"]][r["unit"]].append(r)
    for rid, d in by_reg.items():
        if ("영업일" in d or "근무일" in d) and "일" in d:
            r0 = (d.get("영업일") or d.get("근무일"))[0]
            out.append({"category": "review", "reg_id": rid, "reg_name": r0["reg_name"], "unit": "본문", "article": r0["article"], "para": r0["para"],
                        "quote": f"{r0['n']}{r0['unit']}", "context": r0["context"], "path": "기한 표", "rule": "deadline_unit_mixed",
                        "target": "일수 단위", "priority": "낮음", "handling": "판단 가능",
                        "reason": f"한 규정에서 「{r0['unit']}」와 단순 「일」을 함께 씀 (일 {len(d['일'])}곳 · {r0['unit']} {len(d.get('영업일', [])) + len(d.get('근무일', []))}곳). 달력일인지 확인"})
    return out
