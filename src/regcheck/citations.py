"""조문 정규화 텍스트(`norm`)에서 인용을 뽑아 엣지로 만들고, 대상이 실제로 있는지 해석한다.

엣지 하나 = 출발 단위(본문 항목·부칙 항목·별표·별지) → 대상(조·장·규정·법령·별표·별지).
판단(오류 확정)은 하지 않는다. status는 구조적 사실만 적는다 (02-specs 오류 유형의 후보).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher

from .normalize import compact, normalize

# ── 패턴 ─────────────────────────────────────────────
RE_JO = re.compile(
    r"제\s*(?P<jo>\d+)\s*(?:-\s*(?P<br2>\d+)\s*)?조(?:\s*의\s*(?P<br>\d+))?"  # 제12-1조 = 제12조의1
    r"(?:\s*\((?P<ttl>[^()\d]{2,25})\))?"  # 인용에 붙은 조문 제목: 「제6조(채용제한)」
    r"(?:\s*제?\s*(?P<hang>\d+)\s*항)?"
    r"(?:\s*제?\s*(?P<ho>\d+(?:\s*(?:[·,]|및|또는)\s*(?:제\s*)?\d+\s*호?)*)\s*호)?"
)
RE_JANG = re.compile(r"제\s*(?P<jang>\d+)\s*장")
RE_HANG_ONLY = re.compile(r"(?<![조\d])\s제\s*(?P<hang>\d+)\s*항")
RE_QUOTED = re.compile(r"「([^」]{2,60})」")
# 약칭 정의: 「개인정보 보호법」(이하 '「개보법」'), 「…법률」(이하 "법"이라 한다), …시행령(이하 "영"이라 함)
RE_ALIAS = re.compile(
    # 이름을 따옴표로 감싼 정의도: ‘전산업무규정’(이하 “규정”이라 한다) (전산업무지침 제1조)
    r"""['"]?(「[^」]+」|[가-힣·\s]{2,40}?(?:법|법률|시행령|시행규칙|규정|지침|규칙|정관))['"]?\s*"""
    r"\(이하\s*['\"]?「?([^'\"」)]{1,20})」?['\"]?\s*(?:(?:이)?라\s*(?:한다|함))?\s*\)"
)
RE_EFFECTIVE_NOTE = re.compile(r"\[\s*시행일\s*:?[^\]]*\]\s*제\s*\d+\s*조(?:\s*의\s*\d+)?(?:\s*제?\s*\d+\s*항)?")
RE_HISTORY = re.compile(r"[\[<(][^\]>)]{0,200}?(?:에서\s*이동|종전|조문이동|본조신설)[^\]>)]{0,200}[\]>)]")
LAW_PLACEHOLDER = re.compile(r"^(법|영|시행령|동법|동령|동\s*법|같은\s*법|위\s*법률|같은\s*법\s*시행령|동법\s*시행령)$")
# 「국가를 당사자로 하는 계약에 관한 법률」·「지방자치단체를 …」의 「…를」은 법령명의 일부라 지우지 않는다 (261012: 국가계약법 시행령이 잘려 지방계약법과 구분 못 함)
LAW_LEAD_GARBAGE = re.compile(r"^(?:(?:및|또는|그|등|이|본)\s+|(?!같은\s)(?!(?:국가|지방자치단체)를\s+당사자로)[가-힣]+(?:은|는|을|를|며|고|서|면)\s+)+")
# 별표는 번호 없이도 인용(「별표에 따른」), 별지는 번호나 '서식'이 있어야 서식 인용으로 본다 (「별지 영수지」 제외)
RE_APPX = re.compile(
    # 가지번호까지: 「별표5의 2」(여비규정), 「별지 제1-9호」(근무평정시행지침), 「별지 제2호의2서식」 — 본번호만 보면 다른 별표에 연결된다 (261012 점검)
    r"별\s*(?P<k1>표)\s*(?:제\s*)?(?:(?P<n1>\d+)(?:\s*(?:의|-)\s*(?P<b1>\d+))?)?"
    r"|별\s*(?P<k2>지)\s*(?:제\s*(?P<n2>\d+)(?:\s*-\s*(?P<b2>\d+))?\s*호(?:\s*의\s*(?P<b3>\d+))?|(?P<n3>\d+))?\s*(?=서식)"
    r"|별\s*(?P<k3>지)\s*제\s*(?P<n4>\d+)(?:\s*-\s*(?P<b4>\d+))?\s*호(?:\s*의\s*(?P<b5>\d+))?"
)
LAWISH = ("법", "법률", "시행령", "시행규칙", "령")
# '임직원행동강령'의 '동강령'이 '동 강령'(같은 강령)으로 잡히지 않도록 앞 글자가 한글이면 제외
SELF_WORDS = re.compile(r"(?:^|[^가-힣])(이|본|동|같은)\s*(규정|지침|규칙|정관|강령)\s*$")
TRAIL_PAREN = re.compile(r"(\s*\([^()]*\))+\s*$")  # 「…법률」(이하 "법"이라 한다), …법(법률 제7633호, …)
UNBRACKETED = re.compile(r"((?:[가-힣·]+\s?){1,4}?(?:규정|지침|규칙|강령))$")
GENERIC = {"규정", "지침", "관련규정", "관계규정", "해당규정", "다른규정", "내부규정", "관련지침", "해당지침", "세부지침", "규칙", "제규정"}
# 규정명이 아니라 일반 표현: 「다른 규정 내지 지침」, 「제반규정」, 「종전 규정」, 「…이 명기된 규정 및 지침」(별지 서약서)
GENERIC_DOC = re.compile(
    r"^(?:(?:제반|종전|개정|해당|관련|관계|각종|다른|위|동|본|이|그|현행|기존)\s*)?(?:규정|지침|규칙|내규|강령)$"
    r"|(?:규정|지침|규칙|내규)\s*(?:및|내지|또는|과|와|이나)\s*(?:규정|지침|규칙|내규)$"
)
# 혼자서는 이름이 안 되는 머리말 (앞 단어가 있어야 이름): 「혁신제안제도 운영지침」의 「운영지침」
GENERIC_HEAD = re.compile(r"^(?:운영|관리|업무|시행|세부|처리|관리운영|운영관리)(?:규정|지침|규칙)$")
# 회사 밖 기관의 기준 (법률 DB 범위 확인 대상). 회사 내규 이름과 겹치지 않는 머리말만
EXTERNAL_HEAD = re.compile(r"^(?:중소기업청|중소벤처기업부|국가|공공기관|공기업|정부|공무원|행정|한국거래소|코스닥|유가증권|표준)")
EXTERNAL_NAMES = {"공시규정", "어문규정"}  # 한국거래소 공시규정, 국어 어문 규정 (문맥으로 확인)
NOT_NAME_HEAD = re.compile(r"^(이|본|동|같은|관련|해당|다른|각|위|아래|그)(\s|$)")
NAME_STOPWORDS = {"등", "시", "및", "또는", "당사", "회사", "전", "후", "관련", "해당", "다른", "기타", "그", "각"}
PARTICLE_END = re.compile(r"(은|는|을|를|의|에|에서|에는|으로|로|와|과|도|만|하여|하고|하며|하는|한|할|된|되는|받은|부터|까지|고|며|서)$")
RE_AMEND_OTHER = re.compile(r"「([^」]+)」\s*(?:중\s*)?(?:일부를|전부를)?\s*다음과\s*같이\s*개정한다")
RE_DOC_ONLY = re.compile(
    r"((?:[가-힣·]+\s?){1,3}?(?:규정|지침))\s*(?=에\s*따라|에\s*의하여|에\s*의한|이\s*정한|에서\s*정한|을\s*준용|를\s*준용)"
)
EXTERNAL_HINT = re.compile(r"(고시|훈령|예규|부령|대통령령)\s*$")
SAME_LAW = re.compile(r"(동법|같은\s*법|동\s*법)\s*$")
LIST_GAP = re.compile(r"^[\s,·및또는와과부터내지까지의\d항호조제]*$")


