from regcheck.history import merge, stale_citations


def art(aid, label, title, marks=(), notes=(), text=""):
    return {"id": aid, "label": label, "title": title, "text": text, "amend_notes": list(notes),
            "items": [{"id": f"{aid}-0-0-0", "path": [0, 0, 0], "norm": "본문", "amend_marks": list(marks)}]}


def reg(rid, name, arts, history=()):
    return {"reg_id": rid, "name": name, "articles": arts, "addenda": [], "history": list(history)}


def rel(src, tgt, ev="제21조"):
    return {"src": f"{src}-0-0-0", "src_article": src, "src_kind": "본문", "target": tgt, "target_type": "article",
            "check_status": "ok", "evidence": ev, "context": "…"}


def regs():
    return {
        # 인용 대상: 제21조에 2025.03.28 다른 조문이 들어옴 (본조신설)
        "1": reg("1", "급여규정", [art("1:21", "제21조", "자녀수당", notes=["[본조신설 2025.03.28.]"]),
                                    art("1:22", "제22조", "성과급", marks=["<개정 2024.01.01.>"])], ["제정 2010. 01. 01."]),
        # 인용 쪽: 2023.09.20 이후 그대로
        "2": reg("2", "성과급지급지침", [art("2:1", "제1조", "목적", marks=["<개정 2023.09.20.>"])], ["제정 2015. 01. 01."]),
        # 대상 규정 전부개정
        "3": reg("3", "감사규정", [art("3:4", "제4조", "감사의 범위")], ["제정 2010. 01. 01.", "전부개정  2025. 11. 28."]),
        "4": reg("4", "감사지침", [art("4:6", "제6조", "감사 종류")], ["제정 2012. 02. 23."]),
        # 같은 개편 묶음: 인용 쪽 2021.03.18, 대상 전부개정 2021.03.25
        "5": reg("5", "직제규정", [art("5:7", "제7조", "주요조직")], ["제정 2000. 01. 01.", "전부개정 2021. 03. 25."]),
        "6": reg("6", "직제지침", [art("6:8", "제8조", "하부조직", marks=["<개정 2021.03.18.>"])], ["제정 2005. 01. 01."]),
    }


def test_strong_signals_renumbered_article_and_full_revision():
    out = {f["reg_id"]: f for f in stale_citations(regs(), [rel("2:1", "1:21"), rel("4:6", "3:4", "제4조")])}
    assert out["2"]["priority"] == "높음" and "2025.03.28" in out["2"]["note"] and "자녀수당" in out["2"]["note"]
    assert out["4"]["priority"] == "높음" and "전부개정 2025.11.28" in out["4"]["note"]


def test_same_batch_and_content_amend_are_weak():
    out = {f["reg_id"]: f for f in stale_citations(regs(), [rel("6:8", "5:7", "제7조"), rel("2:1", "1:22", "제22조")])}
    assert out["6"]["signal"] == "batch" and out["6"]["priority"] == "낮음"
    assert out["2"]["signal"] == "amend"


def test_merge_attaches_to_existing_and_drops_weak_standalone():
    hist = stale_citations(regs(), [rel("2:1", "1:21"), rel("6:8", "5:7", "제7조")])
    existing = [{"reg_id": "2", "unit": "본문", "article": "제1조", "para": 0, "quote": "제21조", "note": "의미 검사", "priority": "낮음"}]
    found, stats = merge(existing, hist)
    assert len(found) == 1 and found[0]["priority"] == "높음" and "개정 이력" in found[0]["note"]
    assert stats == {"덧붙임": 1, "단독": 0, "버림(약한 신호)": 1}
