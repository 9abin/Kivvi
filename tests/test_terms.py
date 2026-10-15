"""정의 용어 영향 (regcheck.terms): 정의를 바꾸면 인용이 없어도 그 용어를 쓰는 곳을 후보로 낸다."""
from regcheck.amend import apply, label_map, parse_amendment
from regcheck.build import to_records
from regcheck.change_check import check
from regcheck.parse import parse
from regcheck.terms import definitions, uses

A = """갑규정
제1조(목적) 이 규정은 카드 관리를 정한다.
제2조(정의) 이 규정에서 사용하는 용어의 뜻은 다음과 같다.
1. "관리자"란 카드를 관리하는 직원을 말한다.
2. 사용부서라 함은 카드를 쓰는 부서를 말한다.
제3조(점검) 관리자는 매월 카드를 점검한다.
제4조(교육) 임직원은 교육을 받는다."""
B = """을지침
제1조(목적) 이 지침은 을을 정한다.
제2조(정의) "관리자"란 시설을 관리하는 사람을 말한다.
제3조(시설) 관리자는 시설을 점검한다."""
C = """병지침
제1조(목적) 이 지침은 병을 정한다.
제2조(보고) 관리자는 분기마다 보고한다."""
D = """정지침
제1조(목적) 이 지침은 정을 정한다.
제2조(위임) 카드에 관하여는 「갑규정」에 따른다. 관리자가 확인한다."""


def regs():
    return {rid: {"reg_id": rid, "name": txt.splitlines()[0], **to_records(rid, parse(txt.splitlines()))}
            for rid, txt in (("1", A), ("2", B), ("3", C), ("4", D))}


RELS = [{"src": "4:2-0-0-0", "src_article": "4:2", "src_kind": "본문", "target": "1", "target_type": "regulation",
         "evidence": "「갑규정」", "context": "카드에 관하여는 「갑규정」에 따른다"}]


def run(rs, text):
    new, ch = apply(rs["1"], parse_amendment(text))
    cs = {"reg_id": "1", "reg_name": "갑규정", "amendment": text, "changes": ch, "label_map": label_map(ch)}
    return [f for f in check(cs, rs, RELS, new_reg=new) if f["path"].startswith("정의 용어")]


def test_definitions_quoted_and_bare():
    terms = {d["term"] for d in definitions(regs()["1"])}
    assert terms == {"관리자", "사용부서"}


def test_uses_respects_word_boundary():
    rs = regs()
    assert [a["label"] for a, _, _ in uses("직원", rs["1"])] == []          # 「임직원」 안의 「직원」은 아님 (정의 조는 뺌)
    assert [a["label"] for a, _, _ in uses("관리자", rs["1"])] == ["제3조"]


def test_term_followed_by_another_noun_is_a_different_word():
    from regcheck.terms import _pattern
    hit = lambda t, s: bool(_pattern(t).search(s))  # noqa: E731
    assert hit("기록물", "기록물의 보존") and not hit("기록물", "기록물등록대장에 등록")
    assert hit("직원", "직원들에게 알린다") and not hit("직원", "임직원은")
    assert hit("처리", "처리하는 경우") and not hit("인사", "인사위원회")
    assert not hit("준법감시", "준법감시인이 지침을") and hit("준법감시", "준법감시 업무")   # 「~인」 명사


def test_definition_change_flags_same_reg_and_other_regs():
    found = run(regs(), '제2조제1호 중 "직원"을 "직원 또는 위탁업체 직원"으로 한다.')
    by = {(f["reg_id"], f["path"]): f for f in found}
    assert by[("1", "정의 용어")]["article"] == "제3조" and by[("1", "정의 용어")]["priority"] == "중간"
    assert by[("2", "정의 용어(다른 규정 정의)")]["priority"] == "낮음"          # 을지침은 스스로 정의 → 정의 비교
    assert by[("3", "정의 용어(다른 규정 사용)")]["priority"] == "낮음"          # 인용 없음
    assert by[("4", "정의 용어(다른 규정 사용)")]["priority"] == "중간"          # 갑규정을 인용함
    assert all(f["definition"]["new"].find("위탁업체") >= 0 for f in found)


def test_coverage_separates_off_paths_from_zero_found():
    from regcheck.change_check import coverage
    cov = coverage([{"category": "review", "path": "정의 용어"}], vector_on=False, spell_on=False)
    by = {p["name"]: p for p in cov["paths"]}
    assert by["정의 용어"] == {"name": "정의 용어", "on": True, "found": 1}
    assert by["인용"]["on"] and by["인용"]["found"] == 0                   # 확인했으나 없음
    assert not by["의미 유사"]["on"] and not by["오타"]["on"]                # 꺼짐
    assert any("의미 유사" in n for n in cov["not_covered"])


