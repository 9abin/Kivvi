"""법령명 일괄 정비 (src/regcheck/lawname_fix.py): 점검 결과 → 이름 표 → 규정별 개정문."""
from regcheck.lawname_fix import mapping, plan


def reg():
    it = {"id": "1:3-1-0-0", "path": [1, 0, 0], "text": "「소프트웨어산업 진흥법」 제13조에 따른다.", "norm": "「소프트웨어산업 진흥법」 제13조에 따른다."}
    a = {"id": "1:3", "label": "제3조", "title": "인증", "text": "제3조(인증) ① " + it["text"], "norm": "제3조(인증) ① " + it["norm"],
         "items": [it], "deleted": False, "chapter": ""}
    return {"1": {"reg_id": "1", "name": "가지침", "articles": [a], "addenda": [], "appendices": []}}


def test_mapping_merges_places_and_skips_empty():
    fs = [{"type": "법령명_변경", "quote": "소프트웨어산업 진흥법", "suggest": "소프트웨어 진흥법", "reg_id": "1", "reg_name": "가지침", "article": "제3조"},
          {"type": "법령명_변경", "quote": "소프트웨어산업 진흥법", "suggest": "소프트웨어 진흥법", "reg_id": "2", "reg_name": "나지침", "article": "제5조"},
          {"type": "법령_개정확인", "quote": "제9조", "suggest": "", "reg_id": "1"},
          {"type": "법령명_오기", "quote": "가법", "suggest": "", "reg_id": "1"}]
    rows = mapping(fs)
    assert rows == [{"old": "소프트웨어산업 진흥법", "new": "소프트웨어 진흥법", "kind": "옛 이름", "where": ["가지침 제3조", "나지침 제5조"]}]


def test_plan_writes_amendment_per_regulation():
    p = plan(reg(), [{"old": "소프트웨어산업 진흥법", "new": "소프트웨어 진흥법", "kind": "옛 이름", "where": []}])
    assert [pk["amendment"] for pk in p["packages"]] == ['제3조제1항 중 "소프트웨어산업 진흥법"을 "소프트웨어 진흥법"으로 한다.']
    assert p["packages"][0]["status"] == "ok" and p["not_found"] == []
