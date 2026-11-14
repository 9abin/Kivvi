"""연결 표 주제 층(linked_layer): 같은 개념 · 같은 속성, 개념 위계, 결정자 필터, 바뀐 항 초점."""

import json

from regcheck.linked_layer import LinkLayer, item_texts

META = {"meta": {"concepts": {"휴직": None, "육아휴직": "휴직", "인병휴직": "휴직"}}}
LINKS = [
    {"item": "R:1-1-0-0", "article": "R:1", "concept": "육아휴직", "rel": "근거", "attrs": ["보수"], "decided_by": "사용자"},
    {"item": "R:1-2-0-0", "article": "R:1", "concept": "육아휴직", "rel": "근거", "attrs": ["기간"], "decided_by": "초안"},
    {"item": "R:2-1-0-0", "article": "R:2", "concept": "육아휴직", "rel": "근거", "attrs": ["보수", "절차"], "decided_by": "초안"},
    {"item": "R:3-1-0-0", "article": "R:3", "concept": "인병휴직", "rel": "근거", "attrs": ["보수"], "decided_by": "사용자"},
    {"item": "R:4-1-0-0", "article": "R:4", "concept": "휴직", "rel": "근거", "attrs": ["보수"], "decided_by": "자동·확정"},
    {"item": "R:5-1-0-0", "article": "R:5", "concept": "육아휴직", "rel": "언급", "attrs": ["보수"], "decided_by": "초안"},
    {"item": "R:6-1-0-0", "article": "R:6", "concept": "육아휴직", "rel": "근거", "attrs": ["기간"], "decided_by": "초안"},
]


def _write(tmp_path):
    p = tmp_path / "links.jsonl"
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [META, *LINKS]), encoding="utf-8")
    return p


def test_same_concept_same_attr_only(tmp_path):
    out = LinkLayer(_write(tmp_path)).expand("R:1", "")
    assert out == {"R:2": ["HYUJIK_ATTR"], "R:6": ["HYUJIK_ATTR"]}   # 보수와 기간을 각각 같은 개념에서. 언급(R:5) · 다른 개념(R:3 · R:4)은 제외


def test_hierarchy_adds_parent_and_child_concepts_as_up_path(tmp_path):
    out = LinkLayer(_write(tmp_path), hierarchy=True).expand("R:1", "")
    assert out["R:4"] == ["HYUJIK_UP"]            # 상위 개념(휴직)의 같은 속성
    assert "R:3" not in out                        # 형제 개념(인병휴직)은 위계로 이어지지 않는다
    assert out["R:2"] == ["HYUJIK_ATTR"]


def test_trusted_only_drops_draft_links(tmp_path):
    layer = LinkLayer(_write(tmp_path), hierarchy=True, trusted_only=True)
    assert layer.expand("R:1", "") == {"R:4": ["HYUJIK_UP"]}   # R:1의 보수(사용자)는 남고 기간(초안)과 초안 대상은 빠진다


def test_focus_uses_the_changed_item_when_text_is_given(tmp_path):
    txt = {"R:1-1-0-0": "휴직 기간 중 보수는 무급으로 한다", "R:1-2-0-0": "휴직기간은 3년 이내로 한다"}
    layer = LinkLayer(_write(tmp_path), item_text=txt)
    assert set(layer.expand("R:1", '제1조제2항 중 "3년 이내"를 "2년 이내"로 한다')) == {"R:6"}   # 기간 항만
    assert set(layer.expand("R:1", '제1조 중 "무급"을 "유급"으로 한다')) == {"R:2"}              # 보수 항만
    assert set(layer.expand("R:1", "")) == {"R:2", "R:6"}                                          # 짚은 항이 없으면 조 전체


def test_uncovered_article_and_kind_path(tmp_path):
    layer = LinkLayer(_write(tmp_path), same_concept_any_attr=True)
    assert layer.expand("R:99", "") == {} and not layer.covers("R:99") and layer.covers("R:1")
    assert layer.expand("R:2", "")["R:6"] == ["HYUJIK_KIND"]   # 같은 개념 다른 속성 (기간)


def test_item_texts_builds_ids_from_parsed_items():
    regs = {"R": {"articles": [{"id": "R:1", "items": [{"path": [1, 0, 0], "norm": "가"}, {"path": [1, 2, 0], "norm": "나"}]}]}}
    assert item_texts(regs) == {"R:1-1-0-0": "가", "R:1-1-2-0": "나"}
