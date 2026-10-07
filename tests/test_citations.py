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
