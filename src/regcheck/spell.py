"""개정안 문장 오타·맞춤법 검사: 로컬 LLM이 후보를 내고, 규칙이 확인한다 (바뀐 문장만).

  check(text, model)  → [{wrong, right, reason}]  (확인을 통과한 것만)
LLM은 지적만 하고 판정하지 않는다. 확인 규칙 (모델이 지어낸 지적을 거르기)
  · wrong이 문장에 그대로 있어야 함
  · right와 글자 차이가 작아야 함 (한두 글자, 띄어쓰기만 다른 지적은 버림 — 원문 자체가 띄어쓰기가 들쭉날쭉)
  · 법령명·규정명(「」 안), 숫자·조 번호가 바뀌는 지적은 버림 (오타가 아니라 내용 수정)
  · 문장 부호만 다르거나, 말을 덧붙이기만 하거나, 다른 규정에서 3번 이상 쓰는 낱말을 고치라는 지적은 버림 (261014 평가의 오경보)
폐쇄망 가정이라 외부 맞춤법 API는 쓰지 않는다.
"""

from __future__ import annotations

import json
import re
import urllib.request
from difflib import SequenceMatcher

OLLAMA = "http://127.0.0.1:11434/api/chat"
PROMPT = """너는 한국어 공문서 교정자다. 아래 [문장]에서 오타와 맞춤법 오류만 찾는다.
- 찾을 것: 글자가 틀리거나 순서가 바뀐 낱말, 받침이 빠지거나 틀린 낱말, 맞춤법 오류(되/돼, 로서/로써, 든지/던지, 않/안 등), 같은 글자가 잘못 반복된 곳
- 찾지 말 것: 띄어쓰기, 문체·표현 개선, 법령명·규정명·조 번호·숫자, 문장 부호, 원래 맞는 말
- 확실한 것만 적는다. 오류가 없으면 빈 목록을 낸다.
출력은 JSON 하나만: {{"errors": [{{"wrong": "문장에 있는 틀린 낱말 그대로", "right": "고친 낱말", "reason": "짧은 이유"}}]}}

[문장]
{text}"""


def _ask(text: str, model: str, timeout: int = 300) -> list[dict]:
    body = {"model": model, "stream": False, "think": False, "format": "json",
            "messages": [{"role": "user", "content": PROMPT.format(text=text)}],
            "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 400}}   # 출력 상한: 30B가 끝없이 쓰다 시간 초과된 적 있음
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read())["message"]["content"]
    except (TimeoutError, OSError):   # 한 문장이 실패해도 검사 전체를 멈추지 않는다
        return []
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.S)
    try:
        errs = json.loads(out).get("errors", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    return [e for e in errs if isinstance(e, dict) and e.get("wrong") and e.get("right")]


def corpus_text() -> str:
    """90개 규정 본문 (맞는 말 확인용). 다른 규정에서도 여러 번 쓰는 낱말이면 오타가 아니라 그 회사가 쓰는 말로 본다"""
    import glob
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    return "\n".join(it["norm"] for p in sorted(glob.glob(str(root / "out" / "parsed" / "[0-9]*.json")))
                     for a in json.loads(Path(p).read_text(encoding="utf-8"))["articles"] for it in a["items"])


def verify(text: str, e: dict, corpus: str | None = None) -> bool:
    w, r = str(e["wrong"]).strip(), str(e["right"]).strip()
    if not w or w == r or w not in text:
        return False
    if w.replace(" ", "") == r.replace(" ", ""):      # 띄어쓰기만 다름
        return False
    if re.sub(r"[^가-힣A-Za-z0-9]", "", w) == re.sub(r"[^가-힣A-Za-z0-9]", "", r):   # 문장 부호만 다름 (관리․감독 → 관리·감독)
        return False
    ops = SequenceMatcher(None, w, r).get_opcodes()
    if all(t in ("equal", "insert") for t, *_ in ops):   # 글자를 덧붙이거나 끼워 넣기만 함 (거래 → 거래에, 적극업무 → 적극적 업무) — 문장 고치기
        return False
    if corpus is not None and corpus.count(w) - text.count(w) >= 3:   # 다른 곳에서도 여러 번 쓰는 말 (궐위, 개의하고)
        return False
    if re.search(r"\d", w + r) or "「" in w or "」" in w:   # 숫자·조 번호·법령명은 내용 수정이지 오타가 아님
        return False
    i = text.find(w)
    if text.rfind("「", 0, i) > text.rfind("」", 0, i):   # 「…」 안 (법령명·규정명)
        return False
    ops = SequenceMatcher(None, w, r).get_opcodes()
    edits = sum(max(i2 - i1, j2 - j1) for t, i1, i2, j1, j2 in ops if t != "equal")
    return edits <= 2 or (len(w) >= 4 and sorted(w) == sorted(r))   # 한두 글자, 또는 글자 자리만 바뀜


def check(text: str, model: str, corpus: str | None = None) -> list[dict]:
    return [e for e in _ask(text, model) if verify(text, e, corpus)]
