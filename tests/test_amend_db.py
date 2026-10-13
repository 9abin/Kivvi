import os

import pytest

from regcheck.amend_db import _date, chunk_key, detail_rows, unit_chunk

GABIN = {"1_제1조", "1_제2조_1항", "1_제2조_2항", "1_제3조", "1_제4조_1항", "1_부칙", "1_별지", "1_별표2_01", "1_별표2_02"}


def test_chunk_key_rules():
    assert chunk_key("1", "제2조", 2, [1, 2], GABIN) == ("1_제2조_2항", "같음")
    assert chunk_key("1", "제1조", 1, [1], GABIN) == ("1_제1조", "단일 항 조")       # 우리 ①만, 가빈 조 전체
    assert chunk_key("1", "제4조", 0, [0], GABIN) == ("1_제4조_1항", "단일 항 조")   # 우리 조 전체, 가빈 「_1항」
    assert chunk_key("1", "제3조", 2, [1, 2], GABIN) == ("1_제3조", "가빈 조 단위")
    assert chunk_key("1", "제9조", 1, [1], GABIN) == (None, None)


def test_unit_chunk_rules():
    assert unit_chunk("1", "부칙", "1:부칙3:2-0-0-0", GABIN) == "1_부칙"
    assert unit_chunk("1", "별지", "1:별지제3호서식", GABIN) == "1_별지"
    assert unit_chunk("1", "별표", "1:별표2", GABIN) == "1_별표2_01"
    assert unit_chunk("1", "별표", "1:별표7", GABIN) is None


def test_date_month_only_revision():
    assert _date("2026-08-28") == "2026-08-28"
    assert _date("2021-03") == "2021-03-01"
    assert _date("") is None


def test_duplicate_paragraph_gets_occurrence():
    # 원문 항 번호 중복(⑤⑤): 같은 item_id, 순번 0·1
    reg = {"reg_id": "1", "articles": [{"id": "1:9", "label": "제9조", "title": "t", "items": [
        {"id": "1:9-5-0-0", "path": [5, 0, 0], "text": "가", "norm": "가"},
        {"id": "1:9-5-0-0", "path": [5, 0, 0], "text": "나", "norm": "나"}]}]}
    rows = detail_rows({"1": reg}, set())
    assert [(r[0], r[1]) for r in rows] == [("1:9-5-0-0", 0), ("1:9-5-0-0", 1)]


@pytest.mark.skipif(os.environ.get("REGCHECK_DB_TESTS") != "1", reason="로컬 reg-db가 있을 때만 (REGCHECK_DB_TESTS=1)")
def test_db_permissions():
    from regcheck.db import connect

    c = connect(write=True)
    for role, sql in [("amend_builder", "UPDATE public.chunks SET body = body WHERE false"),
                      ("editor", "DELETE FROM amend.edge WHERE false"),
                      ("editor", "UPDATE amend.decision_log SET state = 'x' WHERE false"),
                      ("chatbot_ro", "SELECT 1 FROM amend.finding LIMIT 1")]:
        c.execute(f"SET ROLE {role}")
        with pytest.raises(Exception):
            c.execute(sql)
        c.rollback()
        c.execute("RESET ROLE")
