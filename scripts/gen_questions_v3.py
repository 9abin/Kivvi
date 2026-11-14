"""합성 질문 본 생성 (프롬프트 v3, 배치 API) — docs/09 가이드라인 A층.

  uv run --extra db python scripts/gen_questions_v3.py submit --n 5 --test          # 5개 조로 파이프라인 검증 (몇 원)
  uv run --extra db python scripts/gen_questions_v3.py submit                        # 전체 조 제출 (배치 = 표준 단가의 절반, 보통 수 분~수 시간)
  uv run --extra db python scripts/gen_questions_v3.py collect                       # 끝났으면 결과를 data/embedding/pilot_gen_v3.jsonl로
v2 파일럿에서 효과가 있었던 「간접」은 2개로 늘리고, 효과가 없었던 「혼동」과 비슷한 조 목록은 뺐다. 대상 청크 표기는 목록에서만 고르게 했다(strict enum).
입력은 공개된 ALIO 규정 문장뿐, 평가 질문은 넣지 않는다. 출력은 data/embedding/ (git 제외). 키는 .env의 OPENAI_API_KEY (출력하지 않음).
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import gen_questions as G  # noqa: E402

DATA = G.DATA
STATE = DATA / "batch_v3.json"
REQ = DATA / "batch_v3_requests.jsonl"
OUT = DATA / "pilot_gen_v3.jsonl"
KRW = 1400

PROMPT_V3 = """너는 한국벤처투자 직원이 사내 규정 도우미에게 묻는 질문을 만든다. 아래 [조문]의 청크(항)마다 질문을 만든다. 이 질문들은 검색 모델을 훈련하는 데 쓰이므로, 조문의 낱말을 따라 쓴 쉬운 질문보다 **일상 사례로 돌려 말한 질문**이 더 쓸모 있다.

청크마다 만들 질문 (총 네 개)
- 구어 1개: 직원이 짧게 묻는 말. 예: 「반반차도 쓸 수 있어요?」
- 간접 2개: 조문의 핵심 낱말(규정 용어)을 하나도 쓰지 않고, 이 조문이 적용되는 **구체적인 일상·업무 사례**로 묻는 1~2문장. 질문만 봐서는 어느 규정인지 바로 떠오르지 않아야 한다. 두 질문은 서로 다른 사례여야 한다. 예: 조문이 「영리를 목적으로 하는 업무에 종사」이면 「주말에 올린 영상 채널에 광고 수익이 붙기 시작했는데 괜찮을까요?」, 「친구 가게에서 주말마다 일을 도와주고 돈을 받는데 문제가 될까요?」
- 검색어 1개: 질문을 규정에서 쓰는 공식 용어로 바꾼 명사구 한 줄(주제어 2~4개, 조사 최소). 예: 「영리업무 겸직 허가 기준」

규칙
1. 질문은 지정한 청크의 내용으로 답이 나와야 한다. 청크에 없는 내용을 전제하지 않는다. 다른 청크·다른 조가 있어야 답이 나오면 만들지 않는다.
2. 규정명, 「제N조」, 청크 번호를 질문에 쓰지 않는다.
3. 같은 청크의 질문끼리 핵심 표현이 서로 달라야 한다.
4. 청크가 단순한 도입·정의·적용 범위라서 간접 질문을 만들기 어려우면 간접은 생략해도 된다. 억지로 만들지 않는다.
5. 간접·구어 질문의 bridge에는 「질문 속 일상 표현 → 조문 속 규정 용어」를 한 쌍 적는다(조문에 실제로 있는 낱말). 없으면 빈 문자열.
6. 질문은 한 문장(간접은 두 문장까지), 90자 이내.

[규정] {reg}
[조문] {label}({title})
[청크]
{chunks}

