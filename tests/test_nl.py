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
