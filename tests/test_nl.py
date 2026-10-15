"""자연어 → 개정문: LLM 없이 검증할 수 있는 부분 (출력 정리, 조문 다시 쓰기 → 개정문, 규칙 계획, 의도 검사)."""
from regcheck.amend import build_changeset
from regcheck.build import to_records
from regcheck.nl import article_blocks, blocks_to_amendment, candidates, clean_output, intent_issues, structural_plan
from regcheck.parse import parse

T = """가규정
제1조(목적) 이 규정은 가를 정한다.
제2조(결산보고) 대표이사는 제128조의 규정에 의한 결산서를 제출한다.
제3조(특별공로금) ① 특별공로금을 지급할 수 있다.
② 특별공로금은 이사회의 의결을 거쳐야 한다.
제4조(당연면직) 사망하면 면직한다.
제5조(위임) 세부사항은 따로 정한다."""


def reg():
    return {"reg_id": "1", "name": "가규정", **to_records("1", parse(T.splitlines()))}


def test_clean_output_strips_title_in_reference_and_noise():
    raw = '물론입니다!\n```\n제3조(특별공로금) 중 "이사회의 의결"을 "대표이사의 승인"으로 한다.\n```'
    assert clean_output(raw) == '제3조 중 "이사회의 의결"을 "대표이사의 승인"으로 한다.'


def test_rewritten_article_becomes_minimal_amendment():
    raw = "제3조(특별공로금)\n① 특별공로금을 지급할 수 있다.\n② 특별공로금은 대표이사의 승인을 거쳐야 한다."
    amend = blocks_to_amendment(reg(), article_blocks(raw))
    assert amend == '제3조제2항 중 "이사회의 의결을"을 "대표이사의 승인을"로 한다.'
    build_changeset(reg(), amend, "t")


def test_structural_plan_delete_by_title_and_insert_with_body():
    r = reg()
    assert structural_plan(r, "가규정에서 당연면직 조항을 삭제해줘", candidates(r, "당연면직")) == "제4조를 삭제한다."
    plan = structural_plan(r, "가규정 제3조 다음으로 포상 조문을 새로 넣고 뒤 번호는 밀어줘. 내용은 '포상은 따로 정한다.'", [])
    assert plan.splitlines() == ["제4조부터 제5조까지를 각각 제5조부터 제6조까지로 하고, 제3조의 다음에 제4조를 다음과 같이 신설한다.",
                                 "제4조(포상) 포상은 따로 정한다."]
    build_changeset(r, plan, "t")


def test_title_in_request_beats_cited_number():
    r = reg()
    assert candidates(r, "결산보고 조항이 제128조를 드는데 제1조로 고쳐줘")[0]["label"] == "제2조"


def test_intent_issues_catch_valid_but_wrong_drafts():
    r = reg()
    req = "가규정 제3조 다음으로 포상 조문을 새로 넣고 뒤 번호는 밀어줘"
    cs, _ = build_changeset(r, "제4조를 다음과 같이 한다.\n제4조(포상) 포상은 따로 정한다.", "t")   # 신설 대신 기존 조를 덮어씀
    issues = intent_issues(req, cs, r, candidates(r, req))
    assert any("신설" in i for i in issues) and any("당연면직" in i for i in issues)


def test_span_pair_balances_parentheses_and_keeps_quantifier():
    # 261013 웹 초안: 「"2회(나누어 … 총 3회"를 …」처럼 괄호 짝이 안 맞고 「최대」가 빠졌던 경우
    from regcheck.nl import _span_pair
    old = "육아휴직 기간은 3년 이내로 하며, 분할하는 횟수는 최대 2회(나누어 사용하게 되는 휴직의 단위는 총 3회)로 한다."
    new = old.replace("최대 2회", "최대 3회").replace("총 3회", "총 4회")
    assert _span_pair(old, new) == ("최대 2회(나누어 사용하게 되는 휴직의 단위는 총 3회)", "최대 3회(나누어 사용하게 되는 휴직의 단위는 총 4회)")


def test_span_pair_widens_by_words_until_unique():
    from regcheck.nl import _span_pair
    old = "직원은 1일 전까지 신청하고 사용 후 1일 이내에 보고한다."
    new = "직원은 1일 전까지 신청하고 사용 후 3일 이내에 보고한다."
    a, b = _span_pair(old, new)
    assert old.count(a) == 1 and new.count(b) == 1
    assert old.replace(a, b) == new
    words = old.split()
    assert a.split()[0] in words and a.split()[-1] in words   # 어절 중간에서 자르지 않음


def test_shift_request_blocked_early_when_branch_articles_follow():
    from regcheck.nl import draft
    reg = {"reg_id": "9", "name": "갑지침", "articles": [
        {"id": "9:3", "label": "제3조", "title": "위임", "deleted": False, "items": [], "text": ""},
        {"id": "9:4", "label": "제4조", "title": "범위", "deleted": False, "items": [], "text": ""},
        {"id": "9:4의2", "label": "제4조의2", "title": "계약서", "deleted": False, "items": [], "text": ""}]}
    res = draft({"9": reg}, "제3조 다음에 교육 조문을 새로 넣고 뒤 번호를 밀어줘", reg=reg)
    assert not res["ok"] and res["tries"] == [] and "가지번호" in res["error"]       # LLM을 부르지 않고 바로 안내
