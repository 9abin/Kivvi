from regcheck.agent import build_package, fix_sentence
from regcheck.amend import build_changeset
from regcheck.build import to_records
from regcheck.parse import parse

A = """갑규정
제1조(목적) 이 규정은 갑을 정한다.
제2조(휴직) ① 휴직은 2회까지 한다.
② 제3조에 따른 면직자는 휴직할 수 없다.
제3조(면직) 직원이 사망하면 면직한다.
제4조(준용) 제3조를 준용한다."""
B = """을지침
제1조(목적) 이 지침은 을을 정한다.
제3조(위임) 면직에 관하여는 「갑규정」 제3조에 따른다."""


def regs():
    return {"1": {"reg_id": "1", "name": "갑규정", "hierarchy": "규정", **to_records("1", parse(A.splitlines()))},
            "2": {"reg_id": "2", "name": "을지침", "hierarchy": "지침", **to_records("2", parse(B.splitlines()))}}


def rel(src, target, ev):
    return {"src": src, "src_article": src.split("-")[0], "src_kind": "본문", "target": target, "target_type": "article",
            "evidence": ev, "context": ev, "check_status": "ok"}


RELS = [rel("1:2-2-0-0", "1:3", "제3조"), rel("1:4-0-0-0", "1:3", "제3조"), rel("2:3-0-0-0", "1:3", "제3조")]


def test_fix_sentence_particles():
    assert fix_sentence("제3조제2항", [("제7조제1항", "제8조제1항")]) == '제3조제2항 중 "제7조제1항"을 "제8조제1항"으로 한다.'
    assert fix_sentence("제9조", [("제7조", "제8조"), ("제10조", "제11조")]) == '제9조 중 "제7조"를 "제8조"로, "제10조"를 "제11조"로 한다.'


def test_renumber_loop_fixes_same_reg_and_drafts_other_reg():
    rs = regs()
    cs, _ = build_changeset(rs["1"], "제3조부터 제4조까지를 각각 제4조부터 제5조까지로 하고, 제2조의 다음에 제3조를 다음과 같이 신설한다.\n"
                                     "제3조(징계) 징계는 따로 정한다.", "T1")
    p = build_package(cs, rs, RELS, [])
    assert (p["breaks_before"], p["breaks_after"]) == (2, 0)
    # 옮겨진 옛 제4조는 지금 제5조로 적는다
    assert set(p["added_sentences"]) == {'제2조제2항 중 "제3조"를 "제4조"로 한다.', '제5조 중 "제3조"를 "제4조"로 한다.'}
    assert [c["reg_name"] for c in p["companion"]] == ["을지침"] and p["companion"][0]["status"] == "ok"
    assert "다른 내규와의 관계" in p["addendum_draft"]


def test_delete_becomes_question_not_fix():
    rs = regs()
    cs, _ = build_changeset(rs["1"], "제3조를 삭제한다.", "T2")
    p = build_package(cs, rs, RELS, [])
    assert p["added_sentences"] == [] and p["breaks_after"] >= 2
    assert any("결정이 필요" in q["question"] for q in p["questions"])
