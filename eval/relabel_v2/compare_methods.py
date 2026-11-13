"""후보 거르기 방법 7가지 비교 (개발 100, 리포트 99). 근거 문장 · 답변은 만들지 않는다. 후보 목록만 낸다.

  1 안 거름        후보 풀 순서 그대로
  2 리랭커         bge-reranker-v2-m3 점수순
  3 판단 모델 단독  모든 후보를 판단 모델로 (문턱 0.7, 확정급 · 정의 변경 USES_TERM은 남김), 순서 = 확정급 먼저 + 점수순
  4 로컬 LLM 단독   모든 후보(풀 순서 상위 60)를 Qwen3.5 9B 목록 방식으로
  5 외부 LLM 단독   같은 입력을 Luna 목록 방식으로
  6 출처별 분리, RAG는 로컬 LLM   규칙 후보 = 판단 모델, RAG 후보(상위 50) = Qwen
  7 출처별 분리, RAG는 외부 LLM   규칙 후보 = 판단 모델, RAG 후보(상위 50) = Luna (현재 구조)

  python eval/relabel_v2/compare_methods.py prep                 # 도메인 그래프 후보를 걷어 낸 풀 + 판단 모델 입력
  python eval/relabel_v2/compare_methods.py kanana [pairs파일]    # 판단 모델 점수 (RAG 후보 + 빠진 규칙 후보)
  python eval/relabel_v2/compare_methods.py rerank [문항 수]      # bge 리랭커 점수
  python eval/relabel_v2/compare_methods.py llm <qwen_single|luna_single|qwen_split|luna_split> [seed] [문항 수]
  python eval/relabel_v2/compare_methods.py eval [seed]           # 목록 만들기 + 지표 + 요약 표

결과는 out/relabel_v2/compare_bm/ (git 제외). 응답 원문 · 후보 목록을 모두 저장하므로 채점은 저장 결과로 다시 할 수 있다.
"""
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT / "eval/relabel_v2")]
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
WORK = ROOT / "out/relabel_v2"
D = os.environ.get("CMP_SET", "bm")     # bm = 개발 100(기본), test = 시험 100 (고른 구성 7번을 한 번만 잰다)
OUT = WORK / ("compare_bm" if D == "bm" else f"compare_{D}")
KANANA = Path(r"C:\Users\Admin\Downloads\kanana_env")
TH = 0.7            # impact_pipeline.DECIDE_TH와 같은 값
LLM_MAX = 60        # 단독 LLM 방법의 입력 상한 (rank_list max_cands)
ORDERS = 2          # 현재 파이프라인과 같은 값 (순서를 섞어 두 번 묻고 합친다)

from run_eval import changes, jl, load_regs  # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def jdump(p, o):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")


def gold_ids():
    from metrics import gold
    return gold(D)


