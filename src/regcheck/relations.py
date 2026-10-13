"""관련 조항 표: 영향 분석에 쓰는 관계를 한 형식으로 모은다.

관계 하나 = 출발 조항 → 대상(조항·장·규정·법령·별표), 종류, 근거 문구, 출처, 신뢰도, 검증 상태, 원문 버전.
- 근거 문구가 없는 관계는 만들지 않는다 (원문에 있는 표현만)
- 출처: rule(규칙) | embedding | llm | human
- 신뢰도: 확실(필수 점검에 사용) | 후보(참고로만 표시)
- 검증 상태: 자동 | 의심(구조 검사에서 걸림) | 확인됨 | 기각 (사람 결정은 덮어쓰지 않고 보존)
- 원문 버전: 출발·대상 규정의 개정일. 규정이 개정되면 그 규정에서 나가는 관계는 다시 만든다.
리포트: docs/reports/261006-*-11-relations.md
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

# 인용 엣지 상태 → 검증 상태 (구조 검사에서 문제가 보이면 '의심')
SUSPECT = {"missing_article", "missing_item", "missing_chapter", "missing_appendix", "deleted_target",
           "title_mismatch", "name_mismatch", "unknown_regulation"}
KIND = {"article": "인용", "chapter": "인용", "regulation": "문서 인용·위임", "law": "법령 인용", "appendix": "별표·별지 인용"}


@dataclass
class Relation:
    id: str
    src: str  # 출발 단위 ID (항·호 단위)
    src_article: str  # 출발 조 ID
    src_kind: str  # 본문 | 부칙 | 별표 | 별지
    target: str  # 대상 ID (조·장·규정ID) 또는 법령명
    target_type: str  # article | chapter | regulation | law | appendix
    kind: str  # 인용 | 문서 인용·위임 | 법령 인용 | 별표·별지 인용 (예외·정의-사용·같은 기준은 추가 예정)
    evidence: str  # 원문 표현
    context: str  # 근거 문맥
    source: str  # rule | embedding | llm | human
    confidence: str  # 확실 | 후보
    verification: str  # 자동 | 의심 | 확인됨 | 기각
    check_status: str  # 인용 엣지의 구조 검사 결과 (citations.STATUS)
    note: str
    src_version: str
    target_version: str
    run: str = ""                # 실행 기록 ID (out/runs/, 역추적용)

    def to_dict(self) -> dict:
        return asdict(self)


def from_edges(edges: list[dict], regs: dict[str, dict]) -> list[Relation]:
    rels = []
    for n, e in enumerate(edges):
        if e["status"] in ("law_pending", "external_rule"):
            ref = e.get("target_ref") or {}
            target = e["target_doc"] + (f" 제{ref['jo']}조" + (f"의{ref['br']}" if ref.get("br") else "") if ref.get("jo") else "")
        else:
            target = e["target_id"] or e["target_reg"] or e["target_doc"]
        suspect = e["status"] in SUSPECT
        rels.append(Relation(
            id=f"R{n:05d}",
            src=e["src"],
            src_article=e["src"].rsplit("-", 3)[0] if e["src_kind"] in ("본문", "부칙") else e["src"],
            src_kind=e["src_kind"],
            target=target,
            # 「○○법 제N조」는 대상 유형이 조여도 법령 인용으로 분류 (법률 DB와 연결될 관계)
            target_type="law" if e["status"] in ("law_pending", "external_rule") else e["target_type"],
            kind="법령 인용" if e["status"] in ("law_pending", "external_rule") else KIND.get(e["target_type"], e["target_type"]),
            evidence=e["raw"],
            context=e["context"],
            source="rule",
            # 부칙은 옛 번호·옛 문구가 많아 영향 분석의 필수 점검에는 쓰지 않는다
            confidence="후보" if e["src_kind"] == "부칙" else "확실",
            verification="의심" if suspect else "자동",
            check_status=e["status"],
            note=e["note"],
            src_version=regs.get(e["src_reg"], {}).get("revised", ""),
            target_version=regs.get(e["target_reg"], {}).get("revised", "") if e.get("target_reg") else "",
        ))
    return rels


def apply_decisions(rels: list[Relation], decisions: dict[str, dict]) -> None:
    """사람의 확인 결과(키: 출발 단위|근거 표현)를 덮어쓰지 않고 반영한다. 다시 만들어도 결정은 유지된다."""
    for r in rels:
        d = decisions.get(f"{r.src}|{r.evidence}")
        if d:
            r.verification = d["verification"]
            if d.get("intended_target"):
                r.note = f"의도 대상: {d['intended_target']}; {r.note}"


def save(rels: list[Relation], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "relations.jsonl").open("w", encoding="utf-8") as f:
        for r in rels:
            f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")


class Related:
    """조항 ID로 관련 조항을 조회한다 (인용함 / 인용받음)."""

    def __init__(self, rels: list[dict]):
        self.out: dict[str, list[dict]] = {}
        self.inb: dict[str, list[dict]] = {}
        for r in rels:
            if r["verification"] == "기각":
                continue
            self.out.setdefault(r["src_article"], []).append(r)
            tgt = r["target"]
            self.inb.setdefault(tgt, []).append(r)
            if r["target_type"] in ("article", "chapter") and ":" in tgt:  # 규정 단위 조회도 되게
                self.inb.setdefault(tgt.split(":")[0], []).append(r)

    def lookup(self, article_id: str, include_candidates: bool = False) -> dict[str, list[dict]]:
        keep = (lambda r: True) if include_candidates else (lambda r: r["confidence"] == "확실")
        reg_id = article_id.split(":")[0]
        return {
            "인용함": [r for r in self.out.get(article_id, []) if keep(r)],
            "인용받음": [r for r in self.inb.get(article_id, []) if keep(r)],
            # 문서 단위로 이 규정 전체를 가리키는 관계 (「인사규정」이 정한 바에 따른다)
            "규정 단위로 인용받음": [r for r in self.inb.get(reg_id, []) if keep(r) and r["target_type"] == "regulation"],
        }
