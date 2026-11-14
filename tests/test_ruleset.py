from regcheck.change_check import basis_of, check
from regcheck.ruleset import infer, load_rules

from test_consistency import art, reg

APPEAL = "결과에 이의가 있으면 통보일로부터 {n}일 이내에 이의를 신청할 수 있다."


def _rules(*ids):
    return load_rules(only=set(ids))


def _rel(src, tgt, ev="제1조"):
    return {"src": f"{src}-1-0-0", "src_article": src, "src_kind": "본문", "target": tgt, "target_type": "article", "kind": "인용",
            "evidence": ev, "context": f"{src}가 {tgt}를 인용"}


def test_deadline_outlier_edge_points_to_majority_article():
    regs = {str(i): reg(str(i), f"규정{i}", [art(f"{i}:1", "제1조", APPEAL.format(n=7))]) for i in range(4)}
    regs["9"] = reg("9", "다른규정", [art("9:1", "제1조", APPEAL.format(n=15))])
    edges = infer(regs, [], _rules("DL-01"))
    assert edges and all(e["src_article"] == "9:1" and e["rule_id"] == "DL-01" and e["verification"] == "의심" and e["confidence"] == "후보" for e in edges)
    assert {e["target"] for e in edges} <= {f"{i}:1" for i in range(4)} and len(edges) <= 3
    assert not any(e["directed"] for e in edges)


def test_chain_edge_skips_direct_and_is_directed():
    regs = {k: reg(k, f"규정{k}", [art(f"{k}:1", "제1조", "본문")]) for k in "abc"}
    rels = [_rel("a:1", "b:1"), _rel("b:1", "c:1"), _rel("a:1", "d:1")]
    edges = infer(regs, rels, _rules("CHAIN-01"))
    assert [(e["src_article"], e["target"], e["via"]) for e in edges] == [("a:1", "c:1", "b:1")] and edges[0]["directed"]
    assert infer(regs, rels + [_rel("a:1", "c:1")], _rules("CHAIN-01")) == []   # 이미 직접 인용하면 잇지 않음


def test_change_check_shows_other_side_of_inferred_edge():
    regs = {"1": reg("1", "A규정", [art("1:1", "제1조", "기한은 7일이다.")]), "2": reg("2", "B규정", [art("2:1", "제1조", "기한은 15일이다.")])}
    edge = {"id": "DL-01-0000", "src": "2:1-1-0-0", "src_article": "2:1", "target": "1:1", "target_type": "article", "kind": "기한 불일치",
            "evidence": "15일", "context": "기한은 15일이다.", "target_context": "기한은 7일이다.", "note": "기한이 다름", "rule_id": "DL-01",
            "priority": "중간", "directed": False}
    cs = {"reg_id": "1", "changes": [{"origin": "1:1", "old_label": "제1조", "new_label": "제1조", "ops": [{"op": "replace"}]}],
          "label_map": {"1:1": "제1조"}, "amendment": "", "fixed": []}
    base = check(cs, regs, [])
    out = check(cs, regs, [], inferred=[edge])
    cards = [x for x in out if str(x.get("path", "")).startswith("규칙 추론")]
    assert not [x for x in base if str(x.get("path", "")).startswith("규칙 추론")]
    assert len(cards) == 1 and cards[0]["reg_id"] == "2" and cards[0]["priority"] == "중간" and basis_of(cards[0]["path"]) == "규칙 추론"
    edge["directed"] = True    # 단방향 엣지는 도착 쪽(1:1)이 바뀔 때만 출발 쪽을 보임 → 이 경우 출발이 2:1, 도착이 1:1이라 그대로 보임
    assert len([x for x in check(cs, regs, [], inferred=[edge]) if str(x.get("path", "")).startswith("규칙 추론")]) == 1
    cs2 = {**cs, "reg_id": "2", "changes": [{"origin": "2:1", "old_label": "제1조", "new_label": "제1조", "ops": [{"op": "replace"}]}], "label_map": {"2:1": "제1조"}}
    assert not [x for x in check(cs2, regs, [], inferred=[edge]) if str(x.get("path", "")).startswith("규칙 추론")]   # 출발 쪽이 바뀌면 단방향은 안 보임