# ── prep: 도메인 그래프 후보를 걷어 낸 풀 + 판단 모델 입력 ─────────────────────────
def prep():
    from regcheck.cand_state import GRAPH_PREFIXES, path_base, source_of
    pool = jload(WORK / f"pool_{D}.json")
    out = []
    for p in pool:
        cs = []
        for c in p["cands"]:
            paths = [x for x in c["paths"] if not path_base(x).startswith(GRAPH_PREFIXES)]
            if not paths:
                continue   # 도메인 그래프만 찾은 후보
            cs.append({**c, "paths": paths, "src": source_of(paths)})
        out.append({**p, "cands": cs})
    jdump(OUT / "pool_nog.json", out)
    n = [len(p["cands"]) for p in out]
    from collections import Counter
    print("문항", len(out), "문항당 후보", round(sum(n) / len(n), 1), "최대", max(n), Counter(c["src"] for p in out for c in p["cands"]))
    G = gold_ids()
    tot = sum(len(g) for g in G.values()); hit = sum(len(G[p["q"]] & {c["id"] for c in p["cands"]}) for p in out)
    print(f"검색 재현율 {hit}/{tot} = {hit / tot:.3f}")

    # 판단 모델 입력 (run_eval.pairs와 같은 방식, 풀만 걷어 낸 것)
    from regcheck.collect import Candidate
    from regcheck.evidence import change_keys
    from regcheck.final_view import decide_inputs
    from regcheck.impact_view import analyze, load_context
    regs = load_regs(); ctx = load_context(ROOT, regs); by = {r["name"]: k for k, r in regs.items()}
    ch = {m["id"]: m for m in changes(D)}
    pairs, src = [], {}
    for p in out:
        m = ch[p["q"].split(":")[1]]; s = m["수정"]
        cq = analyze(regs[by[s["규정"]]], s["개정문"], m["id"])
        cands = [Candidate(x["id"], x["score"], list(x["paths"]), x["tier"]) for x in p["cands"]]
        st = (cq.starts or [""])[0]
        for cid, state in decide_inputs(cands, cq.kind, s["개정문"], ctx.unit_text.get(st, ""), ctx.unit_text,
                                        keys_for=lambda cid: change_keys(cq, ctx.nodes, cid)).items():
            pairs.append({"key": f"{p['q']}|{cid}", "kind": cq.kind, "state": state})
    S0 = jload(WORK / "decide_scores.json")
    cs_src = {f"{p['q']}|{c['id']}": c["src"] for p in out for c in p["cands"]}
    # 규칙 후보는 기존 점수(리포트 97의 새 단계 설명으로 다시 채점한 것)를 쓴다. RAG 후보는 같은 설명으로 새로 채점한다.
    todo = [x for x in pairs if cs_src[x["key"]] == "rag" or x["key"] not in S0]
    jdump(OUT / "pairs_cmp.json", {"pairs": pairs})
    jdump(OUT / "pairs_todo_cmp.json", {"pairs": todo})
    print("판단 모델 입력", len(pairs), "새로 채점", len(todo), "(rag", sum(cs_src[x['key']] == 'rag' for x in todo), ")")


