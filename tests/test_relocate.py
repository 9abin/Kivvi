"""끊긴 인용의 대상 재탐색 (regcheck.relocate)."""
from regcheck.relocate import citing_text, moved_from, relocate


def art(aid, label, title, text, notes=()):
    return {"id": aid, "label": label, "title": title, "text": text, "norm": text, "amend_notes": list(notes), "deleted": False,
            "items": [{"id": f"{aid}-0-0-0", "norm": text, "path": [0, 0, 0]}]}


REG = {"reg_id": "9", "name": "급여규정", "addenda": [], "articles": [
    art("9:21", "제21조", "자녀수당", "제21조(자녀수당) 자녀수당을 준다. [종전 제21조는 제22조로 이동<2025.03.28.>"),
    art("9:22", "제22조", "성과급", "제22조(성과급) 성과급을 준다. [종전 제21조에서 이동, 종전 제22조는 제23조로 이동 <2025.03.28.>"),
    art("9:23", "제23조", "기타", "제23조(기타) 그 밖의 수당. [종전 제22조에서 이동]")]}


def test_moved_from_reads_both_forms_and_not_the_reverse():
    assert [a["label"] for a in moved_from(REG, 21)] == ["제22조"]     # 「제21조는 …로 이동」이 붙은 제21조 자신은 아님
    assert [a["label"] for a in moved_from(REG, 22)] == ["제23조"]


def test_relocate_uses_document_marker_first_and_masks_citation():
    src = {"reg_id": "1", "name": "성과급지급지침", "addenda": [], "articles": [art("1:1", "제1조", "목적", "급여규정 제21조에 따른 성과급 지급")]}
    regs = {"9": REG, "1": src}
    edge = {"src": "1:1-0-0-0", "src_reg": "1", "raw": "제21조", "context": "", "target_reg": "9", "target_ref": {"jo": 21}}
    assert "[인용]" in citing_text(regs, edge) and "제21조" not in citing_text(regs, edge)
    res = relocate(edge, regs, index=None, embed=None, rerank=lambda q, ts: [0.1] * len(ts), scope="target")
    assert res["candidates"][0]["label"] == "제22조" and res["candidates"][0]["tier"] == "문서 명시" and res["found"]


def test_low_scores_mean_not_found():
    src = {"reg_id": "1", "name": "갑", "addenda": [], "articles": [art("1:1", "제1조", "목적", "을규정 제5조에 따른 무엇")]}
    tgt = {"reg_id": "2", "name": "을규정", "addenda": [], "articles": [art("2:1", "제1조", "목적", "을규정의 목적")]}
    edge = {"src": "1:1-0-0-0", "src_reg": "1", "raw": "제5조", "context": "", "target_reg": "2", "target_ref": {"jo": 5}}
    res = relocate(edge, {"1": src, "2": tgt}, index=None, embed=None, rerank=lambda q, ts: [0.05] * len(ts), scope="target")
    assert not res["found"]                                             # 근거 소멸 가능성으로 알림


def test_attach_relocate_tiers():
    from regcheck.findings import attach_relocate
    from regcheck.review import key_baseline
    base = {"reg_id": "1", "unit": "본문", "article": "제1조", "para": 0, "type": "인용_대상불일치", "method": "semantic", "note": ""}
    same = {**base, "quote": "제4조", "suggest": "제5조", "priority": "중간"}
    moved = {**base, "quote": "제21조", "suggest": "제1조", "priority": "중간"}
    gone = {**base, "quote": "제9조", "suggest": "", "priority": "중간"}
    cand = lambda label, tier="모델 추정": {"reg_name": "급여규정", "label": label, "title": "", "score": 0.9, "tier": tier}  # noqa: E731
    reloc = {key_baseline(same): {"kind": "인용한 그 조가 1위", "found": True, "target": "급여규정", "candidates": [cand("제4조")]},
             key_baseline(moved): {"kind": "문서 명시", "found": True, "target": "급여규정", "candidates": [cand("제22조", "문서 명시")]},
             key_baseline(gone): {"kind": "못 찾음", "found": False, "target": "급여규정", "candidates": []}}
    a, b, c = attach_relocate([same, moved, gone], reloc)
    assert a["priority"] == "참고"                                   # 지금 인용한 조가 가장 잘 맞음 → 인용이 맞을 가능성
    assert b["suggest"] == "제22조"                                  # 원문 이동 표시가 모델 제안(제1조)을 덮어씀
    assert "근거 소멸 가능성" in c["note"] and c["priority"] == "중간"