@dataclass
class Edge:
    src: str  # 출발 단위 ID
    src_kind: str  # 본문 | 부칙 | 별표 | 별지
    raw: str  # 매칭된 원문 표현
    context: str  # 앞뒤 문맥 (norm 기준)
    target_type: str  # article | chapter | regulation | law | appendix
    target_doc: str  # 대상 문서 표현 (자기 자신이면 "")
    target_ref: dict = field(default_factory=dict)  # {jo, br, hang, ho[], jang, appx_kind, appx_no}
    target_reg: str = ""  # 해석된 규정ID (규정 내부·규정 간)
    target_id: str = ""  # 해석된 조문·장·별표 ID
    status: str = ""  # 아래 STATUS 참고
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


STATUS = {
    "ok": "대상 있음",
    "missing_article": "대상 조 없음",
    "missing_item": "대상 항·호 없음",
    "missing_chapter": "대상 장 없음",
    "missing_appendix": "대상 별표·별지 없음",
    "deleted_target": "대상 조가 삭제됨",
    "title_mismatch": "인용에 적은 조문 제목이 실제 제목과 다름",
    "name_mismatch": "규정명이 공개 규정과 다름 (유사 규정 있음)",
    "unknown_regulation": "공개 90건에 없는 규정",
    "law_pending": "법령 인용 (법률 DB 연결 대기)",
    "external_rule": "외부 법령·고시로 추정 (법률 DB 범위 확인 필요)",
    "unresolved": "대상 해석 불가",
    "addendum_history": "부칙의 다른 내규 개정 기록 (이력, 오류 제외)",
}


