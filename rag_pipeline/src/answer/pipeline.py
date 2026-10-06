"""질문 → 검색 → 근거 조립 → 답변 생성. 한 번 호출로 끝나는 진입점.

사용 예:
  from src.answer.pipeline import Assistant
  a = Assistant()
  res = a.ask("직위해제 기간 동안 급여는 얼마인가요?")
  print(res["answer"])
  a.close()
"""
import sys

from src.answer.generate import NO_ANSWER, Generator, flatten
from src.search.search import Searcher

sys.stdout.reconfigure(encoding="utf-8")


class Assistant:
    def __init__(self):
        self.searcher = Searcher()
        self.generator = Generator()

    def close(self):
        self.searcher.close()

    def ask(self, question, k=5, filters=None):
        results = self.searcher.search(question, k=k, filters=filters)
        assembled = self.searcher.assemble(results)
        entries = flatten(assembled)

        if not entries:
            # 근거가 하나도 없으면 모델을 부르지 않고 고정 문구로 답한다.
            answer = f"{NO_ANSWER}. 담당 부서에 확인해 주세요."
            return {"answer": answer, "cited": [], "warnings": [], "model": None,
                    "entries": [], "assembled": assembled, "results": results}

        out = self.generator.answer(question, entries)
        out.update({"entries": entries, "assembled": assembled, "results": results})
        return out


if __name__ == "__main__":
    a = Assistant()
    for q in sys.argv[1:] or ["직위해제 기간 동안 급여는 얼마인가요?"]:
        r = a.ask(q)
        print(f"\n[질문] {q}\n{r['answer']}")
        if r["warnings"]:
            print("⚠", r["warnings"])
        for i, e in enumerate(r["entries"], start=1):
            print(f"  [{i}] {e['문서명']} {e['article']} ({e['조문상태']}, {e.get('역할', '')})")
    a.close()
