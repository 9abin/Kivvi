"""2단계 LLM 리랭킹 (검색_설계.md 5절, config.LLM_RERANK, 2026.10.15).

교차 인코더(bge-reranker)가 매긴 상위 LLM_RERANK_TOP개를 LLM이 문서별로 독립 채점(pointwise, 0~10 정수)해 다시 정렬한다.
rank_trace 93문항에서 정답이 리랭크 창(30개) 안에는 있는데 6위 이하로 밀린 묶음이 34건(창 밖 12건)이라, 병목이
창이 아니라 리랭커의 판단이라고 보고 그 판단을 LLM으로 한 번 더 거른다.

- 입력 글자는 교차 인코더와 같은 Searcher.rerank_text(머리말 "[문서명 장 조항]" 포함)를 그대로 쓴다.
- 문서마다 호출 하나, 스레드 풀로 병렬. 호출 실패·점수 해석 실패 문서는 교차 인코더 자리를 그대로 지킨다(점수 None).
- 동점은 교차 인코더 순서를 유지하고, LLM_RERANK_TOP 밖 후보는 교차 인코더 순서 그대로 뒤에 둔다.
- 점수는 (모델, 질문, chunk_id) 키로 json 캐시(config.LLM_RERANK_CACHE)에 쌓아 평가를 다시 돌릴 때 호출을 줄인다.
"""
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

from config import LLM_RERANK_CACHE, LLM_RERANK_MODEL, LLM_RERANK_TOP

load_dotenv()

SYSTEM_PROMPT = """너는 사내 규정 검색의 채점기다. 직원의 질문과 규정 조문 하나가 주어진다. 이 조문이 질문에 답하는 근거로 얼마나 알맞은지 0~10 정수로 채점한다.

기준:
- 8~10: 질문의 핵심 요건(대상·조건·금액·기한·절차 등 질문이 실제로 묻는 것)을 이 조문이 직접 규정한다.
- 3~7: 같은 제도·주제를 다루지만 질문이 묻는 요건은 다른 조문에 있거나, 보충·예외·절차처럼 간접적으로만 관련된다.
- 0~2: 질문과 무관하거나 말만 겹친다 (목적·정의·용어 설명, 다른 대상·다른 제도).

조문 앞의 [문서명 조항]은 어느 규정의 조문인지 알려 주는 머리말이다. 조문에 없는 내용을 추측하지 않는다.
출력은 숫자 하나만 쓴다 (설명 없이). 예: 7"""

_INT = re.compile(r"\d+")


def parse_score(raw):
    """응답에서 0~10 정수 하나. 없으면 None. "7점", "점수: 7", '{"점수": 7}'도 받는다."""
    m = _INT.search(raw or "")
    if not m:
        return None
    v = int(m.group(0))
    return v if 0 <= v <= 10 else None


def reorder(cands, scores):
    """scores[i]가 None인 후보는 원래 자리(교차 인코더 순서)를 지키고, 점수가 있는 후보들만 그 빈자리에
    (점수 내림차순, 동점은 원래 순서) 채운다. 모두 None이면 입력 순서 그대로다."""
    scored = sorted((i for i, s in enumerate(scores) if s is not None), key=lambda i: (-scores[i], i))
    out, it = [None] * len(cands), iter(scored)
    for i, s in enumerate(scores):
        if s is None:
            out[i] = cands[i]
    for i in range(len(out)):
        if out[i] is None:
            out[i] = cands[next(it)]
    return out


class LLMReranker:
    def __init__(self, model=LLM_RERANK_MODEL, top=LLM_RERANK_TOP, cache_path=LLM_RERANK_CACHE, workers=8):
        self.model_name, self.top, self.cache_path = model, top, cache_path
        self.llm = None  # 첫 호출 때 만든다 (캐시만으로 끝나면 모델 객체가 필요 없다)
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.lock = threading.Lock()
        self.cache = self._load_cache()
        self.calls = 0  # 기록용: 실제 API 호출 수 (캐시 적중 제외)

    # 캐시 ------------------------------------------------------------------
    def _load_cache(self):
        if self.cache_path and os.path.exists(self.cache_path):
            try:
                return json.load(open(self.cache_path, encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_cache(self):
        if not self.cache_path:
            return
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        tmp = self.cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, ensure_ascii=False, indent=0)
        os.replace(tmp, self.cache_path)

    def _key(self, question, chunk_id):
        return f"{self.model_name}\t{question}\t{chunk_id}"

    # 호출 ------------------------------------------------------------------
    def _ask(self, question, text):
        """모델 응답 글자. 실패하면 None. 시험에서는 이 메서드를 바꿔 끼운다."""
        try:
            if self.llm is None:
                from langchain_openai import ChatOpenAI
                self.llm = ChatOpenAI(model=self.model_name, temperature=0)
            out = self.llm.invoke([("system", SYSTEM_PROMPT), ("human", f"질문: {question}\n\n조문:\n{text}")])
            return out.content if isinstance(out.content, str) else str(out.content)
        except Exception:  # noqa: BLE001 - 어떤 실패든 그 문서만 교차 인코더 자리로 폴백
            return None

    def score(self, question, chunk_id, text):
        """0~10 정수 또는 None. 캐시에 있으면 호출하지 않는다 (실패(None)는 캐시하지 않는다)."""
        key = self._key(question, chunk_id)
        with self.lock:
            if key in self.cache:
                return self.cache[key]
        with self.lock:
            self.calls += 1
        v = parse_score(self._ask(question, text))
        if v is not None:
            with self.lock:
                self.cache[key] = v
        return v

    def rerank(self, question, cands, text_of):
        """cands: 교차 인코더 순서의 후보(dict). text_of(chunk_id) -> 채점 글자(Searcher.rerank_text).
        앞 top개를 재채점해 정렬하고 각 후보에 "llm_rerank"(점수 또는 None)를 붙인다. 뒤는 그대로."""
        head, tail = cands[: self.top], cands[self.top:]
        if not head:
            return cands
        jobs = [self.pool.submit(self.score, question, c["chunk_id"], text_of(c["chunk_id"])) for c in head]
        scores = [j.result() for j in jobs]
        for c, s in zip(head, scores):
            c["llm_rerank"] = s
        for c in tail:
            c["llm_rerank"] = None
        if any(s is not None for s in scores):
            with self.lock:
                self._save_cache()
        return reorder(head, scores) + tail
