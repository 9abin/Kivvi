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
        "Law": "인용된 법령 조 또는 법령 이름",
    },
    "relations": {
        "PART_OF": {"from": "Article|Supplement|Annex|Form", "to": "Regulation", "method": "구조", "meaning": "어느 규정에 속하는가"},
        "CITES": {"from": "Article|Supplement|Annex|Form", "to": "Article|Supplement|Annex|Form", "method": "인용 표(amend.edge)",
                  "meaning": "조 번호 · 별표 · 서식으로 다른 단위를 가리킨다. sub = 인용 | 준용 | 예외", "inbound_means": "이 단위를 인용하는 곳"},
        "CITES_REG": {"from": "Article|Supplement|Annex|Form", "to": "Regulation", "method": "인용 표", "meaning": "규정 이름만으로 규정 전체를 가리킨다 (위임 · 준용 포함). 조 단위로 좁혀지지 않음"},
        "CITES_LAW": {"from": "Article|Supplement|Annex|Form", "to": "Law", "method": "인용 표", "meaning": "법령 또는 법령 조를 인용한다"},
        "DEFINES": {"from": "Article", "to": "Term", "method": "정의 조의 「용어란 …」 패턴", "meaning": "이 조가 용어를 정의한다"},
        "USES_TERM": {"from": "Article|Form", "to": "Term", "method": "본문 · 별표 · 서식 · 부칙에 용어 글자가 나옴 (3자 이상, 등장 단위 60개 이하)",
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
    # 관계마다 어디서 왔는가 (그래프는 하나이고, 출처는 투명성과 기여 분석용 표시일 뿐이다)
    "origin": {"기존 규칙": ["PART_OF", "CITES", "CITES_REG", "CITES_LAW", "SAME_TITLE", "VALUE_DIFF", "DEF_DIFF", "STALE_CITE"],
             "신규": ["DEFINES", "USES_TERM", "MENTIONS_TITLE", "SUPPLEMENTS", "MENTIONS_BODY", "MENTIONS_FORM", "SHARED_ATTACH", "MENTIONS_STEM", "SAME_VALUE", "MENTIONS_ORG"]},
    # 수정 종류 → 영향이 번지는 경로. (관계, 방향, 가중치) — in: 이 단위를 가리키는 쪽, out: 이 단위가 가리키는 쪽
    "change_paths": {
        "값 변경": [("SAME_TITLE", "both", 0.9), ("CITES", "in", 1.0), ("VALUE_DIFF", "both", 0.8), ("MENTIONS_TITLE", "in", 0.6), ("SAME_VALUE", "both", 0.7), ("MENTIONS_STEM", "in", 0.45), ("MENTIONS_FORM", "out", 0.7), ("SHARED_ATTACH", "both", 0.6), ("SUPPLEMENTS", "reg", 0.4), ("CITES_REG", "reg_in", 0.3)],
        "삭제": [("CITES", "in", 1.0), ("STALE_CITE", "in", 1.0), ("MENTIONS_TITLE", "in", 0.6), ("MENTIONS_STEM", "in", 0.45), ("MENTIONS_FORM", "out", 0.7), ("SHARED_ATTACH", "both", 0.6), ("CITES_REG", "reg_in", 0.3)],
        "번호 이동": [("CITES", "in", 1.0), ("STALE_CITE", "in", 1.0), ("MENTIONS_ORG", "share", 0.5)],
        "정의 변경": [("USES_TERM", "term_in", 0.8), ("CITES", "in", 1.0), ("DEF_DIFF", "both", 0.8), ("MENTIONS_TITLE", "in", 0.6), ("MENTIONS_STEM", "in", 0.45)],
        "위임·법령 변경": [("CITES", "in", 1.0), ("CITES", "out", 0.7), ("MENTIONS_TITLE", "in", 0.6), ("MENTIONS_STEM", "in", 0.45), ("MENTIONS_FORM", "out", 0.7), ("SHARED_ATTACH", "both", 0.6), ("MENTIONS_ORG", "share", 0.5), ("CITES_REG", "reg_in", 0.3), ("SUPPLEMENTS", "reg", 0.4), ("CITES_LAW", "law_in", 1.0)],
        "내용 변경": [("CITES", "in", 1.0), ("STALE_CITE", "in", 1.0), ("MENTIONS_TITLE", "in", 0.6), ("CITES_REG", "reg_in", 0.3)],
        "표현 수정": [],
        "고립 수정": [],
    },
}

GENERIC_TITLES = {"목적", "정의", "적용범위", "용어의 정의", "용어정의", "시행일", "기타", "준용", "보고", "교육", "권한의 위임", "적용 범위",
                  "다른 법령 등과의 관계", "적용례", "위임", "책임", "운영", "관리", "절차", "적용", "다른 규정과의 관계", "그 밖의 사항", "기본원칙"}
DEF_TITLE = re.compile(r"(?<![가-힣])정의|용어정의|용어의 뜻")
TERM_RE = re.compile(r"[“\"‘']\s*([^”\"’']{1,25}?)\s*[”\"’']\s*(?:이?란|이?라\s*함은|이?라\s*함은|은|는)")
BODY_RE = re.compile(r"[가-힣]{2,10}위원회")
BODY_BAD_PREFIX = ("해당", "당해", "각", "본", "동", "관련", "위", "이", "그", "및", "또는", "의", "를", "을", "에", "는", "은")
NAME_SUFFIX = ("운영지침", "관리지침", "시행지침", "처리지침", "시행세칙", "운영규정", "관리규정", "업무규정", "업무지침", "규정", "지침", "규칙")
TERM_MIN_LEN, TERM_DF_MAX = 3, 60
SAME_TITLE_MAX = 8
MENTION_DF_MAX = 30


def _clean_term(t: str) -> str:
    return re.sub(r"\s*[\(（].*?[\)）]\s*$", "", t).strip()


def _stem(name: str) -> str:
    n = re.sub(r"\s+", "", name)
    for s in NAME_SUFFIX:
        if n.endswith(s) and len(n) > len(s):
            return n[: -len(s)]
    return n


VALUE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:회|일|개월|월|년|원|%|명|인|시간|분)")