def test_neighbor_card_is_low_priority_review():
    rs = regs()
    rs["3"]["articles"][1]["items"][0]["norm"] = "관리자는 매월 카드 사용 내역을 보고한다."    # 병지침 제2조: 같은 주제, 인용·제목·용어 경로 밖
    new, ch = apply(rs["1"], parse_amendment('제3조 중 "매월"을 "분기마다"로 한다.'))
    cs = {"reg_id": "1", "reg_name": "갑규정", "amendment": "", "changes": ch, "label_map": label_map(ch)}
    nb = {"1:3": [{"article_id": "3:2", "score": 0.83}, {"article_id": "1:4", "score": 0.9}]}   # 같은 규정 이웃은 무시
    found = [f for f in check(cs, rs, RELS, new_reg=new, neighbors=nb) if f["path"].startswith("의미 이웃")]
    assert [(f["reg_id"], f["article"], f["priority"]) for f in found] == [("3", "제2조", "낮음")]
    assert found[0]["category"] == "review"                                           # 새 충돌로 판정하지 않음


def test_no_definition_change_no_term_cards():
    assert run(regs(), '제3조 중 "매월"을 "분기마다"로 한다.') == []


def test_basis_of_paths():
    from regcheck.change_check import basis_of
    assert basis_of("인용받음") == basis_of("재검사(법령명)") == basis_of("나가는 인용") == "문서 명시"
    assert basis_of("같은 제목") == basis_of("정의 용어(다른 규정 사용)") == "낱말 일치"
    assert basis_of("의미 이웃 (0.83)") == basis_of("오타 의심") == "모델 추정"


def test_same_regulation_duplicate_on_new_article():
    """신설 조문이 같은 내규의 기존 조문과 비슷하면 확인 필요 카드 (임베딩은 시험용 가짜)."""
    import numpy as np
    rs = regs()
    new, ch = apply(rs["1"], parse_amendment("제3조의 다음에 제3조의2를 다음과 같이 신설한다.\n제3조의2(점검 결과 공개) 관리자는 점검 결과를 공개한다."))
    cs = {"reg_id": "1", "reg_name": "갑규정", "amendment": "", "changes": ch, "label_map": label_map(ch)}

    class Idx:
        def search(self, q, reg_id=None, k=5):
            assert reg_id == "1"                                         # 같은 내규 안에서만
            return [(0.84, {"article_id": "1:3"}), (0.60, {"article_id": "1:1"})]
    found = [f for f in check(cs, rs, RELS, new_reg=new, dup=(lambda t: [np.zeros(3)], Idx())) if f["path"] == "의미 이웃(같은 내규)"]
    assert [(f["article"], f["priority"]) for f in found] == [("제3조", "중간")]          # 0.84는 중간, 0.60은 기준 아래
    assert "신설하는 「제3조의2(점검 결과 공개)」" in found[0]["reason"]


def test_duplicate_priority_needs_floor_and_gap():
    """바닥(0.82 이상) AND 간격(1위가 2위보다 0.04 이상): 비슷한 이웃이 여럿이면(연속 조문) 점수가 높아도 낮음."""
    import numpy as np
    rs = regs()
    new, ch = apply(rs["1"], parse_amendment("제3조의 다음에 제3조의2를 다음과 같이 신설한다.\n제3조의2(점검 결과 공개) 관리자는 점검 결과를 공개한다."))
    cs = {"reg_id": "1", "reg_name": "갑규정", "amendment": "", "changes": ch, "label_map": label_map(ch)}

    def run_with(hits):
        class Idx:
            def search(self, q, reg_id=None, k=5):
                return hits
        return [f for f in check(cs, rs, RELS, new_reg=new, dup=(lambda t: [np.zeros(3)], Idx())) if f["path"] == "의미 이웃(같은 내규)"]
    clear = run_with([(0.86, {"article_id": "1:3"}), (0.78, {"article_id": "1:4"})])      # 간격 0.08
    close = run_with([(0.86, {"article_id": "1:3"}), (0.84, {"article_id": "1:4"})])      # 간격 0.02
    low = run_with([(0.80, {"article_id": "1:3"}), (0.60, {"article_id": "1:4"})])        # 간격은 크지만 바닥 아래
    assert sorted((f["article"], f["priority"]) for f in clear) == [("제3조", "중간"), ("제4조", "낮음")]    # 2위(0.78)는 바닥을 넘으므로 낮음으로 함께 나옴
    assert sorted((f["article"], f["priority"]) for f in close) == [("제3조", "낮음"), ("제4조", "낮음")]
    assert [(f["article"], f["priority"]) for f in low] == [("제3조", "낮음")]


