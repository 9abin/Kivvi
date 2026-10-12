from regcheck.build import to_records
from regcheck.citations import Registry, extract_edges, is_law
from regcheck.parse import parse


def reg(reg_id, name, text, hierarchy="규정"):
    r = to_records(reg_id, parse(text.splitlines()))
    return {"reg_id": reg_id, "name": name, "hierarchy": hierarchy, **r}


SELF = reg(
    "1",
    "회계규정",
    """회계규정
제1조(목적) 이 규정은 회계를 정한다.
제2조(결산) ① 결산서는 제128조에 따른다.
  ② 제1조 및 제3조에 따른다.
제3조(인용) 인사규정 제2조 제5호와 「휴직자의 복무관리지침」, 「근로기준법」 제76조의2에 따른다.
제4조(제목) 인사규정 제1조(채용제한)에 따른다. 위임전결 규정에 따라 승인한다. 임직원행동강령 제1조를 준용한다.
제5조(개정) 이 규정 제1조를 따른다.
부      칙 <2020.01.01.>
제1조(다른 내규의 개정) 「인사규정」 일부를 다음과 같이 개정한다. 제2조 중 "직제규정 제99조"를 "직제규정 제1조"로 한다.
""",
)
OTHERS = [
    reg("2", "인사규정", "인사규정\n제1조(채용결격사유) 결격사유.\n제2조(정의) 다음과 같다.\n  1. 가\n  2. 나"),
    reg("3", "휴직자 복무관리 지침", "지침\n제1조(목적) 목적.", "지침"),
    reg("4", "직무전결규정", "규정\n제1조(목적) 목적."),
    reg("5", "임직원행동강령", "강령\n제1조(목적) 목적.", "지침"),
    reg("6", "직제규정", "규정\n제1조(목적) 목적."),
]
EDGES = extract_edges(SELF, Registry([SELF, *OTHERS]))


def find(src, raw):
    return [e for e in EDGES if e.src == src and raw in e.raw]


def test_missing_article_in_same_regulation():
    (e,) = find("1:2-1-0-0", "제128조")
    assert e.status == "missing_article" and e.target_reg == "1"


def test_list_inherits_document():
    assert find("1:2-2-0-0", "제1조")[0].status == "ok"
    assert find("1:2-2-0-0", "제3조")[0].target_reg == "1"


def test_unbracketed_regulation_and_missing_item():
    (e,) = find("1:3-0-0-0", "제2조 제5호")
    assert e.target_reg == "2" and e.status == "missing_item"


def test_name_mismatch_and_law():
    assert find("1:3-0-0-0", "휴직자의 복무관리지침")[0].status == "name_mismatch"
    assert find("1:3-0-0-0", "제76조의2")[0].status == "law_pending"


def test_title_mismatch_unknown_regulation_and_gang_ryeong_is_not_law():
    assert find("1:4-0-0-0", "제1조(채용제한)")[0].status == "title_mismatch"
    (e,) = find("1:4-0-0-0", "위임전결 규정")
    assert e.status == "unknown_regulation" and "직무전결규정" in e.note
    (e,) = find("1:4-0-0-0", "제1조")[-1:]
    assert e.target_reg == "5" and e.status == "ok"
    assert not is_law("임직원행동강령") and is_law("공무원 징계령 시행규칙")


def test_self_reference_words():
    (e,) = find("1:5-0-0-0", "제1조")
    assert e.target_reg == "1" and e.status == "ok"


def test_addendum_amendment_targets_other_regulation_and_skips_quoted_old_text():
    es = [e for e in EDGES if e.src.startswith("1:부칙1:")]
    jo = [e for e in es if e.target_type == "article"]
    assert [(e.raw, e.target_reg) for e in jo] == [("제2조", "2")]  # "직제규정 제99조"(옛 문구)는 해석하지 않음


def test_history_note_and_bare_rule_abbreviation():
    r = reg("7", "테스트지침", "지침\n제1조(목적) 목적.\n제2조(기준) 기준은 규칙 제39조에 따른다. [제1조에서 이동, 종전 제2조는 제3조로 이동]", "지침")
    es = extract_edges(r, Registry([r]))
    assert [(e.raw, e.status) for e in es if e.target_type == "article"] == [("제39조", "law_pending")]
    assert es[0].target_doc == "(약칭 미해석) 규칙"


