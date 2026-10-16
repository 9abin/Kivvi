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


def _ask_openai(text: str, model: str) -> list[dict]:
    """외부 모델 (평가 비교 전용). 폐쇄망 운영에서는 쓰지 않는다 — 공개된 규정 문장으로만 시험할 것.
    키는 .env의 OPENAI_API_KEY (출력하지 않음). 모델 이름은 「openai:모델명」"""
    from openai import OpenAI

    from .db import load_env
    client = OpenAI(api_key=load_env().get("OPENAI_API_KEY"), timeout=120)
    msgs = [{"role": "user", "content": PROMPT.format(text=text)}]
    try:
        r = client.chat.completions.create(model=model, messages=msgs, response_format={"type": "json_object"}, temperature=0)
    except Exception as e:   # 일부 모델은 temperature를 받지 않는다
        if "temperature" not in str(e):
            return []
        try:
            r = client.chat.completions.create(model=model, messages=msgs, response_format={"type": "json_object"})
        except Exception:
            return []
    try:
        errs = json.loads(r.choices[0].message.content or "{}").get("errors", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    return [e for e in errs if isinstance(e, dict) and e.get("wrong") and e.get("right")]


_CACHE: dict[tuple[str, str], list[dict]] = {}   # 연쇄 수정 라운드마다 같은 문장을 다시 묻지 않는다


def _ask(text: str, model: str, timeout: int = 300) -> list[dict]:
    key = (model, text)
    if key not in _CACHE:
        _CACHE[key] = _ask_uncached(text, model, timeout)
    return _CACHE[key]


def _ask_uncached(text: str, model: str, timeout: int = 300) -> list[dict]:
    if model.startswith("openai:"):
        return _ask_openai(text, model.split(":", 1)[1])
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


_LEX: tuple | None = None
STYLE = (("해야", "하여야"), ("해서", "하여서"), ("했", "하였"))   # 줄임 문체는 오타가 아니라 문체 차이 (규정은 「하여야」로 통일 → 「숙지해야」가 새 낱말로 걸림, 261016)


def _lexicon(corpus: str | None):
    global _LEX
    if _LEX is None:
        import collections
        vocab = collections.Counter(re.findall(r"[가-힣]{2,}", corpus if corpus is not None else corpus_text()))
        bg = {w[i:i + 2] for w in vocab for i in range(len(w) - 1)}
        dele: dict[str, set] = collections.defaultdict(set)
        for w in vocab:
            for k in range(len(w)):
                dele[w[:k] + w[k + 1:]].add(w)
        _LEX = (vocab, bg, dele)
    return _LEX


def _near(w: str, vocab, dele) -> str | None:
    """w에서 한 글자만 바로잡으면 규정에 실제 있는 낱말이 되는가 (자리바꿈, 글자 빠짐·추가, 한 글자 바꿈)"""
    for i in range(len(w) - 1):
        t = w[:i] + w[i + 1] + w[i] + w[i + 2:]
        if t != w and t in vocab:
            return t
    for i in range(len(w)):
        if w[:i] + w[i + 1:] in vocab:
            return w[:i] + w[i + 1:]
    for v in dele.get(w, ()):
        return v
    for i in range(len(w)):
        for v in dele.get(w[:i] + w[i + 1:], ()):
            if len(v) == len(w) and v != w and sum(a != b for a, b in zip(v, w)) == 1:
                return v
    return None


def near_miss_words(text: str, corpus: str | None = None) -> list[tuple[str, str]]:
    """새로 들인 문장에서 (깨진 낱말, 고친 낱말) — 90개 규정에 없는 글자 연쇄가 있고 + 한 글자만 바로잡으면 규정에 있는 낱말이 되는 것만.
    LLM 없이 결정적. 오타 문항 284개에서 재현 76%(LLM 70%, 합치면 89%), 다른 규정으로 만든 사전 교차 검증에서 정상 낱말의 약 2.7%가 걸림 → 근거가 아니라
    낮은 우선순위의 확인 신호 (리포트 51)."""
    vocab, bg, dele = _lexicon(corpus)
    flat = re.sub(r"「[^」]*」|\"[^\"]*\"", " ", text)   # 법령명·규정명·따옴표 안은 제외
    for a, b in STYLE:
        flat = flat.replace(a, b)
    out: list[tuple[str, str]] = []
    for w in re.findall(r"[가-힣]{2,}", flat):
        if w in vocab or all(w[i:i + 2] in bg for i in range(len(w) - 1)):
            continue
        if (r := _near(w, vocab, dele)) and (w, r) not in out:
            out.append((w, r))
    return out


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
