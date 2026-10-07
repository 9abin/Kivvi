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

from config import ANSWER_NONE_BELOW, ANSWER_PARTIAL_BELOW, PARTIAL_SHOW
from src.answer.generate import (Generator, flatten, order_entries, revision_notes,
                                 state_notes)
from src.search.langchain_retriever import RegulationRetriever, document_to_entry
from src.search.search import Searcher

sys.stdout.reconfigure(encoding="utf-8")

NONE_MESSAGE = "제공된 규정에서 이 질문과 관련된 내용을 찾을 수 없습니다. 담당 부서에 확인해 주세요."


def decide_mode(results, top_score):
    """점수 구간을 정한다. 정확 조회 결과가 있으면 기준을 적용하지 않는다 (조문이 확정된 경우)."""
    if any(r.get("match") == "정확조회" for r in results):
        return "answer"
    if top_score is None:
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
    def __init__(self):
        self.searcher = Searcher()
        self.generator = Generator()

    def close(self):
        self.searcher.close()

    def _retrieve(self, question, k, filters):
        retriever = RegulationRetriever(searcher=self.searcher, k=k, filters=filters)
        return [document_to_entry(d) for d in retriever.invoke(question)]

    def ask(self, question, k=5, filters=None):
        results = self._retrieve(question, k, filters)
        assembled = self.searcher.assemble(results)
        entries = order_entries(flatten(assembled))  # 현행 먼저, 부칙·삭제는 뒤로

        scored = [r["rerank"] for r in results if r.get("rerank") is not None]
        top_score = max(scored) if scored else None
        mode = decide_mode(results, top_score)

        base = {"mode": mode, "top_score": top_score, "entries": entries, "assembled": assembled,
                "results": results, "conflicts": [], "cited": [], "warnings": [], "model": None,
                "unsupported": []}

        if mode == "none":
            return {**base, "answer": NONE_MESSAGE, "entries": []}

        if mode == "partial":
            related = [r for r in results if r.get("match") != "정확조회"][:PARTIAL_SHOW]
            related = [{**e, "역할": "관련 조문"} for e in related]
            return {**base, "answer": partial_answer(related), "entries": related}

        out = self.generator.answer(question, entries)

        # 문장 단위 근거 검사: 모델이 쓴 각 불릿이 인용한 근거 본문과 맞는지 다시 확인한다 (답변_설계.md 10절).
        unsupported = self.generator.verify(question, out["answer"], entries)
        if unsupported:
            out["warnings"] = out["warnings"] + [f"근거와 맞지 않을 수 있음: {s}" for s in unsupported]

        notes = state_notes(entries, out["cited"]) + revision_notes(entries, out["cited"])
        if notes:
            out["answer"] = out["answer"] + "\n\n" + "\n".join(notes)

        pairs = self.generator.conflicts(question, entries)
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
