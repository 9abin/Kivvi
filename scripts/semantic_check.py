"""의미 검사 2단계: '대상 불일치' 후보 (인용은 맞게 해석되지만 가리키는 조문의 내용이 다른 경우).

방법 (261006 실험 결과로 정함, 리포트 261006-1603-10):
1. 설명구: 인용 바로 뒤 「…에 따른 / …이 정한 X」의 X (예: 「제35조에 따른 행동강령책임관」의 '행동강령책임관')
2. reranker(bge-reranker-v2-m3)로 X와 대상 규정의 모든 조문을 비교해, 인용된 조의 순위를 본다
3. 순위가 --rank 밖이면 후보. 대안 제시는 인용 문장 전체로 다시 reranker를 돌린 1위
설명구가 없는 인용은 1단계(벡터) 결과만 '확인 필요(낮음)'으로 남긴다.
--llm 을 주면 후보에 로컬 LLM 비교를 덧붙인다 (Qwen2.5-3B는 효과 없음 확인, 다른 모델 비교용).

  uv run python scripts/semantic_check.py [--rank 5] [--llm 모델]
입력: out/semantic/stage1.jsonl (scripts/vector_check.py)  출력: out/semantic/stage2.jsonl
판정이 아니라 표시(compare)다. 오류 확정은 사람이 한다 (AGENTS §3).
"""

import argparse
import gc
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

RERANKER = "BAAI/bge-reranker-v2-m3"
MAX_CHARS = 700
RE_DESC = re.compile(
    r"^\s*(?:의\s*규정)?\s*(?:에\s*따른|에\s*의한|이\s*정한|에서\s*정한|에\s*규정된|에\s*의하여\s*지정된)\s*"
    # 조사는 뒤에 공백·문장부호가 올 때만 끝으로 본다 (「감사결과」가 「감사결」로 잘리지 않게)
    r"([^,.()]{2,25}?)(?=(?:을|를|이|가|은|는|의|에|으로|로|과|와|이란|란)(?:\s|[,.)]|$)|\s*(?:대장|\(|,|\.|$))"
)
# 설명구로 쓸 수 없는 일반어·문장 조각 (조문 하나를 특정하지 못하는 말)
GENERIC_DESC = {"조직", "부서", "목적", "사항", "것", "경우", "기준", "절차", "범위", "내용", "업무",
                "조치", "필요한 조치", "감사", "평가", "직원", "보고", "기한 내", "승인", "결과"}


def load_regs() -> dict[str, dict]:
    return {
        r["reg_id"]: r
        for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))
    }


def item_text(regs, e) -> str:
    reg = regs[e["src_reg"]]
    for a in reg["articles"] + [x for ad in reg["addenda"] for x in ad["articles"]]:
        for it in a["items"]:
            if it["id"] == e["src"]:
                return it["norm"]
    return e["context"]


def descriptor(regs, e) -> str | None:
    t = item_text(regs, e)
    i = t.find(e["raw"])
    m = RE_DESC.match(t[i + len(e["raw"]):]) if i >= 0 else None
    if not m:
        return None
    x = m.group(1).strip().strip('"')
    if x.startswith(("바", "것")) or x in GENERIC_DESC:
        return None
    return x


def article_text(a: dict, n: int = MAX_CHARS) -> str:
    return f"{a['label']}({a['title']}) {a['norm'].split(chr(10), 1)[-1]}"[:n]


def cited_rank(e, ranked) -> int:
    if e["target_type"] == "chapter":
        jang = e["target_id"].rsplit("장", 1)[1]
        return next((i for i, (s, a) in enumerate(ranked, 1) if re.match(rf"제\s*{jang}\s*장", a["chapter"])), 0)
    return next((i for i, (s, a) in enumerate(ranked, 1) if a["id"] == e["target_id"]), 0)


def free_gpu():
    import torch

    gc.collect()
    torch.cuda.empty_cache()


def run_reranker(rows, regs):
    from sentence_transformers import CrossEncoder

    model = CrossEncoder(RERANKER, device="cuda", model_kwargs={"dtype": "float16"})

    def rank(query, e, n):
        reg = regs[e["target_reg"]]
        src_article = e["src"].rsplit("-", 3)[0]
        arts = [a for a in reg["articles"] if not a["deleted"] and a["id"] != src_article]
        scores = model.predict([(query, article_text(a, n)) for a in arts], batch_size=16, show_progress_bar=False)
        return sorted(zip(scores.tolist(), arts), key=lambda z: -z[0])

    for r in rows:
        e = r["edge"]
        r["desc"] = descriptor(regs, e)
        if r["desc"]:
            ranked = rank(r["desc"], e, 300)
            r["desc_rank"], r["n_articles"] = cited_rank(e, ranked), len(ranked)
        # 대안 제시: 인용 문장 전체(인용 표현은 가림)로 다시 순위
        query = item_text(regs, e).replace(e["raw"], "[인용]")
        ranked = rank(query, e, MAX_CHARS)
        best = ranked[0][1]
        r["suggest"] = {"id": best["id"], "label": best["label"], "title": best["title"], "chapter": best["chapter"]}
        r["query"] = query
    del model
    free_gpu()


