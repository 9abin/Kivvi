"""동의어 사전 후보 뽑기: 규정 쪽 낱말에서 출발해 직원이 대신 쓸 일상어를 GPT로 모은다.

평가 질문을 보지 않고 규정 원문만 보고 만든다. 그래서 이 후보로 사전을 늘려도 평가셋 점수가 부풀려지지 않는다.
프롬프트 예시도 평가셋에 없는 말(비품, 가지급금)만 쓴다.

1. 규정어 후보: 본문_검색을 Kiwi로 나눠, 붙여 쓴 명사 덩어리(예: "당일귀임", "카드매출전표")와 두 글자 이상 명사를 모은다.
   조 제목처럼 긴 구절을 넘기면 모델이 그 안의 전문용어 낱말을 집어내지 못해서 낱말 단위로 넘긴다.
2. 모델이 "직원은 다른 말로 부를 낱말"만 골라 일상어를 붙인다.
3. 남기는 기준 (data/manual/synonyms.json과 같은 규칙, src/search/synonyms.py)
   - 규정어: 규정 원문에 실제로 있다
   - 일상어: 규정 원문에 거의 없다(청크 8개 이하). 원문에 있는 말이면 키워드 검색이 이미 찾는다
   - 일상어에 규정어에 없는 명사가 하나 이상 있고, 그중 하나는 원문에 거의 없다
     (띄어쓰기·조사만 다른 짝, "식사 비용"처럼 원문에 흔한 낱말로만 된 일상어는 뺀다)
   - 이미 사전에 있는 짝은 뺀다

실행 (저장소 루트에서): uv run python -m scripts.synonym_candidates
결과: data/manual/synonym_candidates.csv (사람이 보고 확정한 것만 synonyms.json에 넣는다. 코드는 이 파일을 읽지 않는다)
"""
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from kiwipiepy import Kiwi
from langchain_openai import ChatOpenAI

from config import MANUAL_DIR, OPENAI_MODEL, OUT_DIR
from src.search.synonyms import load as load_synonyms

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

OUT = os.path.join(MANUAL_DIR, "synonym_candidates.csv")
RARE = 8      # 일상어가 이 개수 이하의 청크에만 나오면 '원문에 거의 없음'
BATCH = 150

PROMPT = """아래는 회사 내부규정 본문에 나오는 낱말 목록이다 (한 줄에 하나).
직원은 사내 규정 검색창에 규정 용어 대신 평소 말투로 질문한다. 각 낱말을 직원이 평소에 뭐라고 부를지 적는다.

- 규정에서 쓰는 행정 용어·한자어·줄임 표현이면, 같은 대상을 가리키는 평소 말 1~3개를 적는다.
  예: "비품" → "사무용품", "가지급금" → "미리 받는 돈", "선지급"
- 직원도 평소에 그 낱말을 그대로 쓰면(예: 출장, 휴가, 채용) 그 낱말은 적지 않는다.
- 사람 이름, 기관 이름, 법령 이름은 적지 않는다.
- 평소 말은 규정어와 같은 대상이어야 한다. 비슷하지만 다른 제도를 적지 않는다. 띄어쓰기만 다른 말도 적지 않는다.
- 출력은 JSON 배열만: [{"규정어": "비품", "일상어": ["사무용품"]}]"""


def nouns(kiwi, text):
    """붙여 쓴 명사 덩어리와 두 글자 이상 명사."""
    found, cur, end = set(), "", -1
    for t in kiwi.tokenize(text):
        if t.tag in ("NNG", "NNP"):
            cur = cur + t.form if (cur and t.start == end) else t.form
            end = t.start + t.len
            if len(cur) >= 3 and cur != t.form:
                found.add(cur)
            if len(t.form) >= 2:
                found.add(t.form)
        else:
            cur, end = "", -1
    return found


def ask(model, batch):
    for _ in range(3):
        try:
            raw = model.invoke([("system", PROMPT), ("human", "\n".join(batch))]).content.strip()
            out = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw))
            if isinstance(out, list):
                return [x for x in out if isinstance(x, dict)]
        except Exception:  # noqa: BLE001 — 형식이 깨지면 다시 묻는다
            pass
    return []


def main():
    rows = [json.loads(line) for line in open(os.path.join(OUT_DIR, "chunks.jsonl"), encoding="utf-8") if line.strip()]
    rows = [r for r in rows if r["상태"] == "정상" and "_부칙" not in r["id"]]
    texts = [r["본문_검색"] for r in rows]
    kiwi = Kiwi()
    df = Counter()
    where = {}
    for r in rows:
        for w in nouns(kiwi, r["본문_검색"]):
            df[w] += 1
            where.setdefault(w, f"{r['문서명']} {r['조항']}")
    words = sorted(df)
    print(f"규정 낱말 {len(words)}개 → {len(words) // BATCH + 1}번 호출")

    model = ChatOpenAI(model=OPENAI_MODEL, temperature=0)
    batches = [words[i:i + BATCH] for i in range(0, len(words), BATCH)]
    with ThreadPoolExecutor(8) as ex:
        picked = [x for got in ex.map(lambda b: ask(model, b), batches) for x in got]

    existing = load_synonyms()
    count = lambda w: sum(w in t for t in texts)  # noqa: E731
    pairs = defaultdict(set)  # 일상어 → 규정어들
    dropped = Counter()
    for p in picked:
        target = str(p.get("규정어") or "").strip()
        if target not in df:
            dropped["규정어가 목록에 없음"] += 1
            continue
        target_nouns = nouns(kiwi, target) | {target}
        for w in p.get("일상어") or []:
            w = str(w).strip()
            if not w or w.replace(" ", "") == target.replace(" ", ""):
                continue
            new = [n for n in nouns(kiwi, w) if not any(n in t for t in target_nouns)]
            if not new:
                dropped["새 낱말 없음(띄어쓰기·조사만 다름)"] += 1
                continue
            if count(w) > RARE or not any(df.get(n, 0) <= RARE for n in new):
                dropped["일상어 낱말이 원문에 이미 많음(키워드 검색이 찾음)"] += 1
                continue
            if target in existing.get(w.lower(), []):
                dropped["이미 사전에 있음"] += 1
                continue
            pairs[w].add(target)

    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        out = csv.writer(f)
        out.writerow(["일상어", "규정어", "일상어 원문 청크 수", "규정어 원문 청크 수", "규정어가 처음 나오는 곳", "채택(O/X)"])
        for w in sorted(pairs):
            for target in sorted(pairs[w]):
                out.writerow([w, target, count(w), df[target], where[target], ""])
    print(f"모델이 고른 짝 {len(picked)}개 → 후보 {sum(len(v) for v in pairs.values())}개 (일상어 {len(pairs)}개)")
    print("뺀 것:", dict(dropped))
    print("저장:", OUT)


if __name__ == "__main__":
    main()