def _nodot(s: str) -> str:
    return re.sub(r"[·ㆍ・•‧]", "", s)


# ── 규정 사전 ─────────────────────────────────────────
class Registry:
    def __init__(self, regs: list[dict]):
        self.regs = {r["reg_id"]: r for r in regs}
        self.by_compact = {}
        for r in regs:
            for key in {compact(r["name"]), compact(r["name"].removeprefix("한국벤처투자 "))}:
                self.by_compact[key] = r["reg_id"]
                # 가운뎃점만 다른 표기도 같은 이름 (「보수복지규정」 = 「보수·복지규정」, 가빈 C103 '맞음')
                self.by_compact.setdefault(_nodot(key), r["reg_id"])
        self.by_compact.setdefault(compact("정관"), next((r["reg_id"] for r in regs if r["hierarchy"] == "정관"), ""))
        # 괄호 없는 규정명 매칭은 긴 이름부터 (예: '해외근무직원 보수·복지규정'이 '복지규정'보다 먼저)
        self.names_by_len = sorted(self.by_compact.items(), key=lambda kv: -len(kv[0]))
        # 다른 규정에서 정의한 법령 약칭도 쓴다 (예: 업무규정의 "특정금융정보법"을 업무지침이 그대로 사용)
        self.law_aliases: dict[str, str] = {}
        for r in regs:
            for ab, full in _aliases(r).items():
                if is_law(full) and not LAW_PLACEHOLDER.match(ab) and len(ab) >= 3:
                    self.law_aliases.setdefault(ab, full)

    def resolve_name(self, name: str) -> tuple[str, str, str]:
        """(reg_id, status, note)"""
        bare = TRAIL_PAREN.sub("", name).strip() or name
        if is_law(bare):   # 「…법률 시행령(이하 …)」처럼 괄호를 떼면 법령
            return "", "law_pending", ""
        key = compact(name).removeprefix("한국벤처투자")
        key = re.sub(r"^(회사의?|당사)", "", key)
        if key in self.by_compact:
            return self.by_compact[key], "ok", ""
        if _nodot(key) in self.by_compact:
            return self.by_compact[_nodot(key)], "ok", ""
        best, score = "", 0.0
        for k, rid in self.by_compact.items():
            s = SequenceMatcher(None, key, k).ratio()
            if s > score:
                best, score = rid, s
        hint = f"가장 비슷한 규정: {self.regs[best]['name']} ({score:.2f})" if best else ""
        if score >= 0.8:
            return best, "name_mismatch", hint
        if looks_external(name):
            return "", "external_rule", ""
        return "", "unknown_regulation", hint

    def resolve_unbracketed(self, pre: str, form: bool = False) -> tuple[str, str, str, str] | None:
        """괄호 없이 앞에 붙은 규정명 후보 (예: '해외근무직원 보수복지규정', '위임전결 규정').
        (원문 이름, reg_id, status, note) | None"""
        m = UNBRACKETED.search(pre)
        if not m:
            return None
        words = m.group(1).split()
        # 마지막 단어(…규정)에서 왼쪽으로 한 단어씩 붙이되, 조사로 끝나는 단어('항의', '고려하여')나
        # 일반어('당사', '이', '등')를 만나면 멈춘다. 그 앞은 규정명이 아니라 문장이다.
        cands = [words[-1]]
        for w in reversed(words[:-1]):
            if w in NAME_STOPWORDS or NOT_NAME_HEAD.match(w) or (PARTICLE_END.search(w) and not w.endswith(("제도", "정도", "한도"))):
                break
            cands.append(f"{w} {cands[-1]}")
        usable = [c for c in cands if compact(c) not in GENERIC and not GENERIC_DOC.search(compact(c))]
        usable = [c for c in usable if not GENERIC_HEAD.match(compact(c))]
        best = None
        for c in usable:
            rid, st, note = self.resolve_name(c)
            if st == "ok":
                return c, rid, st, note
            if st == "name_mismatch":
                best = (c, rid, st, note)   # 가장 긴 비슷한 이름 (「공무직 관리규정」 > 「관리규정」)
        if best:
            return best
        usable = [c for c in usable if len(compact(c)) >= 4]
        if usable:
            # 본문: 멈추기 전까지 붙인 가장 긴 이름 ('위임전결 규정')
            # 별지·별표: 표 칸 머리말이 앞에 붙기 쉬움 → 가장 짧은 이름 ('배정누계 통제누계 통제잔액 임직원복지규정' → '임직원복지규정')
            c = usable[0] if form else usable[-1]
            rid, st, note = self.resolve_name(c)
            return c, rid, st, note
        return None

    def name_suffix(self, text: str) -> tuple[str, str] | None:
        """text 끝에 공개 규정명이 붙어 있으면 (원문 이름, reg_id)."""
        tail = compact(text)[-40:]
        for key, rid in self.names_by_len:
            if key and tail.endswith(key):
                # 원문에서 그 이름이 차지한 부분을 대략 복원 (표시용)
                return key, rid
        return None


