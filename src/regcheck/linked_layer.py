"""연결 표 주제 층: 사람이 만든 「조 · 개념 · 속성」 연결을 후보 수집에 꽂는다 (예: 팀의 휴직 온톨로지).

입력은 scripts/extract_hyujik_links.py가 외부 DB에서 뽑은 JSONL(규정 원문 · 설명 문장 없음). 첫 줄 meta(개념 위계 포함), 이후 한 줄에 연결 하나:
  {"item": "24024:27-1-1-0", "article": "24024:27", "concept": "명령휴직", "rel": "정의|근거|언급", "attrs": ["보수"], "decided_by": "사용자|자동·확정|초안"}
topic_layer.TopicLayer와 같은 인터페이스(expand)라 collect(leave=...)에 그대로 들어간다. 후보를 더하기만 하고 판정하지 않는다.

경로 이름: <prefix>_ATTR(같은 개념 · 같은 속성, 확인 권장), <prefix>_UP(개념 위계로 이은 같은 속성, 참고), <prefix>_KIND(같은 개념 · 다른 속성, 참고 — 기본 꺼짐).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

TRUSTED = {"사용자", "자동·확정"}   # 나머지(초안)는 사람 확정 전


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


class LinkLayer:
    def __init__(self, path: str | Path, prefix: str = "HYUJIK", hierarchy: bool = False, same_concept_any_attr: bool = False,
                 trusted_only: bool = False, item_text: dict[str, str] | None = None):
        rows = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
        meta = rows[0]["meta"] if rows and "meta" in rows[0] else {}
        self.meta = meta
        self.parent: dict[str, str | None] = dict(meta.get("concepts") or {})
        self.prefix = prefix
        self.hierarchy = hierarchy
        self.any_attr = same_concept_any_attr
        self.links = [r for r in rows if "item" in r and (not trusted_only or r.get("decided_by") in TRUSTED)]
        self.by_article: dict[str, list[dict]] = defaultdict(list)
        for r in self.links:
            self.by_article[r["article"]].append(r)
        self.item_text = item_text or {}   # 항 단위 id -> 본문. 없으면 바뀐 항을 못 짚고 조의 모든 연결을 쓴다

    @property
    def paths(self) -> tuple[str, str, str]:
        return (f"{self.prefix}_ATTR", f"{self.prefix}_UP", f"{self.prefix}_KIND")

    def covers(self, article: str) -> bool:
        return article in self.by_article

    def _related(self, concept: str) -> set[str]:
        out = {concept}
        if self.hierarchy:
            if self.parent.get(concept):
                out.add(self.parent[concept])
            out |= {c for c, p in self.parent.items() if p == concept}
        return out

    def _focus(self, mine: list[dict], amendment: str) -> list[dict]:
        quoted = [_norm(q) for q in re.findall(r'"([^"]+)"', amendment or "") if _norm(q)]
        if quoted and self.item_text:
            hit = [r for r in mine if any(q in _norm(self.item_text.get(r["item"], "")) for q in quoted)]
            if hit:
                return hit
        return mine

    def expand(self, start: str, amendment: str = "", start_text: str = "") -> dict[str, list[str]]:
        mine = self.by_article.get(start)
        if not mine:
            return {}
        a_path, up_path, k_path = self.paths
        out: dict[str, list[str]] = {}
        for r in self._focus(mine, amendment):
            if r["rel"] == "언급":
                continue
            concept, attrs = r["concept"], set(r.get("attrs") or [])
            rel_concepts = self._related(concept)
            for o in self.links:
                if o["article"] == start or o["rel"] != "근거":
                    continue
                shared = attrs & set(o.get("attrs") or [])
                if o["concept"] == concept and shared:
                    out.setdefault(o["article"], []).append(a_path)
                elif o["concept"] in rel_concepts and o["concept"] != concept and shared:
                    out.setdefault(o["article"], []).append(up_path)
                elif self.any_attr and o["concept"] == concept:
                    out.setdefault(o["article"], []).append(k_path)
        return {k: sorted(set(v)) for k, v in out.items()}


def item_texts(regs: dict[str, dict]) -> dict[str, str]:
    """연결 표의 항 단위 id(조 id-항-호-목)로 우리 파싱 결과의 항목 본문을 찾는 표."""
    out: dict[str, str] = {}
    for r in regs.values():
        for a in r["articles"]:
            for it in a["items"]:
                out[f"{a['id']}-{'-'.join(str(x) for x in it['path'])}"] = it["norm"]
    return out
