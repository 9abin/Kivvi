"""온톨로지 보강: 약칭(동의어) 사전 · 법령 이름 정규화 · 법령 조 인용.

규칙만 쓰고 근거는 문서에 적힌 것뿐이다 (전문가 목록 · LLM 없음).
  - 약칭: 「A(이하 "B"라 한다)」 정의에서 B를 A의 다른 이름으로 본다. A가 알려진 개체(법령 · 규정 · 위원회 · 용어)일 때만 남긴다.
  - 법령 이름: 띄어쓰기만 다른 이름(개인정보 보호법 · 개인정보보호법)을 한 법령으로 합치고, 법률 DB에 있으면 그 제목을 대표 이름으로 쓴다.
  - 법령 조: 본문의 「법령」 제N조, 약칭 제N조, 「법」 · 「같은 법」 제N조(그 규정이 약칭으로 정의했거나 바로 앞에 법령이 나온 경우만)를 조 단위로 푼다.
판정하지 않는다. 풀리지 않는 것은 버린다 (틀리게 잇는 것보다 비워 두는 쪽이 안전하다).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

BRACKETS = "「」『』“”\"'‘’<>《》"
# 「앞말(이하 "약칭"이라 한다)」
ALIAS_DEF = re.compile(r"[(（]\s*이하\s*[「『\"“'‘]?\s*([^)）」』\"”'’]{1,24}?)\s*[」』\"”'’]?\s*(?:이?라\s*(?:한다|함|칭한다|약칭한다)|라고\s*한다)\s*[)）]")
ART_REF = r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?"
LAW_BRACKET = re.compile(r"[「『]\s*([^」』]{2,50}?)\s*[」』]")
REL_LAW = re.compile(r"(?<![가-힣])(같은 법 시행령|동법 시행령|같은 법|동법|시행령|법|영)\s*" + ART_REF)
# 이 낱말들은 어느 문서에서나 쓰는 상대 지칭이라 검색용 동의어로 쓰지 않는다 (문서 안 해석에만 쓴다)
GENERIC_SHORT = {"법", "영", "시행령", "규정", "지침", "규칙", "강령", "헌장", "정관", "위원회", "회사", "본 규정", "본 지침", "본 규칙", "이 규정", "이 지침"}


def sl(s: str) -> str:
    return re.sub(r"[\s" + re.escape(BRACKETS) + "]+", "", s or "")


@dataclass
class LawRegistry:
    """법령 이름 정규화. 대표 이름 = 법률 DB 제목 > 먼저 본 표기."""
    by_key: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(cls, db_titles: list[str] | tuple = (), seen: list[str] | tuple = ()) -> "LawRegistry":
        r = cls()
        for t in db_titles:
            r.by_key.setdefault(sl(t), t.strip())
        for s in seen:
            if sl(s):
                r.by_key.setdefault(sl(s), s.strip())
        return r

    def canon(self, name: str) -> str | None:
        return self.by_key.get(sl(name))

    def add_seen(self, name: str) -> str | None:
        if sl(name):
            self.by_key.setdefault(sl(name), name.strip())
        return self.canon(name)

    def decree_of(self, law: str) -> str | None:
        return self.canon(law + " 시행령")


def extract_aliases(units: list[tuple[str, str, str]], entities: list[tuple[str, str, str]]) -> list[dict]:
    """units: (규정 id, 단위 id, 본문). entities: (노드 id, 종류, 이름). 반환: {short, node, type, reg, unit, generic}.
    「(이하 "B"라 한다)」 바로 앞 글에서 가장 긴 개체 이름이 끝에 오는 경우만 약칭으로 인정한다."""
    by_len = sorted(((sl(n), nid, typ, n) for nid, typ, n in entities if len(sl(n)) >= 3), key=lambda x: -len(x[0]))
    out: list[dict] = []
    for reg, uid, text in units:
        for m in ALIAS_DEF.finditer(text):
            short = m.group(1).strip().strip(BRACKETS).strip()
            if len(sl(short)) < 1:
                continue
            before = sl(text[max(0, m.start() - 70):m.start()])
            hit = next(((nid, typ, n) for key, nid, typ, n in by_len if before.endswith(key)), None)
            if hit is None or sl(short) == sl(hit[2]):
                continue
            ks, kn = sl(short), sl(hit[2])
            if kn in ks:   # 약칭이 개체 이름을 그대로 품으면 다른 말이다 (「수수 금지 금품등」은 「금품등」의 약칭이 아니다)
                continue
            if hit[1] == "Term" and not any(ks[i:i + 2] in kn for i in range(len(ks) - 1)):   # 용어는 이름과 겹치는 변형만
                continue
            # 이름의 일부만 떼어 쓴 말(위원회 · 강령 · 시스템)과 상대 지칭은 문서 안 해석에만 쓰고 검색 동의어로는 쓰지 않는다
            generic = short in GENERIC_SHORT or len(ks) < 3 or ks in kn
            out.append({"short": short, "node": hit[0], "type": hit[1], "reg": reg, "unit": uid, "generic": generic})
    return out


def _art_label(num: str, sub: str | None) -> str:
    return f"제{num}조" + (f"의{sub}" if sub else "")


def law_article_cites(units: list[tuple[str, str, str]], registry: LawRegistry, aliases: list[dict]) -> list[dict]:
    """본문에서 법령 조 인용을 조 단위로 푼다. 반환: {unit, law(대표 이름), article("제N조"), via}.
    via = 「법령」 | 약칭 | 상대(앞선 법령) | 약칭(법)."""
    # 규정 안에서 「법」 같은 짧은 약칭이 가리키는 법령: 그 규정이 정의한 것 (단위 순서상 가장 가까운 앞 정의)
    reg_alias: dict[tuple[str, str], list[tuple[int, str]]] = {}
    order = {uid: i for i, (_, uid, _) in enumerate(units)}
    for a in aliases:
        if a["type"] == "Law":
            reg_alias.setdefault((a["reg"], sl(a["short"])), []).append((order.get(a["unit"], 0), a["node"][4:]))
    out: list[dict] = []
    for reg, uid, text in units:
        # 이 단위에서 나온 법령을 위치 순으로 모은다
        marks: list[tuple[int, str, str]] = []   # (위치, 법령, via)
        for m in LAW_BRACKET.finditer(text):
            law = registry.canon(m.group(1))
            if law:
                marks.append((m.start(), law, "「법령」"))
        for m in re.finditer(LAW_BRACKET.pattern + r"\s*(?:\([^)]{0,40}\))?\s*" + ART_REF, text):
            law = registry.canon(m.group(1))
            if law:
                out.append({"unit": uid, "law": law, "article": _art_label(m.group(2), m.group(3)), "via": "「법령」"})
        # 약칭 + 제N조 (약칭이 이 규정이 정의한 법령의 이름이거나 법령 이름이 그대로일 때)
        for (r, short), defs in reg_alias.items():
            if r != reg or short in {"법", "영", "시행령"}:
                continue
            for m in re.finditer(re.escape(short) + r"\s*" + ART_REF, re.sub(r"[「」『』]", "", text)):
                out.append({"unit": uid, "law": defs[-1][1], "article": _art_label(m.group(1), m.group(2)), "via": "약칭"})
        # 법 · 같은 법 · 시행령 + 제N조: 바로 앞에 나온 법령, 없으면 이 규정이 「법」으로 정의한 법령
        for m in REL_LAW.finditer(text):
            word = m.group(1)
            prior = [mk for mk in marks if mk[0] < m.start()]
            base = prior[-1][1] if prior else None
            if base is None:   # 바로 앞에 법령이 없으면, 이 규정이 「법」으로 정의한 법령 (시행령 · 영도 그 법의 시행령)
                defs = reg_alias.get((reg, "법"))
                if defs:
                    cand = [d for d in defs if d[0] <= order.get(uid, 0)] or defs
                    base = cand[-1][1]
            if base is None:
                continue
            law = base
            if "시행령" in word or word == "영":
                law = registry.decree_of(base)
                if law is None:
                    continue
            out.append({"unit": uid, "law": law, "article": _art_label(m.group(2), m.group(3)), "via": "상대" if prior else "약칭(법)"})
    # 같은 (단위, 법령, 조)는 하나로
    seen, uniq = set(), []
    for r in out:
        k = (r["unit"], r["law"], r["article"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


def find_law_node(nodes: dict, name: str) -> str | None:
    """법령 이름(띄어쓰기 · 약칭 표기 차이 무시)으로 Law 노드 id를 찾는다."""
    key = sl(name)
    for nid, n in nodes.items():
        if n.get("type") == "Law" and sl(n.get("name", "")) == key:
            return nid
    return None


def find_law_article(nodes: dict, law_id: str, text: str) -> str | None:
    """개정문 · 질의에서 제N조를 읽어, 그 법령의 조 노드가 있으면 id를 돌려준다."""
    for m in re.finditer(ART_REF, text or ""):
        nid = f"LAWART:{law_id[4:]}:{_art_label(m.group(1), m.group(2))}"
        if nid in nodes:
            return nid
    return None


def alias_forms(nodes: dict, alias_index: dict[str, set[str]], phrase: str) -> set[str]:
    """phrase가 개체 이름이나 약칭이면, 같은 개체를 가리키는 다른 표기들(이름 + 약칭)을 돌려준다. 일반 상대 지칭은 쓰지 않는다."""
    key = sl(phrase)
    return {f for f in alias_index.get(key, set()) if sl(f) != key}
