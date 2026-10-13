"""질문 → (LangChain 리트리버) 검색 → 근거 조립 → 점수 구간 판정 → 답변 생성 → 충돌 확인 → 문장 근거 검사.

답변 모드 (리랭커 1위 점수 기준, config.py의 임계값)
  answer  : 일반 답변 (점수 높음, 또는 정확 조회)
  partial : 질문의 핵심은 찾지 못함. 관련 조문만 고정 형식으로 보여줌 (모델을 부르지 않음)
  none    : 관련 내용이 없음. 고정 문구 (모델을 부르지 않음)

검색기(Searcher)는 LangChain `BaseRetriever`(RegulationRetriever)로 감싸서 부른다. 내부 로직은
`src/search/search.py` 그대로이고, 이 파일은 LangChain 호출 형태로만 바꾼다 (`답변_설계.md` 9절).

사용 예:
  from src.answer.pipeline import Assistant
  a = Assistant()
  res = a.ask("직위해제 기간 동안 급여는 얼마인가요?")
  print(res["answer"])
  a.close()
"""
import sys

from config import ANSWER_NONE_BELOW, ANSWER_PARTIAL_BELOW, PARTIAL_SHOW, QUERY_PLAN
from src.answer.generate import (Generator, flatten, order_entries, revision_notes,
                                 state_notes)
from src.search.glossary import filter_by_glossary
from src.answer.guard import (contradicted_distinctions, filter_tables_by_scope, missing_terms,
                              unsupported_numbers)
from src.search.langchain_retriever import RegulationRetriever, document_to_entry
from src.search.search import Searcher

sys.stdout.reconfigure(encoding="utf-8")

NONE_MESSAGE = "제공된 규정에서 이 질문과 관련된 내용을 찾을 수 없습니다. 담당 부서에 확인해 주세요."


def decide_mode(results, top_score):
    """점수 구간을 정한다. 정확 조회 결과가 있으면 기준을 적용하지 않는다 (조문이 확정된 경우)."""
    if not results:
        # 검색 결과 자체가 없으면(필터가 모두 걸러낸 경우 등) 근거가 없는 것이므로 모델을 부르지 않는다.
        return "none"
    if any(r.get("match") == "정확조회" for r in results):
        return "answer"
    if top_score is None:
        # 결과는 있는데 리랭커 점수가 전혀 없는 경우(정상적으로는 거의 없음). 안전하게 일반 답변으로 둔다.
        return "answer"
    if top_score < ANSWER_NONE_BELOW:
        return "none"
    if top_score < ANSWER_PARTIAL_BELOW:
        return "partial"
    return "answer"


def partial_answer(related):
    """애매함 구간의 고정 문구. 모델을 부르지 않으므로 추측이 들어가지 않는다."""
    lines = ["질문하신 내용은 제공된 규정에서 찾을 수 없었습니다. 다만 관련된 조문은 다음과 같습니다."]
    for i, e in enumerate(related, start=1):
        snippet = e["body"].replace("\n", " ")[:60]
        lines.append(f"- [{i}] {e['문서명']} {e['article']}: {snippet}…")
    lines.append("정확한 내용은 담당 부서에 확인해 주세요.")
    return "\n".join(lines)


