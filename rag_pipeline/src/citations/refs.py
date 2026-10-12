"""참조(인용) 추출과 약칭 해석.

참조 상태
  정상연결        : 대상 조항이 청크에 존재
  확인필요(대상없음): 같은 문서(또는 연결된 문서)에 대상 조항이 없음 → 원본 확인
  외부            : 90건 밖의 법령, 규칙·령 등 외부 법규
  옛 규정 참조(검토 완료)   : 부칙의 참조. 옛 규정을 가리키는 경우가 많아 자동 연결하지 않음
  미해결(약칭)     : 약칭 정의를 찾지 못함 → 사람이 확인
"""
import re
import collections


REF = re.compile(r"제(\d+)조(?:\s*[의\-]\s*(\d+))?(?:\s*제(\d+)항)?")
ALIAS = re.compile(
    # '(이하 “X”라 한다)', '((이하 “X”라 한다.))' 모두 인식
    r"\(+\s*이하\s*[“\"‘'「]?\s*([^”\"’'」)\n]{1,20}?)\s*[”\"’'」]?\s*(?:이라|라)\s*(?:고\s*)?(?:한다|함)\.?\s*\)+"
)
EXT_WORD = re.compile(r"(?:시행)?(?:규칙|령|고시|훈령|예규)\s*$")
SELF_WORD = re.compile(r"(?<![가-힣])(?:본|이|같은)\s*(?:규정|지침|운영지침|강령|헌장|정관|법)?\s*$")
UNRESOLVED_WORD = re.compile(r"(?:규정|지침|법|정관|헌장|강령|령)\s*$")


LAW_NAME = re.compile(r"(?:[가-힣]{2,}(?:법|령|규칙)|법률|시행령|시행규칙)\s*$")
DEF_TAIL = re.compile(ALIAS.pattern + r"\s*$")
HISTORY_WORDS = ("이동", "종전")

# "동법"·"같은 법"은 문서가 정의한 1글자 약칭("법")이 아니라 바로 앞에서 쓴 법령을 가리킨다.
# 이 체크를 약칭 사전 조회보다 먼저 해야, "(이하 "법"이라 한다)"로 정의된 그 문서의 법과
# 전혀 다른 "동법"(예: 다른 법을 가리킴)이 섞이지 않는다.
SAME_LAW = re.compile(r"(?<![가-힣])(?:동법|같은\s*법)\s*$")
# "영"·"시행령"이 바로 앞에 있으면 법 자체가 아니라 그 법의 시행령을 가리킨다.
DECREE_WORD = re.compile(r"(?<![가-힣])(?:시행령|영)\s*$")


def _in_history_note(text, pos):
    """pos가 대괄호 개정 이력([… 이동 …]) 안에 있으면 True"""
    left = text.rfind("[", 0, pos)
    right = text.find("]", pos)
    if left < 0 or right < 0 or text.rfind("]", 0, pos) > left:
        return False
    body = text[left:right + 1]
    return any(w in body for w in HISTORY_WORDS)


def _is_corpus_name(pre, norm):
    pc = re.sub(r"\s+", "", pre[-40:])
    return any(pc.endswith(nm) for nm in norm)


def normalize_brackets(s):
    return s.replace("｢", "「").replace("｣", "」")


def _target_id(doc, n, k, h):
    t = f"{doc}_제{n}조" + (f"의{k}" if k else "")
    return t + (f"_{h}항" if h else "")


def build_aliases(rows, names):
    """문서별 약칭 사전: '(이하 “X”라 한다)' 앞의 문서명을 찾아 X → 고유번호 로 저장."""
    norm = {re.sub(r"\s+", "", nm): idx for idx, nm in names.items()}
    aliases = {}
    for idx in names:
        txt = normalize_brackets("\n".join(r["본문"] for r in rows if r["고유번호"] == idx))
        d = {}
        for m in ALIAS.finditer(txt):
            pre = txt[max(0, m.start() - 200):m.start()]
            ali = re.sub(r"[「」]", "", m.group(1)).strip()
            pre_c = re.sub(r"[「」\s]", "", pre)
            cand = [k for k in norm if pre_c.endswith(k)]
            if cand:
                d[ali] = norm[max(cand, key=len)]
                continue
            qm = list(re.finditer(r"「([^」]+)」", pre))
            if qm:
                name = qm[-1].group(1).strip()
                # "「X법」(이하 "법"이라 한다) 및 같은 법 시행령(이하 "영"이라 한다)"처럼, 괄호 뒤부터
                # 이 약칭 정의 앞까지에 "시행령"이 있으면 법이 아니라 그 시행령을 가리키는 약칭이다.
                if re.search(r"시행령", pre[qm[-1].end():]):
                    name += " 시행령"
                d[ali] = "외부:" + name
        if d:
            aliases[idx] = d
    return aliases, norm