def is_law(name: str) -> bool:
    n = compact(name)
    if n.endswith(("시행규칙", "시행령")):
        return True
    return n.endswith(LAWISH) and not n.endswith(("규정", "지침", "규칙", "정관", "강령"))


def looks_external(name: str) -> bool:
    """「관공서의 공휴일에 관한 규정」(대통령령), 「…부 소관 … 규정」(부처 고시)처럼 회사 내규가 아닌 이름."""
    n = compact(name)
    return bool(re.search(r"에관한(규정|규칙|지침)$|부소관|매뉴얼|^(중소벤처기업부|기획재정부|고용노동부|금융위원회|행정안전부)", n)
                or EXTERNAL_HEAD.match(n) or n in EXTERNAL_NAMES)


def _aliases(reg: dict) -> dict[str, str]:
    text = "\n".join(it["norm"] for a in reg["articles"] for it in a["items"])
    text += "\n" + "\n".join(normalize(ap["text"]) for ap in reg["appendices"])
    out: dict[str, str] = {}
    for m in RE_ALIAS.finditer(text):
        full = m.group(1).strip().strip("「」").strip()
        full = re.sub(r"^(이|본|동|같은)\s+", "", full)
        full = LAW_LEAD_GARBAGE.sub("", full).strip()  # '이 지침은 감사규정(이하 "규정")' → '감사규정'
        out[m.group(2).strip()] = canon_law(full, out, {}, "") if is_law(full) else full
    return out


def canon_law(name: str, aliases: dict[str, str], global_aliases: dict[str, str], last_law: str, quoted: bool = False) -> str:
    """법령 표기를 정식 이름에 가깝게: 약칭·'같은 법 시행령' 풀기, 앞에 붙은 문장 조각 떼기.
    quoted: 「」 안의 이름은 그대로가 이름이라 문장 조각을 떼지 않는다 — 「특정범죄신고 등 보호법」(오기)의 「신고」를
    연결어미 「-고」로 보고 잘라 「보호법」만 남던 문제 (261014 오류 주입 평가)"""
    n = re.sub(r"\s+", " ", name.replace("\n", " ")).strip().strip("「」")
    if not LAW_PLACEHOLDER.match(n) and not quoted:
        n = LAW_LEAD_GARBAGE.sub("", n).strip()
    cands = [last_law, aliases.get("법", ""), global_aliases.get("법", "")]
    base = next((c for c in cands if c and not LAW_PLACEHOLDER.match(c)), "")
    if n == "규칙":
        return aliases.get("규칙") or "(약칭 미해석) 규칙"
    if LAW_PLACEHOLDER.match(n):
        if n in aliases and not LAW_PLACEHOLDER.match(aliases[n]):
            return aliases[n]
        if "시행령" in n or n in ("영", "동령"):
            if n == "영" and "영" in aliases:
                return aliases["영"]
            return (base.removesuffix(" 시행령") + " 시행령") if base else n
        return base or f"(약칭 미해석) {n}"
    if n.startswith(("같은 법 ", "동법 ")) and base:
        return base + n[n.index(" ") + (3 if n.startswith("같은 법") else 0):].replace("법 ", " ", 1)
    return aliases.get(n) or global_aliases.get(n) or n


