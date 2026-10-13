"""채팅: 가빈 답변 파이프라인(src/answer/pipeline.py Assistant)을 그대로 쓰고 LLM 호출만 로컬 Ollama 모델로 바꾼다.

모델: 환경변수 CHAT_MODEL (기본 qwen3.5:9b). 문맥 16k, think 끔, temperature 0.
로컬 모드에서 끄는 것 (LOCAL_FAST, 기본 켬):
  - 문장 근거 검사(Generator.verify)·충돌 확인(Generator.conflicts): 9B·30B 모두 맞는 문장까지 거의 전부 「근거와 맞지 않을 수 있음」으로
    표시해(261013 실측, 리포트 없음 — out/llm_compare) 경고가 의미가 없고, 질문당 LLM 호출 5번 중 2번이라 느려진다.
    숫자·구분 모순 검사(코드)와 빠진 핵심어 검사는 그대로 둔다.
GPU 하나를 검색 모델(BGE-m3·재순위)과 같이 쓰므로 질문은 한 번에 하나씩(lock) 처리한다.
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
MODEL = os.environ.get("CHAT_MODEL", "qwen3.5:9b")
LOCAL_FAST = os.environ.get("CHAT_FULL_CHECKS", "") == ""
THINK = re.compile(r"<think>.*?</think>", re.S)

_lock = threading.Lock()
_state: dict = {"assistant": None, "error": None, "loading": False}


def _ollama_ask(self, system: str, user: str) -> str:
    body = {"model": MODEL, "stream": False, "think": False, "options": {"temperature": 0, "num_ctx": 16384},
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
            "checks": "숫자·구분·핵심어 검사 (문장 근거·충돌 확인은 로컬 모드에서 끔)" if LOCAL_FAST else "전체 검사"}


def ask(question: str) -> dict:
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
    with _lock:
        r = _state["assistant"].ask(question)
    entries = [{"n": i + 1, "chunk": e.get("chunk_id"), "reg": e.get("문서명"), "article": e.get("article"),
                "state": e.get("조문상태"), "role": e.get("역할"), "text": (e.get("body") or "")[:160]}
               for i, e in enumerate(r.get("entries", []))]
    warnings = [w for w in r.get("warnings", []) if not w.startswith(("용어집:", "표 범위 필터:"))]
    return {"question": question, "mode": r["mode"], "answer": r["answer"], "entries": entries, "cited": r.get("cited", []),
            "warnings": warnings, "model": MODEL, "sec": round(time.time() - t, 1)}
