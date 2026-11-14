"""규정 지식 그래프: 규정 원문과 인용 표(amend.edge)에서 규칙만으로 노드 · 선을 만들고, 수정 종류별 영향 경로를 따라 후보를 모은다.

  build(regs, edges)                      -> (nodes, edge_rows)
  KG(nodes, edge_rows).impact(start, kind, term_hint=None)   수정 종류에 맞는 선만 따라 영향 후보
  ONTOLOGY                                 클래스 · 관계 · 수정 종류별 경로 (docs 리포트 72와 같은 내용)

설계 원칙
  · LLM을 쓰지 않는다. 모든 선은 원문 규칙으로 만들고 어떤 규칙(method)에서 나왔는지 기록한다.
  · 개별 수정 시나리오를 가정해서 만들지 않는다. 「수정 종류 → 영향이 번지는 선」의 일반 틀만 정한다.
  · 결과는 확인 후보이고 충돌 판정이 아니다 (작업 규칙 §3).
"""

from __future__ import annotations

import re
from collections import defaultdict

from .impact import SKIP, SUSPECT, src_node, target_node

# ── 온톨로지 ────────────────────────────────────────────────────────────────────
ONTOLOGY = {
    "classes": {
        "Regulation": "규정 문서 1건 (정관 · 규정 · 지침 · 헌장)",
        "Article": "본문 조",
        "Supplement": "부칙의 조 (적용례 · 경과조치 포함)",
        "Annex": "별표",
        "Form": "별지 서식",
        "Term": "정의 조에서 정의된 용어 (규정별)",
        "Body": "위원회 이름",
        "Org": "조직 이름 (직제규정에 나오는 본부·실·팀·센터)",
        "Law": "인용된 법령 (법령 이름 단위, 띄어쓰기 · 약칭 표기 차이는 한 노드로 합친다)",
        "LawArticle": "본문이 조 번호까지 인용한 법령의 조 (「법령」 제N조, 약칭 제N조, 앞선 법령을 가리키는 「법」 · 「같은 법」 제N조)",
        "Alias": "규정이 「(이하 \"B\"라 한다)」로 정의한 약칭. 가리키는 개체(법령 · 규정 · 위원회 · 용어)가 알려진 것만 둔다",
    },
    # 상하위 관계: 조항 단위는 규정 안에서 같은 관계를 공유하는 네 종류의 묶음이고, 규정은 위계(정관 > 규정 > 지침)를 갖는다 (내규관리규정 제2 · 7조)
    "abstract_classes": {"Unit": "조항 단위 (본문 조 · 부칙의 조 · 별표 · 별지 서식)"},
    "superclasses": {"Article": "Unit", "Supplement": "Unit", "Annex": "Unit", "Form": "Unit"},
    "regulation_hierarchy": {"order": ["법령", "정관", "규정", "지침"], "property": "Regulation.tier", "source": "내규관리규정 제2조 · 제7조 (법령 · 정관 > 규정 > 지침, 같은 순위는 후법 우선)"},
    "relations": {
        "PART_OF": {"from": "Article|Supplement|Annex|Form", "to": "Regulation", "method": "구조", "meaning": "어느 규정에 속하는가"},
        "CITES": {"from": "Article|Supplement|Annex|Form", "to": "Article|Supplement|Annex|Form|Regulation", "method": "인용 표(amend.edge)",
                  "meaning": "조 번호 · 별표 · 서식으로 다른 단위를 가리킨다. sub = 인용 | 준용 | 예외. 부칙이 본칙 조를 번호로 인용한 경우 조 단위로 풀리지 않아 규정(Regulation)에 연결되는 것이 32개 있음", "inbound_means": "이 단위를 인용하는 곳"},
        "CITES_REG": {"from": "Article|Supplement|Annex|Form", "to": "Regulation", "method": "인용 표", "meaning": "규정 이름만으로 규정 전체를 가리킨다 (위임 · 준용 포함). 조 단위로 좁혀지지 않음"},
        "CITES_CHAPTER": {"from": "Article|Supplement|Annex|Form", "to": "Article", "method": "인용 표의 장 인용 + 조의 장 · 절(parse jang · jeol)",
                          "meaning": "「제3장제2절에 따라」 「복무규정 제4장이 정한 휴가」처럼 장 · 절 단위로 가리킨다. 그 장(절이 있으면 그 절)의 조마다 선을 둔다. sub = 인용 | 준용 (261111)"},
        "CITES_LAW": {"from": "Article|Supplement|Annex|Form", "to": "Law", "method": "인용 표", "meaning": "법령 또는 법령 조를 인용한다"},
        "CITES_LAW_ART": {"from": "Article|Supplement|Annex|Form", "to": "LawArticle", "method": "본문의 법령 조 인용(kg_onto.law_article_cites)", "meaning": "법령의 어느 조를 인용하는가. 조 번호가 없는 인용은 CITES_LAW(법령 단위)만 있다"},
        "ARTICLE_OF": {"from": "LawArticle", "to": "Law", "method": "구조", "meaning": "법령의 조가 어느 법령에 속하는가"},
        "ALIAS_OF": {"from": "Alias", "to": "Law|Regulation|Body|Term", "method": "「A(이하 \"B\"라 한다)」 정의 (kg_onto.extract_aliases)", "meaning": "약칭 B가 가리키는 개체 A. generic=true는 「법」 · 「규정」 같은 상대 지칭이라 검색에 쓰지 않는다"},
        "DEFINES": {"from": "Article", "to": "Term", "method": "정의 조의 「용어란 …」 패턴", "meaning": "이 조가 용어를 정의한다"},
        "USES_TERM": {"from": "Article|Supplement|Annex|Form", "to": "Term", "method": "본문 · 별표 · 서식 · 부칙에 용어 글자가 나옴 (3자 이상, 등장 단위 60개 이하)",
                      "meaning": "정의된 용어를 쓴다. scope = same_reg | cross_reg. 같은 글자가 다른 뜻으로 쓰였을 수 있음"},
        "MENTIONS_TITLE": {"from": "Article|Supplement|Annex|Form", "to": "Article", "method": "다른 단위 본문에 이 조의 제목이 글자 그대로 나옴 (제목 3자 이상, 일반 제목 제외, 나오는 곳 30개 이하)",
                           "meaning": "제목으로 불리는 제도를 언급한다 (별표 · 서식 · 부칙 본문 포함). 조 번호 인용이 없어도 이어진다"},
        "SAME_TITLE": {"from": "Article", "to": "Article", "method": "조 제목 일치 (다른 규정, 일반 제목 제외, 같은 제목 8곳 이하)",
                       "meaning": "같은 사항을 규정마다 따로 정했을 가능성 (값이 엇갈릴 수 있음)", "symmetric": True},
        "SUPPLEMENTS": {"from": "Regulation", "to": "Regulation", "method": "이름 줄기 일치 (직제규정 ↔ 직제지침 등)", "meaning": "규정과 그 시행 지침의 짝", "symmetric": True},
        "MENTIONS_BODY": {"from": "Article|Form", "to": "Body", "method": "「…위원회」 이름이 나옴 (2개 조 이상)", "meaning": "같은 위원회를 언급한다"},
        "MENTIONS_FORM": {"from": "Article|Supplement", "to": "Annex|Form", "method": "본문에 서식·별표의 이름이 그대로 나옴 (이름 4자 이상, 나오는 조 8개 이하)", "meaning": "서식·별표를 이름으로 가리킨다 (조 번호 인용이 없어도)"},
        "SHARED_ATTACH": {"from": "Article|Supplement", "to": "Article|Supplement", "method": "같은 서식·별표를 인용하거나 이름으로 언급하는 조끼리 (6개 이하)", "meaning": "같은 부속을 쓰는 절차 · 서식 흐름의 앞뒤 조", "symmetric": True},
        "MENTIONS_STEM": {"from": "Article|Supplement|Annex|Form", "to": "Article", "method": "조 제목에서 접두(당연·특별 등)·접미(사유·기준 등) 수식어를 뗀 줄기가 본문에 나옴 (줄기 2자 이상, 30개 이하)", "meaning": "제도 이름의 표기 변형(당연면직 ↔ 면직사유)으로 언급한다"},
        "SAME_VALUE": {"from": "Article", "to": "Article", "method": "같은 대상어 + 같은 값(「1인당 3만원」)이 든 조끼리 (6개 이하)", "meaning": "같은 기준값을 따로 정한 곳 (한쪽을 바꾸면 어긋날 수 있음)", "symmetric": True},
        "MENTIONS_ORG": {"from": "Article|Supplement|Annex|Form", "to": "Org", "method": "직제규정에 나오는 조직 이름이 본문에 나옴 (2~30개 단위)", "meaning": "같은 조직을 언급한다 (조직 개편 · 업무분장 영향)"},
        "VALUE_DIFF": {"from": "Article", "to": "Article", "method": "규칙 레지스트리 DL-01: 같은 종류의 청구 기한이 다수 규정과 다른 규정을 다수 쪽 조문과 잇는다", "meaning": "같은 사항의 기한이 엇갈린다 (의심 관계, 판정 아님)", "symmetric": True},
        "DEF_DIFF": {"from": "Article", "to": "Article", "method": "규칙 레지스트리 DEF-01: 같은 용어를 둘 이상의 규정이 정의하면서 문구 유사도가 낮다", "meaning": "같은 용어를 규정마다 다르게 정의한다 (의심 관계)", "symmetric": True},
        "STALE_CITE": {"from": "Article", "to": "Article", "method": "규칙 레지스트리 MOVE-01: 대상 조가 이동·삭제됐는데 인용 조가 그 뒤 개정되지 않음", "meaning": "옛 번호를 그대로 인용하고 있을 수 있다 (의심 관계)"},
    },
    # 규칙 레지스트리의 엣지 산출 규칙 중 그래프(위 relations)에 넣지 않은 것. 후보 설명 · 검수 시트에만 쓰고, 넣으려면 평가를 다시 잰다 (RULE_REL 참고)
    # change_paths 밖에서 후보 수집(collect)이 더하는 경로 (그래프 관계를 따라가지만 방향·가중치 표에 넣지 않은 것)
    "collect_extra_paths": {"삭제": "정의 조를 삭제하면 DEFINES → USES_TERM으로 그 조가 정의하던 용어를 쓰는 조(같은 규정 포함)를 확인 권장으로 낸다 (261106, 5차를 본 뒤의 사후 조정)"},
    "rules_outside_graph": {"COUNT-01": "횟수 한도 불일치(표본 1)", "AUTH-01": "승인 · 의결 주체 차이(표본 5)", "CHAIN-01": "인용 전이(영향 범위 표시, 충돌 판단 아님)", "QUORUM-01": "사용 안 함"},
    # 관계마다 어디서 왔는가 (그래프는 하나이고, 출처는 투명성과 기여 분석용 표시일 뿐이다)
    "origin": {"기존 규칙": ["PART_OF", "CITES", "CITES_REG", "CITES_LAW", "SAME_TITLE", "VALUE_DIFF", "DEF_DIFF", "STALE_CITE"],
             "신규": ["CITES_CHAPTER", "CITES_LAW_ART", "ARTICLE_OF", "ALIAS_OF", "DEFINES", "USES_TERM", "MENTIONS_TITLE", "SUPPLEMENTS", "MENTIONS_BODY", "MENTIONS_FORM", "SHARED_ATTACH", "MENTIONS_STEM", "SAME_VALUE", "MENTIONS_ORG"]},
    # 수정 종류 → 영향이 번지는 경로. (관계, 방향, 가중치) — in: 이 단위를 가리키는 쪽, out: 이 단위가 가리키는 쪽
    "change_paths": {
        "값 변경": [("SAME_TITLE", "both", 0.9), ("CITES", "in", 1.0), ("CITES_CHAPTER", "in", 0.8), ("VALUE_DIFF", "both", 0.8), ("MENTIONS_TITLE", "in", 0.6), ("SAME_VALUE", "both", 0.7), ("MENTIONS_STEM", "in", 0.45), ("MENTIONS_FORM", "out", 0.7), ("SHARED_ATTACH", "both", 0.6), ("SUPPLEMENTS", "reg", 0.4), ("CITES_REG", "reg_in", 0.3)],
        "삭제": [("CITES", "in", 1.0), ("CITES_CHAPTER", "in", 0.8), ("STALE_CITE", "in", 1.0), ("MENTIONS_TITLE", "in", 0.6), ("MENTIONS_STEM", "in", 0.45), ("MENTIONS_FORM", "out", 0.7), ("SHARED_ATTACH", "both", 0.6), ("CITES_REG", "reg_in", 0.3)],
        "번호 이동": [("CITES", "in", 1.0), ("CITES_CHAPTER", "in", 0.8), ("STALE_CITE", "in", 1.0), ("MENTIONS_ORG", "share", 0.5)],
        "정의 변경": [("USES_TERM", "term_in", 0.8), ("CITES", "in", 1.0), ("CITES_CHAPTER", "in", 0.8), ("DEF_DIFF", "both", 0.8), ("MENTIONS_TITLE", "in", 0.6), ("MENTIONS_STEM", "in", 0.45)],
        "위임·법령 변경": [("CITES", "in", 1.0), ("CITES_CHAPTER", "in", 0.8), ("CITES", "out", 0.7), ("MENTIONS_TITLE", "in", 0.6), ("MENTIONS_STEM", "in", 0.45), ("MENTIONS_FORM", "out", 0.7), ("SHARED_ATTACH", "both", 0.6), ("MENTIONS_ORG", "share", 0.5), ("CITES_REG", "reg_in", 0.3), ("SUPPLEMENTS", "reg", 0.4), ("CITES_LAW", "law_in", 1.0), ("CITES_LAW_ART", "law_art_in", 1.0)],
        "내용 변경": [("CITES", "in", 1.0), ("CITES_CHAPTER", "in", 0.8), ("STALE_CITE", "in", 1.0), ("MENTIONS_TITLE", "in", 0.6), ("CITES_REG", "reg_in", 0.3)],
        "표현 수정": [],
        "고립 수정": [],
    },
}