def test_neighbor_small_gap_is_demoted_to_reference():
    """의미 이웃: 다음 이웃보다 0.03 이상 높으면 낮음, 비슷한 이웃이 여럿(간격 작음)이면 참고 (지우지 않음)."""
    rs = regs()
    rs["3"]["articles"][1]["items"][0]["norm"] = "관리자는 매월 카드 사용 내역을 보고한다."
    new, ch = apply(rs["1"], parse_amendment('제3조 중 "매월"을 "분기마다"로 한다.'))
    cs = {"reg_id": "1", "reg_name": "갑규정", "amendment": "", "changes": ch, "label_map": label_map(ch)}
    def prio(gap):
        nb = {"1:3": [{"article_id": "3:2", "score": 0.83, "gap": gap}]}
        return [f["priority"] for f in check(cs, rs, RELS, new_reg=new, neighbors=nb) if f["path"].startswith("의미 이웃")]
    assert prio(0.08) == ["낮음"] and prio(0.01) == ["참고"]


def test_hypothesis_cards_skip_covered_and_stay_low():
    """가설 카드: 모델이 낸 주제로 다른 내규 조문을 찾되, 이미 구조 경로로 낸 조문은 빼고 낮음·모델 추정으로 낸다."""
    import numpy as np
    from regcheck.hypothesis import hypotheses
    rs = regs()
    post = lambda m: '{"topic": "카드 점검", "why": "점검 주기가 바뀌면 점검 보고도 확인할 만함"}\nnot json'
    chunks = [{"article_id": "2:3"}, {"article_id": "3:2"}, {"article_id": "1:3"}]
    index = (np.eye(3), chunks)
    embed = lambda t: [np.array([1.0, 0.9, 0.0])]
    rerank = lambda q, ts: [0.9 if "관리자는 분기마다" in t else 0.5 for t in ts]
    art = rs["1"]["articles"][2]
    out = hypotheses(post, rs, rs["1"], art, None, "삭제되는 조문: 관리자는 매월 점검한다", index, embed, rerank, skip={("2", "제3조")})
    assert [(f["reg_id"], f["article"], f["priority"], f["path"]) for f in out] == [("3", "제2조", "낮음", "가설(모델 추정)")]
    assert "모델의 가설" in out[0]["reason"] and "근거가 아니라 추정" in out[0]["reason"]
    from regcheck.change_check import basis_of
    assert basis_of(out[0]["path"]) == "모델 추정"


def test_model_cards_are_capped_and_hypotheses_go_first():
    from regcheck.change_check import cap_model_cards
    prio = {"높음": 0, "중간": 1, "낮음": 2, "참고": 3}
    card = lambda path, p="낮음", n=0: {"category": "review", "path": path, "priority": p, "n": n}  # noqa: E731
    out = [card("인용받음", "높음")] + [card("의미 유사 (0.74)", n=i) for i in range(4)] + [card("가설(모델 추정)", n=i) for i in range(3)] + [card("의미 이웃 (0.80·간격 작음)", "참고")]
    kept = cap_model_cards(out, prio, limit=6)
    paths = [x["path"] for x in kept]
    assert len([p for p in paths if p != "인용받음"]) == 6 and "인용받음" in paths            # 문서 명시 카드는 건드리지 않음
    assert "의미 이웃 (0.80·간격 작음)" not in paths                                          # 참고부터 덜어 냄
    assert sum(p.startswith("가설") for p in paths) == 2 and sum(p.startswith("의미 유사") for p in paths) == 4   # 같은 순위에서는 가설부터


def test_spell_failure_does_not_break_the_check(monkeypatch):
    """오타 검사용 로컬 모델이 꺼져 있어도(예외) 나머지 점검 결과는 그대로 나온다."""
    import regcheck.spell as spell
    monkeypatch.setattr(spell, "check", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("ollama down")))
    rs = regs()
    new, ch = apply(rs["1"], parse_amendment('제3조 중 "매월"을 "분기마다"로 한다.'))
    cs = {"reg_id": "1", "reg_name": "갑규정", "amendment": "", "changes": ch, "label_map": label_map(ch)}
    found = check(cs, rs, RELS, new_reg=new, spell_model="없는모델")
    assert isinstance(found, list)
