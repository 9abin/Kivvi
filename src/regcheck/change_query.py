"""변경 분석: 개정문이 규정에서 무엇을 어떻게 바꾸는지 읽어 「변경 질의(ChangeQuery)」로 정리한다. 후보를 모으는 단계(그래프 탐색 · 문구 검색 · 의미 이웃)의 입력이다.

  analyze(reg, amendment, cid)    -> ChangeQuery
  ChangeQuery.search_terms(df)    -> 후보 수집에 쓸 검색어 목록 [(검색어, 역할)]

원칙
  · 코드만 쓴다 (LLM 없음). 개정문 해석은 regcheck.amend의 결과(조 번호 · 연산 · 대응표)를 그대로 쓴다.
  · 수정 종류는 개정문에서 자동으로 분류한다. 입력으로 받지 않는다. 판단 근거를 evidence에 남긴다.
  · 못 읽는 개정문은 추측하지 않고 kind=「분석 실패」와 이유를 남긴다.
종류: 번호 이동 / 삭제 / 정의 변경 / 값 변경 / 표현 수정 / 위임·법령 변경 / 내용 변경(그 밖)
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Callable

from .amend import AmendError, apply, build_changeset, parse_amendment
from .kg import DEF_TITLE, SHORT_TERM_MIN_LEN, TERM_RE, VALUE_RE, def_terms

PARTICLES = ("으로서", "으로써", "에서는", "에게는", "이라고", "이라는", "하여", "하고", "에서", "에게", "으로", "까지", "부터", "보다", "처럼", "마다", "에는", "과의", "와의",
             "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "로", "도", "만", "및", "등")
TERM_SUFFIX = ("의무", "제도", "업무", "체계", "절차", "기준", "사항", "행위", "관리", "대상")
STOP = {"다음", "각호", "경우", "때에는", "따라", "정한다", "한다", "있다", "하여야", "아니한다", "그밖", "관한", "대한", "관련", "위한", "따른", "이하", "이상"}
DELEG = re.compile(r"별도로\s*정|정하는\s*바|따라\s*정|위임|「[^」]*법[^」]*」|법\s*제\s*\d+\s*조|에\s*따른다|에\s*따라|[가-힣]+(?:이|가)\s*정(?:한다|하고|하여|하며)")


@dataclass
class ChangeQuery:
    id: str
    reg_id: str
    kind: str
    evidence: str = ""
    starts: list[str] = field(default_factory=list)          # 영향을 따라갈 시작 조 노드 (이동이면 옮겨지는 범위 전체)
    units: list[str] = field(default_factory=list)           # 바뀌는 단위 표기 (제30조제2항)
    removed: list[str] = field(default_factory=list)         # 지워지거나 바뀌기 전의 말
    added: list[str] = field(default_factory=list)           # 새로 들어가거나 바뀐 뒤의 말
    old_phrases: list[str] = field(default_factory=list)     # 개정문이 따옴표로 지목한 바뀌기 전 말 전체 (「매월 25일」). 같은 말을 그대로 쓴 다른 단위를 찾는 데 쓴다
    old_values: list[str] = field(default_factory=list)      # 바뀌기 전 값 (2회, 10일)
    new_values: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)           # 정의가 바뀌는 용어
    term_cores: list[str] = field(default_factory=list)      # 용어의 핵심 부분 (고객확인의무 -> 고객확인)
    phrases: list[str] = field(default_factory=list)         # 새로 들어간 핵심 말 (낱말과 연속 2~3어절)
    renumber: dict[str, str] = field(default_factory=dict)   # 옛 조 노드 -> 새 조 라벨
    new_articles: list[str] = field(default_factory=list)    # 신설 조 라벨
    error: str = ""

    def search_terms(self, doc_freq: Callable[[str], int] | None = None, df_max: int = 40) -> list[tuple[str, str]]:
        """후보 수집용 검색어 [(검색어, 역할)]. doc_freq(검색어)가 df_max를 넘는 흔한 말은 뺀다."""
        out: list[tuple[str, str]] = []
        for role, items in (("용어", self.terms), ("용어핵심", self.term_cores), ("새 말", self.phrases), ("지운 말", [r for r in self.removed if len(r) <= 20])):
            for t in items:
                t = re.sub(r"\s+", "", t)
                if len(t) >= 3 and (doc_freq is None or 0 < doc_freq(t) <= df_max) and (t, role) not in out:
                    out.append((t, role))
        return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _strip_particle(w: str) -> str:
    for p in PARTICLES:
        if w.endswith(p) and len(w) - len(p) >= 2:
            return w[: -len(p)]
    return w


def _tokens(text: str) -> list[str]:
    toks = []
    for w in re.findall(r"[가-힣A-Za-z0-9]+", text or ""):
        w = _strip_particle(w)
        if len(w) >= 2 and not w.isdigit():
            toks.append(w)
    return toks


SEG_TOKEN = re.compile(r"\d+(?:\.\d+)?[가-힣%]{0,2}|[가-힣A-Za-z]+|\S")


def _art_text(a: dict) -> str:
    """조 본문. 다시 쓴 조는 norm이 옛 값 그대로라서 항목(items)에서 모은다."""
    if a.get("items"):
        return "\n".join(it.get("norm") or it.get("text") or "" for it in a["items"])
    return a.get("norm") or a.get("text", "")


def _value_pairs(old: str, new: str) -> tuple[list[str], list[str]]:
    """같은 순서의 값끼리 짝지어 바뀐 값만 (2회 -> 3회, 3회 -> 4회). 개수가 다르면 서로 없는 값을 낸다."""
    ov, nv = _values(old), _values(new)
    if len(ov) == len(nv):
        pairs = [(x, y) for x, y in zip(ov, nv) if x != y]
        return [x for x, _ in pairs], [y for _, y in pairs]
    return [v for v in ov if v not in nv], [v for v in nv if v not in ov]


def _diff_segments(old: str, new: str) -> tuple[list[str], list[str]]:
    """토큰(낱말 · 숫자+단위 · 기호) 단위 비교로 지워진 말과 새로 들어간 말."""
    a, b = SEG_TOKEN.findall(old or ""), SEG_TOKEN.findall(new or "")
    rem, add = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("replace", "delete") and i2 > i1:
            rem.append(" ".join(a[i1:i2]))
        if tag in ("replace", "insert") and j2 > j1:
            add.append(" ".join(b[j1:j2]))
    return rem, add


def _similar_to_gone(t: str, gone: set[str]) -> bool:
    """지워진 낱말과 거의 같은 낱말(관리하다 -> 관리한다)은 새 말이 아니다."""
    return any(difflib.SequenceMatcher(None, t, g, autojunk=False).ratio() >= 0.75 for g in gone)


def _phrases(added: list[str], removed: list[str]) -> list[str]:
    """새로 들어간 말에서만 나오는 낱말과 연속 2~3어절 (지워진 말에도 있거나 거의 같은 낱말은 뺀다)."""
    gone = {t for r in removed for t in _tokens(r)}
    out: list[str] = []
    for seg in added:
        toks = [t for t in _tokens(seg)]
        keep = [t for t in toks if t not in gone and t not in STOP and len(t) >= 3 and not _similar_to_gone(t, gone)]
        for t in keep:
            if t not in out:
                out.append(t)
        for n in (3, 2):
            for i in range(len(toks) - n + 1):
                g = toks[i:i + n]
                if all(x not in gone and x not in STOP for x in g):
                    ph = " ".join(g)
                    if ph not in out and len(_norm(ph)) >= 5:
                        out.append(ph)
    return out


def _core(term: str) -> str:
    t = _norm(term)
    for s in TERM_SUFFIX:
        if t.endswith(s) and len(t) - len(s) >= 3:
            return t[: -len(s)]
    return ""


NUM_TOKEN = re.compile(r"\d+(?:\s*/\s*\d+)?(?:\.\d+)?\s*[천백십만억]*")


def _numeric_only_change(old: str, new: str) -> bool:
    """숫자(분수 · 천 · 만 단위 포함)를 가리면 두 문구가 같으면 값만 바뀐 것이다."""
    mask = lambda t: re.sub(r"\s+", "", NUM_TOKEN.sub("#", t or ""))  # noqa: E731
    return old != new and "#" in mask(old) and mask(old) == mask(new)


PERIOD_WORDS = {"연", "매년", "연간", "반기", "매반기", "반기별", "분기", "매분기", "분기별", "월", "매월", "월별", "주", "매주"}


def _period_change(removed: list[str], added: list[str]) -> bool:
    """바뀐 말이 모두 주기 말(연 -> 반기, 분기 -> 월)이면 값 변경이다 (「연 1회 이상」 -> 「반기 1회 이상」은 숫자가 같아 값 비교로는 안 잡힌다)."""
    rm, ad = [_norm(x) for x in removed], [_norm(x) for x in added]
    return bool(rm) and bool(ad) and all(x in PERIOD_WORDS for x in rm + ad) and set(rm) != set(ad)


def _values(text: str) -> list[str]:
    return [re.sub(r"\s+", "", m) for m in VALUE_RE.findall(text or "")]


def _context_lines(new_by_label: dict, q: "ChangeQuery", old_arts: dict) -> list[str]:
    """새 문구가 들어간 항의 전체 문장 (새 말 조각만으로는 「별도로 정하되」 같은 위임 표현이 잘린다)."""
    out = []
    for origin in q.starts:
        a = new_by_label.get(old_arts[origin]["label"])
        for it in (a or {}).get("items", []):
            t = it.get("norm") or it.get("text") or ""
            if any(_norm(seg)[:6] and _norm(seg)[:6] in _norm(t) for seg in q.added):
                out.append(t)
    return out


APPX_TARGET = re.compile(r"^\s*(?P<name>별지\s*제\s*(?P<n>\d+)\s*호\s*서식|별표\s*(?P<b>\d+))\s*(?=중)")
PSEUDO_LABEL = "제9999조"


def _appendix_pseudo(reg: dict, m: re.Match) -> dict | None:
    """개정문의 대상이 별지 서식 · 별표이면 그 단위를 임시 조(제9999조)로 감싼 규정 사본. 서식 본문의 문구 치환도 조와 같은 분석을 탄다."""
    kind, num = ("별지", f"제{m.group('n')}호 서식") if m.group("n") else ("별표", m.group("b"))
    ap = next((a for a in reg.get("appendices") or [] if a.get("kind") == kind and re.sub(r"\s+", "", a.get("number") or "") == re.sub(r"\s+", "", num)), None)
    if ap is None:
        return None
    text = re.sub(r"\s+", " ", ap.get("text") or "").strip()
    pseudo = {"id": ap["id"], "label": PSEUDO_LABEL, "title": "", "chapter": "", "deleted": False, "amend_notes": [],
              "items": [{"id": ap["id"] + "-0-0-0", "path": [0, 0, 0], "text": text, "norm": text, "amend_marks": [], "deleted": False}]}
    return {**reg, "articles": [*reg["articles"], pseudo]}


def analyze(reg: dict, amendment: str, cid: str = "") -> ChangeQuery:
    m_ap = APPX_TARGET.match(amendment or "")
    if m_ap:
        reg2 = _appendix_pseudo(reg, m_ap)
        if reg2 is not None:
            q = analyze(reg2, PSEUDO_LABEL + " " + amendment[m_ap.end():].lstrip(), cid)
            q.units = [re.sub(r"\s+", " ", m_ap.group("name"))]
            q.evidence = (q.evidence + " (별지 서식 · 별표)").strip()
            return q
    q = ChangeQuery(id=cid, reg_id=reg["reg_id"], kind="분석 실패")
    try:
        ops = parse_amendment(amendment)
        cs, _ = build_changeset(reg, amendment, cid or "q", "")
        new_reg, _ = apply(reg, ops)
    except AmendError as e:
        q.error = str(e)
        return q
    old_arts = {a["id"]: a for a in reg["articles"]}
    new_by_label = {a["label"]: a for a in new_reg["articles"]}
    renum = {c["origin"]: c["new_label"] for c in cs["changes"] if any(o["op"] == "renumber" for o in c["ops"])}
    inserts = [c["new_label"] for c in cs["changes"] if any(o["op"] == "insert" for o in c["ops"])]
    q.renumber, q.new_articles = renum, inserts
    whole_del, edits = [], []   # (origin, op)
    for c in cs["changes"]:
        for o in c["ops"]:
            if o["op"] == "delete" and o.get("whole"):
                whole_del.append(c["origin"])
            elif o["op"] in ("replace", "rewrite", "delete"):
                edits.append((c["origin"], c, o))
    # 번호 이동 / 삭제
    if renum or (inserts and not edits):
        q.kind, q.evidence = "번호 이동", f"조 번호가 {len(renum)}개 밀리고 {len(inserts)}개 신설"
        q.starts = sorted(renum)
        q.units = [f"{old_arts[k]['label']}→{v}" for k, v in sorted(renum.items())][:6]
        for lab in inserts:
            if lab in new_by_label:
                q.added.append(_art_text(new_by_label[lab])[:300])
        q.phrases = _phrases(q.added, [])
        return q
    if whole_del and not edits:
        q.kind, q.evidence = "삭제", "조 전체를 삭제"
        q.starts = whole_del
        q.units = [old_arts[k]["label"] for k in whole_del]
        for k in whole_del:
            a = old_arts[k]
            q.removed += [a.get("title") or ""] if a.get("title") else []
        q.removed = [r for r in q.removed if r]
        return q
    if not edits:
        q.error = "바뀐 내용을 찾지 못함"
        return q
    # 문구 단위 수정 (치환 · 삭제 · 다시 쓰기)
    for origin, c, o in edits:
        if origin not in q.starts:
            q.starts.append(origin)
        if o["unit"] not in q.units:
            q.units.append(o["unit"])
        if o["op"] == "replace":
            if o["old"] not in q.old_phrases:
                q.old_phrases.append(o["old"])
            rem, add = _diff_segments(o["old"], o["new"])
            q.removed += rem or [o["old"]]
            q.added += add or [o["new"]]
            ov_, nv_ = _value_pairs(o["old"], o["new"])
            q.old_values += [v for v in ov_ if v not in q.old_values]
            q.new_values += [v for v in nv_ if v not in q.new_values]
        elif o["op"] == "delete":
            q.removed.append(o.get("old", ""))
        else:   # rewrite: 낡은 조와 새 조를 비교
            old_a = old_arts[origin]
            new_a = new_by_label.get(c["new_label"]) or {}
            rem, add = _diff_segments(_art_text(old_a), _art_text(new_a))
            q.removed += rem
            q.added += add
    q.removed = [r for r in q.removed if r]
    q.added = [a for a in q.added if a]
    if not (q.old_values or q.new_values):   # 다시 쓰기: 낡은 · 새 문구 전체에서
        ov, nv = _value_pairs(" ".join(q.removed), " ".join(q.added))
        q.old_values, q.new_values = ov, nv
    # 종류 분류
    first = old_arts[q.starts[0]]
    touched = [it for it in first["items"] if any(_norm(r)[:30] in _norm(it["norm"]) for r in q.removed if r)]
    # 정의 조에서 정의 항목 자체 또는 그 하위 호(정의 항목 아래 열거)를 고치면 정의 변경이다
    def_items = [t for t in touched if def_terms(t["norm"])]
    if not def_items:   # 고친 곳이 정의 항목 아래 열거(호 · 목)이면 그 정의 항목(머리 문장)을 본다. 경로 [2,3,0] -> [2,0,0], [0,1,2] -> [0,1,0]
        by_path = {tuple(it["path"]): it for it in first["items"]}
        for t in touched:
            p_ = list(t["path"])
            for k in range(len(p_) - 1, 0, -1):
                cand = by_path.get(tuple(p_[:k] + [0] * (len(p_) - k)))
                if cand is not None and cand is not t and def_terms(cand["norm"]):
                    def_items.append(cand)
                    break
    is_def = bool(DEF_TITLE.search(first.get("title") or "")) and bool(def_items)
    ins_defs = [t for _, _, o in edits for t in (o.get("inserted") or []) if def_terms(t)]   # 호 신설(「제N조에 제M호를 다음과 같이 신설」)로 새 용어 정의가 들어옴
    if not is_def and ins_defs and DEF_TITLE.search(first.get("title") or ""):
        is_def = True
    if is_def:
        q.kind, q.evidence = "정의 변경", "정의 조의 용어 정의 항목을 고침" if def_items else "정의 조에 용어 정의 호를 신설"
        for norm in [it["norm"] for it in def_items] + ins_defs:
            for t in def_terms(norm):
                if len(t) >= SHORT_TERM_MIN_LEN and t not in q.terms:
                    q.terms.append(t)
        q.term_cores = [c for c in (_core(t) for t in q.terms) if c]
    elif _period_change(q.removed, q.added):
        q.kind, q.evidence = "값 변경", f"주기가 바뀜: {q.removed} -> {q.added}"
        q.old_values, q.new_values = list(q.removed), list(q.added)
    elif (q.old_values != q.new_values and (q.old_values or q.new_values)) or any(_numeric_only_change(r, a_) for r, a_ in zip(q.removed, q.added)):
        q.kind, q.evidence = "값 변경", f"값이 바뀜: {q.old_values or q.removed[:2]} -> {q.new_values or q.added[:2]}"
    else:
        ot, nt = _norm(" ".join(q.removed)), _norm(" ".join(q.added))
        sim = difflib.SequenceMatcher(None, ot, nt, autojunk=False).ratio() if ot and nt else 0
        new_words = [t for t in _phrases(q.added, q.removed) if " " not in t]
        if q.added and not new_words and len(nt) <= 24 and len(ot) <= 24:   # 바꿔 넣는 말이 없는 삭제는 표현 수정이 아니다 (말이나 항이 사라지면 그것에 기댄 곳이 있다)
            q.kind, q.evidence = "표현 수정", f"문구 유사도 {sim:.2f}, 새 실질 낱말(3자 이상) 없음"
        elif any(DELEG.search(ln) for ln in q.added + _context_lines(new_by_label, q, old_arts)):
            q.kind, q.evidence = "위임·법령 변경", "바뀐 항에 위임 · 법령 표현이 있음"
        else:
            q.kind, q.evidence = "내용 변경", "값 · 정의 · 위임 어느 것도 아닌 내용 수정"
    q.phrases = _phrases(q.added, q.removed)
    return q


def analyze_law(cid: str, law_name: str, amendment: str, law_nodes: dict | None = None) -> ChangeQuery:
    """법령 개정(규정 밖). 법령 노드가 그래프에 있으면 시작점으로, 없으면 문구 · 의미 검색에만 맡긴다."""
    from .kg_onto import find_law_article, find_law_node
    nid = (find_law_node(law_nodes, law_name) if law_nodes is not None else None) or f"LAW:{law_name}"   # 띄어쓰기 · 약칭 표기 차이는 같은 법령
    q = ChangeQuery(id=cid, reg_id="", kind="위임·법령 변경", evidence="법령 개정(가정)")
    if law_nodes is not None and nid in law_nodes:
        art = find_law_article(law_nodes, nid, amendment)   # 개정문이 제N조를 말하고 그 조를 인용한 규정이 있으면 조 단위로 먼저 본다
        q.starts = [art, nid] if art else [nid]
    q.units = [law_name]
    q.added = [amendment]
    q.phrases = _phrases([amendment], [])
    return q
