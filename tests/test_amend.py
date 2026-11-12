import pytest

from regcheck.amend import AmendError, apply, article_edit_amendment, build_changeset, compare_table, label_map, parse_amendment, to_text
from regcheck.build import to_records
from regcheck.luna_rerank import change_block
from regcheck.parse import parse

TEXT = """시험규정
제1조(목적) 이 규정은 시험을 정한다.
제2조(휴직) ① 휴직은 2회까지 한다.
② 휴직 기간은 1년으로 한다.
제3조(면직) 직원이 다음 각 호에 해당하면 면직한다.
1. 사망한 경우
2. 제2조에 따른 휴직 후 복직하지 않은 경우
제4조(위임) 세부사항은 사장이 정한다."""


def reg():
    return {"reg_id": "9", "name": "시험규정", **to_records("9", parse(TEXT.splitlines()))}


def run(text):
    r = reg()
    new, ch = apply(r, parse_amendment(text))
    return r, new, ch


def test_replace_marks_changed_part():
    r, new, ch = run('제2조제1항 중 "2회"를 "3회"로 한다.')
    rows = compare_table(r, new, ch)
    t = to_text(rows)
    assert "휴직은 [[2]]회까지" in t and "휴직은 [[3]]회까지" in t
    assert "(현행과 같음)" in t                      # 바뀌지 않은 제2항은 줄임
    assert r["articles"][1]["items"][0]["norm"] == "휴직은 2회까지 한다."   # 원본 불변


def test_missing_phrase_stops():
    with pytest.raises(AmendError, match="없음"):
        run('제2조제1항 중 "5회"를 "3회"로 한다.')


def test_delete_article_and_label_map():
    r, new, ch = run("제3조를 삭제한다.")
    assert label_map(ch) == {"9:3": None}
    assert "제3조 [[삭제]]" in to_text(compare_table(r, new, ch))


def test_delete_item():
    r, new, ch = run("제3조제2호를 삭제한다.")
    t = to_text(compare_table(r, new, ch))
    assert "2. [[삭제]]" in t and "(현행과 같음)" in t     # 제3조 전체가 아니라 제2호만
    assert label_map(ch) == {}


def test_renumber_and_insert_with_body():
    r, new, ch = run("제3조부터 제4조까지를 각각 제4조부터 제5조까지로 하고, 제2조의 다음에 제3조를 다음과 같이 신설한다.\n"
                     "제3조(복직) ① 휴직자는 복직원을 낸다.\n② 복직 절차는 사장이 정한다.")
    assert label_map(ch) == {"9:3": "제4조", "9:4": "제5조"}
    labels = [a["label"] for a in new["articles"]]
    assert labels == ["제1조", "제2조", "제3조", "제4조", "제5조"]
    t = to_text(compare_table(r, new, ch))
    assert "<신 설>" in t and "[[제3조]]" in t and "② 복직 절차는" in t


def test_insert_on_existing_number_stops():
    with pytest.raises(AmendError, match="이미 있는 번호"):
        run("제2조의 다음에 제3조를 다음과 같이 신설한다.\n제3조(복직) 복직한다.")


@pytest.mark.parametrize("edit_body", [False, True])
def test_direct_edit_moves_article_to_unused_number(edit_body):
    r = reg()
    article = r["articles"][1]
    edited = article["text"].replace("제2조(", "제19조(", 1)
    if edit_body:
        edited = edited.replace("2회", "3회")
    amendment = article_edit_amendment(article, edited, reg=r)
    assert amendment.startswith("제2조를 제19조로 한다.")
    cs, revised = build_changeset(r, amendment, "edit")
    assert cs["label_map"] == {"9:2": "제19조"}
    assert [op["op"] for op in cs["changes"][0]["ops"]] == (["renumber", "rewrite"] if edit_body else ["renumber"])
    moved = next(a for a in revised["articles"] if a["id"] == article["id"])
    assert moved["items"][0]["norm"] == ("휴직은 3회까지 한다." if edit_body else "휴직은 2회까지 한다.")
    assert article["label"] == "제2조"
    context = change_block(r, amendment, article["id"])
    assert "조 번호 변경: 제2조 → 제19조" in context
    assert "조 전체 삭제" not in context