GENERIC_TITLES = {"목적", "정의", "적용범위", "용어의 정의", "용어정의", "시행일", "기타", "준용", "보고", "교육", "권한의 위임", "적용 범위",
                  "다른 법령 등과의 관계", "적용례", "위임", "책임", "운영", "관리", "절차", "적용", "다른 규정과의 관계", "그 밖의 사항", "기본원칙"}
DEF_TITLE = re.compile(r"(?<![가-힣])정의|용어정의|용어의 뜻")
TERM_RE = re.compile(r"[“\"‘']\s*([^”\"’']{1,25}?)\s*[”\"’']\s*(?:이?란|이?라\s*함은|이?라\s*함은|은|는)")
UNQUOTED_DEF_RE = re.compile(r"^\s*(?:\d+[\.\)]\s*)?([가-힣][가-힣A-Za-z0-9ㆍ·\s]{1,18}?)\s*이?라\s*함은\s")   # 따옴표 없는 정의 「회의비라 함은 …」 (항 · 호의 첫머리일 때만)
SHORT_TERM_MIN_LEN, SHORT_TERM_DF_MAX = 2, 25   # 2자 용어(물품 · 취득)는 같은 규정 안 사용처로만 잇고 흔하면(25조 초과) 잇지 않는다
BODY_RE = re.compile(r"[가-힣]{2,10}위원회")
BODY_BAD_PREFIX = ("해당", "당해", "각", "본", "동", "관련", "위", "이", "그", "및", "또는", "의", "를", "을", "에", "는", "은")
NAME_SUFFIX = ("운영지침", "관리지침", "시행지침", "처리지침", "시행세칙", "운영규정", "관리규정", "업무규정", "업무지침", "규정", "지침", "규칙")
TERM_MIN_LEN, TERM_DF_MAX = 3, 60
SAME_TITLE_MAX = 8
MENTION_DF_MAX = 30