JSON 하나로만 답한다: {{"items": [{{"target": "청크 표시(예: 2항)", "form": "구어|간접|검색어", "q": "…", "bridge": "…"}}]}}"""


def schema_for(keys):
    return {"type": "json_schema", "json_schema": {"name": "qs", "strict": True, "schema": {
        "type": "object", "additionalProperties": False, "required": ["items"],
        "properties": {"items": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["target", "form", "q", "bridge"],
            "properties": {"target": {"type": "string", "enum": keys}, "form": {"type": "string", "enum": ["구어", "간접", "검색어"]},
                           "q": {"type": "string"}, "bridge": {"type": "string"}}}}}}}}


def build(node, arts, parsed):
    name, art = parsed[node]
    chunks = sorted(arts[node], key=lambda r: r["chunk_id"])[:8]
    keys = [G.chunk_key(r["chunk_id"]) for r in chunks]
    text = "\n".join(f"({k}) {r['body_search'].strip()}" for k, r in zip(keys, chunks))
    prompt = PROMPT_V3.format(reg=name, label=art["label"], title=art["title"] or "", chunks=text)
    return prompt, keys, chunks, name, art


def client():
    from openai import OpenAI
    from regcheck.db import load_env
    return OpenAI(api_key=load_env().get("OPENAI_API_KEY"), timeout=300)


def cmd_submit(a):
    rows, arts, parsed = G.load_articles()
    nodes = sorted(n for n in arts if n in parsed)
    if a.n:
        random.Random(a.seed).shuffle(nodes)
        nodes = nodes[:a.n]
    import tiktoken
    enc = tiktoken.get_encoding("o200k_base")
    lines, tin = [], 0
    for node in nodes:
        prompt, keys, _, _, _ = build(node, arts, parsed)
        tin += len(enc.encode(prompt))
        lines.append(json.dumps({"custom_id": node, "method": "POST", "url": "/v1/chat/completions", "body": {
            "model": a.model, "messages": [{"role": "user", "content": prompt}], "response_format": schema_for(keys),
            "reasoning_effort": "low", "max_completion_tokens": 6000}}, ensure_ascii=False))
    pin, pout = G.PRICE[a.model]
    est_out = len(nodes) * 1100                      # 파일럿 실측: 조당 출력 약 520 + 질문 증가분, 추론 포함 약 1,000
    usd = (tin * pin + est_out * pout) / 1e6 / 2    # 배치는 표준의 절반
    print(f"조 {len(nodes)}개 · 입력 약 {tin:,}토큰 · 예상 비용(배치) ${usd:.2f} ≈ {usd * KRW:,.0f}원 (상한 ${a.cap_usd})")
    if usd > a.cap_usd:
        sys.exit("예상 비용이 상한을 넘어 제출하지 않습니다 (--cap-usd로 조정)")
    REQ.write_text("\n".join(lines) + "\n", encoding="utf-8")
    c = client()
    f = c.files.create(file=REQ.open("rb"), purpose="batch")
    b = c.batches.create(input_file_id=f.id, endpoint="/v1/chat/completions", completion_window="24h",
                         metadata={"purpose": "embedding-tuning-synthetic-questions", "prompt": "v3", "test": str(a.test)})
    STATE.write_text(json.dumps({"batch_id": b.id, "input_file_id": f.id, "n": len(nodes), "model": a.model, "test": a.test,
                                 "submitted": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False), encoding="utf-8")
    print(f"제출 완료: 배치 {b.id} (상태 {b.status}). 끝나면: collect")


def cmd_collect(a):
    st = json.loads(STATE.read_text(encoding="utf-8"))
    c = client()
    b = c.batches.retrieve(st["batch_id"])
    rc = b.request_counts
    print(f"배치 {b.id}: {b.status} · 완료 {rc.completed}/{rc.total} · 실패 {rc.failed}")
    if b.status != "completed":
        if b.status in ("failed", "expired", "cancelled"):
            print("  오류:", (b.errors.data[:2] if b.errors else None))
        return
    out = c.files.content(b.output_file_id).text
    rows, arts, parsed = G.load_articles()
    pin, pout = G.PRICE[st["model"]]
    tin = tout = n_items = bad = 0
    recs = []
    for line in out.splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        node = r["custom_id"]
        body = (r.get("response") or {}).get("body") or {}
        if not body.get("choices"):
            bad += 1
            continue
        u = body["usage"]
        tin += u["prompt_tokens"]
        tout += u["completion_tokens"]
        try:
            items = json.loads(body["choices"][0]["message"]["content"])["items"]
        except Exception:
            items = []
            bad += 1
        _, keys, chunks, name, art = build(node, arts, parsed)
        n_items += len(items)
        recs.append({"node": node, "reg": name, "label": art["label"], "title": art["title"],
                     "chunks": {G.chunk_key(ch["chunk_id"]): {"chunk_id": ch["chunk_id"], "text": ch["body_search"]} for ch in chunks},
                     "items": items, "usage": {"in": u["prompt_tokens"], "out": u["completion_tokens"]}, "model": st["model"], "prompt": "v3"})
    OUT.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in recs), encoding="utf-8")
    usd = (tin * pin + tout * pout) / 1e6 / 2
    print(f"수집 {len(recs)}개 조 · 질문 {n_items}개 · 실패/비어 있음 {bad} · 입력 {tin:,} 출력 {tout:,}")
    print(f"실제 비용(배치 절반 단가) ${usd:.3f} ≈ {usd * KRW:,.0f}원 → {OUT}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["submit", "collect"])
    ap.add_argument("--n", type=int, default=0, help="0이면 전체 조")
    ap.add_argument("--model", default="gpt-5.4-mini")
    ap.add_argument("--cap-usd", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    {"submit": cmd_submit, "collect": cmd_collect}[a.cmd](a)


if __name__ == "__main__":
    main()
