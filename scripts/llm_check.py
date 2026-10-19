"""로컬 LLM(Ollama) 비교 실험: 의미 검사 후보 거르기와 대안 조문 제시.

  uv run python scripts/llm_check.py --model qwen3.5:9b [--think]
입력: out/semantic/stage2.jsonl (scripts/semantic_check.py)
출력: out/semantic/llm_{모델}.jsonl

과제 A (거르기): 1단계 후보마다 "현재 인용 조문(A) vs reranker 1위(B)" 중 인용 문장에 맞는 것 선택
과제 B (대안 제시): 대상 규정의 조문 제목 목록을 주고 [인용] 자리에 맞는 조문 선택 → 1주차 수정안과 비교
Ollama는 이 PC 안에서만 동작한다 (외부 전송 없음, AGENTS §4). LLM 출력은 판정이 아니라 참고(compare)다.
"""

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from semantic_check import article_text, load_regs  # noqa: E402

OLLAMA = "http://127.0.0.1:11434/api/chat"
# 1주차 검토의 수정안 (최종_문제목록 F2, 검토결과 C2·C5·C12)
EXPECTED = {"C2": "제36조", "C5": "제14조", "F2a": "제6조", "F2b": "제6조", "C12": "제7장"}

PROMPT_A = """너는 회사 내부규정 검토를 돕는 보조자다. 법적 판단은 하지 않는다.
[인용 문장]의 [인용] 자리에 들어갈 조문으로 [후보 A]와 [후보 B] 중 내용이 더 맞는 것을 고른다.
둘 다 맞지 않으면 "없음", 근거가 부족하면 "판단불가".

[인용 문장]
{q}

[후보 A]
{a}

[후보 B]
{b}

JSON으로만 답하라: {{"answer": "A|B|없음|판단불가", "reason": "근거 한 문장"}}"""

PROMPT_B = """너는 회사 내부규정 검토를 돕는 보조자다. 법적 판단은 하지 않는다.
[인용 문장]의 [인용] 자리는 「{doc}」의 어느 {unit}을 가리켜야 내용이 맞는가? [목록]에서 하나 고른다. 맞는 것이 없으면 "없음".

[인용 문장]
{q}

[목록] ({doc})
{items}

JSON으로만 답하라: {{"answer": "{example}", "reason": "근거 한 문장"}}"""


def chat(model: str, prompt: str, think: bool) -> dict:
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False, "format": "json",
            "think": think, "options": {"temperature": 0, "num_ctx": 4096}}
    req = urllib.request.Request(OLLAMA, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        text = json.loads(resp.read())["message"]["content"]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {"answer": "판단불가", "raw": text[:200]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--think", action="store_true", help="생각(thinking) 모드 켜기")
    args = ap.parse_args()
    regs = load_regs()
    rows = [json.loads(l) for l in (ROOT / "out" / "semantic" / "stage2.jsonl").read_text(encoding="utf-8").splitlines()]
    t0 = time.time()

    # 과제 A
    a_rows = [r for r in rows if r["flag"] or r["gold"]]
    for r in a_rows:
        e = r["edge"]
        by_id = {a["id"]: a for a in regs[e["target_reg"]]["articles"]}
        if e["target_type"] == "chapter":
            jang = e["target_id"].rsplit("장", 1)[1]
            in_ch = [a for a in regs[e["target_reg"]]["articles"] if re.match(rf"제\s*{jang}\s*장", a["chapter"])]
            cited = f"{in_ch[0]['chapter']}: " + ", ".join(f"{a['label']}({a['title']})" for a in in_ch[:12])
            s = r["suggest"]
            sug_ch = [a for a in regs[e["target_reg"]]["articles"] if a["chapter"] == s["chapter"]]
            best = f"{s['chapter']}: " + ", ".join(f"{a['label']}({a['title']})" for a in sug_ch[:12])
            same = s["chapter"] == in_ch[0]["chapter"] if in_ch else False
        else:
            cited, best = article_text(by_id[e["target_id"]]), article_text(by_id[r["suggest"]["id"]])
            same = r["suggest"]["id"] == e["target_id"]
        r["A"] = {"answer": "A", "reason": "reranker 1위가 현재 인용과 같음"} if same else chat(
            args.model, PROMPT_A.format(q=r["query"], a=cited, b=best), args.think)

    # 과제 B: 정답 사례와 2단계 후보
    b_rows = [r for r in rows if r["gold"] or r["stage2"] == "대상 불일치 후보"]
    for r in b_rows:
        e = r["edge"]
        reg = regs[e["target_reg"]]
        src_article = e["src"].rsplit("-", 3)[0]
        if e["target_type"] == "chapter":
            chs = []
            for a in reg["articles"]:
                if a["chapter"] and a["chapter"] not in chs:
                    chs.append(a["chapter"])
            items, unit, example = "\n".join(chs), "장", "제N장"
        else:
            items = "\n".join(f"{a['label']}({a['title']})" for a in reg["articles"] if not a["deleted"] and a["id"] != src_article)
            unit, example = "조", "제N조"
        r["B"] = chat(args.model, PROMPT_B.format(doc=reg["name"], unit=unit, q=r["query"], items=items, example=example), args.think)

    out = ROOT / "out" / "semantic" / f"llm_{args.model.replace(':', '_')}{'_think' if args.think else ''}.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            if "A" in r or "B" in r:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"모델 {args.model} (think={args.think}), {time.time() - t0:.0f}초\n")
    print("■ 과제 A: 1단계 후보 거르기 (B 또는 없음 = 후보 유지)")
    keep = [r for r in a_rows if r["A"].get("answer") in ("B", "없음")]
    gk = sum(1 for r in keep if r["gold"])
    print(f"  후보 {len(a_rows)}개 → 유지 {len(keep)}개, 그중 정답 {gk}/{sum(bool(r['gold']) for r in a_rows)}")
    for r in a_rows:
        if r["gold"]:
            print(f"  {r['gold']:4} {r['A'].get('answer')}  {str(r['A'].get('reason', r['A'].get('raw', '')))[:90]}")
    print("\n■ 과제 B: 대안 조문 제시")
    ok = 0
    for r in b_rows:
        ans = str(r["B"].get("answer", ""))
        exp = EXPECTED.get(r["gold"], "")
        hit = bool(exp) and re.sub(r"\s", "", ans).startswith(exp)
        ok += hit
        mark = ("정답" if hit else "오답") if exp else "참고"
        print(f"  {r['gold'] or '-':4} {mark:3} 인용 {(r['edge']['target_doc'] or '자기') + ' ' + r['edge']['raw']:22} → {ans:12} (수정안 {exp or '-'})  {str(r['B'].get('reason', ''))[:70]}")
    print(f"\n  1주차 수정안 일치 {ok}/{len(EXPECTED)} (참고: reranker 제안은 2/5)")
    print(f"\n결과: {out}")


if __name__ == "__main__":
    main()
