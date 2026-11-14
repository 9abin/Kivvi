import sqlite3

from regcheck import legal_basis as lb


def test_rank_and_precedence_by_kind():
    assert lb.reg_rank("취업규칙") == 2 and lb.reg_rank("징계지침") == 1 and lb.reg_rank("한국벤처투자 정관") == 3
    assert "규정인 「인사규정」가 지침인 「징계지침」보다 우선" in lb.precedence_hint("인사규정", "2026-09-18", "징계지침", "2025-07")
    assert lb.precedence_winner("인사규정", None, "징계지침", None) == "인사규정"


def test_same_rank_later_revision_wins():
    assert lb.precedence_winner("취업규칙", "2025-06-25", "인사규정", "2026-09-18") == "인사규정"
    assert lb.precedence_winner("취업규칙", "2025-06", "인사규정", "2025-06") is None


def _db():
    con = sqlite3.connect(":memory:")
    con.execute("create table documents (id text, api_title text)")
    con.execute("create table chunks (id integer primary key, document_id text, path text, content text)")
    con.execute("insert into documents values ('d1', '남녀고용평등법')")
    con.execute("insert into chunks (document_id, path, content) values ('d1', '19 육아휴직', '⑦ 육아휴직은 1년에 1회에 한정하여 주 단위로 사용할 수 있다.')")
    con.execute("insert into chunks (document_id, path, content) values ('d1', '19의4 사용형태', '① 근로자는 육아휴직을 3회에 한정하여 나누어 사용할 수 있다.')")
    return con


def test_law_counts_requires_same_kind_of_limit():
    laws = lb.law_counts(_db(), "육아휴직", "육아휴직은 3회에 한정하여 나누어 사용할 수 있다.")
    assert [x["count"] for x in laws] == [3] and lb.article_label(laws[0]["path"]) == "제19조의4"


def test_floor_hint_relation():
    laws = lb.law_counts(_db(), "육아휴직", "나누어 사용")
    assert "법령보다 적게 정함" in lb.floor_hint("인사규정", 2, laws)
    assert "같음" in lb.floor_hint("취업규칙", 3, laws)
    assert lb.agrees_with_law(3, laws) is True and lb.agrees_with_law(2, laws) is False


def test_article7_applies_in_order_law_first():
    laws = lb.law_counts(_db(), "육아휴직", "나누어 사용")
    r = lb.apply_article7({"name": "취업규칙", "revised": "2025-06-25", "count": 3}, {"name": "인사규정", "revised": "2026-09-18", "count": 2}, laws)
    assert r["decided"] == 1 and r["keep"] == "취업규칙" and r["change"] == "인사규정"   # 후법 우선(③)이 아니라 법령(①)에서 결정


def test_article7_falls_through_to_rank_then_time():
    r = lb.apply_article7({"name": "인사규정", "revised": "2026-01", "count": 3}, {"name": "징계지침", "revised": "2026-09", "count": 5}, [])
    assert r["decided"] == 2 and r["keep"] == "인사규정"
    r = lb.apply_article7({"name": "취업규칙", "revised": "2025-06", "count": 3}, {"name": "인사규정", "revised": "2026-09", "count": 2}, [])
    assert r["decided"] == 3 and r["keep"] == "인사규정"
    r = lb.apply_article7({"name": "취업규칙", "revised": "2025-06", "count": 3}, {"name": "인사규정", "revised": "2025-06", "count": 2}, [])
    assert r["decided"] is None
