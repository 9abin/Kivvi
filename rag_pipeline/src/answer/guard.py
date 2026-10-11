"""답변이 질문의 말을 근거 밖 다른 항목으로 바꿔치기했는지 코드로 확인한다 (모델을 부르지 않음).

사례(Q073): 질문은 "계약보증금"인데 근거에는 사택 "임차보증금"만 있었고, 답이 "계약보증금은 임차보증금의 10%…"로 나갔다.
질문의 핵심 명사(붙어 있는 명사 덩어리)가 근거 본문에 한 번도 안 나오는데 답에는 쓰였으면 의심한다.
동의어(예: 질문 "병가", 근거 "인병휴가")도 걸릴 수 있으므로 확정이 아니라 '다시 써 보게 하는 신호'로만 쓴다.
"""
import re

_NOUN_TAGS = {"NNG", "NNP", "XR"}
MIN_TERM_LEN = 3


def question_terms(kiwi, question):
    """질문에서 붙어 있는 명사 덩어리(예: 계약 + 보증금 -> 계약보증금)를 뽑는다. 3글자 이상만."""
    terms, start, end = [], None, None
    for t in kiwi.tokenize(question):
        if t.tag in _NOUN_TAGS and end is not None and t.start == end:
            end = t.start + t.len
        elif t.tag in _NOUN_TAGS:
            if start is not None:
                terms.append(question[start:end])
            start, end = t.start, t.start + t.len
        else:
            if start is not None:
                terms.append(question[start:end])
            start = end = None
    if start is not None:
        terms.append(question[start:end])
    seen, out = set(), []
    for term in terms:
        if len(term) >= MIN_TERM_LEN and term not in seen:
            seen.add(term)
            out.append(term)
    return out


def missing_terms(kiwi, question, evidence_texts, answer):
    """질문에는 있고 근거 본문에는 없는데 답에는 쓰인 명사 덩어리."""
    squash = lambda s: re.sub(r"\s+", "", s)
    evidence = squash("".join(evidence_texts))
    answer = squash(answer)
    return [t for t in question_terms(kiwi, question) if squash(t) not in evidence and squash(t) in answer]


_NUMBER = re.compile(r"(?<![\d.,])\d[\d,]*(?:\.\d+)?")
_REFERENCE = re.compile(r"\[\d+\]|제\s*\d+\s*(?:조의\d+|조|항|호|장|절)|별표\s*\d+|별지\s*제?\s*\d+\s*호?|\d+\s*호\s*서식")


def _numbers(text):
    return {n.replace(",", "").rstrip(".") for n in _NUMBER.findall(_REFERENCE.sub("", text))}


_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|퍼센트)")


def _multipliers(text):
    """근거의 비율 p%에서 계산되는 배수: 'p%를 가산'이면 1+p/100배, p%는 p/100배. (예: 50% 가산 -> 1.5배)"""
    out = set()
    for m in _PERCENT.findall(text):
        p = float(m)
        for v in (1 + p / 100, p / 100):
            out.add(f"{v:.2f}".rstrip("0").rstrip("."))
    return out


def unsupported_numbers(question, answer, entries):
    """답의 숫자가 근거 어디에도 없으면 (숫자 목록, 줄)로 돌려준다 (모델을 부르지 않음).

    사례(Q054): 근거 표는 일비 "40"(외화)인데 답이 "40,000원"이라고 썼다.
    질문에 이미 있는 숫자(예: "입사 4개월")와 조·항·별표 번호, 정정 안내 줄(※)은 제외한다.
    인용이 없는 줄은 근거 전체와 비교한다. 근거 안의 숫자는 쉼표를 뗀 정수 토큰으로 비교한다.
    근거 전체와 비교하도록 풀어 봤더니 Q054의 틀린 "40,000원"이 통과했다(국내여비 별표1에 같은 숫자가 있음).
    인용 번호만 틀린 정상 답변 약 3건/190건이 막히는 것은 감수한다. 틀린 숫자가 나가는 것이 더 나쁘다.
    """
    asked = _numbers(question)
    found = []
    for line in answer.split("\n"):
        line = line.strip()
        if not line or line.startswith("※"):
            continue
        cited = sorted({int(x) for x in re.findall(r"\[(\d+)\]", line)})
        pool = [entries[n - 1]["body"] for n in cited if 1 <= n <= len(entries)] or [e["body"] for e in entries]
        known = {t for body in pool for t in re.findall(r"\d+", body)} | _numbers(" ".join(pool))
        known |= _multipliers(" ".join(pool))
        missing = sorted(n for n in _numbers(line) if n not in known and n not in asked)
        if missing:
            found.append((missing, line))
    return found