# ── 대상 존재 확인 ─────────────────────────────────────
def _article_index(reg: dict) -> dict[tuple[int, int], dict]:
    idx = {}
    for a in reg["articles"]:
        m = re.match(r"제(\d+)조(?:의(\d+))?", a["label"])
        idx[(int(m.group(1)), int(m.group(2) or 0))] = a
    return idx


def _chapters(reg: dict) -> dict[int, str]:
    out = {}
    for a in reg["articles"]:
        if m := re.match(r"제\s*(\d+)\s*장\s*(.*)", a["chapter"]):
            out.setdefault(int(m.group(1)), m.group(2).strip())
    return out


def _check_article(reg: dict, ref: dict) -> tuple[str, str, str]:
    """(status, target_id, note)"""
    art = _article_index(reg).get((ref["jo"], ref.get("br", 0)))
    label = f"제{ref['jo']}조" + (f"의{ref['br']}" if ref.get("br") else "")
    if art is None:
        last = reg["articles"][-1]["label"] if reg["articles"] else "-"
        return "missing_article", "", f"{reg['name']}에 {label} 없음 (마지막 조 {last})"
    if art["deleted"]:
        return "deleted_target", art["id"], f"{reg['name']} {label}은 삭제된 조문"
    paths = [tuple(it["path"]) for it in art["items"]]
    hang, hos = ref.get("hang"), ref.get("ho") or []
    if hang and paths and not any(p[0] == hang for p in paths):
        if any(p[0] > 0 for p in paths):
            return "missing_item", art["id"], f"{label}에 제{hang}항 없음"
        return "ok", art["id"], f"{label}은 항 구분 없음 (제{hang}항 확인 불가)"
    for ho in hos:
        if not any(p[1] == ho and (not hang or p[0] == hang) for p in paths):
            return "missing_item", art["id"], f"{label}" + (f" 제{hang}항" if hang else "") + f"에 제{ho}호 없음"
    if ref.get("ttl") and art["title"]:
        a, b = compact(ref["ttl"]), compact(art["title"])
        if a not in b and b not in a:
            return "title_mismatch", art["id"], f"인용 제목 「{ref['ttl']}」, 실제 {label}({art['title']})"
    return "ok", art["id"], art["title"]


def _check_appendix(reg: dict, kind: str, no: int | None, br: int = 0) -> tuple[str, str, str]:
    cands = [x for x in reg["appendices"] if x["kind"] == f"별{kind}"]
    if not cands:
        return "missing_appendix", "", f"{reg['name']}에 별{kind} 없음"
    if no is None:
        return "ok", cands[0]["id"], ""
    for x in cands:
        nums = [int(v) for v in re.findall(r"\d+", x["number"])] or [1]
        if nums[0] == no and (nums[1] if len(nums) > 1 else 0) == br:
            return "ok", x["id"], x["title"]
    want = f"{no}" + (f"의{br}" if br else "")
    return "missing_appendix", "", f"{reg['name']}에 별{kind} {want} 없음 (있는 것: {', '.join(x['number'] or '번호없음' for x in cands)})"


# ── 추출 ─────────────────────────────────────────────
def _ho_list(s: str | None) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", s or "")]


def _context(text: str, start: int, end: int, width: int = 40) -> str:
    return text[max(0, start - width) : end + width].replace("\n", " ")


def _units(reg: dict):
    for a in reg["articles"]:
        for it in a["items"]:
            yield it["id"], "본문", it["norm"], a
    for ad in reg["addenda"]:
        for a in ad["articles"]:
            for it in a["items"]:
                yield it["id"], "부칙", it["norm"], a
    for ap in reg["appendices"]:
        yield ap["id"], ap["kind"], normalize(ap["text"]), None