def _clean_term(t: str) -> str:
    return re.sub(r"\s*[\(（].*?[\)）]\s*$", "", t).strip()


def def_terms(norm: str) -> list[str]:
    """정의 항목 하나에서 정의되는 용어: 따옴표 정의 「"A"란 …」 전부 + 따옴표 없는 첫머리 정의 「A라 함은 …」."""
    out = [_clean_term(m.group(1)) for m in TERM_RE.finditer(norm)]
    if not out:
        m = UNQUOTED_DEF_RE.match(norm)
        if m:
            out.append(_clean_term(m.group(1)))
    return out


def _stem(name: str) -> str:
    n = re.sub(r"\s+", "", name)
    for s in NAME_SUFFIX:
        if n.endswith(s) and len(n) > len(s):
            return n[: -len(s)]
    return n


VALUE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:회|일|개월|월|년|원|%|명|인|시간|분)")


def _values(text: str) -> set[str]:
    return {re.sub(r"\s+", "", m) for m in VALUE_RE.findall(text)}


# 고재현 모드의 관계 가중치: 다른 평가(AI 라벨 4묶음, 리포트 80)에서 잰 경로별 정밀도. 이 가중치로 동반 개정 벤치마크를 맞추지 않았다.
# 정밀도가 낮은 관계(규정 이름 인용 · 조직 · 위원회)는 뺐다.
EXPAND_WEIGHTS = {"CITES": 1.0, "USES_TERM": 0.6, "DEFINES": 0.6, "SAME_TITLE": 0.67, "SAME_VALUE": 0.22, "MENTIONS_TITLE": 0.14, "MENTIONS_STEM": 0.14,
                  "SHARED_ATTACH": 0.3, "MENTIONS_FORM": 0.3, "VALUE_DIFF": 0.5, "DEF_DIFF": 0.5, "STALE_CITE": 0.5}
