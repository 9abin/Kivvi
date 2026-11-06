"""주제 개념 층 (규칙 기반 자동 초안, 사람 검토 전).

주제 하나 = 사전(TopicSpec): 종류(클래스), 속성(프로퍼티), 주제를 알리는 낱말. 인스턴스 = 조 · 별표.
조의 각 항에 종류 낱말과 속성 낱말이 함께 있으면 (종류, 속성)을 배정한다.
expand()는 바뀐 (종류, 속성)과 같은 배정의 다른 단위를 후보로 낸다. 추론은 계층 닫힘(공통 ⊃ 각 종류)과 규칙표뿐이고,
후보를 지우지 않고 더하기만 한다. 판정은 하지 않는다. 경로 이름은 `<prefix>_ATTR | <prefix>_COMMON | <prefix>_KIND`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TopicSpec:
    prefix: str                       # 경로 이름 머리말 (예: LEAVE)
    trigger: str                      # 주제를 알리는 낱말 (예: 휴직)
    common: str                       # 종류를 가리지 않는 조의 종류 이름
    kinds: dict = field(default_factory=dict)   # 종류 -> 낱말들
    attrs: dict = field(default_factory=dict)   # 속성 -> 낱말들
    extra_triggers: tuple = ()        # 주제 낱말이 여럿일 때 (예: 보수 · 연봉 · 수당)
    use_kind: bool = True             # 같은 종류 · 다른 속성(_KIND) 후보를 낼지 (주제별로 정밀도를 재서 정한다)
    min_mentions: int = 2             # 주제 낱말이 이만큼 나와야 그 주제를 다루는 조로 본다 (스쳐 지나가는 조 제외)

    @property
    def paths(self) -> tuple[str, str, str]:
        return (f"{self.prefix}_ATTR", f"{self.prefix}_COMMON", f"{self.prefix}_KIND")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _hits(spec: "TopicSpec", text: str) -> int:
    t = _norm(text)
    return sum(t.count(w) for w in (spec.trigger, *spec.extra_triggers))


def _focus(amendment: str, start_text: str) -> str:
    """개정문이 따옴표로 지목한 말이 든 항(없으면 조 전체)."""
    quoted = re.findall(r'"([^"]+)"', amendment or "")
    parts = re.split(r"(?=[①-⑩])|\n", start_text or "")
    for q in quoted[:1]:
        for p in parts:
            if _norm(q) and _norm(q) in _norm(p):
                return p
    return start_text or ""


class TopicLayer:
    def __init__(self, spec: TopicSpec, unit_text: dict[str, str]):
        self.spec = spec
        self.assign: dict[str, set[tuple[str, str]]] = {}
        self.by_attr: dict[str, set[str]] = defaultdict(set)
        for uid, text in unit_text.items():
            if _hits(spec, text) < spec.min_mentions:
                continue
            art_kinds = self.kinds_of(text)
            if not art_kinds:
                continue
            pairs: set[tuple[str, str]] = set()
            for par in re.split(r"(?=[①-⑩])|\n", text):
                if not _norm(par):
                    continue
                ks = self.kinds_of(par) if _hits(spec, par) else art_kinds   # 항 단위: 항에 주제 낱말이 없으면 조의 종류를 잇는다
                if spec.common not in ks and self.has_generic(par):
                    ks = ks + [spec.common]
                at = self.attrs_of(par)
                pairs |= {(k, a) for k in ks for a in at}
            if not pairs:
                pairs = {(k, "미분류") for k in art_kinds}
            self.assign[uid] = pairs
            for a in {a for _, a in pairs}:
                self.by_attr[a].add(uid)

    def kinds_of(self, text: str) -> list[str]:
        t = _norm(text)
        return [k for k, ws in self.spec.kinds.items() if any(w in t for w in ws)] or ([self.spec.common] if _hits(self.spec, t) else [])

    def has_generic(self, text: str) -> bool:
        """특정 종류 이름에 묶이지 않은 주제 낱말이 있는가 (예: "휴직의 기간 중에는")."""
        t = _norm(text)
        for ws in self.spec.kinds.values():
            for w in sorted(ws, key=len, reverse=True):
                t = t.replace(w, "")
        return bool(_hits(self.spec, t))

    def attrs_of(self, text: str) -> list[str]:
        t = _norm(text)
        return [a for a, ws in self.spec.attrs.items() if any(w in t for w in ws)]

    def expand(self, start: str, amendment: str = "", start_text: str = "") -> dict[str, list[str]]:
        """{단위: [경로]}. ATTR = 같은 종류 · 같은 속성, COMMON = 공통 조가 그 속성을 정함, KIND = 같은 종류 · 다른 속성."""
        a_path, c_path, k_path = self.spec.paths
        common = self.spec.common
        ks = set(self.kinds_of(start_text) or self.kinds_of(amendment))
        if not ks:
            return {}
        as_ = set(self.attrs_of(amendment)) or set(self.attrs_of(_focus(amendment, start_text)))
        out: dict[str, list[str]] = {}
        for uid, pairs in self.assign.items():
            if uid == start:
                continue
            ukinds = {k for k, _ in pairs}
            if not (ukinds & ks or common in ukinds or common in ks):
                continue
            uattrs = {a for _, a in pairs}
            if uattrs & as_ and ukinds & ks - {common}:
                out.setdefault(uid, []).append(a_path)
            elif uattrs & as_ and ks - {common}:
                out.setdefault(uid, []).append(c_path)
            elif self.spec.use_kind and ukinds & ks - {common}:
                out.setdefault(uid, []).append(k_path)
        return out