def kanana(pairs_file=None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(KANANA / "tf") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    pf = pairs_file or str(OUT / "pairs_todo_cmp.json")
    cmd = [str(ROOT / ".venv/Scripts/python.exe"), str(ROOT / "scripts/decide_score_pairs.py"), str(KANANA / "model"), pf,
           str(OUT / "decide_scores_cmp.json")]
    print(" ".join(cmd), flush=True)
    sys.exit(subprocess.run(cmd, env=env).returncode)


def lv_map():
    """후보별 판단 모델 점수 {q|id: lv}. 규칙 후보 = 기존 점수, RAG 후보 = 이번에 새로 채점한 점수."""
    # 261113 수정: 예전에는 규칙 후보 점수를 로컬 decide_scores.json(11/12 16:17, 팀의 입력 자르기 · 단계 설명 수정 전)에서 가져와
    # 시험 T073 · T076 등이 낡은 점수로 거르는 문제가 있었다. 이제 규칙 후보 점수는 현재 코드로 다시 채점한 파일(decide_scores_rule_cur.json)만 쓴다.
    S1 = jload(OUT / "decide_scores_cmp.json") if (OUT / "decide_scores_cmp.json").exists() else {}
    f2 = OUT / "decide_scores_rule_cur.json"
    if not f2.exists():
        raise SystemExit(f"{f2} 없음: 규칙 후보 점수를 현재 코드로 다시 채점해야 한다 (낡은 decide_scores.json은 쓰지 않는다)")
    S2 = jload(f2)
    pool = jload(OUT / "pool_nog.json")
    lv = {}
    for p in pool:
        for c in p["cands"]:
            k = f"{p['q']}|{c['id']}"
            S = S1 if c["src"] == "rag" else S2
            if k in S:
                lv[k] = S[k]["lv"]
    return lv


def rebuild_split(arm="luna_split", seed=0):
    """출처별 분리(7번) 목록을 새 규칙 후보 점수로 다시 만든다. Luna가 고른 RAG 후보는 저장된 것을 그대로 쓴다
    (RAG 후보를 Luna에 넣는 것은 규칙 후보 점수와 무관하다). 원본은 *_stale.jsonl로 남긴다."""
    from regcheck.impact_pipeline import DECIDE_TH, apply_decide, final_list
    E = Env(); LV = lv_map()
    f = OUT / f"{arm}_s{seed}.jsonl"
    bak = OUT / f"{arm}_s{seed}_stale.jsonl"
    if not bak.exists():
        bak.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    old = {r["q"]: r for r in jl(bak)}
    out = []
    for q in sorted(E.pool):
        cq, states, amend, ct, start = E.setup(q)
        apply_decide(states, {s.id: LV[f"{q}|{s.id}"] for s in states if f"{q}|{s.id}" in LV}, DECIDE_TH, cq.kind)
        rule_ids = [s.id for s in final_list(states)["rule"]]
        luna_route = {s.id for s in states if s.route == "luna"}
        luna_ids = [x for x in old[q]["final"] if x in luna_route]
        rec = dict(old[q]); rec["final"] = rule_ids + luna_ids; rec["rebuilt"] = "규칙 후보 점수 재채점(261113), Luna 선택은 원본 그대로"
        out.append(rec)
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n", encoding="utf-8", newline="\n")
    ch = sum(1 for r in out if set(r["final"]) != set(old[r["q"]]["final"]))
    print(f"{arm} 목록 다시 만듦: {len(out)}문항, 목록이 바뀐 문항 {ch}")


# ── 공통 준비 ─────────────────────────────────────────────────────────────────
class Env:
    def __init__(self):
        from regcheck.impact_view import load_context
        self.regs = load_regs(); self.ctx = load_context(ROOT, self.regs)
        self.by = {r["name"]: k for k, r in self.regs.items()}
        self.ch = {m["id"]: m for m in changes(D)}
        self.pool = {p["q"]: p for p in jload(OUT / "pool_nog.json")}

    def setup(self, q):
        from regcheck.cand_state import build_states
        from regcheck.collect import Candidate
        from regcheck.impact_view import analyze
        from regcheck.luna_rerank import change_block
        m = self.ch[q.split(":")[1]]; s0 = m["수정"]; rid = self.by[s0["규정"]]; amend = s0["개정문"]
        cq = analyze(self.regs[rid], amend, m["id"])
        states = build_states([Candidate(c["id"], c["score"], list(c["paths"]), c["tier"]) for c in self.pool[q]["cands"]])
        start = cq.starts[0] if cq.starts else None
        return cq, states, amend, change_block(self.regs[rid], amend, start), start


# ── 리랭커 (bge-reranker-v2-m3) ────────────────────────────────────────────────
def rerank(lim=None):
    import torch
    from sentence_transformers import CrossEncoder
    from regcheck.evidence import change_keys
    from regcheck.final_view import cand_window
    E = Env()
    model = CrossEncoder("BAAI/bge-reranker-v2-m3", max_length=1024, device="cuda" if torch.cuda.is_available() else "cpu")
    f = OUT / "rerank_scores.json"
    R = jload(f) if f.exists() else {}
    qs = sorted(E.pool)[:lim]
    t0 = time.time()
    for q in qs:
        if all(f"{q}|{c['id']}" in R for c in E.pool[q]["cands"]):
            continue
        cq, states, amend, ct, start = E.setup(q)
        query = f"{amend}\n{E.ctx.unit_text.get(start, '')[:600]}"[:700]
        docs = [cand_window(E.ctx.unit_text.get(s.id, ""), change_keys(cq, E.ctx.nodes, s.id)) for s in states]
        sc = model.predict([(query, d) for d in docs], batch_size=16, show_progress_bar=False)
        for s, v in zip(states, sc):
            R[f"{q}|{s.id}"] = float(v)
        jdump(f, R)
    print("리랭커 완료", len(R), f"{time.time() - t0:.0f}초")


# ── LLM 방법 ──────────────────────────────────────────────────────────────────
def _lean():
    """Qwen용 간결판: 근거 구절 · 이유를 쓰지 않고 고른 후보와 관계 유형만 낸다.
    이유: Qwen 9B(8GB GPU, 초당 약 22토큰)는 근거 구절 · 이유까지 쓰면 문항당 수 분이 걸려 7번 방법을 비교할 수 없다. 채점하는 것은 후보 선택뿐이다."""
    from regcheck.luna_rerank import PROMPT, RELATIONS
    prompt = PROMPT[:PROMPT.index("근거 (고른 후보마다)")] + PROMPT[PROMPT.index("규칙\n- 「찾은 경로」"):]
    schema = {"type": "object", "additionalProperties": False, "required": ["selected"],
              "properties": {"selected": {"type": "array", "items": {
                  "type": "object", "additionalProperties": False, "required": ["id", "relation"],
                  "properties": {"id": {"type": "string"}, "relation": {"type": "string", "enum": list(RELATIONS)}}}}}}
    return prompt, schema


def rank_list_lean(change_text, items, call, orders=1, seed=0, max_cands=60):
    """luna_rerank.rank_list와 같은 절차(순서 섞기, 별칭 id, 여러 번 묻고 합치기). 프롬프트와 출력만 간결판."""
    import random
    from regcheck.luna_rerank import RELATIONS, cand_lines, merge
    prompt, _ = _lean()
    if not items:
        return {"order": [], "picks": {}, "runs": [], "usage": []}
    if len(items) > max_cands:
        raise ValueError(f"후보 {len(items)}개 > {max_cands}")
    ids = [it["id"] for it in items]
    rnd = random.Random(seed)
    runs, usage = [], []
    for _ in range(max(1, orders)):
        sh = items[:]; rnd.shuffle(sh)
        body, alias = cand_lines(sh)
        out, use = call(prompt.format(change=change_text, cands=body))
        picks, seen = [], set()
        for x in (out or {}).get("selected") or []:
            cid = alias.get(str(x.get("id", "")).strip().strip("[]"))
            if not cid or cid in seen:
                continue
            seen.add(cid)
            picks.append({"id": cid, "relation": RELATIONS.get(x.get("relation"), ""), "candidate_quote": "", "candidate_quote_ok": False,
                          "source_quote": "", "source_quote_ok": False, "reason": ""})
        runs.append(picks); usage.append(use or {})
    order, picks = merge(runs, ids)
    return {"order": order, "picks": picks, "runs": runs, "usage": usage}


def ollama_call(model="qwen3.5:9b", num_ctx=32768, timeout=1800, schema=None, num_predict=4500):
    """schema 기본 = 간결판(Qwen용). Gemma는 Luna와 같은 원래 형식(근거 구절 · 이유 포함)을 쓰므로 schema를 넘긴다."""
    import requests
    schema = schema or _lean()[1]

    def call(prompt):
        err = None
        for _ in range(3):
            try:
                r = requests.post("http://localhost:11434/api/chat", timeout=timeout, json={
                    "model": model, "messages": [{"role": "user", "content": prompt}], "stream": False, "format": schema, "think": False,
                    "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": num_predict}})   # 간결판은 후보 60개를 전부 골라도 약 2,700토큰이라 잘리지 않는다
                r.raise_for_status(); j = r.json()
                use = {"input": j.get("prompt_eval_count", 0), "output": j.get("eval_count", 0)}
                txt = j["message"]["content"]
                try:
                    return json.loads(txt), use
                except json.JSONDecodeError:
                    # 출력이 한도(num_predict)에서 잘린 경우: 끝까지 쓴 항목만 살려 쓰고 잘렸다고 기록한다 (온도 0이라 다시 물어도 같은 결과다).
                    # Qwen이 후보를 계속 골라 한도까지 쓴 경우이며, 그것도 이 방법의 실제 동작이다.
                    import re
                    sel = [{"id": a, "relation": b} for a, b in re.findall(r'"id":\s*"([^"]+)",\s*"relation":\s*"([ABC])"', txt)]
                    if not sel and '"selected"' not in txt:
                        raise
                    use["truncated"] = True
                    return {"selected": sel}, use
            except Exception as e:  # noqa: BLE001
                err = e
        raise RuntimeError(f"ollama 호출 실패: {err!r}")
    return call