JUNGWAN_ID = None  # 한국벤처투자 정관의 고유번호
STEM_RULE = {}     # 'X지침' 문서 고유번호 → 'X규정' 문서 고유번호


def _set_rules(names):
    global JUNGWAN_ID, STEM_RULE
    by_name = {re.sub(r"\s+", "", nm): idx for idx, nm in names.items()}
    JUNGWAN_ID = by_name.get("한국벤처투자정관")
    STEM_RULE = {}
    for idx, nm in names.items():
        n = re.sub(r"\s+", "", nm)
        if n.endswith("지침") and (n[:-2] + "규정") in by_name:
            STEM_RULE[idx] = by_name[n[:-2] + "규정"]


def resolve_citations(rows, names):
    """모든 청크의 cites를 채운다. 반환: (rows, aliases, 상태별 건수)"""
    _set_rules(names)
    aliases, norm = build_aliases(rows, names)
    ids = {r["id"] for r in rows}

    def exists(t):
        return t in ids or any(i.startswith(t + "_") for i in ids)

    stat = collections.Counter()
    for r in rows:
        own = r["고유번호"]
        text = normalize_brackets(r["본문"])
        cites = []
        last_kind, last_target, last_law_name, last_end = None, None, None, -1
        for m in REF.finditer(text):
            if _in_history_note(text, m.start()):
                continue
            pre = text[max(0, m.start() - 200):m.start()]
            n, k, h = m.group(1), m.group(2), m.group(3)
            kind, target, law_name = None, None, None
            between = text[last_end:m.start()] if last_end >= 0 else None
            # 나열 이어받기: 사이에 쉼표·'및'·'또는'과 호·목·항 표기(제2호, 가목 등)만 있을 때
            if last_kind and between is not None and re.fullmatch(
                    r"(?:\s|,|、|및|또는|제\d+(?:호|항)(?:[가-하]목)?|\d+호|[가-하]목)*", between):
                kind, target, law_name = last_kind, last_target, last_law_name  # 나열 참조는 앞 대상을 이어받음
            else:
                hit = None
                if SAME_LAW.search(pre) and last_law_name:
                    hit = "외부:" + last_law_name  # "동법"·"같은 법" = 바로 앞에서 쓴 법령
                if hit is None:
                    for ali, tgt in aliases.get(own, {}).items():
                        if pre.rstrip().endswith(ali):
                            hit = tgt
                            break
                if hit is None:
                    dm = DEF_TAIL.search(pre)
                    if dm:
                        ali = re.sub(r"[「」]", "", dm.group(1)).strip()
                        hit = aliases.get(own, {}).get(ali, "외부:" + ali)
                # 뒤쪽 괄호 없는 구간이 다음 호(목록 항목, "\n2." 등)로 넘어가면 끊어서,
                # 앞 호의 법령명이 뒤 호의 다른 참조에 잘못 이어붙지 않게 한다.
                bm = re.search(r"「([^」]+)」\s*(?:제\d+조(?:(?!\n\s*\d+[.)])[^「」]){0,40})?$", pre)
                if hit is None and bm:
                    nm = re.sub(r"\s+", "", bm.group(1))
                    hit = norm.get(nm, "외부:" + bm.group(1))
                if hit is None and re.search(r"정관\s*$", pre[-6:]) and JUNGWAN_ID and own != JUNGWAN_ID:
                    hit = JUNGWAN_ID  # '정관 제N조' = 한국벤처투자 정관
                if hit is None and re.search(r"(?<![가-힣])규정\s*$", pre[-6:]) and own in STEM_RULE:
                    hit = STEM_RULE[own]  # 'X지침'이 '규정'이라 하면 같은 이름의 'X규정'
                if hit is None:  # 문서명을 괄호 없이 쓴 경우 (예: '전산업무규정 제4조')
                    pc = re.sub(r"\s+", "", pre[-40:])
                    cand = [nm for nm in norm if pc.endswith(nm)]
                    if cand:
                        hit = norm[max(cand, key=len)]
                # 여기까지 못 찾았고 바로 앞이 "영"/"시행령"이면, 그 표기를 뗀 글로 다시 한 번
                # 시도한다(법 자체가 아니라 그 법의 시행령을 가리키는 경우). 문서가 이미 "영"을
                # 약칭으로 정의해 둔 경우는 위에서 먼저 잡히므로 여기까지 오지 않는다.
                decree_m = DECREE_WORD.search(pre) if hit is None else None
                if decree_m:
                    pre_law = pre[:decree_m.start()]
                    if SAME_LAW.search(pre_law) and last_law_name:
                        hit = "외부:" + last_law_name
                    if hit is None:
                        bm2 = re.search(
                            r"「([^」]+)」\s*(?:제\d+조(?:(?!\n\s*\d+[.)])[^「」]){0,40})?$", pre_law)
                        if bm2:
                            nm = re.sub(r"\s+", "", bm2.group(1))
                            if nm not in norm:  # 내부 문서의 시행령은 없으므로 외부 법령만 인정
                                hit = "외부:" + bm2.group(1)
                if hit is not None and decree_m and str(hit).startswith("외부:") and not str(hit).endswith(" 시행령"):
                    hit = "외부:" + str(hit)[3:] + " 시행령"
                if hit is not None and not str(hit).startswith("외부:"):
                    kind, target = "내부문서", hit
                elif hit is not None:
                    kind, law_name = "외부", str(hit)[3:]
                elif SELF_WORD.search(pre[-20:]):
                    kind, target = "내부", own
                elif LAW_NAME.search(pre[-40:]) and not _is_corpus_name(pre, norm):
                    kind = "외부"
                elif EXT_WORD.search(pre[-12:]):
                    kind = "외부"
                elif UNRESOLVED_WORD.search(pre[-12:]):
                    kind = "미해결"
                else:
                    kind, target = "내부", own
            if kind in ("내부", "내부문서"):
                t = _target_id(target, n, k, h)
                st = "정상연결" if exists(t) else "확인필요(대상없음)"
            elif kind == "외부":
                t, st = None, "외부"
            else:
                t, st = None, "미해결(약칭)"
            if r["id"].endswith("_부칙") and st in ("확인필요(대상없음)", "미해결(약칭)"):
                st = "옛 규정 참조(검토 완료)"
            last_kind, last_target, last_law_name = kind, (target if kind != "외부" else None), law_name
            last_end = m.end()
            stat[st] += 1
            entry = {"대상": t, "유형": kind, "원문": m.group(0), "상태": st}
            if law_name:
                entry["법령명"] = law_name
            cites.append(entry)
        r["cites"] = cites
    return rows, aliases, stat


def review_old_references(rows, names):
    """부칙의 옛 규정 참조 중, 앞뒤 문맥에 현행 규정 이름이 있는 것은 '연결 후보'로 표시한다.
    나머지는 '옛 규정 참조(검토 완료)'로 둔다."""
    norm_names = [re.sub(r"\s+", "", nm) for nm in names.values() if nm]
    n_candidate = 0
    for r in rows:
        own = re.sub(r"\s+", "", r["문서명"])
        for c in r["cites"]:
            if not c["상태"].startswith("옛 규정") or c.get("정정ID"):
                continue  # 사람이 판정한 항목(정정ID 있음)은 건드리지 않음
            p = r["본문"].find(c["원문"])
            win = re.sub(r"\s+", "", r["본문"][max(0, p - 40):p + 30]) if p >= 0 else ""
            if any(n in win and n != own for n in norm_names):
                c["상태"] = "옛 규정 참조(연결 후보)"
                n_candidate += 1
    return rows, n_candidate
