"""오류 주입 (src/regcheck/inject.py): 주입은 한 곳만, 모호하면 하지 않는다. 원문 사본만 바꾼다."""
from regcheck import inject


def reg():
    it1 = {"id": "1:3-1-0-0", "path": [1, 0, 0], "text": "인사규정 제16조에 따른다.", "norm": "인사규정 제16조에 따른다."}
    it2 = {"id": "1:3-2-0-0", "path": [2, 0, 0], "text": "여비규정 제22조의 2에 따른 위원회", "norm": "여비규정 제22조의 2에 따른 위원회"}
    a = {"id": "1:3", "label": "제3조", "text": it1["text"] + "\n" + it2["text"], "norm": it1["norm"] + "\n" + it2["norm"], "items": [it1, it2]}
    return {"reg_id": "1", "name": "가지침", "articles": [a]}


def test_misname_keeps_suffix():
    assert inject.misname("인사규정") == "인사관리규정"
    assert inject.misname("정보보안관리지침") == "정보보안지침"


def test_apply_changes_copy_only():
    r = reg()
    out = inject.apply(r, {"type": "없는조", "src_id": "1:3-1-0-0", "old": "제16조", "new": "제99조"})
    assert "제99조" in out["articles"][0]["items"][0]["norm"] and "제99조" in out["articles"][0]["norm"]
    assert "제16조" in r["articles"][0]["items"][0]["norm"]


def test_apply_refuses_branch_number():
    """「제22조의 2」의 앞부분만 바꾸면 다른 오류(없는 가지 번호)가 되므로 주입하지 않는다"""
    assert inject.apply(reg(), {"type": "없는조", "src_id": "1:3-2-0-0", "old": "제22조", "new": "제99조"}) is None


def test_quoted_law_name_kept_whole():
    """「」 안 법령명은 앞부분을 문장 조각으로 떼지 않는다 (「…신고 등 보호법」의 「신고」를 연결어미 「-고」로 보던 문제)"""
    from regcheck.citations import canon_law
    assert canon_law("특정범죄신고 등 보호법", {}, {}, "", quoted=True) == "특정범죄신고 등 보호법"
    assert canon_law("전자문서 및 전자거래기본법", {}, {}, "", quoted=True) == "전자문서 및 전자거래기본법"