def luna_lean_call(model="gpt-5.6-luna", effort="low", timeout=300, attempts=3, max_retries=2):
    """luna_rerank.luna_call과 같은 호출, 출력 형식만 Qwen과 같은 간결판(대조용: 근거를 쓰는지가 선택에 영향을 주는지)."""
    from openai import OpenAI
    from regcheck.db import load_env
    schema = _lean()[1]
    client = OpenAI(api_key=load_env().get("OPENAI_API_KEY", "").strip('"'), timeout=timeout, max_retries=max_retries)

    def call(prompt):
        err = None
        for _ in range(attempts):
            try:
                r = client.responses.create(model=model, input=prompt, store=False, reasoning={"effort": effort}, max_output_tokens=8000,
                                            text={"format": {"type": "json_schema", "name": "pick", "strict": True, "schema": schema}})
                u = getattr(r, "usage", None)
                use = {"input": getattr(u, "input_tokens", 0), "output": getattr(u, "output_tokens", 0)} if u else {}
                return json.loads(r.output_text), use
            except Exception as e:  # noqa: BLE001
                err = e
        raise RuntimeError(f"luna 호출 실패: {err!r}")
    return call


def llm(arm, seed=0, lim=None):
    from regcheck.impact_pipeline import DECIDE_TH, apply_decide, apply_luna, final_list, luna_items, pick_for_luna
    from regcheck.luna_rerank import luna_call, rank_list
    kind_llm, mode = arm.split("_")        # qwen|luna, single|split
    E = Env(); LV = lv_map()
    if kind_llm == "gemma":   # Gemma 4 12B-it: Luna와 같은 원래 형식(근거 포함), 원래 rank_list
        from regcheck.luna_rerank import SCHEMA
        call = ollama_call(model="gemma4:12b-it-q4_K_M", schema=SCHEMA, num_predict=6000)
    else:
        call = luna_call() if kind_llm == "luna" else (luna_lean_call() if kind_llm == "lunalean" else ollama_call())
    if kind_llm in ("qwen", "lunalean"):
        rank_list = rank_list_lean   # noqa: F811  (간결판, _lean 설명 참고)
    out = OUT / f"{arm}_s{seed}.jsonl"
    done = {r["q"] for r in jl(out)} if out.exists() else set()
    lock = threading.Lock()
    todo = [q for q in sorted(E.pool) if q not in done][:lim]
    print("할 문항", len(todo), flush=True)

    def one(q):
        t0 = time.time()
        try:
            cq, states, amend, ct, start = E.setup(q)
        except Exception as e:  # noqa: BLE001  개정문을 분석하지 못한 문항: 빈 목록으로 기록하고 이유를 남긴다(숨기지 않는다)
            rec = {"q": q, "arm": arm, "seed": seed, "kind": "", "n_pool": 0, "n_llm_in": 0, "final": [], "runs": [], "usage": [],
                   "sec": round(time.time() - t0, 1), "error": repr(e)[:200]}
            with lock, out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return q, 0, rec["sec"]
        if mode == "single":
            picked = states[:LLM_MAX]
            res = rank_list(ct, luna_items(picked, E.ctx.nodes, E.ctx.unit_text), call, orders=ORDERS, seed=seed) if picked else {"order": [], "runs": [], "usage": []}
            final = list(res["order"])
            n_in = len(picked)
        else:
            apply_decide(states, {s.id: LV[f"{q}|{s.id}"] for s in states if f"{q}|{s.id}" in LV}, DECIDE_TH, cq.kind)
            picked = pick_for_luna(states, 50, 10)
            res = rank_list(ct, luna_items(picked, E.ctx.nodes, E.ctx.unit_text), call, orders=ORDERS, seed=seed) if picked else {"order": [], "runs": [], "usage": []}
            if picked:
                apply_luna(picked, res)
            view = final_list(states)
            final = [s.id for s in view["rule"]] + [s.id for s in view["other"]]
            n_in = len(picked)
        rec = {"q": q, "arm": arm, "seed": seed, "kind": cq.kind, "n_pool": len(states), "n_llm_in": n_in, "final": final,
               "runs": [[x["id"] for x in r] for r in res["runs"]], "usage": res["usage"], "sec": round(time.time() - t0, 1)}
        with lock, out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return q, len(final), rec["sec"]

    workers = 6 if kind_llm in ("luna", "lunalean") else 1
    with ThreadPoolExecutor(workers) as ex:
        for r in ex.map(one, todo):
            print(r, flush=True)