def _values(text: str) -> set[str]:
    return {re.sub(r"\s+", "", m) for m in VALUE_RE.findall(text)}


RULE_REL = {"기한 불일치": "VALUE_DIFF", "정의 불일치": "DEF_DIFF", "이동 후 미반영": "STALE_CITE"}


def build(regs: dict[str, dict], edges: list[dict], inferred: list[dict] | None = None) -> tuple[dict[str, dict], list[dict]]:
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
            t = "LAW:" + (target_node(e) or e.get("target_doc", ""))
            nodes.setdefault(t, {"type": "Law", "name": t[4:]})
            add_edge(s, t, "CITES_LAW", "인용 표", evidence=e["raw"], **flags)

    # 정의 조 → Term, 본문의 용어 사용
    terms: dict[str, dict] = {}
    for rid, a in arts:
        if a.get("deleted") or not DEF_TITLE.search(a.get("title") or ""):
            continue
        for it in a["items"]:
            for m in TERM_RE.finditer(it["norm"]):
                t = _clean_term(m.group(1))
                if len(t) >= TERM_MIN_LEN and re.search(r"[가-힣]", t):
                    tid = f"TERM:{rid}:{t}"
                    terms.setdefault(tid, {"type": "Term", "name": t, "reg": rid, "defined_in": a["id"], "item": it["id"]})
                    add_edge(a["id"], tid, "DEFINES", "정의 패턴")
    nodes.update(terms)
    by_name: dict[str, list[str]] = defaultdict(list)
    for tid, n in terms.items():
        by_name[n["name"]].append(tid)
    for name, tids in by_name.items():
        users = [k for k, tx in text.items() if name in tx and nodes[k]["type"] in ("Article", "Form", "Annex", "Supplement") and not nodes[k].get("deleted")]
        for tid in tids:
            d_art = terms[tid]["defined_in"]
            u = [k for k in users if k != d_art]
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

    # 규칙 레지스트리가 만든 의심 관계 (scripts/infer_rules.py -> out/rules/inferred.jsonl). 같은 그래프에 합친다
    for e in inferred or []:
        rel = RULE_REL.get(e.get("kind"))
        s_, t_ = e.get("src_article"), e.get("target")
        if rel and s_ in nodes and t_ in nodes:
            add_edge(s_, t_, rel, f"규칙 {e.get('rule_id')}", rule_id=e.get("rule_id"), directed=bool(e.get("directed")), note=e.get("note", ""))
    return nodes, rows


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

    def label(self, nid: str) -> str:
        n = self.nodes.get(nid, {})
        reg = self.regname.get(n.get("reg", ""), "")
        return f"{reg} {n.get('label', nid)}" + (f"({n['title']})" if n.get("title") and n.get("type") in ("Article", "Supplement") else "")

    def terms_defined_in(self, art: str) -> list[str]:
        return [r["dst"] for r in self.out.get(("DEFINES", art), [])]

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
                    elif rel == "USES_TERM" and d == "term_in" and h == 0:
                        for tid in (term_hint if term_hint is not None else self.terms_defined_in(cur)):
                            for r in self.inb.get(("USES_TERM", tid), []):
                                add(r["src"], rel, w if r.get("scope") == "cross_reg" else w + 0.1)
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
