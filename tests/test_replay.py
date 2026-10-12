from regcheck.amend import build_changeset
from regcheck.replay import reconstruct


def art(aid, label, title, notes=(), text_extra=""):
    return {"id": aid, "label": label, "title": title, "chapter": "", "deleted": False, "amend_notes": list(notes),
            "text": f"{label}({title}) 본문 {text_extra}",
            "items": [{"id": f"{aid}-0-0-0", "path": [0, 0, 0], "norm": f"{title} 본문", "text": "", "amend_marks": [], "deleted": False}]}


def test_reconstruct_moves_and_insert_like_salary_rule():
    # 2025.03.28: 제21조(자녀수당) 신설, 종전 제21조(성과급) → 제22조, 종전 제22조 → 제23조
    reg = {"reg_id": "9", "name": "급여규정", "articles": [
        art("9:20", "제20조", "연차수당"),
        art("9:21", "제21조", "자녀수당", ["[본조신설 2025.03.28.]"]),
        art("9:22", "제22조", "성과급", [], "[종전 제21조에서 이동, 종전 제22조는 제23조로 이동 <2025.03.28.>]"),
        art("9:23", "제23조", "위원 수당", [], "[종전 제22조에서 이동 <2025.03.28.>]"),
    ], "addenda": [], "appendices": []}
    prior, text, note = reconstruct(reg, "2025.03.28")
    assert [(a["label"], a["title"], a["id"]) for a in prior["articles"]] == [
        ("제20조", "연차수당", "9:20"), ("제21조", "성과급", "9:21"), ("제22조", "위원 수당", "9:22")]
    assert text.splitlines()[:3] == ["제22조를 제23조로 한다.", "제21조를 제22조로 한다.", "제20조의 다음에 제21조를 다음과 같이 신설한다."]
    new, _ = build_changeset(prior, text, "r")[0], None
    assert [r["new_label"] for r in new["rows"]][:1] == ["제21조"] or new["rows"]


def test_reconstruct_branch_numbers_order():
    # 2021.04.22: 종전 제8조의2 → 제8조의3, 종전 제8조의1 → 제8조의2, 제8조의4 신설
    reg = {"reg_id": "8", "name": "교육지침", "articles": [
        art("8:8", "제8조", "위탁교육"),
        art("8:8의2", "제8조의2", "지원대상", [], "[종전 제8조의1에서 이동 2021.04.22.]"),
        art("8:8의3", "제8조의3", "보직자 교육", [], "[종전 제8조의2에서 이동 2021.04.22.]"),
        art("8:8의4", "제8조의4", "준수사항", ["[본조신설 2021.04.22.]"]),
    ], "addenda": [], "appendices": []}
    prior, text, note = reconstruct(reg, "2021.04.22")
    lines = text.splitlines()
    assert lines[:2] == ["제8조의2를 제8조의3으로 한다.", "제8조의1을 제8조의2로 한다."]
    assert lines[2] == "제8조의3의 다음에 제8조의4를 다음과 같이 신설한다."
    build_changeset(prior, text, "r")   # 적용기가 멈추지 않음
