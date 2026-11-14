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


# ── 횟수 한도 (COUNT-01) ──────────────────────────────────────────────────────

RE_COUNT = re.compile(r"(?<![\d제])(\d+)\s*회(?!의\s*기간)")
RE_COUNT_WORDS = re.compile(r"분할|나누어|한정|최대|이내|까지|한하여|횟수")


def count_limits(text: str) -> dict[int, str]:
    """조 본문에서 횟수 한도처럼 쓰인 「N회」: {N: 근거 문장}. 문장에 분할 · 한정 · 최대 같은 한도 말이 있을 때만 (「각 1회의 기간」 「2회 이상 거부」 같은 경우를 거른다)."""
    out: dict[int, str] = {}
    for m in RE_COUNT.finditer(text):
        sent = _sentence(text, m.start())
        if RE_COUNT_WORDS.search(sent):
            out.setdefault(int(m.group(1)), sent)
    return out


def count_diffs(regs: dict[str, dict], same_title_max: int = 8) -> list[dict]:
    """제목이 같은 다른 규정의 조끼리 횟수 한도 값 집합이 다른 쌍. 판정이 아니라 후보다 (적용 대상 · 횟수의 의미 · 시행 시점 · 예외는 사람이 본다)."""
    from .kg import GENERIC_TITLES   # 같은 제목 기준을 그래프(SAME_TITLE)와 맞춘다
    generic = {re.sub(r"\s+", "", g) for g in GENERIC_TITLES}
    by_title: dict[str, list[tuple[dict, dict]]] = collections.defaultdict(list)
    for reg in regs.values():
        for a in reg["articles"]:
            t = re.sub(r"\s+", "", a.get("title") or "")
            if a.get("deleted") or len(t) < 3 or t in generic:
                continue
            by_title[t].append((reg, a))
    out = []
    for t, group in by_title.items():
        if len({r["reg_id"] for r, _ in group}) < 2 or len(group) > same_title_max:
            continue
        lim = [(r, a, count_limits(a["norm"])) for r, a in group]
        for (r1, a1, c1), (r2, a2, c2) in itertools.combinations(lim, 2):
            if r1["reg_id"] != r2["reg_id"] and c1 and c2 and set(c1) != set(c2):
                out.append({"title": a1.get("title"), "a": a1["id"], "b": a2["id"], "a_reg": r1["name"], "b_reg": r2["name"], "a_label": a1["label"], "b_label": a2["label"],
                            "a_counts": sorted(c1), "b_counts": sorted(c2), "a_ctx": next(iter(c1.values())), "b_ctx": next(iter(c2.values())),
                            "a_revised": r1.get("revised"), "b_revised": r2.get("revised")})
    return out


# ── 권한 · 의결 주체 차이 (AUTH-01)와 의결 정족수 차이 (QUORUM-01) ─────────────────────

AUTH_BODIES = r"(대표이사|이사회|주주총회|인사위원회|징계위원회|감사위원회|준법감시인)"
AUTH_ACTS = r"(승인|의결|결의|결정|결재)"
RE_AUTH = re.compile(AUTH_BODIES + r"(?:의|가|이|에서|로부터)?\s*(?:[가-힣]{1,6}\s*){0,3}?" + AUTH_ACTS)
AUTH_STOP = {"승인", "의결", "결정", "결재", "결의", "대표이사", "이사회", "주주총회", "인사위원회", "징계위원회", "감사위원회", "준법감시인", "회사", "한국벤처투자", "경우", "다음", "사항", "관한",
             "거쳐", "최종", "받아", "정할", "달리", "이동", "종전", "개정", "위원", "위원회", "의결사항", "확정한다", "시행한다", "요청할", "정한다", "한다", "있다"}
RE_PUNCT = re.compile(r"[\s·ㆍ‧・,，、()（）]")