RULE_REL = {"기한 불일치": "VALUE_DIFF", "정의 불일치": "DEF_DIFF", "이동 후 미반영": "STALE_CITE"}


def build(regs: dict[str, dict], edges: list[dict], inferred: list[dict] | None = None, *, law_titles: list[str] | None = None,
          law_article_titles: dict | None = None) -> tuple[dict[str, dict], list[dict]]:
    from .kg_onto import LawRegistry
    registry = LawRegistry.build(law_titles or (), [e.get("target_doc") or "" for e in edges if e.get("target_type") == "law"])
    nodes: dict[str, dict] = {}
    rows: list[dict] = []
    text: dict[str, str] = {}  # 용어 · 위원회 검색용 본문
    arts: list[tuple[str, dict]] = []

    def add_edge(s, t, typ, method, **props):
        rows.append({"src": s, "dst": t, "type": typ, "method": method, **props})

    for reg in regs.values():
        rid = reg["reg_id"]
        nodes[rid] = {"type": "Regulation", "name": reg["name"], "tier": reg.get("hierarchy"), "revised": reg.get("revised")}
        for a in reg["articles"]:
            nodes[a["id"]] = {"type": "Article", "reg": rid, "label": a["label"], "title": a.get("title") or "", "deleted": bool(a.get("deleted"))}
            text[a["id"]] = a["norm"]
            arts.append((rid, a))
            add_edge(a["id"], rid, "PART_OF", "구조")
        for ad in reg.get("addenda") or []:
            for a in ad.get("articles") or []:
                nodes[a["id"]] = {"type": "Supplement", "reg": rid, "label": a["label"], "title": a.get("title") or "", "date": ad.get("date")}
                text[a["id"]] = a["norm"]
                add_edge(a["id"], rid, "PART_OF", "구조")
        for ap in reg.get("appendices") or []:
            typ = "Annex" if ap.get("kind") == "별표" else "Form"
            nodes[ap["id"]] = {"type": typ, "reg": rid, "label": f"{ap.get('kind')} {ap.get('number')}", "title": ap.get("title") or ""}
            text[ap["id"]] = ap.get("text") or ""
            add_edge(ap["id"], rid, "PART_OF", "구조")

    # 인용 표 → CITES / CITES_REG / CITES_LAW
    for e in edges:
        if e["status"] in SKIP or e["target_type"] == "chapter":
            continue
        s = src_node(e)
        if s not in nodes:
            continue
        ctx = e.get("context", "")
        sub = "준용" if "준용" in ctx else ("예외" if "불구하고" in ctx else "인용")
        flags = {"suspect": e["status"] in SUSPECT, "status": e["status"]}
        if e["target_type"] in ("article", "appendix"):
            t = target_node(e)
            if t in nodes:
                add_edge(s, t, "CITES", "인용 표", sub=sub, evidence=e["raw"], **flags)
        elif e["target_type"] == "regulation":
            if e["target_id"] in nodes:
                add_edge(s, e["target_id"], "CITES_REG", "인용 표", evidence=e["raw"], context=ctx[:160], **flags)
        elif e["target_type"] == "law":
            raw_name = target_node(e) or e.get("target_doc", "")
            if re.sub(r"\s+", "", raw_name) in {"법", "영", "시행령", "시행규칙", "시행세칙", "법률"}:   # 어느 법인지 알 수 없는 상대 이름은 법령 노드로 만들지 않는다
                continue
            t = "LAW:" + (registry.canon(raw_name) or raw_name)   # 띄어쓰기 · 약칭 표기 차이는 한 법령으로
            nodes.setdefault(t, {"type": "Law", "name": t[4:]})
            add_edge(s, t, "CITES_LAW", "인용 표", evidence=e["raw"], **flags)

    # 장 · 절 인용 → CITES_CHAPTER (그 장 · 절의 조마다). 인용 표의 장 인용은 장 번호만 읽으므로 절은 문맥에서 읽는다.
    # 조의 장 · 절은 parse의 jang · jeol(없으면 chapter가 「제N장」일 때만). 장을 못 찾으면(missing_chapter) 이 칸으로 다시 찾는다.
    def _no(s: str, unit: str) -> int | None:
        m = re.match(rf"\s*제\s*(\d+)\s*{unit}", s or "")
        return int(m.group(1)) if m else None

    for e in edges:
        if e["target_type"] != "chapter" or not (j := (e.get("target_ref") or {}).get("jang")):
            continue
        s = src_node(e)
        treg = regs.get(e.get("target_reg") or "") or regs.get((e.get("target_id") or ":").split(":")[0])
        if s not in nodes or not treg:
            continue
        ctx = (e.get("context") or "")
        m = re.search(rf"제\s*{j}\s*장\s*제\s*(\d+)\s*절", ctx)
        jeol = int(m.group(1)) if m else None
        sub = "준용" if "준용" in ctx else "인용"
        for a in treg["articles"]:
            aj = _no(a.get("jang") or a.get("chapter"), "장")
            if a.get("deleted") or aj != j or a["id"] == s or (jeol is not None and _no(a.get("jeol"), "절") != jeol):
                continue
            if a["id"] in nodes:
                add_edge(s, a["id"], "CITES_CHAPTER", "인용 표", sub=sub, evidence=e["raw"] + (f" 제{jeol}절" if jeol else ""), jang=j, jeol=jeol)

    # 정의 조 → Term, 본문의 용어 사용
    terms: dict[str, dict] = {}
    for rid, a in arts:
        if a.get("deleted") or not DEF_TITLE.search(a.get("title") or ""):
            continue
        for it in a["items"]:
            for t in def_terms(it["norm"]):
                if len(t) >= SHORT_TERM_MIN_LEN and re.search(r"[가-힣]", t):
                    tid = f"TERM:{rid}:{t}"
                    terms.setdefault(tid, {"type": "Term", "name": t, "reg": rid, "defined_in": a["id"], "item": it["id"]})
                    add_edge(a["id"], tid, "DEFINES", "정의 패턴")
    nodes.update(terms)
    by_name: dict[str, list[str]] = defaultdict(list)
    for tid, n in terms.items():
        by_name[n["name"]].append(tid)
    nterm_text = {k: re.sub(r"\s+", "", v) for k, v in text.items()}   # 「전화녹음 파일」도 용어 「녹음파일」의 사용으로 본다 (제목 언급과 같은 띄어쓰기 무시 기준)
    for name, tids in by_name.items():
        nname = re.sub(r"\s+", "", name)
        users = [k for k, tx in nterm_text.items() if nname in tx and nodes[k]["type"] in ("Article", "Form", "Annex", "Supplement") and not nodes[k].get("deleted")]
        for tid in tids:
            d_art = terms[tid]["defined_in"]
            u = [k for k in users if k != d_art]
            if len(nname) < TERM_MIN_LEN:   # 2자 용어: 같은 규정 안에서만, 흔하면 잇지 않는다
                u = [k for k in u if nodes[k]["reg"] == terms[tid]["reg"]]
                if len(u) > SHORT_TERM_DF_MAX:
                    continue
            if len(u) <= TERM_DF_MAX:
                for k in u:
                    add_edge(k, tid, "USES_TERM", "용어 글자 일치", scope="same_reg" if nodes[k]["reg"] == terms[tid]["reg"] else "cross_reg", df=len(u))

    # 제목 언급: 다른 단위(별표 · 서식 · 부칙 포함) 본문에 조 제목이 그대로 나옴
    ntext = {k: re.sub(r"\s+", "", v) for k, v in text.items()}
    generic = {re.sub(r"\s+", "", g) for g in GENERIC_TITLES}
    for rid, a in arts:
        t = re.sub(r"\s+", "", a.get("title") or "")
        if a.get("deleted") or len(t) < 3 or t in generic:
            continue
        hits = [k for k, v in ntext.items() if t in v and k != a["id"] and not nodes[k].get("deleted")]
        if 0 < len(hits) <= MENTION_DF_MAX:
            for k in hits:
                add_edge(k, a["id"], "MENTIONS_TITLE", "제목 글자 일치", title=t, scope="same_reg" if nodes[k].get("reg") == rid else "cross_reg")

    # 같은 제목 (다른 규정)
    by_title: dict[str, list[str]] = defaultdict(list)
    for rid, a in arts:
        t = re.sub(r"\s+", "", a.get("title") or "")
        if t and not a.get("deleted") and len(t) >= 3 and (a.get("title") or "").strip() not in GENERIC_TITLES and t not in {re.sub(r"\s+", "", g) for g in GENERIC_TITLES}:
            by_title[t].append(a["id"])
    for t, ids in by_title.items():
        regs_ = {nodes[i]["reg"] for i in ids}
        if len(regs_) >= 2 and len(ids) <= SAME_TITLE_MAX:
            for i, x in enumerate(ids):
                for y in ids[i + 1:]:
                    if nodes[x]["reg"] != nodes[y]["reg"]:
                        vx, vy = _values(text[x]), _values(text[y])
                        add_edge(x, y, "SAME_TITLE", "조 제목 일치", title=t, value_diff=bool(vx and vy and vx != vy))

    # 규정 ↔ 시행 지침 짝
    stems: dict[str, list[str]] = defaultdict(list)
    for rid, reg in regs.items():
        stems[_stem(reg["name"])].append(rid)
    for st, ids in stems.items():
        if len(ids) >= 2 and len(st) >= 2:
            for i, x in enumerate(ids):
                for y in ids[i + 1:]:
                    add_edge(x, y, "SUPPLEMENTS", "이름 줄기 일치", stem=st)

    # 위원회 언급
    cnt: dict[str, set[str]] = defaultdict(set)
    for k, tx in text.items():
        if nodes[k]["type"] in ("Article", "Form"):
            for m in BODY_RE.finditer(tx):
                n = m.group(0)
                if not n.startswith(BODY_BAD_PREFIX):
                    cnt[n].add(k)
    for n, ks in cnt.items():
        if len(ks) >= 2:
            nodes[f"BODY:{n}"] = {"type": "Body", "name": n}
            for k in ks:
                add_edge(k, f"BODY:{n}", "MENTIONS_BODY", "위원회 이름")
    from .kg_extra import add_extra
    add_extra(nodes, rows, text, arts, add_edge)
    _add_ontology_boost(regs, nodes, rows, registry, law_article_titles or {}, add_edge)

    # 규칙 레지스트리가 만든 의심 관계 (scripts/infer_rules.py -> out/rules/inferred.jsonl). 같은 그래프에 합친다
    for e in inferred or []:
        rel = RULE_REL.get(e.get("kind"))
        s_, t_ = e.get("src_article"), e.get("target")
        if rel and s_ in nodes and t_ in nodes:
            add_edge(s_, t_, rel, f"규칙 {e.get('rule_id')}", rule_id=e.get("rule_id"), directed=bool(e.get("directed")), note=e.get("note", ""))
    return nodes, rows