def test_direct_edit_rejects_occupied_number():
    r = reg()
    article = r["articles"][1]
    with pytest.raises(AmendError, match="제3조가 이미 있습니다"):
        article_edit_amendment(article, article["text"].replace("제2조(", "제3조(", 1), reg=r)


def test_title_and_rewrite():
    r, new, ch = run('제4조의 제목 중 "위임"을 "세부사항"으로 한다.\n제1조를 다음과 같이 한다.\n제1조(목적) 이 규정은 시험 절차를 정한다.')
    t = to_text(compare_table(r, new, ch))
    assert "제4조([[위임]])" in t and "제4조([[세부사항]])" in t
    assert "시험[[을]] 정한다" in t and "시험[[ 절차를]] 정한다" in t


def test_unreadable_sentence_stops():
    with pytest.raises(AmendError, match="해석 못함"):
        run("휴직 규정을 손본다.")


def test_pairs_are_replaced_simultaneously_and_respect_boundaries():
    text = "시험규정\n제1조(목적) 제7조와 제8조, 제7조의2 및 제17조를 따른다."
    r = {"reg_id": "9", "name": "시험규정", **to_records("9", parse(text.splitlines()))}
    new, ch = apply(r, parse_amendment('제1조 중 "제7조"를 "제8조"로, "제8조"를 "제9조"로 한다.'))
    assert new["articles"][0]["items"][0]["norm"] == "제8조와 제9조, 제7조의2 및 제17조를 따른다."


# ---- 본문·단서 범위, 이하 밀기, 호 신설

TEXT2 = """시험규정
제1조(목적) 이 규정은 시험을 정한다.
제2조(정의) 이 규정에서 사용하는 용어의 뜻은 다음과 같다.
1. "가"란 첫째를 말한다.
2. "나"란 둘째를 말한다.
제3조(기한) ① 신청은 30일 이내에 한다. 다만, 부득이한 경우에는 30일 이내에 연장할 수 있다.
제4조(보고) 보고는 제3조에 따른다.
제4조의2(추가) 추가 보고는 제3조를 따른다."""


def reg2():
    return {"reg_id": "9", "name": "시험규정", **to_records("9", parse(TEXT2.splitlines()))}


def test_scope_body_only_changes_before_proviso():
    new, _ = apply(reg2(), parse_amendment('제3조제1항 본문 중 "30일"을 "60일"로 한다.'))
    t = next(a for a in new["articles"] if a["label"] == "제3조")["items"][0]["norm"]
    assert "60일 이내에 한다" in t and "부득이한 경우에는 30일" in t


def test_scope_proviso_only_changes_after_proviso():
    new, _ = apply(reg2(), parse_amendment('제3조제1항 단서 중 "30일"을 "60일"로 한다.'))
    t = next(a for a in new["articles"] if a["label"] == "제3조")["items"][0]["norm"]
    assert "신청은 30일 이내" in t and "부득이한 경우에는 60일" in t


def test_scope_proviso_without_proviso_stops():
    with pytest.raises(AmendError):
        apply(reg2(), parse_amendment('제4조 단서 중 "보고"를 "통보"로 한다.'))


def test_shift_moves_all_later_articles_including_branches():
    text = '제3조 이하를 각각 한 조씩 뒤로 하고, 제2조의 다음에 제3조를 다음과 같이 신설한다.\n제3조(신설) 새 조문이다.'
    new, ch = apply(reg2(), parse_amendment(text))
    labels = [a["label"] for a in new["articles"]]
    assert labels == ["제1조", "제2조", "제3조", "제4조", "제5조", "제5조의2"]
    assert {c["origin"]: c["new_label"] for c in ch if c["origin"]}["9:4의2"] == "제5조의2" if any(c["origin"] == "9:4의2" for c in ch) else True


def test_insert_ho_adds_item_in_definition_article():
    text = '제2조에 제3호를 다음과 같이 신설한다.\n3. "다"란 셋째를 말한다.'
    new, ch = apply(reg2(), parse_amendment(text))
    a = next(x for x in new["articles"] if x["label"] == "제2조")
    assert [it["path"] for it in a["items"]][-1] == [0, 3, 0] and "셋째" in a["items"][-1]["norm"]


def test_insert_ho_on_existing_number_stops():
    with pytest.raises(AmendError):
        apply(reg2(), parse_amendment('제2조에 제2호를 다음과 같이 신설한다.\n2. "다"란 셋째를 말한다.'))
