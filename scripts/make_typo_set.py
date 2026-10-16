"""검색 평가셋에 일부러 오타를 넣은 세트를 만든다 (오타에 강한지 재기 위해). 정답은 그대로 둔다.

질문마다 한글 글자 2곳에 오타 하나씩을 넣는다. 오타 종류(같은 비율로 고름):
  모음 바꾸기  : 자판에서 가까운 모음으로 (휴가 → 휴거)
  받침 빼기/붙이기 (출장 → 추장, 지급 → 지급ㄱ 대신 다른 받침)
  글자 빠뜨리기 (근무시간 → 근시간)
  앞뒤 글자 바꾸기 (연차 → 차연)
질문의 30%는 띄어쓰기도 모두 붙인다. 같은 결과가 나오도록 난수 씨앗을 고정한다(SEED).

실행 (저장소 루트에서):
  uv run python -m scripts.make_typo_set --src eval/questions_100_retrieval.jsonl --dst eval/questions_100_typo.jsonl
"""
import argparse
import json
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")

SEED = 20261014
BASE, CHO, JUNG, JONG = 0xAC00, 19, 21, 28
# 두벌식 자판에서 붙어 있는 모음 (중성 인덱스 → 바꿀 후보)
NEAR_VOWEL = {0: [4, 20], 4: [0, 8], 8: [4, 13], 13: [8, 18], 18: [13, 20], 20: [0, 18],
              2: [6], 6: [2], 1: [5], 5: [1], 12: [17], 17: [12]}
JONG_POOL = [1, 4, 8, 16, 17, 19, 21]  # ㄱ ㄴ ㄹ ㅁ ㅂ ㅅ ㅇ


def is_hangul(ch):
    return BASE <= ord(ch) < BASE + CHO * JUNG * JONG


def split(ch):
    code = ord(ch) - BASE
    return code // (JUNG * JONG), (code % (JUNG * JONG)) // JONG, code % JONG


def join(cho, jung, jong):
    return chr(BASE + (cho * JUNG + jung) * JONG + jong)


def typo(text, rng):
    chars = list(text)
    spots = [i for i, ch in enumerate(chars) if is_hangul(ch)]
    if len(spots) < 4:
        return text
    for i in sorted(rng.sample(spots[1:], 2), reverse=True):  # 첫 글자는 건드리지 않는다
        kind = rng.choice(["vowel", "jong", "drop", "swap"])
        cho, jung, jong = split(chars[i])
        if kind == "vowel" and jung in NEAR_VOWEL:
            chars[i] = join(cho, rng.choice(NEAR_VOWEL[jung]), jong)
        elif kind == "jong" or kind == "vowel":
            chars[i] = join(cho, jung, 0 if jong else rng.choice(JONG_POOL))
        elif kind == "drop":
            del chars[i]
        elif kind == "swap" and i + 1 < len(chars) and is_hangul(chars[i + 1]):
            chars[i], chars[i + 1] = chars[i + 1], chars[i]
        else:
            chars[i] = join(cho, jung, 0 if jong else rng.choice(JONG_POOL))
    out = "".join(chars)
    return out.replace(" ", "") if rng.random() < 0.3 else out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="eval/questions_100_retrieval.jsonl")
    ap.add_argument("--dst", default="eval/questions_100_typo.jsonl")
    args = ap.parse_args()
    rng = random.Random(SEED)
    rows = [json.loads(line) for line in open(args.src, encoding="utf-8") if line.strip()]
    with open(args.dst, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps({**r, "q": typo(r["q"], rng), "원래질문": r["q"]}, ensure_ascii=False) + "\n")
    print(f"{len(rows)}문항 → {args.dst}")
    for r in rows[:5]:
        print(" ", r["q"])


if __name__ == "__main__":
    main()