def _add_ontology_boost(regs, nodes, rows, registry, law_article_titles, add_edge) -> None:
    """약칭(Alias) · 법령 조(LawArticle). 규칙만, 문서에 적힌 정의와 인용만 근거 (kg_onto)."""
    from .kg_onto import extract_aliases, law_article_cites, sl
    units: list[tuple[str, str, str]] = []
    for reg in regs.values():
        for a in reg["articles"]:
            if not a.get("deleted"):
                units.append((reg["reg_id"], a["id"], a["norm"]))
        for ad in reg.get("addenda") or []:
            for a in ad.get("articles") or []:
                units.append((reg["reg_id"], a["id"], a["norm"]))
    entities = [(k, v["type"], v.get("name", "")) for k, v in nodes.items() if v["type"] in ("Law", "Regulation", "Body", "Term")]
    aliases = extract_aliases(units, entities)
    seen_alias = set()
    for al in aliases:
        if al["type"] == "Law" and not al["generic"]:
            registry.by_key.setdefault(sl(al["short"]), al["node"][4:])   # 「개보법」 같은 약칭도 한 법령으로
        aid = f"ALIAS:{al['reg']}:{al['short']}"
        if (aid, al["node"]) in seen_alias:
            continue
        seen_alias.add((aid, al["node"]))
        nodes.setdefault(aid, {"type": "Alias", "short": al["short"], "reg": al["reg"], "defined_in": al["unit"], "for": al["node"], "generic": al["generic"]})
        add_edge(aid, al["node"], "ALIAS_OF", "「(이하 \"B\"라 한다)」 정의", generic=al["generic"])
    have_law = {(r["src"], r["dst"]) for r in rows if r["type"] == "CITES_LAW"}
    for c in law_article_cites(units, registry, aliases):
        lid = "LAW:" + c["law"]
        nodes.setdefault(lid, {"type": "Law", "name": c["law"]})
        aid = f"LAWART:{c['law']}:{c['article']}"
        if aid not in nodes:
            nodes[aid] = {"type": "LawArticle", "law": lid, "label": c["article"], "title": law_article_titles.get((c["law"], c["article"]), "")}
            add_edge(aid, lid, "ARTICLE_OF", "구조")
        add_edge(c["unit"], aid, "CITES_LAW_ART", "법령 조 인용", via=c["via"])
        if (c["unit"], lid) not in have_law:   # 조 단위 인용은 법령 단위 인용이기도 하다
            have_law.add((c["unit"], lid))
            add_edge(c["unit"], lid, "CITES_LAW", "법령 조 인용에서 파생", via=c["via"])


