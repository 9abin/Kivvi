"""자연어 개정 요청 평가셋 만들기 (외부 모델이 말투를 다양하게 씀).

  uv run python scripts/gen_nl_requests.py --n 100 --seed 11
의도한 개정(숫자 바꾸기·조 삭제·문장 덧붙이기)은 규정 원문에서 코드가 정한다 → 정답(expect)은 코드가 만든 것이라 모델이 쓴 말투와 무관하다.
외부 모델(gpt-5.4-mini)은 그 의도를 4가지 말투 중 하나로 풀어 쓰기만 한다. 입력은 공개된 ALIO 규정 문장뿐, 키는 .env (출력하지 않음).
  A 격식: 규정명·조 번호를 모두 말함 / B 구어: 규정명만, 조 번호 없이 내용으로 가리킴 / C 상황 설명: 규정명·조 번호 없이 업무 상황으로 (화면에서 조를 연 채로 쓰는 경우)
  D 짧고 거침: 줄임말·반말·오타 섞임, 규정명은 대충
B·C는 화면처럼 규정을 열어 둔 상태(with_reg)로, A·D는 규정 지정 없이 평가한다.
출력: eval/agent/nl_requests_ext.jsonl (git 제외: 규정 원문 일부 포함)
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from regcheck.db import load_env  # noqa: E402

OUT = ROOT / "eval" / "agent" / "nl_requests_ext.jsonl"
NUM = re.compile(r"(\d+)(일|개월|회|년|명|퍼센트|%|만원|세|시간)")
ADD_SENTENCE = "이 경우 소속 부서장에게 사전에 보고하여야 한다."
INS_TITLE = "업무 협조"
INS_BODY = "소속 부서장은 필요한 경우 관련 부서에 협조를 요청할 수 있다."
STYLES = {
    "A": "격식 있는 업무 요청. 규정명과 조 번호를 모두 말한다.",
    "B": "동료에게 말하듯 구어체. 규정명은 말하지만 조 번호는 말하지 않고, 그 조의 내용(제목 낱말을 그대로 쓰지 말고 풀어서)으로 가리킨다.",
    "C": "규정명도 조 번호도 말하지 않는다. 그 조가 다루는 업무 상황을 설명하며 무엇을 어떻게 바꾸고 싶은지만 말한다.",
    "D": "짧고 거친 말투. 줄임말·반말을 쓰고 오타를 한두 개 섞는다. 규정명은 대충(줄임말·일부만) 말한다.",
}
KIND_TEXT = {
    "NUM": "{old}을(를) {new}(으)로 바꾼다 (숫자 변경)",
    "DEL": "이 조 전체를 삭제한다",
    "ADD": f"이 조의 마지막 항 끝에 「{ADD_SENTENCE}」 문장을 덧붙인다",
    "INS": f"이 조 바로 앞에 새 조를 신설한다 (뒤 번호는 하나씩 밀림). 제목 「{INS_TITLE}」, 내용 「{INS_BODY}」",
}


def candidates(regs: dict):
    for r in regs.values():
        for a in r["articles"]:
            if a["deleted"] or not re.fullmatch(r"제\d+조", a["label"]) or len(a["items"]) == 0:
                continue
            body = [it for it in a["items"] if it["path"][1:] == [0, 0]]
            txt = " ".join(it["norm"] for it in body)
            if not body or len(txt) > 500:
                continue
            yield r, a, body, txt


def build_intents(regs: dict, n: int, rng: random.Random, only: str = "") -> list[dict]:
    pool = list(candidates(regs))
    rng.shuffle(pool)
    if only:
        kinds = [only] * n
    else:
        kinds = ["NUM"] * int(n * 0.4) + ["DEL"] * int(n * 0.3) + ["ADD"] * (n - int(n * 0.4) - int(n * 0.3))
    out, used = [], set()
    for kind in kinds:
        for r, a, body, txt in pool:
            key = (r["reg_id"], a["label"])
            if key in used:
                continue
            if kind == "NUM":
                ms = [m for m in NUM.finditer(txt) if txt.count(m.group(0)) == 1]
                if not ms:
                    continue
                m = ms[0]
                new = str(int(m.group(1)) + (1 if int(m.group(1)) < 10 else 5)) + m.group(2)
                intent = {"old": m.group(0), "new": new}
                expect = {"contains": new}
            elif kind == "INS":
                num = int(a["label"][1:-1])
                if num < 2 or any("의" in x["label"] for x in r["articles"] if not x["deleted"] and int(re.match(r"제(\d+)조", x["label"]).group(1)) >= num):
                    continue   # 가지번호 조가 밀릴 범위에 있으면 적용기가 표현하지 못함
                intent, expect = {}, {"inserted": a["label"]}
            elif kind == "DEL":
                intent, expect = {}, {"deleted": True}
            else:
                intent, expect = {}, {"contains": ADD_SENTENCE}
            used.add(key)
            out.append({"kind": kind, "reg_id": r["reg_id"], "reg": r["name"], "article": a["label"], "title": a["title"],
                        "text": txt, "intent": intent, "expect": expect})
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--model", default="gpt-5.4-mini")
    ap.add_argument("--only", default="", help="한 가지 개정 유형만 (예: INS). 출력은 nl_requests_ext_<유형>.jsonl")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    rng = random.Random(a.seed)
    intents = build_intents(regs, a.n, rng, a.only)
    rng.shuffle(intents)
    from openai import OpenAI
    client = OpenAI(api_key=load_env().get("OPENAI_API_KEY"), timeout=120)
    rows, cost_in, cost_out = [], 0, 0
    for i, it in enumerate(intents):
        style = "ABCD"[i % 4]
        what = KIND_TEXT[it["kind"]].format(**it["intent"])
        prompt = (f"한국벤처투자 직원이 사내 규정 수정 도우미에게 개정을 요청하는 문장을 쓴다.\n\n[규정] {it['reg']}\n[조] {it['article']}({it['title']})\n[현재 조문]\n{it['text']}\n\n"
                  f"[바꾸려는 내용] {what}\n[말투] {STYLES[style]}\n\n"
                  "규칙: 바꾸려는 내용이 요청에서 분명히 드러나야 한다(숫자 변경이면 어떤 값을 어떤 값으로인지, 문장 덧붙이기면 덧붙일 문장 그대로). "
                  "개정문(「…을 …으로 한다」) 형식으로 쓰지 말고 사람이 말하는 요청으로 쓴다. 1~2문장.\n"
                  '출력은 JSON 하나: {"request": "..."}')
        try:
            r = client.chat.completions.create(model=a.model, messages=[{"role": "user", "content": prompt}],
                                               response_format={"type": "json_object"}, reasoning_effort="low", max_completion_tokens=2000)
            req = json.loads(r.choices[0].message.content)["request"].strip()
            cost_in += r.usage.prompt_tokens
            cost_out += r.usage.completion_tokens
        except Exception as e:   # 한 건 실패는 건너뜀
            print("실패", i, type(e).__name__)
            continue
        rows.append({"id": f"{a.only or 'X'}{len(rows) + 1:03d}", "style": style, "kind": it["kind"], "request": req, "reg": it["reg"], "reg_id": it["reg_id"],
                     "article": it["article"], "with_reg": style in "BC", "expect": it["expect"]})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out_path = OUT.with_name(f"nl_requests_ext_{a.only.lower()}.jsonl") if a.only else OUT
    out_path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
    print(f"{len(rows)}건 → {out_path} · 토큰 입력 {cost_in} 출력 {cost_out} (약 {(cost_in * 0.25 + cost_out * 2) / 1e6 * 1400:.0f}원 이하 추정)")


if __name__ == "__main__":
    main()