def extract_edges(reg: dict, registry: Registry) -> list[Edge]:
    aliases = _aliases(reg)
    edges: list[Edge] = []

    def law(name: str, last: str, quoted: bool = False) -> tuple[str, str]:
        return ("law", canon_law(name, aliases, registry.law_aliases, last, quoted))

    def prev_law(text: str, pos: int) -> str:
        """pos 앞에서 마지막으로 나온 「…법」 (동법·같은 법이 가리키는 대상)."""
        found = ""
        for q in RE_QUOTED.finditer(text[:pos]):
            if is_law(q.group(1)) or q.group(1) in registry.law_aliases:
                found = canon_law(q.group(1), aliases, registry.law_aliases, "")
        return found
    for src, kind, text, art in _units(reg):
        n0 = len(edges)
        last_doc: tuple[str, str] | None = None  # (target_type, doc) — 나열 인용 상속용
        last_end = -1
        last_law = ""
        spans: list[tuple[int, int]] = []

        # 부칙의 '다른 내규 개정' 문장: 「X」 일부를 다음과 같이 개정한다 → 이후 인용은 X 기준
        mo = RE_AMEND_OTHER.search(text) if kind == "부칙" else None
        default_doc = ("regulation", mo.group(1)) if mo else None
        quote_spans = [(q.start(), q.end()) for q in re.finditer(r'"[^"\n]{1,80}"', text)] if mo else []

        # 개정 이력 표기: [제11조에서 이동, 종전 제13조는 제15조로 이동], [본조신설, 종전 …] — 옛 번호라 인용이 아니다
        history_spans = [(h.start(), h.end()) for h in RE_HISTORY.finditer(text)]
        # 시행일 표시 「[시행일 : 2021.09.01.] 제16조」의 조는 그 시행일이 적용되는 조를 적은 것이라 인용이 아니다
        history_spans += [(h.start(), h.end()) for h in RE_EFFECTIVE_NOTE.finditer(text)]

        def in_history(pos: int) -> bool:
            return any(s < pos < e for s, e in history_spans)

        def in_quotes(pos: int) -> bool:
            # 개정문 안의 "…"는 바뀌기 전 옛 문구라 해석하지 않는다
            return any(s < pos < e for s, e in quote_spans)

        def doc_before(start: int) -> tuple[str, str] | None:
            """start 바로 앞에 붙은 문서 표현을 판정. (type, doc) | None(자기 규정)"""
            pre = text[max(0, start - 80) : start]
            pre_s = TRAIL_PAREN.sub("", pre.rstrip()).rstrip()
            if in_quotes(start) or in_history(start):
                return ("skip", "")
            if m := re.search(r"「([^」]+)」\s*$", pre_s):
                if is_law(m.group(1)) or m.group(1) in registry.law_aliases:
                    return law(m.group(1), last_law, quoted=True)
                if EXTERNAL_HINT.search(pre_s[: m.start()].rstrip()):
                    return ("external", m.group(1))
                return ("regulation", m.group(1))
            # 「…법률」에 따른 제9조 제1항 — 법령 이름과 조 사이에 연결어가 낀 경우 (법령만, 261015 정밀도 표본)
            if (m := re.search(r"「([^」]+)」\s*(?:에\s*따른|에\s*의한|에서\s*정한|에\s*규정된)\s*$", pre_s)) and \
                    (is_law(m.group(1)) or m.group(1) in registry.law_aliases):
                return law(m.group(1), last_law, quoted=True)
            if SELF_WORDS.search(pre_s):
                return None
            # 「규칙 제39조」처럼 앞에 이름 없이 '규칙'만 오면 법령의 규칙(예: 산업안전보건기준에 관한 규칙)이다.
            # 자기 규정은 '이 규칙·본 규칙'으로 쓰고, 그건 위에서 걸린다.
            if re.search(r"(?:^|[^가-힣])규칙$", pre_s):
                return law(aliases.get("규칙", "규칙"), last_law)
            if m := re.search(r"(동법|같은\s*법|동\s*법|위\s*법률)(\s*시행령)?$", pre_s):
                return law(m.group(0), prev_law(text, start) or last_law)
            if m := re.search(r"「([^」]+)」\s*시행령$", pre_s):
                return law(m.group(1) + " 시행령", last_law, quoted=True)
            for ab, full in {**registry.law_aliases, **aliases}.items():
                if re.search(rf"(^|[^가-힣]){re.escape(ab)}$", pre_s):
                    return law(full, last_law) if is_law(full) else ("regulation", full)
            if hit := registry.name_suffix(pre_s):
                if hit[1] != reg["reg_id"]:
                    return ("regulation", registry.regs[hit[1]]["name"])
                return None
            if m := re.search(r"([가-힣A-Za-z·]*(?:법률|시행령|시행규칙|법|령))$", pre_s):
                if not re.fullmatch(r"(방법|사법|입법|편법|위법|적법|불법|합법|명령|지령|법령|훈령|규정|대통령)", m.group(1)):
                    # '벤처투자 촉진에 관한 법률'처럼 띄어 쓴 법률명은 앞 단어까지 붙인다
                    if m.group(1) == "법률" and (m2 := re.search(r"((?:[가-힣·]+\s){1,6}법률)$", pre_s)):
                        return law(m2.group(1), last_law)
                    if m.group(1) in ("시행령", "령") and (m3 := re.search(r"([가-힣·]+(?:법|법률))\s*시행령$", pre_s)):
                        return law(m3.group(0), last_law)
                    return law(m.group(1), last_law)
            if hit := registry.resolve_unbracketed(pre_s, form=kind in ("별지", "별표")):
                if hit[1] == reg["reg_id"]:
                    return None
                return ("regulation", hit[0])
            return default_doc

        # 1) 조 인용 (+ 장)
        for rx, ttype in ((RE_JO, "article"), (RE_JANG, "chapter")):
            last_doc, last_end = None, -1
            for m in rx.finditer(text):
                if any(s <= m.start() < e for s, e in spans):
                    continue
                gap = text[last_end : m.start()] if last_end >= 0 else None
                # 나열 사이의 괄호 설명은 빼고 본다: 「도로교통법」 제44조제1항(술에 취한 상태에서의 운전금지), 제44조제2항
                # (261015 정밀도 표본: 뒤 조를 이 규정의 조로 읽어 「대상 없음」 오탐)
                # 「영 제5조제1호, 제9조제1호가목, 제13조제2항」의 「가목」도 나열 안의 표기 (261015 정밀도 표본 2차)
                gap = re.sub(r"\([^()]*\)|[가-하]목", "", gap) if gap is not None else None
                if gap is not None and LIST_GAP.match(gap) and len(gap) < 12:
                    doc = last_doc
                else:
                    doc = doc_before(m.start())
                last_doc, last_end = doc, m.end()
                spans.append((m.start(), m.end()))
                if doc and doc[0] == "skip":
                    continue
                ref = (
                    {"jo": int(m["jo"]), "br": int(m["br"] or m["br2"] or 0), "hang": int(m["hang"] or 0) or None, "ho": _ho_list(m["ho"]),
                     "ttl": (m["ttl"] or "").strip() or None}
                    if ttype == "article"
                    else {"jang": int(m["jang"])}
                )
                e = Edge(src, kind, m.group(0), _context(text, m.start(), m.end()), ttype, doc[1] if doc else "", ref)
                if kind in ("별지", "별표") and doc is None and ttype == "article":
                    # 계약서형 별지의 자체 조문 인용은 규정 본문을 가리키지 않는다 → 해석 보류
                    if re.search(r"^\s*제\s*1\s*조\s*\(", text, re.M) and "계약" in text[:400]:
                        continue
                _resolve(e, doc, reg, registry)
                if e.status == "deleted_target" and kind == "부칙" and re.match(r"\s*(?:를|을)?\s*삭제한다", text[m.end():m.end() + 8]):
                    e.status, e.note = "ok", "부칙의 삭제 지시"
                if doc and doc[0] == "law":
                    last_law = doc[1]
                edges.append(e)

        # 2) 문서 단위 인용: 「규정명」 단독
        for m in RE_QUOTED.finditer(text):
            name = m.group(1)
            after = text[m.end() : m.end() + 6]
            if re.match(r"\s*제\s*\d+\s*(조|장)", after):
                continue  # 이미 조·장 인용으로 처리
            name = TRAIL_PAREN.sub("", name).strip() or name   # 「…시행령(이하 "국가계약법 시행령"이라 한다)」
            if GENERIC_DOC.search(compact(name)) or GENERIC_DOC.search(name):
                continue  # 규정명이 아닌 일반 표현 (「…이 명기된 규정 및 지침」)
            if is_law(name) or name in registry.law_aliases:
                name = law(name, last_law, quoted=True)[1]
                last_law = name
                e = Edge(src, kind, m.group(0), _context(text, m.start(), m.end()), "law", name, {}, status="law_pending")
            else:
                if not re.search(r"(규정|지침|규칙|정관|강령|헌장)$", compact(name)):
                    continue  # 「 」가 규정명이 아닌 강조 표현
                e = Edge(src, kind, m.group(0), _context(text, m.start(), m.end()), "regulation", name, {})
                rid, st, note = registry.resolve_name(name)
                if EXTERNAL_HINT.search(text[max(0, m.start() - 12) : m.start()].rstrip()):
                    st, note = "external_rule", ""
                e.target_reg, e.target_id, e.status, e.note = rid, rid if st == "ok" else "", st, note
            edges.append(e)

        # 2-2) 괄호 없는 문서 단위 인용: '위임전결 규정에 따라'
        for m in RE_DOC_ONLY.finditer(text):
            if "」" in text[max(0, m.start() - 2) : m.start() + 1]:
                continue
            hit = registry.resolve_unbracketed(m.group(1).strip(), form=kind in ("별지", "별표"))
            if not hit or hit[1] == reg["reg_id"]:
                continue
            name, rid, st, note = hit
            edges.append(Edge(src, kind, name, _context(text, m.start(), m.end()), "regulation", name, {},
                              rid, rid if st == "ok" else "", st, note))

        # 3) 별표·별지 인용 — 자기 규정, 다른 규정(「징계지침」 별표 2, 차량관리지침 별지 제2호 서식), 법령(「…시행령」 별표 1)
        #    예전에는 앞에 다른 문서 이름이 있으면 건너뛰어(조문 인용 쪽에서 다룬다고 적었으나 실제로는 처리 안 됨) 관계에서 빠졌다 (261012 점검)
        if kind in ("본문", "부칙"):
            for m in RE_APPX.finditer(text):
                if any(s <= m.start() < e for s, e in spans):
                    continue
                kind_ch = m["k1"] or m["k2"] or m["k3"]
                num = m["n1"] or m["n2"] or m["n3"] or m["n4"]
                # 번호 없는 「별표·별지서식」·「[별표] 또는 [별표 1]」은 서식 종류를 말하는 개념어지 인용이 아니다
                # (내규관리규정 제10조·제18조, 261015 정밀도 표본)
                if not num and re.match(r"\s*(?:·|또는|및|이나|와|과)\s*별지", text[m.end():m.end() + 8]):
                    continue
                if text[max(0, m.start() - 1):m.start()] == "[" and text[m.end():m.end() + 2].lstrip()[:1] == "]":
                    continue   # 본문 속 「[별표 1]」은 표기 방법의 예시 (별표 머리글은 별표 단위라 여기 오지 않음)
                no = int(num) if num else None
                br = int(m["b1"] or m["b2"] or m["b3"] or m["b4"] or m["b5"] or 0)
                raw, ctx = m.group(0).strip(), _context(text, m.start(), m.end())
                ref = {"appx_kind": f"별{kind_ch}", "appx_no": no, **({"appx_br": br} if br else {})}
                doc = doc_before(m.start())
                if doc and doc[0] == "skip":
                    continue
                if doc is None:
                    st, tid, note = _check_appendix(reg, kind_ch, no, br)
                    edges.append(Edge(src, kind, raw, ctx, "appendix", "", ref, reg["reg_id"], tid, st, note))
                elif doc[0] == "law":
                    edges.append(Edge(src, kind, raw, ctx, "law", doc[1], ref, status="law_pending"))
                elif doc[0] == "external":
                    edges.append(Edge(src, kind, raw, ctx, "regulation", doc[1], ref, status="external_rule"))
                else:
                    rid, st, note = registry.resolve_name(doc[1])
                    if st == "ok":
                        st, tid, note = _check_appendix(registry.regs[rid], kind_ch, no, br)
                    else:
                        tid = ""
                    edges.append(Edge(src, kind, raw, ctx, "appendix", doc[1], ref, rid, tid, st, note))

        # 부칙의 다른 내규 개정문에서 대상 내규가 공개 90건에 없으면 이력 (현재 문안의 오류가 아님, 02-specs)
        if mo:
            for e in edges[n0:]:
                if e.status == "unknown_regulation" and compact(e.target_doc) == compact(mo.group(1)):
                    e.status = "addendum_history"
    return edges


def _resolve(e: Edge, doc: tuple[str, str] | None, reg: dict, registry: Registry) -> None:
    if doc and doc[0] == "law":
        e.status, e.note = "law_pending", ""
        return
    if doc and doc[0] == "external":
        e.status, e.note = "external_rule", ""
        return
    if doc:
        rid, st, note = registry.resolve_name(doc[1])
        if st != "ok":
            e.status, e.note, e.target_reg = st, note, rid
            if st in ("unknown_regulation", "external_rule", "law_pending"):
                return
        target = registry.regs[rid]
    else:
        target = reg
    e.target_reg = target["reg_id"]
    if e.target_type == "chapter":
        chs = _chapters(target)
        j = e.target_ref["jang"]
        if j in chs:
            e.status = e.status or "ok"
            e.target_id, e.note = f"{target['reg_id']}:장{j}", e.note or f"제{j}장 {chs[j]}"
        else:
            e.status, e.note = "missing_chapter", f"{target['name']}에 제{j}장 없음"
        return
    st, tid, note = _check_article(target, e.target_ref)
    if e.status == "name_mismatch":
        e.target_id, e.note = tid, f"{e.note}; {note}"
    else:
        e.status, e.target_id, e.note = st, tid, note
