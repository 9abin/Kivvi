from regcheck.amend import apply, parse_amendment
from regcheck.build import to_records
from regcheck.change_check import check, score
from regcheck.parse import parse

A = """갑규정
제1조(목적) 이 규정은 갑을 정한다.
제2조(휴직) ① 휴직은 2회까지 한다.
② 제3조에 따른 면직자는 휴직할 수 없다.
제3조(면직) 직원이 사망하면 면직한다.
제4조(준용) 제3조를 준용한다."""
B = """을지침
제1조(목적) 이 지침은 을을 정한다.
제2조(휴직) 휴직은 3회까지 한다.
제3조(위임) 면직에 관하여는 「갑규정」 제3조에 따른다."""


def regs():
    return {"1": {"reg_id": "1", "name": "갑규정", **to_records("1", parse(A.splitlines()))},
            "2": {"reg_id": "2", "name": "을지침", **to_records("2", parse(B.splitlines()))}}


def rel(src, target, ev, ctx, ttype="article", kind="본문"):
    return {"src": src, "src_article": src.split("-")[0], "src_kind": kind, "target": target, "target_type": ttype,
            "evidence": ev, "context": ctx}


RELS = [rel("1:2-2-0-0", "1:3", "제3조", "제3조에 따른 면직자는"), rel("1:4-0-0-0", "1:3", "제3조", "제3조를 준용한다"),
        rel("2:3-0-0-0", "1:3", "제3조", "「갑규정」 제3조에 따른다")]


def changeset(rs, text):
    from regcheck.amend import label_map
    new, ch = apply(rs["1"], parse_amendment(text))
    return {"reg_id": "1", "reg_name": "갑규정", "amendment": text, "changes": ch, "label_map": label_map(ch)}, new


def test_delete_breaks_same_reg_and_other_reg():
    rs = regs()
    cs, new = changeset(rs, "제3조를 삭제한다.")
    found = check(cs, rs, RELS, new_reg=new)
    breaks = {(f["reg_id"], f["article"]) for f in found if f["category"] == "breaks"}
    assert breaks == {("1", "제2조"), ("1", "제4조"), ("2", "제3조")}
    assert any(f["category"] == "undeterminable" for f in found)


def test_renumber_same_reg_breaks_other_reg_review_with_suggest():
    rs = regs()
    cs, new = changeset(rs, "제3조부터 제4조까지를 각각 제4조부터 제5조까지로 한다.")
    found = check(cs, rs, RELS, new_reg=new)
    b = [f for f in found if f["category"] == "breaks"]
    r = [f for f in found if f["category"] == "review" and f["reg_id"] == "2"]
    assert {f["article"] for f in b} == {"제2조", "제4조"} and all(f["suggest"] == "제4조" for f in b)
    assert r and r[0]["suggest"] == "제4조"


def test_value_change_resolves_same_title_conflict():
    rs = regs()
    cs, new = changeset(rs, '제2조제1항 중 "2회"를 "3회"로 한다.')
    found = check(cs, rs, RELS, new_reg=new)
    res = [f for f in found if f["category"] == "resolved"]
    assert [(f["reg_id"], f["article"]) for f in res] == [("2", "제2조")]
    sc = score(found, {"resolved": [{"reg_id": "2", "article": "제2조"}], "not_affected": [{"reg_id": "2", "article": "제1조"}]})
    assert sc["resolved"][0]["result"] == "탐지" and sc["not_affected"][0]["result"] == "통과"


def test_value_change_new_conflict():
    rs = regs()
    rs["2"]["articles"][1]["items"][0]["norm"] = "휴직은 2회까지 한다."
    cs, new = changeset(rs, '제2조제1항 중 "2회"를 "4회"로 한다.')
    found = check(cs, rs, RELS, new_reg=new)
    assert [f["article"] for f in found if f["category"] == "new_conflicts"] == ["제2조"]
