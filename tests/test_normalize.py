from regcheck.normalize import amend_marks, compact, index_header, normalize


def test_normalize_removes_amend_marks_and_unifies_symbols():
    raw = "임원의 “보수”는 이사회ㆍ주주총회의  결의로 정한다. <개정 2022.08.09., 2025.07.02.>"
    assert normalize(raw) == '임원의 "보수"는 이사회·주주총회의 결의로 정한다.'
    assert amend_marks(raw) == ["<개정 2022.08.09., 2025.07.02.>"]


def test_normalize_bracket_style_amend_mark():
    assert normalize("중간정산을 실시할 수 있다.[2014.05.29. 개정]") == "중간정산을 실시할 수 있다."


def test_compact_ignores_spacing():
    assert compact("퇴 직 금 지 급 규 정") == compact("퇴직금지급규정")


def test_index_header():
    assert index_header("2-6. 퇴직급지급규정") == "퇴직급지급규정"
    assert index_header("1-2   이사회규정") == "이사회규정"
    assert index_header("제1조(목적) 이 규정은") is None
    assert index_header("2014. 05. 29.") is None