# ── 목록 만들기 + 지표 ─────────────────────────────────────────────────────────
def build_lists(seed=0):
    """방법별 최종 목록 {q: [id…]}. 1 · 2는 전체 순서 목록과 7번 크기에 맞춘 목록을 함께 만든다."""
    from regcheck.cand_state import source_of
    pool = jload(OUT / "pool_nog.json"); LV = lv_map()
    R = jload(OUT / "rerank_scores.json") if (OUT / "rerank_scores.json").exists() else {}
    L = {"1 안 거름 (전체)": {}, "2 리랭커 (전체)": {}, "3 판단 모델 단독": {}}
    for p in pool:
        q = p["q"]; cs = p["cands"]
        L["1 안 거름 (전체)"][q] = [c["id"] for c in cs]
        if R:
            L["2 리랭커 (전체)"][q] = [c["id"] for c in sorted(cs, key=lambda c: -R.get(f"{q}|{c['id']}", -1e9))]
        keep = []
        for i, c in enumerate(cs):
            sc = LV.get(f"{q}|{c['id']}")
            strong = source_of(c["paths"]) == "rule" and c["tier"] == "확정급"
            bypass = any(x.split(":")[0] == "USES_TERM" for x in c["paths"]) and p["kind"] == "정의 변경"
            if strong or bypass or sc is None or sc >= TH:
                keep.append((0 if strong else 1, -(sc if sc is not None else 3.0), i, c["id"]))
        L["3 판단 모델 단독"][q] = [k[3] for k in sorted(keep)]
    for arm, name in [("qwen_single", "4 로컬 LLM 단독 (Qwen 9B)"), ("luna_single", "5 외부 LLM 단독 (Luna)"),
                      ("qwen_split", "6 출처별 분리, RAG는 Qwen"), ("luna_split", "7 출처별 분리, RAG는 Luna (현재)"),
                      ("lunalean_single", "5' 외부 LLM 단독 (Luna 간결판, 대조)"), ("lunalean_split", "7' 출처별 분리, RAG는 Luna 간결판 (대조)"),
                      ("gemma_single", "4g 로컬 LLM 단독 (Gemma 4 12B, 원래 형식)"), ("gemma_split", "6g 출처별 분리, RAG는 Gemma 4 12B (원래 형식)"),
                      ("luna_split_reuse", "7 출처별 분리, 팀 Luna 선택 재사용")]:
        f = OUT / f"{arm}_s{seed}.jsonl"
        if f.exists():
            rs = jl(f)
            if len(rs) < 100:
                print(f"건너뜀: {arm} 문항 {len(rs)} / 100 (실행 중이거나 미완)")
                continue
            L[name] = {r["q"]: r["final"] for r in rs}
    if D != "bm":   # 시험 100: 고른 구성(7번)과 기준선(1번 안 거름)만. 다른 방법은 개발 100에서 비교했다.
        L = {k: v for k, v in L.items() if k.startswith(("1 ", "7 "))}
    ref = L.get("7 출처별 분리, RAG는 Luna (현재)")
    if ref:
        avg = lambda d: sum(len(v) for v in d.values()) / max(len(d), 1)  # noqa: E731
        for nm in list(L):
            if nm.startswith("7 ") or nm.endswith("(7번 크기)") or "간결판, 대조" in nm or "대조)" in nm:
                continue
            if nm.endswith("(전체)") or avg(L[nm]) > avg(ref) * 1.05:
                key = nm.replace("(전체)", "(7번 크기)") if nm.endswith("(전체)") else nm + " (7번 크기)"
                L[key] = {q: v[:len(ref[q])] for q, v in L[nm].items() if q in ref}
    return {k: v for k, v in L.items() if v}