def _auth_statements(regs: dict[str, dict]) -> list[dict]:
    from .change_query import _tokens   # 낱말 쪼개기는 변경 분석과 같은 방식
    out = []
    for reg in regs.values():
        for a in reg["articles"]:
            if a.get("deleted"):
                continue
            title = a.get("title") or ""
            heads = {it["path"][0]: it["norm"] for it in a.get("items", []) if list(it["path"][1:]) == [0, 0]}
            for it in a.get("items", []):
                m = RE_AUTH.search(it["norm"])
                if not m and list(it["path"][1:]) != [0, 0]:
                    m = RE_AUTH.search(heads.get(it["path"][0], ""))   # 호는 항의 머리 문장에서 주체 · 행위를 이어받는다
                if m:
                    out.append({"reg": reg, "article": a, "item": it, "body": m.group(1), "act": m.group(2), "toks": set(_tokens(title + " " + it["norm"])), "title_toks": set(_tokens(title))})
    return out


def authority_diffs(regs: dict[str, dict], max_df: int = 70, min_shared: int = 2) -> list[dict]:
    """같은 사안(희귀하지 않은 일반어를 뺀 공통 낱말 2개 이상, 그중 하나는 조 제목의 낱말)에 대해 다른 규정이 승인 · 의결 주체를 다르게 정한 쌍. 후보일 뿐이다."""
    stm = _auth_statements(regs)
    df: collections.Counter = collections.Counter()
    for s in stm:   # 조 단위 문서 빈도 대신 문장 집합의 빈도로 근사하지 않고, 전체 조에서 센다
        pass
    from .change_query import _tokens
    for reg in regs.values():
        for a in reg["articles"]:
            if not a.get("deleted"):
                for t in set(_tokens((a.get("title") or "") + " " + (a.get("norm") or ""))):
                    df[t] += 1
    key = lambda toks: {t for t in toks if t not in AUTH_STOP and df[t] <= max_df and len(t) >= 2}   # noqa: E731
    out, seen = [], set()
    for s1, s2 in itertools.combinations(stm, 2):
        if s1["reg"]["reg_id"] == s2["reg"]["reg_id"] or s1["body"] == s2["body"]:
            continue
        sh = key(s1["toks"]) & key(s2["toks"])
        if len(sh) < min_shared or not (sh & (s1["title_toks"] | s2["title_toks"])):
            continue
        k = (s1["article"]["id"], s2["article"]["id"])
        if k in seen:
            continue
        seen.add(k)
        out.append({"a": s1["article"]["id"], "b": s2["article"]["id"], "a_reg": s1["reg"]["name"], "b_reg": s2["reg"]["name"], "a_label": s1["article"]["label"], "b_label": s2["article"]["label"],
                    "a_body": s1["body"], "b_body": s2["body"], "a_act": s1["act"], "b_act": s2["act"], "shared": sorted(sh), "a_ctx": s1["item"]["norm"], "b_ctx": s2["item"]["norm"], "a_item": s1["item"]["id"]})
    return sorted(out, key=lambda d: -len(d["shared"]))


RE_QUORUM = re.compile(r"(재적|출석)[가-힣]{0,8}\s*(\d+\s*분의\s*\d+|과반수)")
QUORUM_BODIES = ("인사위원회", "이사회", "징계위원회")   # 이름이 분명한 기관만 (「심의회」 「위원회」처럼 규정마다 다른 기구는 섞이지 않게 뺀다)


