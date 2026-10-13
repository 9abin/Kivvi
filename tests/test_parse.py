from regcheck.build import hierarchy, revision_date, to_records
from regcheck.parse import parse

SAMPLE = """\
1-1. 테스트규정
테 스 트 규 정
제정 2020. 01. 01.
개정 2021. 02. 03.
제1장 총칙
제1조(목적) 이 규정은 테스트를 목적으로 한다.
제2조(정의) ① 이 규정에서 사용하는 용어는 다음과 같다.
  1. "갑"이란 첫째를 말한다.
  2. 삭제 <2021.02.03.>
  ② 그 밖의 용어는 제1조에 따른다. <개정 2021.02.03.>
제2조의2(특례) 이 조는 제2조에도 불구하고 적용한다.
제3조 삭제
제4조 <삭제 2016.12.28.>
부      칙 <2020.01.01.>
제1조(시행일) 이 규정은 2020년 1월 1일부터 시행한다.
부      칙 <2021.02.03.>
이 규정은 2021년 2월 3일부터 시행한다.
【별표 1】 지급 기준
구분 금액
【별지 제1호 서식】
임대차계약서
제1조(목적) 이 계약은 임대차를 목적으로 한다.
"""


def test_body_articles_exclude_addendum_and_form_articles():
    r = parse(SAMPLE.splitlines())
    assert [a.label for a in r.articles] == ["제1조", "제2조", "제2조의2", "제3조", "제4조"]
    assert r.articles[0].chapter == "제1장 총칙"


def test_items_and_deleted():
    r = parse(SAMPLE.splitlines())
    a2 = r.articles[1]
    assert [it.path for it in a2.items] == [(1, 0, 0), (1, 1, 0), (1, 2, 0), (2, 0, 0)]
    assert a2.items[2].deleted
    assert a2.amend_notes == ["<2021.02.03.>", "<개정 2021.02.03.>"]  # 본문 등장 순서
    assert r.articles[3].deleted and r.articles[4].deleted


def test_addenda_and_appendices():
    r = parse(SAMPLE.splitlines())
    assert [ad.date for ad in r.addenda] == ["2020.01.01.", "2021.02.03."]
    assert [a.label for a in r.addenda[0].articles] == ["제1조"]
    assert [(ap.kind, ap.number) for ap in r.appendices] == [("별표", "1"), ("별지", "제1호 서식")]
    assert "제1조(목적) 이 계약은" in r.appendices[1].text  # 계약서 조문은 별지 텍스트에 남는다


def test_ids():
    rec = to_records("999", parse(SAMPLE.splitlines()))
    assert [a["id"] for a in rec["articles"]] == ["999:1", "999:2", "999:2의2", "999:3", "999:4"]
    assert rec["articles"][1]["items"][1]["id"] == "999:2-1-1-0"
    assert rec["addenda"][0]["articles"][0]["id"] == "999:부칙1:1"
    assert [x["id"] for x in rec["appendices"]] == ["999:별표1", "999:별지제1호서식"]


def test_charter_without_articles():
    text = "윤리헌장\n제정 2024.12.06.\n하나, 우리는 정직하다.\n부      칙 <2024.12.06.>\n이 헌장은 시행한다."
    r = parse(text.splitlines())
    assert r.articles == [] and r.preamble == ["하나, 우리는 정직하다."]


def test_metadata_helpers():
    assert hierarchy("한국벤처투자 정관") == "정관"
    assert hierarchy("취업규칙") == "규정"
    assert hierarchy("임직원행동강령") == "지침"
    assert revision_date("242068_인사규정(2026년도 09월 18일 개정).hwp") == "2026-09-18"
    assert revision_date("188094_회계규정(2022년 3월 개정).hwp") == "2022-03"
    assert revision_date("24116_업무추진비 지침 (131011).pdf") == "2013-10-11"


def test_continuation_line_is_not_article_and_bracketed_addendum():
    text = (
        "규정\n제1조(목적) 목적을 정한다.\n제2조(준용) 다음은\n"
        "제1조에 따라 처리한다.\n<부   칙>\n제1조(시행일) 시행한다."
    )
    r = parse(text.splitlines())
    assert [a.label for a in r.articles] == ["제1조", "제2조"]
    assert "제1조에 따라" in r.articles[1].text
    assert len(r.addenda) == 1 and r.addenda[0].articles[0].title == "시행일"


def test_mok_numbering():
    r = parse("규정\n제1조(정의) 다음과 같다.\n  1. 금품등\n    가. 금전\n    나. 물품\n    하. 기타".splitlines())
    assert [it.path for it in r.articles[0].items] == [(0, 0, 0), (0, 1, 0), (0, 1, 1), (0, 1, 2), (0, 1, 14)]


