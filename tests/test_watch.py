"""변경 감시 (src/regcheck/watch.py): review_key로 새로 생김·해결됨·그대로를 가른다. DB·법률 DB 없이."""
from regcheck import watch
from regcheck.review import key_baseline


def f(article, quote, typ="법령_개정확인", para=1):
    return {"reg_id": "1", "unit": "본문", "article": article, "para": para, "quote": quote, "type": typ}


def test_diff_splits_new_resolved_kept():
    prev = [f("제1조", "제9조"), f("제2조", "제3조")]
    cur = [f("제1조", "제9조"), f("제3조", "여성폭력기본법", "법령명_오기")]
    d = watch.diff(prev, cur)
    assert [x["article"] for x in d["new"]] == ["제3조"]
    assert [x["article"] for x in d["resolved"]] == ["제2조"]
    assert [x["article"] for x in d["kept"]] == ["제1조"]


def test_diff_uses_stored_review_key_and_dedups():
    """DB에서 읽은 직전 결과는 review_key를 그대로 쓴다. 같은 키가 두 번 나와도 한 건"""
    stored = {"review_key": key_baseline(f("제1조", "제9조")), "reg_id": "1", "type": "법령_개정확인"}
    d = watch.diff([stored], [f("제1조", "제9조"), f("제1조", "제9조")])
    assert (len(d["new"]), len(d["resolved"]), len(d["kept"])) == (0, 0, 1)