class Assistant:
    def __init__(self, scope_filter=True, use_glossary=True, use_plan=QUERY_PLAN):
        self.use_glossary = use_glossary  # 용어집의 수치 근거 제외 (search/glossary.py). 전후 비교를 위해 끌 수 있다
        self.scope_filter = scope_filter  # 표 범위 필터 (guard.py). 전후 비교를 위해 끌 수 있다
        self.use_plan = use_plan  # 질문 나누기 (search_plan). 끄면 질문 한 줄 + 바꿔 쓴 검색어로 검색한다. 전후 비교용
        self.last_plan = None
        self.searcher = Searcher()
        self.generator = Generator()

    def close(self):
        self.searcher.close()

    def _retrieve(self, question, k, filters):
        self.searcher.glossary_filter = self.use_glossary
        if self.use_plan:
            # 질문을 하위 질문으로 나눠 각각 검색한다 (search_plan). 나누지 않는 질문은 지금까지와 같은 검색이다.
            self.last_plan = self.generator.plan_queries(question)
            retriever = RegulationRetriever(searcher=self.searcher, k=k, filters=filters, plan=self.last_plan)
        else:
            self.last_plan = None
            retriever = RegulationRetriever(searcher=self.searcher, k=k, filters=filters,
                                            rewritten=self.generator.rewrite_query(question))
        return [document_to_entry(d) for d in retriever.invoke(question)]

    def _check(self, question, answer, entries):
        """(근거 밖 문장, 검사 성공 여부, 질문에는 있고 근거에는 없는데 답에 쓰인 말)."""
        unsupported, ok = self.generator.verify(question, answer, entries)
        evidence = [f"{e['문서명']} {e['body']}" for e in entries]
        return unsupported, ok, missing_terms(self.searcher.kiwi, question, evidence, answer)

    def ask(self, question, k=5, filters=None):
        results = self._retrieve(question, k, filters)
        assembled = self.searcher.assemble(results)
        entries = order_entries(flatten(assembled))  # 현행 먼저, 부칙·삭제는 뒤로
        scope_excluded = []
        if self.scope_filter:
            entries, scope_excluded = filter_tables_by_scope(question, entries)
        # 용어집 제외: 후보 단계(Searcher)에서 뺀 것과, 형제 항 확장 뒤에 한 번 더 거른 것을 모두 기록한다
        glossary_excluded = [(self.searcher.documents.get(d, {}).get("title") or d, a, r)
                             for d, a, r in self.searcher.last_glossary_excluded] if self.use_glossary else []
        if self.use_glossary:
            entries, late = filter_by_glossary(question, entries, self.searcher.glossary)
            glossary_excluded += late

        scored = [r["rerank"] for r in results if r.get("rerank") is not None]
        top_score = max(scored) if scored else None
        mode = decide_mode(results, top_score)
        if mode == "answer" and not entries:
            # results는 있었지만 근거 조립 후 항목이 0개인 경우(글자 예산 등). 근거 없이 모델을 부르지 않는다.
            mode = "none"

        base = {"mode": mode, "top_score": top_score, "entries": entries, "assembled": assembled,
                "results": results, "conflicts": [], "cited": [], "warnings": [], "model": None,
                "unsupported": [], "scope_excluded": scope_excluded, "glossary_excluded": glossary_excluded,
                "search_hint": self.searcher.last_hint, "plan": self.last_plan}

        if mode == "none":
            return {**base, "answer": NONE_MESSAGE, "entries": []}

        if mode == "partial":
            related = [r for r in results if r.get("match") != "정확조회"][:PARTIAL_SHOW]
            related = [{**e, "역할": "관련 조문"} for e in related]
            return {**base, "answer": partial_answer(related), "entries": related}

        out = self.generator.answer(question, entries)

        # 문장 단위 근거 검사: 모델이 쓴 각 불릿이 인용한 근거 본문과 맞는지 다시 확인한다 (답변_설계.md 10절).
        # ok=False(응답을 해석하지 못함)와 "검사 통과"(빈 목록)를 구분해서 각각 경고한다.
        unsupported, verify_ok, missing = self._check(question, out["answer"], entries)

        # 숫자·단위가 인용한 근거에 없으면(코드 검사, 오탐이 거의 없음) 답을 내지 않고 관련 조문만 보여 준다.
        bad_numbers = unsupported_numbers(question, out["answer"], entries)
        bad_dims = contradicted_distinctions(out["answer"], entries)
        if bad_numbers or bad_dims:
            related = [{**e, "역할": "관련 조문"} for e in entries[:PARTIAL_SHOW]]
            reasons = []
            if bad_numbers:
                reasons.append("답의 숫자(" + ", ".join(n for nums, _ in bad_numbers for n in nums) + ")가 근거 본문에 없어")
            if bad_dims:
                reasons.append("표가 '" + bad_dims[0][1] + " 구분 없이'라고 정한 '" + bad_dims[0][0] + "'을(를) 답이 " + bad_dims[0][1] + "으로 설명해")
            return {**base, "mode": "partial", "answer": partial_answer(related), "entries": related,
                    "warnings": [" ".join(reasons) + " 답 대신 관련 조문만 보여 줍니다."],
                    "withheld": out["answer"]}

        # 검사 결과로 답을 막거나 다시 쓰는 방식은 실측에서 정상 답변 29건 중 22건을 막아서 경고만 붙이는 방식으로 되돌렸다.
        # 검사 모델이 "맞습니다. …" 같은 결론 문장과 맞는 문장까지 근거 밖으로 자주 표시한다 (정밀도 낮음).
        if not verify_ok:
            out["warnings"] = out["warnings"] + ["문장 근거 검사 실패: 검사 모델 응답을 해석하지 못해 확인을 건너뛰었습니다."]
        elif unsupported:
            out["warnings"] = out["warnings"] + [f"근거와 맞지 않을 수 있음: {s}" for s in unsupported]
        # 질문의 핵심 말이 근거에 없는데 답이 그 말로 답했으면 주제가 바뀌었을 수 있다 (예: 질문 계약보증금, 근거 임차보증금).
        out["warnings"] = out["warnings"] + [f'질문의 "{t}"이(가) 근거 본문에 없음: 비슷한 다른 항목으로 답했을 수 있음' for t in missing]

        if glossary_excluded:
            out["warnings"] = out["warnings"] + [f"용어집: {m} {a}를 근거에서 제외({r})" for m, a, r in glossary_excluded]
        if scope_excluded:
            out["warnings"] = out["warnings"] + [f"표 범위 필터: {m} {a}({t} 대상)는 {r}이라 근거에서 제외" for m, a, t, r in scope_excluded]

        notes = state_notes(entries, out["cited"]) + revision_notes(entries, out["cited"])
        if notes:
            out["answer"] = out["answer"] + "\n\n" + "\n".join(notes)

        pairs, conflicts_ok = self.generator.conflicts(question, entries)
        if not conflicts_ok:
            out["warnings"] = out["warnings"] + ["충돌 확인 실패: 검사 모델 응답을 해석하지 못해 확인을 건너뛰었습니다."]
        pairs = [p for p in pairs if not _same_article(entries[p[0] - 1], entries[p[1] - 1])]
        conflicts = [{"pair": (a, b), "items": (entries[a - 1], entries[b - 1])} for a, b in pairs]
        return {**base, **out, "conflicts": conflicts, "unsupported": unsupported}


def _same_article(a, b):
    """같은 문서의 같은 조에 속한 두 항은 충돌이 아니라 같은 조의 보완 내용이다."""
    return a["doc_id"] == b["doc_id"] and a["article"].split(" ")[0] == b["article"].split(" ")[0]


if __name__ == "__main__":
    a = Assistant()
    for q in sys.argv[1:] or ["직위해제 기간 동안 급여는 얼마인가요?"]:
        r = a.ask(q)
        score = f"{r['top_score']:.3f}" if r["top_score"] is not None else "-"
        print(f"\n[질문] {q}  (모드: {r['mode']}, 점수: {score})\n{r['answer']}")
        for c in r["conflicts"]:
            x, y = c["items"]
            print(f"  ⚠ 충돌 가능: [{c['pair'][0]}] {x['문서명']} {x['article']} / [{c['pair'][1]}] {y['문서명']} {y['article']}")
        for w in r.get("warnings", []):
            print("  ⚠", w)
    a.close()
