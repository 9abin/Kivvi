"""채팅: 가빈 답변 파이프라인(src/answer/pipeline.py Assistant)을 그대로 쓰고 LLM 호출만 바꾼다.

검색 설정은 config.py가 정한다 (261019 최종, 리포트 59): Qwen3-Embedding-4B(Ollama) + bge-reranker-v2-m3, 가중치 0.7,
리랭크 창 30, 근거 10(TOP_K), 바꿔 쓰기 끔, LLM 선택 끔, 항 단위 색인에서 찾고 조 전체를 넘김(RETURN_ARTICLE).
모델: 환경변수 CHAT_MODEL (기본 gpt-5.6-luna, API, 261018). 「gpt-」로 시작하면 API(.env의 OPENAI_API_KEY), 아니면 로컬 Ollama
(예: qwen3.5:9b — 문맥 16k, think 끔, temperature 0). 폐쇄망 운영은 로컬 모델로 되돌린다.
문장 근거 검사(Generator.verify)·충돌 확인(Generator.conflicts):
  - API 모델: 켬. 실험(리포트 59)의 답변 · 근거 검사를 둘 다 luna로 잰 구성과 같게
  - 로컬 모델(LOCAL_FAST): 끔. 9B·30B 모두 맞는 문장까지 거의 전부 「근거와 맞지 않을 수 있음」으로 표시해(261013 실측,
    out/llm_compare) 경고가 의미가 없고 느려진다. CHAT_FULL_CHECKS=1이면 로컬에서도 켬.
    숫자·구분 모순 검사(코드)와 빠진 핵심어 검사는 늘 그대로 둔다.
GPU 하나를 검색 모델과 같이 쓰므로 질문은 한 번에 하나씩(lock) 처리한다.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = os.environ.get("CHAT_MODEL", "gpt-5.6-luna")   # 261018 API로 바꿈 (로컬 9B는 근거를 받고도 틀리는 답이 많음, 리포트 53). 로컬: CHAT_MODEL=qwen3.5:9b
LOCAL_FAST = not MODEL.startswith("gpt-") and os.environ.get("CHAT_FULL_CHECKS", "") == ""
THINK = re.compile(r"<think>.*?</think>", re.S)

_lock = threading.Lock()
_state: dict = {"assistant": None, "error": None, "loading": False}


def _ollama_ask(self, system: str, user: str) -> str:
    if MODEL.startswith("gpt-"):   # API 모델 (261018 기본: gpt-5.6-luna). 보내는 것: 질문과 공개 규정(ALIO) 조문. 폐쇄망 운영은 CHAT_MODEL=로컬 모델
        from openai import OpenAI
        sys.path.insert(0, str(ROOT / "src"))
        from regcheck.db import load_env
        r = OpenAI(api_key=load_env().get("OPENAI_API_KEY", "").strip('"'), timeout=180).chat.completions.create(
            model=MODEL, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            reasoning_effort="low", max_completion_tokens=4000)
        return THINK.sub("", r.choices[0].message.content or "").strip()
    body ={"model": MODEL, "stream": False, "think": False, "options": {"temperature": 0, "num_ctx": 16384},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    req = urllib.request.Request(OLLAMA, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=600).read())
    return THINK.sub("", r["message"]["content"]).strip()


def _load():
    """처음 한 번: 가빈 검색 모델(GPU)·Generator를 올린다. 서버 시작 때 뒤에서 미리 부른다."""
    if _state["assistant"] or _state["loading"]:
        return
    _state["loading"] = True
    try:
        sys.path.insert(0, str(ROOT))
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
        from src.answer import generate
        generate.Generator.__init__ = lambda self, model=MODEL: setattr(self, "model_name", model)
        generate.Generator._ask = _ollama_ask
        if LOCAL_FAST:
            generate.Generator.verify = lambda self, question, answer, entries: ([], True)
            generate.Generator.conflicts = lambda self, question, entries: ([], True)
        from src.answer.pipeline import Assistant
        _state["assistant"] = Assistant()
    except Exception as e:  # 화면에 원인 표시
        _state["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        _state["loading"] = False


def warmup() -> None:
    threading.Thread(target=_load, daemon=True).start()


def status() -> dict:
    return {"model": MODEL, "ready": _state["assistant"] is not None, "loading": _state["loading"], "error": _state["error"],
            "checks": "숫자·구분·핵심어 검사 (문장 근거·충돌 확인은 로컬 모드에서 끔)" if LOCAL_FAST else "전체 검사",
            "retrieval": _retrieval()}


def _retrieval() -> dict:
    """지금 검색 설정 (config.py). 화면에는 보이지 않고 /api/chat/status로 확인용"""
    try:
        import config as c
        from src.search import search as s
        return {"embed": c.EMBED_MODEL, "rerank": c.RERANK_MODEL, "k": c.TOP_K, "rewrite": c.USE_REWRITE,
                "select_pool": s.LLM_SELECT_POOL, "rerank_top": s.RERANK_TOP, "weights": [c.HYBRID_VECTOR_WEIGHT, c.HYBRID_KEYWORD_WEIGHT],
                "return_article": c.RETURN_ARTICLE}
    except Exception as e:  # 아직 못 읽었으면
        return {"error": type(e).__name__}


FOLLOW_PROMPT = """너는 사내 규정 챗봇의 질문 정리기다. 이전 대화와 새 질문을 보고, 새 질문을 이전 대화 없이도 뜻이 통하는 한 문장 질문으로 고쳐 쓴다.
- 새 질문이 이전 대화를 이어받는 말(그럼, 그건, 거기, 이 경우, ~는요?, 생략된 주어·대상)이면 빠진 대상을 이전 대화에서 채운다.
- 새 질문이 이미 혼자 뜻이 통하면 그대로 출력한다.
- 이전 답변의 내용(숫자·결론)을 질문에 넣지 않는다. 답하지 않는다. 설명 없이 물음표(?)로 끝나는 질문 한 줄만 출력한다."""
QUESTION_END = re.compile(r"(\?|？|까|요|니|나|냐|어|야)$")


def standalone(question: str, history: list[dict]) -> str | None:
    """멀티턴: 이어 묻는 질문을 독립 질문으로 다시 쓴다 (가빈 파이프라인은 질문 하나만 받으므로 우리 층에서 처리).
    이전 대화가 없으면 None. 다시 쓴 결과가 이상하면 None → 원래 질문으로 검색.
    261019: 말투 규칙(「그럼 · 그건 · ~는요?」)으로 이어 묻기를 먼저 골라내던 것을 뺐다 — 「남자 직원도 쓸 수 있어?」
    「신청은 언제까지 해야 돼」처럼 흔한 이어 묻기를 놓쳤다. 이전 대화가 있으면 늘 모델에 묻는다 (혼자 뜻이 통하면 그대로 돌려받음)."""
    if not history:
        return None
    # 이전 답변은 넘기지 않는다: 넘기면 답의 숫자(「7일 이내」)를 질문에 섞는다 (261018 실측). 이전 질문만으로 대상을 채운다
    turns = "\n".join(f"질문: {h.get('q', '')[:200]}" for h in history[-3:])
    try:
        out = _ollama_ask(None, FOLLOW_PROMPT, f"이전 질문:\n{turns}\n\n새 질문: {question}").splitlines()[0].strip().strip('"「」')
    except Exception:
        return None
    if not out or len(out) > 200 or out == question:
        return None
    # 질문 대신 답을 쓰는 경우를 버린다 (「…1개월 이내에 신청해야 합니다.」처럼 지어낸 숫자가 들어감, 261018 실측)
    asked = question + " ".join(h.get("q", "") for h in history)
    if not QUESTION_END.search(out.rstrip(" .")) or any(n not in asked for n in re.findall(r"\d+", out)):
        return None
    # 이전 질문에서 가져온 말이 없으면 이어 묻기가 아니다 (「연차휴가는 며칠이에요?」 → 「연차휴가는 몇 일인가요?」).
    # 조사가 붙어 낱말 모양이 달라지므로(특별휴가 / 특별휴가는) 앞 두 글자로 비교한다 — 낱말 그대로 비교하면 맞게 고친 것도 버렸다 (261019)
    past = " ".join(h.get("q", "") for h in history)
    if not any(w[:2] in past and w[:2] not in question for w in re.findall(r"[가-힣A-Za-z]{2,}", out)):
        return None
    return out


def ask(question: str, history: list[dict] | None = None) -> dict:
    question = (question or "").strip()
    if not question:
        return {"error": "질문이 비어 있습니다"}
    if len(question) > 500:
        return {"error": "질문은 500자 안으로 적어 주세요"}
    if _state["assistant"] is None:
        _load()
    if _state["assistant"] is None:
        return {"error": _state["error"] or "답변 모델을 준비하지 못했습니다"}
    t = time.time()
    history = [h for h in (history or []) if isinstance(h, dict)][-3:]
    with _lock:
        rewritten = standalone(question, history)
        r = _state["assistant"].ask(rewritten or question)
    entries = [{"n": i + 1, "chunk": e.get("chunk_id"), "reg": e.get("문서명"), "article": e.get("article"),
                "state": e.get("조문상태"), "role": e.get("역할"), "text": (e.get("body") or "")[:160]}
               for i, e in enumerate(r.get("entries", []))]
    warnings = [w for w in r.get("warnings", []) if not w.startswith(("용어집:", "표 범위 필터:"))]
    return {"question": question, "standalone": rewritten, "followup_of": history[-1]["q"][:40] if rewritten else None, "mode": r["mode"], "answer": r["answer"], "entries": entries, "cited": r.get("cited", []),
            "warnings": warnings, "model": MODEL, "sec": round(time.time() - t, 1)}
