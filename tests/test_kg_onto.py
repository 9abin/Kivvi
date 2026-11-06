"""온톨로지 보강(kg_onto): 약칭 · 법령 이름 정규화 · 법령 조 인용 · 상하위 정의."""

from regcheck.kg import KG, ONTOLOGY
from regcheck.kg_onto import LawRegistry, alias_forms, extract_aliases, find_law_article, find_law_node, law_article_cites, sl

LAWS = ["개인정보 보호법", "공직자의 이해충돌 방지법", "공직자의 이해충돌 방지법 시행령", "지방공기업법"]


def test_registry_merges_spacing_and_prefers_db_title():
    r = LawRegistry.build(LAWS, ["개인정보보호법", "신규법"])
    assert r.canon("개인정보보호법") == "개인정보 보호법"
    assert r.canon("「개인정보 보호법」") == "개인정보 보호법"
    assert r.canon("신규법") == "신규법"            # DB에 없으면 먼저 본 표기
    assert r.canon("없는법") is None
    assert r.decree_of("공직자의 이해충돌 방지법") == "공직자의 이해충돌 방지법 시행령"
    assert r.decree_of("개인정보 보호법") is None


def test_alias_needs_known_entity_and_drops_wrong_forms():
    ents = [("LAW:민원 처리에 관한 법률", "Law", "민원 처리에 관한 법률"), ("BODY:고객헌장심의위원회", "Body", "고객헌장심의위원회"),
            ("TERM:1:금품등", "Term", "금품등"), ("TERM:1:임직원", "Term", "임직원")]
    units = [("R1", "R1:1", "「민원 처리에 관한 법률」(이하 \"민원처리법\"이라 한다)에 따라"),
             ("R1", "R1:2", "고객헌장심의위원회(이하 \"위원회\"라 한다)를 둔다"),
             ("R1", "R1:3", "한국벤처투자(이하 \"회사\"라 한다)는"),                        # 개체가 아님
             ("R1", "R1:4", "금품등(이하 \"수수 금지 금품등\"이라 한다)"),                    # 약칭이 이름을 품음
             ("R1", "R1:5", "행동강령 책임관을 지정할 임직원(이하 \"행동강령책임관\"이라 한다)")]  # 용어와 겹치지 않음
    got = {a["short"]: a for a in extract_aliases(units, ents)}
    assert set(got) == {"민원처리법", "위원회"}
    assert got["민원처리법"]["node"] == "LAW:민원 처리에 관한 법률" and not got["민원처리법"]["generic"]
    assert got["위원회"]["generic"]                  # 이름의 일부만 떼어 쓴 말은 검색 동의어로 쓰지 않는다


def test_law_article_cites_resolves_explicit_alias_and_relative():
    reg = LawRegistry.build(LAWS)
    ents = [("LAW:공직자의 이해충돌 방지법", "Law", "공직자의 이해충돌 방지법")]
    units = [("R1", "R1:1", "이 지침은 「공직자의 이해충돌 방지법」(이하 \"법\"이라 한다)에 따른다."),
             ("R1", "R1:2", "임직원은 법 제4조제1항에 따라 신고하고 영 제24조에 따른다."),
             ("R1", "R1:3", "「지방공기업법」 제49조 및 같은 법 제76조에 따른다."),
             ("R1", "R1:4", "「이사회규정」 제3조에 따른다."),                     # 법령이 아님
             ("R2", "R2:1", "법 제9조에 따른다.")]                                  # 다른 규정은 「법」을 정의하지 않았다
    al = extract_aliases(units, ents)
    got = {(c["unit"], c["law"], c["article"]) for c in law_article_cites(units, reg, al)}
    assert ("R1:2", "공직자의 이해충돌 방지법", "제4조") in got
    assert ("R1:2", "공직자의 이해충돌 방지법 시행령", "제24조") in got              # 영 -> 시행령
    assert ("R1:3", "지방공기업법", "제49조") in got and ("R1:3", "지방공기업법", "제76조") in got   # 같은 법 -> 바로 앞 법령
    assert not [g for g in got if g[0] in ("R1:4", "R2:1")]


def test_find_law_node_and_article():
    nodes = {"LAW:개인정보 보호법": {"type": "Law", "name": "개인정보 보호법"}, "LAWART:개인정보 보호법:제29조": {"type": "LawArticle", "label": "제29조"}}
    assert find_law_node(nodes, "개인정보보호법") == "LAW:개인정보 보호법"
    assert find_law_node(nodes, "없는법") is None
    assert find_law_article(nodes, "LAW:개인정보 보호법", "제29조제1항을 다음과 같이 한다") == "LAWART:개인정보 보호법:제29조"
    assert find_law_article(nodes, "LAW:개인정보 보호법", "제30조를 고친다") is None


def test_alias_forms_skip_generic_aliases():
    nodes = {"LAW:L": {"type": "Law", "name": "민원 처리에 관한 법률"}, "A1": {"type": "Alias", "short": "민원처리법", "for": "LAW:L", "generic": False},
             "A2": {"type": "Alias", "short": "법", "for": "LAW:L", "generic": True}}
    kg = KG(nodes, [])
    assert kg.alias_forms("민원처리법") == {"민원 처리에 관한 법률"}
    assert kg.alias_forms("민원 처리에 관한 법률") == {"민원처리법"}
    assert kg.alias_forms("법") == set()
    assert alias_forms(nodes, kg.alias_index, "없는말") == set()


def test_unit_superclass_and_relations_are_consistent():
    sup = ONTOLOGY["superclasses"]
    assert set(sup.values()) <= set(ONTOLOGY["abstract_classes"])
    assert all(c in ONTOLOGY["classes"] for c in sup)
    unit = set(sup)                                    # Article · Supplement · Annex · Form
    assert unit == {"Article", "Supplement", "Annex", "Form"}
    for name, rel in ONTOLOGY["relations"].items():
        for end in (rel["from"], rel["to"]):
            parts = set(end.split("|"))
            assert parts <= set(ONTOLOGY["classes"]), (name, parts)
    # 새 관계는 정의에 있고 출처 표시가 있다
    new = {r for rs in ONTOLOGY["origin"].values() for r in rs}
    assert {"CITES_LAW_ART", "ARTICLE_OF", "ALIAS_OF"} <= set(ONTOLOGY["relations"]) <= new
    assert ONTOLOGY["regulation_hierarchy"]["order"] == ["법령", "정관", "규정", "지침"]
    assert sl("「개인정보 보호법」") == "개인정보보호법"
