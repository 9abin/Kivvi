"""오타 만들기: 문장 속 낱말 하나를 흔한 방식으로 틀리게 한다 (평가용 — 오타 검사·변경안 골든셋이 같이 쓴다).

종류: 자리바꿈(휴직기간→휴기직간) · 모음(직→즉) · 받침빠짐 · 맞춤법(됀다·로써·던지·안는) · 반복(지급급)
"""

import random
import re

VOWEL = {0: 4, 4: 0, 1: 5, 5: 1, 8: 13, 13: 8, 20: 18, 18: 20, 6: 2, 2: 6}   # ㅏ↔ㅓ ㅐ↔ㅔ ㅗ↔ㅜ ㅣ↔ㅡ ㅕ↔ㅑ
COMMON = [("된다", "됀다"), ("되어", "돼어"), ("로서", "로써"), ("든지", "던지"), ("않는", "안는"), ("않은", "안은"), ("됩니다", "됍니다"),
          ("하였다", "하엿다"), ("결재", "결제"), ("금지", "금기")]


def _split(c: str) -> tuple[int, int, int]:
    n = ord(c) - 0xAC00
    return n // 588, (n % 588) // 28, n % 28


def _join(l: int, v: int, t: int) -> str:
    return chr(0xAC00 + l * 588 + v * 28 + t)


def mutate(kind: str, s: str, rng: random.Random) -> tuple[str, str, str] | None:
    """(바뀐 문장, 원래 낱말, 바뀐 낱말). 문장에 한 번만 나오는 한글 낱말만"""
    words = [w for w in sorted(set(re.findall(r"[가-힣]{3,}", s))) if s.count(w) == 1]   # sorted: 같은 시드가 항상 같은 결과
    rng.shuffle(words)
    if kind == "맞춤법":
        for a, b in rng.sample(COMMON, len(COMMON)):
            m = re.search(r"[가-힣]*" + a + r"[가-힣]*", s)
            if m and s.count(m.group(0)) == 1:
                return s.replace(m.group(0), m.group(0).replace(a, b, 1), 1), m.group(0), m.group(0).replace(a, b, 1)
        return None
    for w in words:
        if kind == "자리바꿈":
            i = 1 if len(w) > 3 else 0
            if w[i] == w[i + 1]:
                continue
            nw = w[:i] + w[i + 1] + w[i] + w[i + 2:]
        elif kind == "모음":
            i = rng.randrange(len(w))
            l, v, t = _split(w[i])
            if v not in VOWEL:
                continue
            nw = w[:i] + _join(l, VOWEL[v], t) + w[i + 1:]
        elif kind == "받침빠짐":
            idx = [i for i, c in enumerate(w) if _split(c)[2]]
            if not idx:
                continue
            i = rng.choice(idx)
            l, v, _ = _split(w[i])
            nw = w[:i] + _join(l, v, 0) + w[i + 1:]
        else:   # 반복
            i = rng.randrange(1, len(w))
            nw = w[:i] + w[i - 1] + w[i:]
        return s.replace(w, nw, 1), w, nw
    return None