def evaluate(seed=0):
    import numpy as np
    from metrics import per_q
    G = gold_ids(); pool = jload(OUT / "pool_nog.json")
    P = {p["q"]: {c["id"] for c in p["cands"]} for p in pool}
    L = build_lists(seed)
    qs = sorted(G)
    tot = sum(len(g) for g in G.values())
    pool_hit = sum(len(G[q] & P[q]) for q in qs)
    rows = {}
    for name, lists in L.items():
        r = []
        for q in qs:
            lst = lists.get(q, [])
            d = per_q(lst, G[q])
            d["len"] = len(lst); d["hit_all"] = sum(x in G[q] for x in lst)
            # 정밀도 지표 (리포트 94): Precision@5 = 보인 상위 5 안 정답 / min(5, 목록 길이), 빈 목록 문항은 제외.
            # Context Precision (RAGAS 식, 순위 반영) = 정답 위치마다의 정밀도 평균. 정답이 없으면 0, 빈 목록 문항은 제외.
            d["ne"] = 1.0 if lst else 0.0
            d["p5h"] = float(sum(x in G[q] for x in lst[:5])); d["p5d"] = float(min(5, len(lst)))
            hits, s = 0, 0.0
            for i, x in enumerate(lst, 1):
                if x in G[q]:
                    hits += 1; s += hits / i
            d["cp"] = s / hits if hits else 0.0
            r.append(d)
        rows[name] = r
    N = len(qs); rng = np.random.default_rng(0); B = 5000
    idx = rng.integers(0, N, (B, N))

    def arr(name, k):
        return np.array([x.get(k, 0.0) for x in rows[name]], dtype=float)

    def metrics(name, ix=None):
        g = (lambda k: arr(name, k)) if ix is None else (lambda k: arr(name, k)[ix])
        n = g("n"); w = (n > 0).astype(float); ax = 0 if ix is None else 1
        S = lambda v: v.sum(axis=ax)  # noqa: E731
        T = S(n); W = S(w)
        zero = (n == 0).astype(float)
        return {"최종 재현율": S(g("all")) / T, "Recall@5": S(g("r@5")) / T, "Recall@10": S(g("r@10")) / T,
                "Hit@5": S(g("hit@5") * w) / W, "MRR": S(g("rr") * w) / W, "MAP": S(g("ap") * w) / W, "nDCG@10": S(g("ndcg@10") * w) / W,
                "문항당 목록 크기": S(g("len")) / len(qs) if ix is None else S(g("len")) / ix.shape[1],
                "정밀도": S(g("hit_all")) / np.maximum(S(g("len")), 1),
                "Precision@5": S(g("p5h")) / np.maximum(S(g("p5d")), 1),
                "Context Precision": S(g("cp") * g("ne")) / np.maximum(S(g("ne")), 1),
                "정답 0개 문항 후보 수": S(g("len") * zero) / np.maximum(S(zero), 1)}

    point = {k: metrics(k) for k in rows}
    boot = {k: metrics(k, idx) for k in rows}
    ci = lambda a: (np.percentile(a, 2.5), np.percentile(a, 97.5))  # noqa: E731
    cols = ["최종 재현율", "Recall@5", "Recall@10", "Hit@5", "MRR", "MAP", "nDCG@10", "문항당 목록 크기", "정밀도", "Precision@5", "Context Precision", "정답 0개 문항 후보 수"]
    lines = [f"# 후보 거르기 방법 비교 ({'개발 100' if D == 'bm' else '시험 100'}, seed {seed})", "",
             f"정답 {tot}개, 정답 있는 문항 {sum(1 for g in G.values() if g)}, 정답 0개 문항 {sum(1 for g in G.values() if not g)}. "
             f"검색 후보 풀(도메인 그래프 제외) 재현율 {pool_hit}/{tot} = {pool_hit / tot:.3f}, 문항당 {sum(len(v) for v in P.values()) / N:.1f}개.", "",
             "| 방법 | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for k in rows:
        cells = []
        for c in cols:
            v = float(point[k][c])
            if c in ("최종 재현율", "Recall@5", "Recall@10", "nDCG@10", "MRR", "Hit@5", "MAP", "정밀도", "Precision@5", "Context Precision"):
                lo, hi = ci(boot[k][c]); cells.append(f"{v:.3f} ({lo:.3f}~{hi:.3f})")
            elif c in ("문항당 목록 크기", "정답 0개 문항 후보 수"):
                cells.append(f"{v:.1f}")
            else:
                cells.append(f"{v:.3f}")
        lines.append(f"| {k} | " + " | ".join(cells) + " |")
    refs = [k for k in rows if k.startswith("1 안 거름 (7번 크기)") or k.startswith("1 안 거름 (전체)")][:1] + [k for k in rows if k.startswith("7 ")]
    for ref in refs:
        lines += ["", f"### 짝지은 차이 (방법 − {ref}), 95% 구간 (문항 단위 부트스트랩 5,000번)", "",
                  "| 방법 | 최종 재현율 | Recall@5 | Recall@10 | Hit@5 | MRR | MAP | nDCG@10 | 문항당 목록 크기 | 정밀도 | Precision@5 | Context Precision |", "|---|" + "---|" * 11]
        for k in rows:
            if k == ref:
                continue
            cells = []
            for c in ("최종 재현율", "Recall@5", "Recall@10", "Hit@5", "MRR", "MAP", "nDCG@10", "문항당 목록 크기", "정밀도", "Precision@5", "Context Precision"):
                d = boot[k][c] - boot[ref][c]; lo, hi = ci(d)
                cells.append(f"{float(point[k][c] - point[ref][c]):+.3f} [{lo:+.3f}, {hi:+.3f}]")
            lines.append(f"| {k} | " + " | ".join(cells) + " |")
    # 정답을 놓친 이유
    lines += ["", "### 정답을 놓친 이유 (정답 단위)", "", "| 방법 | 후보 풀에 없음 | 거르기에서 탈락 | 목록에 있음 |", "|---|---|---|---|"]
    for name, lists in L.items():
        miss_pool = sum(len(G[q] - P[q]) for q in qs)
        in_list = sum(len(G[q] & set(lists.get(q, []))) for q in qs)
        dropped = (tot - miss_pool) - in_list      # 후보 풀에는 있었는데 최종 목록에 없는 정답
        lines.append(f"| {name} | {miss_pool} | {dropped} | {in_list} |")
    # 운영
    lines += ["", "### 운영", "", "| 방법 | 문항당 시간(초, 평균) | 입력 토큰(합) | 출력 토큰(합) | 출력이 한도에서 잘린 호출 수 |", "|---|---|---|---|---|"]
    for arm in ("qwen_single", "luna_single", "qwen_split", "luna_split", "lunalean_single", "lunalean_split", "gemma_single", "gemma_split"):
        f = OUT / f"{arm}_s{seed}.jsonl"
        if f.exists():
            rs = jl(f)
            ti = sum(u.get("input", 0) for r in rs for u in r["usage"]); to = sum(u.get("output", 0) for r in rs for u in r["usage"])
            tr = sum(1 for r in rs for u in r["usage"] if u.get("truncated"))
            lines.append(f"| {arm} | {sum(r['sec'] for r in rs) / len(rs):.1f} | {ti} | {to} | {tr} / {sum(len(r['usage']) for r in rs)} |")
    (OUT / f"summary_s{seed}.md").write_text("\n".join(lines), encoding="utf-8")
    jdump(OUT / f"lists_s{seed}.json", L)
    print("\n".join(lines))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    a = sys.argv[2:]
    if cmd == "prep":
        prep()
    elif cmd == "kanana":
        kanana(a[0] if a else None)
    elif cmd == "rerank":
        rerank(int(a[0]) if a else None)
    elif cmd == "llm":
        llm(a[0], int(a[1]) if len(a) > 1 else 0, int(a[2]) if len(a) > 2 else None)
    elif cmd == "rebuild":
        rebuild_split(a[0] if a else "luna_split", int(a[1]) if len(a) > 1 else 0)
    elif cmd == "eval":
        evaluate(int(a[0]) if a else 0)
    else:
        sys.exit(__doc__)
