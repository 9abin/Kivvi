from regcheck.consistency import deadline_findings, deadlines, vocab_findings
from regcheck.moves import move_conflicts, parse_moves, stale_by_moves

VOCAB = {"기관": {"대표이사": {"변형": ["사장", "이사장"], "우선순위": "중간"}}, "표기": {"지체 없이": ["지체없이"]}}


def art(aid, label, text, notes=()):
    return {"id": aid, "label": label, "title": "", "text": text, "amend_notes": list(notes),
            "items": [{"id": f"{aid}-1-0-0", "path": [1, 0, 0], "norm": text, "amend_marks": list(notes)}]}


def reg(rid, name, arts):
    return {"reg_id": rid, "name": name, "articles": arts, "addenda": [], "history": []}


def test_org_variant_and_external_title():
    regs = {"1": reg("1", "계약규정", [art("1:1", "제1조", "사장은 계약을 체결한다. 지체없이 보고한다."),
                                         art("1:2", "제2조", "진흥공단 이사장이 지정하는 임원"),
                                         art("1:3", "제3조", "사장 또는 사장의 위임을 받은 자")])}
    out = vocab_findings(regs, VOCAB)
    assert {(f["article"], f["quote"]) for f in out} == {("제1조", "사장"), ("제3조", "사장"), ("제1조", "지체없이")}   # 외부 기관 직함은 제외


def test_deadline_skips_dates_and_age_and_flags_outlier():
    regs = {str(i): reg(str(i), f"규정{i}", [art(f"{i}:1", "제1조", "결과에 이의가 있으면 통보일로부터 7일 이내에 이의를 신청할 수 있다.")]) for i in range(4)}
    regs["9"] = reg("9", "다른규정", [art("9:1", "제1조", "2020년 5월 3일부터 시행하며 만 20세 이상은 결과에 이의가 있으면 영업일 5일 이내에 재심을 청구할 수 있다.")])
    rows = deadlines(regs)
    assert not any(r["n"] == 3 and r["reg_id"] == "9" for r in rows) and not any(r["n"] == 20 for r in rows)   # 날짜·나이 제외
    out = deadline_findings(rows)
    assert [f["reg_name"] for f in out if f["rule"] == "deadline_outlier"] == ["다른규정"]


def test_move_parse_and_stale_citation():
    regs = {"1": reg("1", "급여규정", [art("1:21", "제21조", "자녀수당", ["[종전 제21조는 제22조로 이동<2025.03.28.>]"]),
                                         art("1:22", "제22조", "성과급", ["[제21조에서 이동 <2025.03.28.>]"])]),
            "2": reg("2", "성과급지급지침", [art("2:1", "제1조", "급여규정 제21조에 의하여", ["<개정 2023.09.20.>"])])}
    mv = parse_moves(regs)
    assert [(m["old"], m["new"], m["date"]) for m in mv] == [("21", "22", "2025.03.28")]
    rel = {"src": "2:1-1-0-0", "src_article": "2:1", "src_kind": "본문", "target": "1:21", "target_type": "article", "kind": "인용", "evidence": "제21조", "context": "…"}
    out = stale_by_moves(regs, mv, [rel])
    assert len(out) == 1 and out[0]["suggest"] == "22"
    regs["2"]["articles"][0]["amend_notes"] = ["<개정 2025.06.01.>"]   # 이동 뒤에 인용 쪽이 고쳐졌으면 제외
    regs["2"]["articles"][0]["items"][0]["amend_marks"] = ["<개정 2025.06.01.>"]
    assert stale_by_moves(regs, mv, [rel]) == []


def test_move_conflict_two_old_to_same_new():
    mv = [{"reg_id": "1", "reg_name": "이사회규정", "old": o, "new": "15", "kind": "이동", "date": "2025.08.27", "where": f"제{o}조", "mark": "m"} for o in ("13", "14")]
    assert len(move_conflicts(mv)) == 1
