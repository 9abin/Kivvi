"""후보 state: 후보 하나가 파이프라인을 지나며 쌓는 기록 (리포트 93).

  states = build_states(cands)          # 수집 결과 -> state. 출처(source)와 경로(route)를 코드가 고정한다
  s.source  = "rule" | "graph" | "rag"   # 어디서 찾았나
  s.route   = "decide" | "luna"          # 다음에 누가 거르나: 규칙 = 판단 모델, 그래프 · RAG = luna
  s.rerank / s.evidence / s.answer       # 각 단계가 자기 칸만 채운다. 칸마다 누가 채웠는지(by)를 남긴다

원칙
- source · paths · route는 코드만 쓴다. LLM은 읽기만 한다.
- 할 일은 state 칸을 보고 코드가 정한다. LLM에게 「이 후보를 다룰지」 묻지 않는다.
- LLM이 쓴 인용은 원문 대조를 통과해야 quote_verified=True가 되고, 통과한 것만 다음 단계로 넘어간다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

RAG_PATHS = ("RAG", "RAG_NEW")
GRAPH_PREFIXES = ("GRAPH_", "SCOPED_")   # 도메인 그래프(한석희 scoped) 경로 이름
# 도메인 그래프만 찾은 후보 중 luna에 넘기는 연결 (261111 val · 최종 100: 정답은 이 두 연결에서만 나옴 = 이름 없는 위임 · 준용).
# 나머지(위임 out 2,773개 · 언급 in 661개 · 개념 370개)는 정답 0. 도메인 그래프는 구조 연결이지 충돌(같은 사항) 관계가 아니다
GRAPH_ONLY_EDGES = ("regulation_delegation_in", "specific_reference_out")

# 경로 이름 -> 사람이 읽는 「어떻게 찾았나」 (리랭커 입력 · 화면용 사실. 단계 · 점수는 넣지 않는다)
FOUND_BY = {
    "CITES_in": "고쳐지는 조를 번호로 인용함",
    "CITES_internal": "함께 번호가 바뀌는 다른 조를 번호로 인용함",
    "STALE_CITE": "옛 번호를 그대로 인용하고 있을 수 있음",
    "USES_TERM": "정의가 바뀌는 용어를 씀",
    "TERM_CORE": "정의가 바뀌는 용어의 핵심 부분이 나옴",
    "PHRASE": "개정으로 새로 들어간 말이 나옴",
    "OLD_PHRASE": "개정으로 빠지는 말이 나옴",
    "MENTIONS_TITLE": "고쳐지는 조의 제목이 본문에 나옴",
    "CO_CITE": "고쳐지는 조가 인용하는 조를 함께 인용함",
    "CITE_MENTION": "고쳐지는 조가 인용하는 조의 제목을 언급함",
    "CO_MENTION": "고쳐지는 조와 같은 조 제목을 언급함",
    "DELETED_TERM": "개정으로 빠지는 낱말이 나옴",
    "CITES_CHAPTER_in": "고쳐지는 조가 속한 장 · 절을 인용함",
    "CITES_out": "고쳐지는 조가 이 조를 번호로 인용함",
    "CITES_REG": "고쳐지는 규정을 이름으로 가리킴",
    "MENTIONS_TITLE_out": "고쳐지는 조가 이 조의 제목을 언급함",
    "MENTIONS_STEM": "고쳐지는 조의 제도 이름이 본문에 나옴",
    "MENTIONS_FORM": "고쳐지는 조가 이 서식을 언급함",
    "SAME_VALUE": "같은 값(금액 · 기간 등)을 정함",
    "SAME_TITLE": "다른 규정에 같은 제목의 조가 있음",
    "DEF_DIFF": "같은 용어를 다르게 정의함",
    "SHARED_ATTACH": "같은 별표 · 서식을 함께 씀",
    "RAG": "의미 검색으로 찾음",
    "RAG_NEW": "의미 검색으로 찾음",
    "VECTOR": "벡터 의미 검색",
    "BM25": "BM25 키워드 검색",
    "GRAPH_CONCEPT": "도메인 그래프에서 연결된 개념",
    "GRAPH_specific_reference_in": "도메인 그래프: 고쳐지는 조를 인용함",
    "GRAPH_specific_reference_out": "도메인 그래프: 고쳐지는 조가 인용함",
    "GRAPH_regulation_delegation_in": "도메인 그래프: 고쳐지는 규정에 맡김",
    "GRAPH_regulation_delegation_out": "도메인 그래프: 고쳐지는 조가 이 규정에 맡김",
    "GRAPH_definition_use": "도메인 그래프: 정의된 용어를 씀",
    "GRAPH_schema_concept_attribute": "도메인 그래프: 같은 개념 · 속성을 다룸",
}


def path_base(p: str) -> str:
    return p.split(":")[0]


def source_of(paths: list[str]) -> str:
    """규칙 경로가 하나라도 있으면 rule (근거가 코드로 확실). 아니면 그래프, 아니면 RAG."""
    base = [path_base(p) for p in paths]
    if any(b not in RAG_PATHS and not b.startswith(GRAPH_PREFIXES) for b in base):
        return "rule"
    if any(b.startswith(GRAPH_PREFIXES) for b in base):
        return "graph"
    return "rag"


ROUTE = {"rule": "decide", "graph": "luna", "rag": "luna"}


def found_by(paths: list[str]) -> list[str]:
    out = []
    for p in paths:
        b = path_base(p)
        key = "GRAPH_" + b[len("SCOPED_"):] if b.startswith("SCOPED_") else b
        out.append(FOUND_BY.get(key, f"경로 {b}"))
    return list(dict.fromkeys(out))


@dataclass
class CandState:
    id: str
    source: str
    route: str
    paths: list[str]
    tier: str
    order: int                                            # 수집 순서 (0부터). 같은 출처 안의 검색 순위로 쓴다
    rerank: dict = field(default_factory=dict)            # {"by", "kept", "rank", "score"?, "relation"?}
    evidence: dict = field(default_factory=dict)          # {"by", "cause", "effect", "quote", "quote_verified", "source_quote", "reason"}
    answer: dict = field(default_factory=dict)            # {"by", "text", "checked"}
    log: list[str] = field(default_factory=list)          # 단계 기록 (사람이 추적용)

    def to_dict(self) -> dict:
        return asdict(self)


def build_states(cands: list) -> list[CandState]:
    """collect.Candidate 목록 -> state 목록. 순서는 그대로."""
    out = []
    for i, c in enumerate(cands):
        src = source_of(c.paths)
        s = CandState(id=c.id, source=src, route=ROUTE[src], paths=list(c.paths), tier=c.tier, order=i)
        s.log.append(f"수집: {src} ({', '.join(dict.fromkeys(path_base(p) for p in c.paths))})")
        out.append(s)
    return out


def by_route(states: list[CandState], route: str) -> list[CandState]:
    return [s for s in states if s.route == route]