def _edges_of(text, others=()):
    from regcheck.build import to_records
    from regcheck.citations import Registry, extract_edges
    from regcheck.parse import parse
    regs = [{"reg_id": str(i), "name": t.splitlines()[0], "hierarchy": "규정", **to_records(str(i), parse(t.splitlines()))}
            for i, t in enumerate([text, *others], 1)]
    return extract_edges(regs[0], Registry(regs))


def test_generic_phrases_are_not_citations():
    es = _edges_of("가규정\n제1조(목적) 「퇴직금 중간정산이 명기된 규정 및 지침」을 숙지한다. 다른 「규정 내지 지침」, 「제반규정」에 따른다.")
    assert [e for e in es if e.target_type == "regulation"] == []


def test_trailing_alias_paren_law():
    es = _edges_of('가규정\n제1조(목적) 「국가를 당사자로 하는 계약에 관한 법률 시행령(이하 "국가계약법 시행령"이라 한다)」에 따른다.')
    assert [e.status for e in es] == ["law_pending"]


def test_external_agency_rules():
    es = _edges_of("가규정\n제1조(목적) 「중소기업청 정보보안 기본지 침」과 「공공기관 통합공시 매뉴얼, 전자문서 작성지침」을 따른다.")
    assert {e.status for e in es} == {"external_rule"}


def test_unbracketed_name_with_jedo_and_longest_mismatch():
    es = _edges_of("가규정\n제1조(목적) 혁신제안제도 운영지침에 의한 포상은 제외한다.\n제2조(수당) 기타수당은 공무직 관리규정에 따라 지급한다.",
                   ["공무직관리지침\n제1조(목적) 목적."])
    docs = {e.target_doc for e in es}
    assert "운영지침" not in docs and "관리규정" not in docs
    assert "혁신제안제도 운영지침" in docs and "공무직 관리규정" in docs


def test_addendum_amending_other_unknown_is_history():
    es = _edges_of('가규정\n제1조(목적) 목적.\n부      칙 <2020.01.01.>\n제1조(다른 내규의 개정) 「없는출자지침」 일부를 다음과 같이 개정한다. '
                   '제2조제1항제1호 중 "가"를 "나"로 한다.')
    assert es and all(e.status == "addendum_history" for e in es)


def test_article_citation_of_law_with_alias_paren():
    es = _edges_of('가규정\n제1조(목적) 「국가를 당사자로 하는 계약에 관한 법률 시행령(이하 "국가계약법 시행령"이라 한다)」제76조제1항에 따른다.')
    assert [e.status for e in es] == ["law_pending"]


def test_unbracketed_generic_phrases_are_not_citations():
    es = _edges_of("가규정\n제1조(목적) 목적.\n부      칙 <2020.01.01.>\n제2조(경과조치) 개정 전 선정된 자는 개정 지침에 의한 것으로 본다. "
                   "종전 규정에 의한 호봉을 적용한다.")
    assert [e for e in es if e.target_type == "regulation"] == []


def test_middle_dot_difference_is_same_name():
    es = _edges_of("가규정\n제1조(목적) 해외근무직원 보수복지규정 제5조에 따른다.", ["해외근무직원 보수·복지규정\n제5조(수당) 수당."])
    assert [e.status for e in es if e.target_type == "article"] == ["ok"]


def test_quoted_alias_and_effective_note():
    # 전산업무지침: ‘전산업무규정’(이하 “규정”이라 한다) → 「규정 제4조」는 전산업무규정 / [시행일 : …] 제16조는 인용 아님
    from regcheck.citations import RE_ALIAS, RE_EFFECTIVE_NOTE
    from regcheck.normalize import normalize
    m = RE_ALIAS.search(normalize("제정한 ‘전산업무규정’(이하 “규정”이라 한다)에서 위임된 사항"))
    assert m and m.group(1).strip() == "전산업무규정" and m.group(2) == "규정"
    assert RE_EFFECTIVE_NOTE.search("본부장으로 본다. [시행일 : 2021.09.01.] 제16조")