RANGE_RE = re.compile(r"제\s*(\d+)조(?:의\s*(\d+))?\s*부터\s*제\s*(\d+)조(?:의\s*(\d+))?")


def moved_range(amendment: str, reg_id: str, nodes: dict[str, dict]) -> list[str]:
    """번호 이동 개정문(「제N조부터 제K조까지를 각각 …」)에서 옮겨지는 조 노드. 개정문만 본다 (정답을 보지 않음)."""
    m = RANGE_RE.search(amendment or "")
    if not m:
        return []
    lo, hi = int(m.group(1)), int(m.group(3))
    out = []
    for n in range(lo, hi + 1):
        nid = f"{reg_id}:{n}"
        if nid in nodes and not nodes[nid].get("deleted"):
            out.append(nid)
        k = 2
        while f"{reg_id}:{n}의{k}" in nodes:
            out.append(f"{reg_id}:{n}의{k}")
            k += 1
    return out


class KG:
    def __init__(self, nodes: dict[str, dict], rows: list[dict]):
        self.nodes = nodes
        self.rows = rows
        self.out: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self.inb: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for r in rows:
            self.out[(r["type"], r["src"])].append(r)
            self.inb[(r["type"], r["dst"])].append(r)
        self.regname = {k: v["name"] for k, v in nodes.items() if v["type"] == "Regulation"}
        # 약칭 사전: 개체 이름 · 약칭(상대 지칭 제외) -> 같은 개체를 가리키는 표기 모두
        from .kg_onto import sl
        self.alias_index: dict[str, set[str]] = {}
        for n in nodes.values():
            if n.get("type") == "Alias" and not n.get("generic") and n.get("for") in nodes:
                forms = {nodes[n["for"]].get("name", ""), n["short"]} - {""}
                for f in forms:
                    self.alias_index.setdefault(sl(f), set()).update(forms)

    def label(self, nid: str) -> str:
        n = self.nodes.get(nid, {})
        reg = self.regname.get(n.get("reg", ""), "")
        return f"{reg} {n.get('label', nid)}" + (f"({n['title']})" if n.get("title") and n.get("type") in ("Article", "Supplement") else "")

    def terms_defined_in(self, art: str) -> list[str]:
        return [r["dst"] for r in self.out.get(("DEFINES", art), [])]

    def alias_forms(self, phrase: str) -> set[str]:
        """phrase가 개체 이름이나 약칭이면 같은 개체의 다른 표기(이름 + 약칭). 「법」 · 「규정」 같은 상대 지칭(generic)은 쓰지 않는다."""
        from .kg_onto import alias_forms
        return alias_forms(self.nodes, self.alias_index, phrase)

    def expand_hops(self, starts: list[str], hops: int = 2, weights: dict[str, float] | None = None, cap: int = 30, hub: int = 60) -> dict[str, dict]:
        """고재현 모드: 시작 조에서 hops홉까지 관계 가중치(다른 평가에서 잰 경로별 정밀도)로 번져 간 다른 조 {노드: {score, hop, path}}.
        용어 · 서식 같은 중간 노드(Term)는 거쳐 가며, 이웃이 hub개를 넘는 노드는 2홉부터 번지지 않는다. 시작 조와 같은 규정의 조는 뺀다."""
        w = EXPAND_WEIGHTS if weights is None else weights
        starts_set = set(starts)
        regs = {self.nodes[s].get("reg") for s in starts if s in self.nodes}
        art = ("Article", "Supplement", "Annex", "Form")

        def nbrs(u):
            for rel, wt in w.items():
                for r in self.out.get((rel, u), []):
                    yield r["dst"], rel, wt
                for r in self.inb.get((rel, u), []):
                    yield r["src"], rel, wt

        best: dict[str, dict] = {}
        frontier = {s: (1.0, "") for s in starts_set}
        seen = set(starts_set)
        for h in range(1, hops + 1):
            nxt: dict[str, tuple[float, str]] = {}
            for u, (sc, path) in frontier.items():
                nb = list(nbrs(u))
                if h > 1 and len(nb) > hub:
                    continue
                damp = 1.0 / max(len(nb), 1) ** 0.25 if h > 1 else 1.0   # 허브 감쇠
                for v, rel, wt in nb:
                    if v in seen or v not in self.nodes or self.nodes[v].get("deleted"):
                        continue
                    nsc, npath = sc * wt * damp * (0.5 if h > 1 else 1.0), (path + ">" if path else "") + rel
                    if v not in nxt or nsc > nxt[v][0]:
                        nxt[v] = (nsc, npath)
            for v, (sc, path) in nxt.items():
                seen.add(v)
                if self.nodes[v]["type"] in art and self.nodes[v].get("reg") not in regs:
                    best[v] = {"score": sc, "hop": h, "path": path}
            frontier = nxt
        top = sorted(best.items(), key=lambda kv: (kv[1]["hop"], -kv[1]["score"]))
        # 홉 거리 순이 아니라 점수 순이되 가까운 홉을 먼저: 1홉은 파이프라인 경로가 이미 맡으므로 여기서는 점수 순으로 상한까지
        top = sorted(best.items(), key=lambda kv: -kv[1]["score"])[:cap]
        return dict(top)

    def impact(self, start: str, kind: str, term_hint: list[str] | None = None, hops: int = 1, allowed: set[str] | None = None) -> dict:
        """수정 종류에 맞는 경로만 따라 영향 후보를 모은다. 결과 {candidates: {노드: {score, paths}}, reg_level: [...]}.
        start 자신은 뺀다. 구조 검사에서 걸린 인용(suspect)은 표시만 한다."""
        paths = ONTOLOGY["change_paths"].get(kind, [("CITES", "in", 1.0)])
        if allowed is not None:  # 온톨로지를 단계별로 늘려 가며 비교할 때: 이 관계들만 쓴다
            paths = [p for p in paths if p[0] in allowed]
        cand: dict[str, dict] = {}
        reg_level: dict[str, dict] = {}
        reg_id = self.nodes.get(start, {}).get("reg")

        def add(node, rel, w, **info):
            if node == start or node not in self.nodes or self.nodes[node].get("deleted"):
                return
            c = cand.setdefault(node, {"score": 0.0, "paths": []})
            c["score"] = max(c["score"], w)
            tag = rel + (":" + info["sub"] if info.get("sub") and info["sub"] != "인용" else "")
            if tag not in c["paths"]:
                c["paths"].append(tag)

        frontier = {start}
        for h in range(hops):
            nxt = set()
            for cur in frontier:
                for rel, d, w in paths:
                    if rel == "CITES" and d in ("in", "out"):
                        rs = self.inb.get((rel, cur), []) if d == "in" else self.out.get((rel, cur), [])
                        for r in rs:
                            node = r["src"] if d == "in" else r["dst"]
                            add(node, f"CITES_{d}", w * (0.5 ** h), sub=r.get("sub"))
                            nxt.add(node)
                    elif rel == "SAME_TITLE" and h == 0:
                        for r in self.out.get((rel, cur), []) + self.inb.get((rel, cur), []):
                            add(r["dst"] if r["src"] == cur else r["src"], rel + (":값다름" if r.get("value_diff") else ""), w + (0.05 if r.get("value_diff") else 0))
                    elif rel in ("VALUE_DIFF", "DEF_DIFF") and d == "both" and h == 0:
                        for r in self.out.get((rel, cur), []) + self.inb.get((rel, cur), []):
                            add(r["dst"] if r["src"] == cur else r["src"], rel, w)
                    elif rel == "CITES_CHAPTER" and d == "in" and h == 0:
                        for r in self.inb.get((rel, cur), []):
                            add(r["src"], "CITES_CHAPTER_in", w)
                    elif rel == "STALE_CITE" and d == "in" and h == 0:
                        for r in self.inb.get((rel, cur), []):
                            add(r["src"], rel, w)
                    elif rel in ("SHARED_ATTACH", "SAME_VALUE") and d == "both" and h == 0:
                        for r in self.out.get((rel, cur), []) + self.inb.get((rel, cur), []):
                            add(r["dst"] if r["src"] == cur else r["src"], rel, w)
                    elif rel == "MENTIONS_FORM" and d == "out" and h == 0:
                        for r in self.out.get((rel, cur), []):
                            add(r["dst"], rel, w)
                    elif rel == "MENTIONS_STEM" and d == "in" and h == 0:
                        for r in self.inb.get((rel, cur), []):
                            add(r["src"], rel, w)
                    elif rel == "MENTIONS_ORG" and d == "share" and h == 0:
                        for r in self.out.get((rel, cur), []):
                            for r2 in self.inb.get((rel, r["dst"]), []):
                                add(r2["src"], rel, w)
                    elif rel == "MENTIONS_TITLE" and d == "in" and h == 0:
                        for r in self.inb.get((rel, cur), []):
                            add(r["src"], rel, w)
                    elif rel == "MENTIONS_TITLE" and d == "out" and h == 0:   # 바뀐 조가 제목으로 언급하는 조 (change_paths에 넣은 수정 종류에서만)
                        for r in self.out.get((rel, cur), []):
                            add(r["dst"], "MENTIONS_TITLE_out", w)
                    elif rel == "USES_TERM" and d == "term_in" and h == 0:
                        for tid in (term_hint if term_hint is not None else self.terms_defined_in(cur)):
                            for r in self.inb.get(("USES_TERM", tid), []):
                                add(r["src"], rel, w if r.get("scope") == "cross_reg" else w + 0.1)
                    elif rel == "CITES_LAW_ART" and d == "law_art_in" and h == 0 and self.nodes.get(cur, {}).get("type") == "LawArticle":
                        for r in self.inb.get(("CITES_LAW_ART", cur), []):
                            add(r["src"], rel, w)
                    elif rel == "CITES_LAW" and d == "law_in" and h == 0 and self.nodes.get(cur, {}).get("type") == "Law":
                        for r in self.inb.get(("CITES_LAW", cur), []):
                            add(r["src"], rel, w)
            frontier = nxt
        for rel, d, w in paths:
            if rel == "CITES_REG" and d == "reg_in" and reg_id:
                title = re.sub(r"\s+", "", self.nodes.get(start, {}).get("title", ""))
                for r in self.inb.get(("CITES_REG", reg_id), []):
                    if len(title) >= 3 and title in re.sub(r"\s+", "", r.get("context", "")):   # 문맥에 바뀐 조의 제목어가 있으면 후보로 올린다
                        add(r["src"], "CITES_REG:제목어", 0.6)
                    else:
                        reg_level.setdefault(r["src"], {"score": w})
            elif rel == "SUPPLEMENTS" and d == "reg" and reg_id:
                for r in self.out.get(("SUPPLEMENTS", reg_id), []) + self.inb.get(("SUPPLEMENTS", reg_id), []):
                    other = r["dst"] if r["src"] == reg_id else r["src"]
                    reg_level.setdefault(f"REG:{other}", {"score": w, "pair": True})
        return {"start": start, "kind": kind, "candidates": cand, "reg_level": reg_level}

    def impact_many(self, starts: list[str], kind: str, **kw) -> dict:  # kw: impact()의 인자(term_hint, hops, allowed)
        """시작 조가 여럿일 때(번호 이동 범위): 각 시작 조의 결과를 합친다. 범위 안의 조가 범위 안의 다른 조를 인용하면 그 조도 후보다(번호가 함께 밀리므로)."""
        merged = {"start": starts, "kind": kind, "candidates": {}, "reg_level": {}}
        for st in starts:
            r = self.impact(st, kind, **kw)
            for n, c in r["candidates"].items():
                m = merged["candidates"].setdefault(n, {"score": 0.0, "paths": []})
                m["score"] = max(m["score"], c["score"])
                m["paths"] += [p for p in c["paths"] if p not in m["paths"]]
            for n, c in r["reg_level"].items():
                merged["reg_level"].setdefault(n, c)
        return merged
