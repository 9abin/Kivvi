"""ragas 라이브러리로 같은 지표를 재서 우리 계산과 비교한다 (리포트 94). ragas 전용 환경에서 돈다(프로젝트 환경과 의존성이 충돌):
  uv venv ragas_env && uv pip install --python ragas_env/Scripts/python.exe "ragas==0.4.3" "langchain-community<0.4" langchain-openai
  ragas_env/Scripts/python.exe eval/relabel_v2/ragas_compare.py <bm|val> <실행 결과.jsonl> <판정 보완 adj.jsonl> <충실도 faith.jsonl> <출력.json>

입력 원문 · 맥락은 judge_solar.py가 남긴 파일(adj · faith)에서 그대로 쓴다(같은 입력으로 비교).
- IDBasedContextPrecision: 보여 준 후보 id ↔ 정답 id (LLM 없음)
- LLMContextPrecisionWithReference: 정답 설명(라벨의 규정 · 조 · 이유)을 reference로 주고, 후보마다 「reference에 쓸모 있나」를 판정 모델이 판정 -> 순위 반영 정밀도
- Faithfulness: 답변 ↔ 답변 모델이 본 입력
판정 모델: solar-pro4 (업스테이지, OpenAI 호환). 키는 .env의 UPSTAGE_API_KEY.
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.stdout.reconfigure(encoding="utf-8")


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def env_key(name):
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


async def main(d, run_f, adj_f, faith_f, out_f):
    from langchain_openai import ChatOpenAI
    from ragas import SingleTurnSample
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import Faithfulness, IDBasedContextPrecision, LLMContextPrecisionWithReference

    llm = LangchainLLMWrapper(ChatOpenAI(model="solar-pro4", base_url="https://api.upstage.ai/v1", api_key=env_key("UPSTAGE_API_KEY"), temperature=0, timeout=120))
    cp_llm, cp_id, fa = LLMContextPrecisionWithReference(llm=llm), IDBasedContextPrecision(), Faithfulness(llm=llm)

    labels = {f"{d}:{r['id']}": r for r in jl(ROOT / f"eval/relabel_v2/{d}/gold.jsonl")}
    adj = {x["key"]: x for x in jl(adj_f)}
    runs = jl(run_f)
    sem = asyncio.Semaphore(6)
    out = {"cp": {}, "faith": {}}

    async def one_q(r):
        q = r["q"]; ids = [s["id"] for s in r["rule"] + r["other"]]
        if not ids:
            return
        ctxs = [f"{adj[f'{q}|{i}']['name']}\n{adj[f'{q}|{i}']['text'][:1500]}" for i in ids]
        lab = labels[q]
        ref = ("함께 살펴볼 조:\n" + "\n".join(f"- {e['규정']} {e['조']}: {e['이유']}" for e in lab["영향"])) if lab["영향"] else "함께 살펴볼 조 없음"
        gold_ids = [e["id"] for e in lab["영향"] if e.get("id")]
        change = adj[f"{q}|{ids[0]}"]["change"]
        s = SingleTurnSample(user_input="다음 개정 때문에 담당자가 함께 살펴보거나 고쳐야 하는 조는 무엇인가?\n" + change,
                             retrieved_contexts=ctxs, reference=ref, retrieved_context_ids=ids, reference_context_ids=gold_ids or ["(없음)"])
        async with sem:
            try:
                v = await cp_llm.single_turn_ascore(s)
            except Exception as e:  # noqa: BLE001
                v = None; print("cp 실패", q, repr(e)[:120], flush=True)
        out["cp"][q] = {"llm": v, "id": await cp_id.single_turn_ascore(s), "n": len(ids), "n_gold": len(gold_ids)}

    async def one_f(x):
        s = SingleTurnSample(user_input=f"이 개정 때문에 [확인 대상]을 왜 살펴봐야 하는가?", response=x["answer"], retrieved_contexts=[x["context"]])
        async with sem:
            try:
                out["faith"][x["key"]] = {"v": await fa.single_turn_ascore(s), "route": x["route"]}
            except Exception as e:  # noqa: BLE001
                print("faith 실패", x["key"], repr(e)[:120], flush=True)

    await asyncio.gather(*(one_q(r) for r in runs))
    await asyncio.gather(*(one_f(x) for x in jl(faith_f)))
    Path(out_f).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    cps = [v for v in out["cp"].values() if v["llm"] is not None]
    wg = [v for v in cps if v["n_gold"]]
    fv = [v["v"] for v in out["faith"].values() if v["v"] is not None and v["v"] == v["v"]]
    print(f"ragas LLMContextPrecisionWithReference: 전체 {sum(v['llm'] for v in cps)/max(len(cps),1):.3f} ({len(cps)}문항), 정답 있는 문항 {sum(v['llm'] for v in wg)/max(len(wg),1):.3f} ({len(wg)})")
    print(f"ragas IDBasedContextPrecision: 전체 {sum(v['id'] for v in out['cp'].values())/max(len(out['cp']),1):.3f}")
    print(f"ragas Faithfulness: {sum(fv)/max(len(fv),1):.3f} ({len(fv)}개 답변)")


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:6]))