PROMPT = """너는 회사 내부규정 검토를 돕는 보조자다. 법적 판단은 하지 않는다.
[인용 문장]의 [인용] 자리에 들어갈 조문으로 [후보 A]와 [후보 B] 중 내용이 더 맞는 것을 고른다.
둘 다 맞지 않으면 "없음", 근거가 부족하면 "판단불가".

[인용 문장]
{q}

[후보 A]
{a}

[후보 B]
{b}

JSON 한 줄로만 답하라. 예: {{"answer": "B", "reason": "후보 B는 …를 정하고 있어 인용 문장의 …와 맞는다"}}"""


def run_llm(rows, regs, llm_name):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(llm_name)
    model = AutoModelForCausalLM.from_pretrained(llm_name, dtype=torch.float16).to("cuda").eval()
    for r in rows:
        e = r["edge"]
        by_id = {a["id"]: a for a in regs[e["target_reg"]]["articles"]}
        if e["target_type"] != "article" or e["target_id"] not in by_id or r["suggest"]["id"] == e["target_id"]:
            continue
        msg = [{"role": "user", "content": PROMPT.format(
            q=r["query"], a=article_text(by_id[e["target_id"]]), b=article_text(by_id[r["suggest"]["id"]]))}]
        enc = tok.apply_chat_template(msg, add_generation_prompt=True, return_tensors="pt", return_dict=True).to("cuda")
        out = model.generate(**enc, max_new_tokens=120, do_sample=False)
        text = tok.decode(out[0][enc["input_ids"].shape[1]:], skip_special_tokens=True)
        m = re.search(r"\{.*\}", text, re.S)
        try:
            r["llm"] = {**json.loads(m.group(0)), "model": llm_name} if m else {"answer": "판단불가", "raw": text[:120]}
        except json.JSONDecodeError:
            r["llm"] = {"answer": "판단불가", "raw": text[:120], "model": llm_name}
    del model
    free_gpu()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rank", type=int, default=5, help="설명구 기준 인용된 조 순위가 이보다 크면 후보")
    ap.add_argument("--llm", default="", help="후보에 덧붙일 로컬 LLM (기본: 안 씀)")
    args = ap.parse_args()
    regs = load_regs()
    rows = [json.loads(l) for l in (ROOT / "out" / "semantic" / "stage1.jsonl").read_text(encoding="utf-8").splitlines()]
    run_reranker(rows, regs)
    for r in rows:
        if r.get("desc"):
            r["stage2"] = "대상 불일치 후보" if r["desc_rank"] > args.rank else "통과"
        else:
            r["stage2"] = "확인 필요(낮음)" if r["flag"] else "통과"
    if args.llm:
        run_llm([r for r in rows if r["stage2"] != "통과"], regs, args.llm)
    with (ROOT / "out" / "semantic" / "stage2.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with_desc = [r for r in rows if r.get("desc")]
    cand = [r for r in rows if r["stage2"] == "대상 불일치 후보"]
    low = [r for r in rows if r["stage2"] == "확인 필요(낮음)"]
    gold = [r for r in rows if r["gold"]]
    print(f"검사한 인용 {len(rows)}개, 설명구 있음 {len(with_desc)}개 (기준: 설명구 순위 > {args.rank})\n")
    print(f"■ 대상 불일치 후보 {len(cand)}개")
    for r in sorted(cand, key=lambda r: (not r["gold"], -r["desc_rank"])):
        e, s = r["edge"], r["suggest"]
        sug = f"{s['label']}({s['title']})" + (f" [{s['chapter']}]" if e["target_type"] == "chapter" else "")
        print(f"  {r['gold'] or '-':4} {e['src']:20} {(e['target_doc'] or '자기') + ' ' + e['raw']:24} 「{r['desc']}」"
              f" 순위 {r['desc_rank']}/{r['n_articles']} → 제안 {sug}" + (f"  LLM {r['llm']['answer']}" if r.get("llm") else ""))
    print(f"\n■ 확인 필요(낮음, 설명구 없이 1단계만) {len(low)}개")
    hit = sum(1 for r in cand if r["gold"])
    print(f"\n1주차 정답 {len(gold)}개 중 후보 {hit}개, 후보 {len(cand)}개 중 정답 {hit}개 (정답 외 후보는 사람 확인 필요)")


if __name__ == "__main__":
    main()
