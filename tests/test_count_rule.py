from regcheck import consistency


def reg(rid, name, label, title, norm):
    return {"reg_id": rid, "name": name, "articles": [{"id": f"{rid}:1", "label": label, "title": title, "norm": norm, "items": [], "deleted": False}]}


def test_count_limits_ignore_per_use_and_threshold():
    t = "나누어 사용하는 각 1회의 기간은 30일 이상이어야 한다. 정당한 이유 없이 2회 이상 채용을 거부한 경우는 제외한다."
    assert consistency.count_limits(t) == {}


def test_count_limits_keep_split_limit():
    t = "육아휴직은 3회(사용하게 되는 휴직의 단위는 최대 4회)에 한정하여 나누어 사용할 수 있다."
    assert set(consistency.count_limits(t)) == {3, 4}


def test_count_diffs_flags_different_limits_in_same_title():
    regs = {"a": reg("a", "취업규칙", "제43조", "육아휴직", "육아휴직은 3회에 한정하여 나누어 사용할 수 있다."),
            "b": reg("b", "인사규정", "제30조", "육아휴직", "분할하는 횟수는 최대 2회로 한다.")}
    d = consistency.count_diffs(regs)
    assert len(d) == 1 and d[0]["a_counts"] == [3] and d[0]["b_counts"] == [2]


def test_count_diffs_same_limits_not_flagged():
    regs = {"a": reg("a", "A규정", "제1조", "육아휴직", "최대 2회로 한다."), "b": reg("b", "B규정", "제2조", "육아휴직", "분할 횟수는 최대 2회")}
    assert consistency.count_diffs(regs) == []


def _reg(rid, name, label, title, norm, items=None):
    return {"reg_id": rid, "name": name, "articles": [{"id": f"{rid}:1", "label": label, "title": title, "norm": norm, "items": items or [{"id": f"{rid}:1-1-0-0", "path": [1, 0, 0], "norm": norm}], "deleted": False}]}


def test_authority_diffs_flags_different_bodies_on_same_matter():
    regs = {"a": _reg("a", "회계규정", "제29조", "예산의 전용", "대표이사의 승인을 득하여 예산의 각 항목 간의 금액을 전용할 수 있다."),
            "b": _reg("b", "이사회규정", "제3조", "심의 · 의결사항", "이사회는 다음 사항을 의결한다. 예산의 항목간 전용",
                      items=[{"id": "b:1-1-0-0", "path": [1, 0, 0], "norm": "이사회는 다음 사항을 의결한다."}, {"id": "b:1-1-1-0", "path": [1, 1, 0], "norm": "예산의 항목간 전용"}])}
    d = consistency.authority_diffs(regs, max_df=5)
    assert len(d) == 1 and {d[0]["a_body"], d[0]["b_body"]} == {"대표이사", "이사회"}   # 호는 항의 머리 문장에서 주체를 이어받는다


def test_authority_diffs_same_body_not_flagged():
    regs = {"a": _reg("a", "A규정", "제1조", "예산의 전용", "대표이사의 승인을 받아 예산을 전용한다."), "b": _reg("b", "B규정", "제2조", "예산의 전용", "예산의 전용은 대표이사가 승인한다.")}
    assert consistency.authority_diffs(regs, max_df=5) == []


def test_structure_findings_dup_label_and_self_cite():
    regs = {"a": {"reg_id": "a", "name": "A지침", "articles": [
        {"id": "a:12", "label": "제12조", "title": "벌점", "norm": "", "items": [], "deleted": False},
        {"id": "a:12#2", "label": "제12조", "title": "그 밖의 사항", "norm": "", "items": [], "deleted": False}]}}
    rels = [{"kind": "인용", "src_article": "a:12", "target": "a:12", "target_type": "article", "evidence": "제12조", "context": "제12조의 규정에 의하여 표시한다"},
            {"kind": "인용", "src_article": "a:12", "target": "a:12", "target_type": "article", "evidence": "제12조 2항", "context": "제12조 2항에 따른 위원회"}]
    f = consistency.structure_findings(regs, rels)
    assert [x["rule"] for x in f] == ["DUPNUM-01", "SELFCITE-01"]