def test_branch_without_ui_and_addendum_after_appendix():
    text = "규정\n제9조(점수) 점수.\n제9조2(동점자) 동점자.\n【별표 1】 표\n값 1\n부      칙 <2020.01.01.>\n제1조(시행일) 시행한다."
    r = parse(text.splitlines())
    assert [a.label for a in r.articles] == ["제9조", "제9조의2"]
    assert len(r.addenda) == 1 and r.addenda[0].articles[0].title == "시행일"
    assert "부" not in r.appendices[0].text


def test_hyphen_branch_article():
    r = parse("지침\n제12조(관리) 관리한다.\n제12-1조(태블릿) ① 태블릿을 관리한다.\n② 반납한다.".splitlines())
    assert [a.label for a in r.articles] == ["제12조", "제12조의1"]
    assert r.articles[1].title == "태블릿"


def test_inline_next_paragraph_marker_is_split():
    # 자금세탁행위 방지 업무지침 제15조: 「…하여야 한다.② …」가 한 줄
    p = parse(["제15조(위험평가) ① 위험을 평가하여야 한다.② 위험을 반영하여야 한다. ③ 등급을 산정한다."])
    items = p.articles[0].items
    assert [i.path[0] for i in items] == [1, 2, 3]
    assert items[0].text.endswith("한다.")


def test_inline_marker_kept_when_not_next_number():
    # 「제1항 ②」처럼 본문 속 기호이거나 다음 번호가 아니면 나누지 않는다
    p = parse(["제3조(준용) ① 제2조를 준용한다.④ 표기는 그대로 둔다."])
    assert [i.path[0] for i in p.articles[0].items] == [1]


def test_inline_items_and_marker_after_list_are_split():
    # 자금세탁행위 방지 업무지침 제15조②: 호 번호와 다음 항이 한 줄에 이어짐
    p = parse(["제15조(위험평가) ① 평가한다.② 다음 각 호의 위험을 반영하여야 한다.1. 국가위험2. 고객고유위험3. 상품 위험 등③ 등급을 산정한다."])
    assert [i.path[:2] for i in p.articles[0].items] == [(1, 0), (2, 0), (2, 1), (2, 2), (2, 3), (3, 0)]
    assert p.articles[0].items[3].text == "고객고유위험"


def test_inline_number_without_list_is_kept():
    p = parse(["제5조(요율) 요율은 연 2. 5퍼센트로 한다."])
    assert len(p.articles[0].items) == 1


def test_marker_listed_after_comma_is_kept():
    # 내규관리규정 제15조: 「항은 ①, ② 등과 같이 … 표시한다」
    p = parse(["제15조(항) ① 항은 ①, ② 등과 같이 표시한다.", "  ② 호는 1., 2.과 같이 표시한다."])
    assert [i.path[0] for i in p.articles[0].items] == [1, 2]


def test_decimal_cell_is_not_item():
    # 근무평정시행지침 제20조 표: 「40.0%」를 40호로 읽지 않는다
    p = parse(["제20조(가중치) ① 가중치는 아래와 같다.", "40.0%", "60.0% 실장"])
    assert [i.path for i in p.articles[0].items] == [(1, 0, 0)]


def test_title_with_inner_parentheses():
    # 복무규정 제19조(휴(무)일 대체)
    p = parse(["제19조(휴(무)일 대체) ① 회사는 휴(무)일을 대체할 수 있다."])
    assert p.articles[0].title == "휴(무)일 대체"
    assert p.articles[0].items[0].text.startswith("회사는")


def test_lookalike_paragraph_marks():
    # 인사규정 제19조 「ⓛ」, 도서관리지침 제7조 「➃」
    p = parse(["제19조(기준) ⓛ 환산한다.", "  ② 산입하지 아니한다.", "  ③ 연장할 수 있다. ➃ 초과할 수 있다."])
    assert [i.path[0] for i in p.articles[0].items] == [1, 2, 3, 4]


def test_lowercase_l_for_one_in_article_number():
    # 보안업무지침 「제l조(목적)」: 숫자 1 대신 영문 소문자 l
    p = parse(["제l조(목적) 이 지침은 필요한 사항을 규정하는데 있다.", "제2조(적용범위) 회사의 보안업무에 적용한다."])
    assert [a.label for a in p.articles] == ["제1조", "제2조"]
    assert parse(["제2조(용어) 「제l조」라고 쓴 문장은 고치지 않는다."]).articles[0].items[0].text.count("제l조") == 1


def test_wrapped_da_is_not_mok():
    # 정보보안관리지침 제15조: 「…익일에 실시한」 + 다음 줄 「다.」
    p = parse(["제15조(진단) ① 익일에 실시한", "다.", "  ② 보안진단을 한다."])
    assert [i.path for i in p.articles[0].items] == [(1, 0, 0), (2, 0, 0)]