# ---- 표 범위 필터 -----------------------------------------------------------
# 같은 규정 안에 적용 대상이 다른 별표가 있다(여비규정: 국내 별표1, 국외 별표5, 파견 별표1의2, 외국인 초청 별표6).
# 그 표들이 한 근거 묶음에 섞이면 모델이 값을 섞는다(Q054: 국외 일비 "40"을 국내 표의 "40,000원"과 섞음).
# 질문의 적용 범위가 확실할 때만, 범위가 다른 별표를 답의 근거에서 뺀다. 조문은 빼지 않는다.
_FOREIGN = re.compile(
    r"국외|해외|외국|현지|미국|일본|중국|영국|독일|프랑스|캐나다|호주|싱가포르|베트남|인도네시아|인도|태국|홍콩|대만|필리핀|"
    r"말레이시아|이탈리아|스페인|네덜란드|스위스|러시아|브라질|멕시코|두바이|유럽|동남아|뉴욕|런던|도쿄|베이징|상하이|파리|"
    r"시애틀|샌프란시스코|실리콘밸리|보스턴")
_DOMESTIC = re.compile(
    r"국내|시내|시외|지방|서울|부산|대구|인천|광주|대전|울산|세종|수원|제주|강원|경기|충청|충북|충남|전라|전북|전남|경상|경북|경남|"
    r"여수|창원|판교|천안|청주|전주|춘천|포항")
_COMPARE = re.compile(r"비교|차이|다른가|다르|각각|둘 다|모두|vs|VS")
_SPECIAL_MENTION = {"파견": re.compile(r"파견|사무소|센터"), "외국인초청": re.compile(r"외국인|초청|전문가")}


def question_scope(question):
    """원래 질문이 국내 출장이면 '국내', 국외 출장이면 '국외', 불명확하거나 둘을 비교하면 None."""
    foreign, domestic = bool(_FOREIGN.search(question)), bool(_DOMESTIC.search(question))
    if _COMPARE.search(question) or foreign == domestic:
        return None
    return "국외" if foreign else "국내"


def table_scope(entry):
    """별표 첫머리(제목과 첫 줄)에서 표의 적용 대상을 읽는다. 읽을 수 없으면 None."""
    if not str(entry.get("article", "")).startswith("별표"):
        return None
    # 정확한 구절만 본다. '국외'·'파견' 같은 단어 하나는 다른 표에도 나온다
    # (직무전결규정 별표는 첫머리에 '국외출장' 행이 있을 뿐 모든 업무의 전결권자 표다).
    head = entry["body"][:140]
    if re.search(r"외국인\s*전문가\s*초청", head):
        return "외국인초청"
    if re.search(r"파견\s*및\s*사무소", head):
        return "파견"
    if "국외여비" in head:
        return "국외"
    if "국내여비" in head:
        return "국내"
    return None


def filter_tables_by_scope(question, entries):
    """(남긴 근거, 뺀 표 목록). 뺀 표는 (문서명, 조항, 표의 대상, 이유)다. 다 빼게 되면 하나도 빼지 않는다."""
    scope = question_scope(question)
    if scope is None:
        return entries, []
    kept, excluded = [], []
    for e in entries:
        t = table_scope(e)
        drop = (
            t is not None and t != scope
            and not (t in _SPECIAL_MENTION and _SPECIAL_MENTION[t].search(question))
        )
        if drop:
            excluded.append((e["문서명"], e["article"], t, f"질문은 {scope} 출장인데 표는 {t} 대상"))
        else:
            kept.append(e)
    return (kept, excluded) if kept else (entries, [])


# ---- 표 비고가 부정한 구분을 답이 이유로 쓰는지 --------------------------------------
# 표에 "일비는 표에서 등급 구분 없이 직급당 한 값"이라고 적혀 있는데 답이 "나등급이라 일비는 40"이라고 하면
# 근거의 비고와 답이 모순이다(Q054). 같은 줄에 그 항목(일비)과 그 구분(등급)이 함께 나오면 걸러낸다.
_NO_DISTINCTION = re.compile(r"(?P<s>[가-힣]{2,8})(?:는|은)\s*(?:표에서\s*)?(?P<d>[가-힣]{1,6}?)\s*(?:구분\s*없이|과\s*무관|와\s*무관)")
_NEGATION = r"(?:구분\s*없이|과\s*무관|와\s*무관|구분이\s*없)"


def contradicted_distinctions(answer, entries):
    """근거가 'S는 D 구분 없이/무관'이라고 정했는데 답의 한 줄이 S를 D로 설명하면 (S, D, 줄) 목록."""
    pairs = {(m.group("s"), m.group("d")) for e in entries for m in _NO_DISTINCTION.finditer(e["body"])}
    found = []
    for line in answer.split("\n"):
        line = line.strip()
        if not line or line.startswith("※"):
            continue
        for s, d in pairs:
            if s in line and d in line and not re.search(rf"{d}\s*{_NEGATION}", line):
                found.append((s, d, line))
    return found