def quorum_diffs(regs: dict[str, dict]) -> list[dict]:
    """같은 기관(인사위원회 · 이사회 · 징계위원회)의 의결 정족수 표현(재적 · 출석 + 분수 · 과반수)을 서로 다른 규정이 다르게 정한 조 쌍.
    한쪽이 다른 쪽의 부분집합이면(일반 정족수와 일부 표현) 뺀다. 같은 규정 안의 특례는 대상이 아니다."""
    by_body: dict[str, list[tuple[dict, dict, dict]]] = collections.defaultdict(list)
    for reg in regs.values():
        for a in reg["articles"]:
            if a.get("deleted"):
                continue
            found = {(m.group(1), re.sub(r"\s+", "", m.group(2))): m.start() for m in RE_QUORUM.finditer(a["norm"])}
            if not found:
                continue
            for body in QUORUM_BODIES:
                near = [pos for pos in found.values() if body in a["norm"][max(0, pos - 200): pos + 40]]
                if near:
                    by_body[body].append((reg, a, {k: v for k, v in found.items() if v in near}))
    out = []
    for body, group in by_body.items():
        for (r1, a1, q1), (r2, a2, q2) in itertools.combinations(group, 2):
            if r1["reg_id"] == r2["reg_id"] or set(q1) <= set(q2) or set(q2) <= set(q1):
                continue
            out.append({"body": body, "a": a1["id"], "b": a2["id"], "a_reg": r1["name"], "b_reg": r2["name"], "a_label": a1["label"], "b_label": a2["label"],
                        "a_quorum": sorted(f"{x}{y}" for x, y in q1), "b_quorum": sorted(f"{x}{y}" for x, y in q2),
                        "a_ctx": _sentence(a1["norm"], min(q1.values())), "b_ctx": _sentence(a2["norm"], min(q2.values()))})
    return out


# ── 구조 점검 (DUPNUM-01 · SELFCITE-01): 원본 문서의 번호 · 인용 오류 후보 ───────────────

def structure_findings(regs: dict[str, dict], rels: list[dict]) -> list[dict]:
    """한 규정 안의 같은 조 번호 중복(DUPNUM-01)과, 조가 자기 번호를 그대로 인용하는 경우(SELFCITE-01). 「제N조 M항」 · 「제N조부터 제M조까지」 같은 표현은 자기 인용이 아니라서 뺀다.
    파서가 번호를 잘못 읽었을 수도 있어 원본 확인이 필요한 후보다 (판정이 아니라 확인 요청)."""
    out = []
    for reg in regs.values():
        cnt = collections.Counter(a["label"] for a in reg["articles"] if not a.get("deleted"))
        for label, n in cnt.items():
            if n > 1:
                a = next(x for x in reg["articles"] if x["label"] == label and not x.get("deleted"))
                titles = [x.get("title") for x in reg["articles"] if x["label"] == label and not x.get("deleted")]
                out.append({"category": "review", "reg_id": reg["reg_id"], "reg_name": reg["name"], "unit": "본문", "article": label, "para": 0, "quote": label, "context": " / ".join(map(str, titles)),
                            "path": "조 번호", "rule": "DUPNUM-01", "target": "조 번호", "priority": "중간", "handling": "판단 가능",
                            "reason": f"같은 규정에 「{label}」가 {n}개 있음({', '.join(map(str, titles))}). 번호 오기인지 원본 확인"})
    for r in rels:
        if r.get("kind") != "인용" or r.get("src_article") != r.get("target") or r.get("target_type") != "article":
            continue
        ev, ctx = r.get("evidence", ""), r.get("context", "")
        if re.search(r"항|호|목", ev):   # 「제10조 2항」처럼 같은 조의 항 · 호를 가리키는 표현
            continue
        i = ctx.find(ev)
        after = ctx[i + len(ev): i + len(ev) + 8] if i >= 0 else ""
        if re.search(r"^\s*제?\s*\d+\s*항|^\s*부터|^\s*\(", after) or "부터" in ctx[i: i + len(ev) + 12]:
            continue
        reg = regs.get(r["src_article"].split(":")[0])
        a = next((x for x in reg["articles"] if x["id"] == r["src_article"]), None) if reg else None
        if a:
            out.append({"category": "review", "reg_id": reg["reg_id"], "reg_name": reg["name"], "unit": "본문", "article": a["label"], "para": 0, "quote": ev, "context": ctx,
                        "path": "인용 표", "rule": "SELFCITE-01", "target": a["label"], "priority": "낮음", "handling": "판단 가능",
                        "reason": f"{a['label']}가 자기 번호 「{ev}」를 인용함. 다른 조를 가리키려던 번호 오기인지 확인"})
    return out
