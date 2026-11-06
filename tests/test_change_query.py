import pytest

from regcheck.build import to_records
from regcheck.change_query import analyze
from regcheck.parse import parse

TEXT = """시험규정
제1조(목적) 이 규정은 시험을 정한다.
제2조(휴직) ① 휴직은 2회까지 한다.
② 휴직 기간은 1년으로 한다.
제3조(용어의 정의) 이 규정에서 사용하는 용어의 뜻은 다음과 같다.
1. "고객확인의무"란 고객의 신원 확인 및 검증을 하는 것을 말한다.
2. "보고책임자"란 보고를 총괄하는 사람을 말한다.
제4조(면직) 직원이 다음 각 호에 해당하면 면직한다.
1. 사망한 경우
2. 제2조에 따른 휴직 후 복직하지 않은 경우
제5조(위임) 세부사항은 사장이 정한다.
제6조(보고) 보고는 서면으로 한다."""


def reg():
    return {"reg_id": "9", "name": "시험규정", **to_records("9", parse(TEXT.splitlines()))}


def test_value_change():
    q = analyze(reg(), '제2조제1항 중 "2회"를 "3회"로 한다.', "T")
    assert q.kind == "값 변경"
    assert q.starts == ["9:2"] and q.old_values == ["2회"] and q.new_values == ["3회"]


def test_value_change_with_each_phrase():
    q = analyze(reg(), '제2조제2항 중 "1년"을 각각 "2년"으로 한다.', "T")   # 「각각」이 끼어도 읽는다
    assert q.kind == "값 변경" and q.new_values == ["2년"]


def test_whole_article_delete():
    q = analyze(reg(), "제4조를 삭제한다.", "T")
    assert q.kind == "삭제" and q.starts == ["9:4"] and "면직" in q.removed


def test_renumber_range_keeps_every_moved_article():
    q = analyze(reg(), "제4조부터 제6조까지를 각각 제5조부터 제7조까지로 하고, 제3조의 다음에 제4조를 다음과 같이 신설한다.\n제4조(신설) 신설 조문이다.", "T")
    assert q.kind == "번호 이동"
    assert q.starts == ["9:4", "9:5", "9:6"] and q.new_articles == ["제4조"]


def test_definition_change_extracts_term_and_new_words():
    q = analyze(reg(), '제3조제1호 중 "신원 확인 및 검증을 하는 것"을 "신원 확인 및 검증과 지속적 거래 모니터링을 하는 것"으로 한다.', "T")
    assert q.kind == "정의 변경"
    assert q.terms == ["고객확인의무"] and q.term_cores == ["고객확인"]
    assert any("모니터링" in p for p in q.phrases)


def test_wording_only_change_is_not_content_change():
    q = analyze(reg(), '제6조 중 "서면으로"를 "서면으로써"로 한다.', "T")
    assert q.kind == "표현 수정"


def test_unreadable_amendment_is_reported_not_guessed():
    q = analyze(reg(), "제9조를 마음대로 고친다.", "T")
    assert q.kind == "분석 실패" and q.error


def test_search_terms_skip_common_words():
    q = analyze(reg(), '제3조제1호 중 "신원 확인 및 검증을 하는 것"을 "신원 확인 및 검증과 지속적 거래 모니터링을 하는 것"으로 한다.', "T")
    terms = dict(q.search_terms(lambda t: 100 if t == "고객확인" else 3))
    assert "고객확인" not in terms and "고객확인의무" in terms


def test_sub_item_of_definition_is_definition_change():
    text = TEXT + "\n제7조(용어의 정의) 이 지침에서 사용하는 용어의 뜻은 다음과 같다.\n1. \"임기제 직원\"은 다음 각 호에 해당하는 사람을 말한다.\n가. 프로젝트 업무\n나. 9개월 이상 2년 이내 기간 동안 수행되는 업무"
    r = {"reg_id": "9", "name": "시험규정", **to_records("9", parse(text.splitlines()))}
    q = analyze(r, '제7조제1호나목 중 "9개월 이상 2년 이내 기간"을 "6개월 이상 3년 이내 기간"으로 한다.', "T")
    assert q.kind == "정의 변경" and q.terms == ["임기제 직원"]


def test_body_decides_is_delegation():
    q = analyze(reg(), '제5조 중 "사장이 정한다"를 "사장이 정하고 이사회에 보고한다"로 한다.', "T")
    assert q.kind == "위임·법령 변경"


def test_item_reference_when_numbered_list_is_parsed_as_paragraphs():
    r = reg()
    a = next(x for x in r["articles"] if x["label"] == "제3조")
    for n, it in enumerate(a["items"]):   # 항 구분 없이 1. 2. 만 있는 조: 호가 항 자리([n,0,0])로 읽힌 모양
        it["path"] = [n, 0, 0]
    q = analyze(r, '제3조제1호 중 "신원 확인 및 검증을 하는 것"을 "신원 확인 및 검증과 지속적 거래 모니터링을 하는 것"으로 한다.', "T")
    assert q.kind != "분석 실패", q.error


def test_period_word_change_is_value_change():
    from regcheck.change_query import _period_change
    assert _period_change(["연"], ["반기"])
    assert _period_change(["분기"], ["월"])
    assert not _period_change(["연"], ["연"])
    assert not _period_change(["연"], ["반기별 점검"])   # 주기 말이 아닌 것이 섞이면 아니다
    assert not _period_change([], ["반기"])


def _reg_with_appendix():
    art = {"id": "R:1", "label": "제1조", "title": "목적", "chapter": "", "deleted": False, "amend_notes": [],
           "items": [{"id": "R:1-0-0-0", "path": [0, 0, 0], "text": "이 규정은 급여를 정한다.", "norm": "이 규정은 급여를 정한다.", "amend_marks": [], "deleted": False}]}
    ap = {"id": "R:별지제1호서식", "kind": "별지", "number": "제1호 서식", "title": "", "text": "근 로 계 약 서\n임금은 매월 25일에 지급한다."}
    return {"reg_id": "R", "name": "가규정", "articles": [art], "appendices": [ap]}


def test_appendix_target_is_analyzed_as_a_value_change():
    from regcheck.change_query import analyze
    q = analyze(_reg_with_appendix(), '별지 제1호 서식 중 "매월 25일"을 "매월 20일"로 한다.', "T")
    assert q.kind == "값 변경" and q.starts == ["R:별지제1호서식"], (q.kind, q.starts, q.error)
    assert q.old_values == ["25일"] and q.new_values == ["20일"] and q.units == ["별지 제1호 서식"]


def test_appendix_target_missing_is_reported_not_crashed():
    from regcheck.change_query import analyze
    q = analyze(_reg_with_appendix(), '별지 제9호 서식 중 "매월 25일"을 "매월 20일"로 한다.', "T")
    assert q.kind == "분석 실패"
