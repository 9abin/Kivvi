"""조항 관계 그래프의 추가 관계 5종 (kg.build가 부른다). 모두 원문 글자 규칙으로 만들고 LLM을 쓰지 않는다.

  MENTIONS_FORM  조 -> 서식 · 별표      본문에 서식 · 별표의 이름(「서류전형 평정표」)이 그대로 나옴
  SHARED_ATTACH  조 <-> 조              같은 서식 · 별표를 인용하거나 이름으로 언급하는 조끼리 (부속을 매개로 한 연결)
  MENTIONS_STEM  단위 -> 조             조 제목의 줄기(「당연면직」 -> 「면직」)가 본문에 나옴. 접두 · 접미 수식어를 닫힌 목록으로만 뗀다
  SAME_VALUE     조 <-> 조              같은 대상어 + 같은 값(「1인당 3만원」)을 가진 조끼리
  MENTIONS_ORG   단위 -> 조직            직제규정에 나오는 본부 · 실 · 팀 · 센터 이름이 다른 단위에 나옴

설계 원칙: 평가 문항을 보고 만든 사전이나 예외가 없다. 모든 임계(등장 조 수 상한 등)는 아래 상수 하나로 정한다.
"""

from __future__ import annotations

import re
from collections import defaultdict

NAME_DF_MAX = 8        # 서식 · 별표 이름이 이 개수보다 많은 조에 나오면 버린다 (흔한 말)
ATTACH_GROUP_MAX = 6   # 같은 부속을 쓰는 조가 이 개수보다 많으면 연결하지 않는다
STEM_DF_MAX = 30
VALUE_GROUP_MAX = 6
ORG_DF_MAX = 30
MIN_NAME = 4
GENERIC_NAMES = {"구분평가항목비고", "별지서식", "서식", "별표", "신청서", "보고서", "확인서", "계획서", "결과보고서", "조서"}
STEM_SUFFIX = ("사유", "기준", "절차", "대상", "범위", "방법", "요건", "제도", "관리", "신청", "심사", "지급")
STEM_PREFIX = ("당연", "특별", "일반", "정기", "임시", "신규")
VALUE_UNIT = r"(?:만원|천원|억원|원|회|일|개월|월|년|%|명|인|시간|분|세|점|배|개)"
VALUE_KEY = re.compile(r"([가-힣]{2,6})\s*(\d+(?:\.\d+)?\s*" + VALUE_UNIT + r")")
ORG_RE = re.compile(r"[가-힣]{2,10}(?:본부|센터|실|팀|단)")
ORG_BAD = {"사무실", "회의실", "교육실", "상담실", "연구실", "대표이사실", "집행간부실"}


def _n(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def unit_name(node: dict, body: str) -> str:
    """서식은 본문 첫 줄, 별표는 제목. 괄호 · 개정 표시를 뗀다."""
    if node["type"] == "Form":
        first = next((ln.strip() for ln in (body or "").splitlines() if ln.strip()), "")
        raw = first
    else:
        raw = node.get("title") or ""
    raw = re.sub(r"<[^>]*>", "", raw)
    raw = re.sub(r"[\(（\[].*?[\)）\]]", "", raw)
    n = _n(raw)
    n = re.sub(r"[^0-9A-Za-z가-힣]", "", n)
    return n if len(n) >= MIN_NAME and n not in GENERIC_NAMES and not n.isdigit() else ""


def stem_of(title: str) -> str:
    t = _n(title)
    for s in STEM_SUFFIX:
        if t.endswith(s) and len(t) - len(s) >= 2:
            t = t[: -len(s)]
            break
    for p in STEM_PREFIX:
        if t.startswith(p) and len(t) - len(p) >= 2:
            t = t[len(p):]
            break
    return t


def add_extra(nodes: dict[str, dict], rows: list[dict], text: dict[str, str], arts: list[tuple[str, dict]], add_edge) -> None:
    ntext = {k: _n(v) for k, v in text.items()}
    live = lambda k: not nodes[k].get("deleted")  # noqa: E731
    article_ids = [a["id"] for _, a in arts if not a.get("deleted")]

    # 1) 서식 · 별표 이름 언급, 2) 같은 부속을 쓰는 조끼리
    names = {}
    for k, v in nodes.items():
        if v["type"] in ("Annex", "Form"):
            nm = unit_name(v, text.get(k, ""))
            if nm:
                names[k] = nm
    attach_users: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        if r["type"] == "CITES" and nodes.get(r["dst"], {}).get("type") in ("Annex", "Form") and nodes.get(r["src"], {}).get("type") in ("Article", "Supplement"):
            attach_users[r["dst"]].add(r["src"])
    for u, nm in names.items():
        hits = [k for k in article_ids if nm in ntext[k]]
        if 0 < len(hits) <= NAME_DF_MAX:
            for k in hits:
                add_edge(k, u, "MENTIONS_FORM", "서식·별표 이름 일치", name=nm)
                attach_users[u].add(k)
    for u, users in attach_users.items():
        us = sorted(users)
        if 2 <= len(us) <= ATTACH_GROUP_MAX:
            for i, x in enumerate(us):
                for y in us[i + 1:]:
                    add_edge(x, y, "SHARED_ATTACH", "같은 부속 사용", via=u)

    # 3) 조 제목 줄기 언급
    for rid, a in arts:
        if a.get("deleted"):
            continue
        t = _n(a.get("title") or "")
        st = stem_of(a.get("title") or "")
        if len(st) < 2 or st == t or t in {_n(g) for g in ("목적", "정의", "적용범위")}:
            continue
        hits = [k for k, v in ntext.items() if st in v and k != a["id"] and live(k) and nodes[k]["type"] in ("Article", "Supplement", "Annex", "Form")]
        if 0 < len(hits) <= STEM_DF_MAX:
            for k in hits:
                add_edge(k, a["id"], "MENTIONS_STEM", "제목 줄기 일치", stem=st, scope="same_reg" if nodes[k].get("reg") == rid else "cross_reg")

    # 4) 같은 대상어 + 같은 값
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for _, a in arts:
        if a.get("deleted"):
            continue
        for m in VALUE_KEY.finditer(text[a["id"]]):
            groups[(m.group(1), _n(m.group(2)))].add(a["id"])
    for (noun, val), ids in groups.items():
        ids = sorted(ids)
        if 2 <= len(ids) <= VALUE_GROUP_MAX:
            for i, x in enumerate(ids):
                for y in ids[i + 1:]:
                    add_edge(x, y, "SAME_VALUE", "대상어·값 일치", noun=noun, value=val)

    # 5) 조직 이름 공유 (조직 이름은 직제규정에서만 뽑는다)
    org_names: set[str] = set()
    for rid, a in arts:
        if nodes.get(rid, {}).get("name") == "직제규정":
            org_names |= {m for m in ORG_RE.findall(text[a["id"]]) if m not in ORG_BAD}
    for nm in sorted(org_names):
        hits = [k for k, v in ntext.items() if nm in v and live(k) and nodes[k]["type"] in ("Article", "Supplement", "Annex", "Form")]
        if 2 <= len(hits) <= ORG_DF_MAX:
            nodes.setdefault(f"ORG:{nm}", {"type": "Org", "name": nm})
            for k in hits:
                add_edge(k, f"ORG:{nm}", "MENTIONS_ORG", "조직 이름 일치")
