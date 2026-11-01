"""⑥ 답변·채점 (주지표 상위 조합에만). 저장해 둔 리랭크·선택 결과로 근거를 만들고, 답변 LLM으로 답을 쓰고, 고정된 채점 모델로 채점한다."""
import json
import time

import config as app_config
from src.answer.generate import Generator
from src.answer.pipeline import Assistant
from src.combo import graph_expand
from src.combo import metrics as M
from src.combo.graph_answer import graph_entries
from src.combo.models import LLM, strip_think
from src.eval import judge_answers


class ComboGenerator(Generator):
    """역할별 LLM을 쓰는 Generator. 프롬프트와 파싱은 운영 코드(Generator)를 그대로 쓰고, 모델과 사고 과정 제거만 다르다."""

    def __init__(self, llm):
        self.llm = llm
        self.model = llm.chat
        self.model_name = llm.model

    def _ask(self, system, user):
        return strip_think(super()._ask(system, user))

    def _ask_stream(self, system, user, on_token):
        return strip_think(super()._ask_stream(system, user, on_token))


class RoleGenerators:
    """답변은 answer 모델, 충돌 확인·근거 검사는 verify 모델. Assistant가 부르는 메서드를 역할에 맞는 Generator로 보낸다."""

    def __init__(self, answer, verify):
        self.ans, self.ver = answer, verify

    def answer(self, *a, **k):
        return self.ans.answer(*a, **k)

    def conflicts(self, *a, **k):
        return self.ver.conflicts(*a, **k)

    def verify(self, *a, **k):
        return self.ver.verify(*a, **k)

    def __getattr__(self, name):
        return getattr(self.ans, name)


class ComboAssistant(Assistant):
    """검색은 저장해 둔 결과를 쓰고(_retrieve), 나머지(근거 정리, 모드 판단, 답변, 검사, 코드 가드)는 운영 Assistant 그대로다."""

    def __init__(self, searcher, generator, results_fn):
        self.use_glossary, self.scope_filter, self.use_plan, self.last_plan = True, True, False, None
        self.searcher, self.generator, self.gap, self.pool = searcher, generator, None, None
        self._results_fn = results_fn

    def _retrieve(self, question, k, filters):
        return self._results_fn(question, k)


def stage_answer(run, cands, rr, sel):
    cfg = run.cfg
    k = cfg["eval"]["answer_k"]
    run.stop_llms()
    s = run.searcher(None, None)
    ans_llm, ver_llm = run.llm("answer"), run.llm("verify")
    judge_spec = cfg.get("judge") or {"model": app_config.JUDGE_MODEL}
    judge_llm = LLM(judge_spec)
    asst = ComboAssistant(s, RoleGenerators(ComboGenerator(ans_llm), ComboGenerator(ver_llm)), None)
    byq = {g["id"]: g for g in run.gold}
    gcfg = cfg.get("graph") or {}
    near_k = gcfg.get("near_k", 3)
    gx = graph_expand.Expander(graph_expand.load_edges(gcfg.get("uri", graph_expand.DEFAULT_URI)), run.indexed_nodes,
                               gcfg.get("policy", {})) if gcfg.get("enabled") else None
    recs = []
    for n, g in enumerate(run.gold, 1):
        arts = run.ranked_of(s, g, cands, rr)
        exact = s.exact_hits(g["q"])
        if run.S:
            byid = {h["chunk_id"]: h for h in arts}
            pick = [byid[cid] for cid in sel["items"][g["id"]][str(k)]["chosen"] if cid in byid]
        else:
            pick = arts[:k]
        order = M.first_seen([M.node_key(h["doc_id"], h["article"]) for h in exact] + [run.node_of[h["chunk_id"]] for h in pick])
        asst._results_fn = lambda q, kk, pick=pick, exact=exact, order=order: with_graph(s, gx, s._as_articles(exact + pick, kk, True), order, near_k)
        first = []
        ans_llm.take_tokens(), ver_llm.take_tokens()
        t0 = time.perf_counter()
        try:
            r = asst.ask(g["q"], k=k, on_token=lambda piece: first.append(time.perf_counter() - t0) if not first else None, wait_checks=True)
            total = time.perf_counter() - t0
            rec = {"id": g["id"], "mode": r["mode"], "answer": r["answer"], "withheld": r.get("withheld"), "warnings": r.get("warnings", []),
                   "entries": [{"문서명": e["문서명"], "article": e["article"], "node": M.node_key(e["doc_id"], e["article"])} for e in r["entries"]],
                   "cited": r.get("cited"), "sec_first_token": round(first[0], 3) if first else None, "sec_answer": round(r["timing"]["answer"], 3),
                   "sec_total": round(total, 3), "error": None}
        except Exception as e:  # noqa: BLE001
            rec = {"id": g["id"], "mode": None, "answer": None, "error": repr(e)[:300], "sec_total": round(time.perf_counter() - t0, 3)}
        t_ans, t_ver = ans_llm.take_tokens(), ver_llm.take_tokens()
        rec["tokens"] = {"in": t_ans["in"] + t_ver["in"], "out": t_ans["out"] + t_ver["out"]}
        recs.append(rec)
        if n % 10 == 0:
            print(f"  답변 {n}/{len(run.gold)}", flush=True)
    print("채점 중 (고정 채점 모델)", flush=True)
    for g, rec in zip(run.gold, recs):
        j = judge_answers.judge(judge_llm.chat, g, rec, judge_answers.PROMPT_TIERED, judge_answers.criteria_tiered)
        rec["judge"] = j
        rec["label"] = j.get("판정")
    n = len(recs)
    labels = {x: sum(1 for r in recs if r["label"] == x) for x in ("정답", "부분", "오답", "답못함", "채점실패")}
    fills = [sum(r["judge"]["요지_충족"]) / len(r["judge"]["요지_충족"]) for r in recs if r["judge"].get("요지_충족")]
    cite_ok = cite_all = 0
    for g, r in zip(run.gold, recs):
        gn = run.gold_of(g)
        ents = r.get("entries") or []
        for c in r.get("cited") or []:
            if isinstance(c, int) and 1 <= c <= len(ents):
                cite_all += 1
                cite_ok += ents[c - 1]["node"] in (gn["core"] | gn["aux"])
    unans = [r for g, r in zip(run.gold, recs) if g.get("답가능여부") == "불가"]
    agg = {"n": n, "labels": labels, "label_rate": {k_: v / n for k_, v in labels.items()},
           "core_point_fill": sum(fills) / len(fills) if fills else None,
           "forbidden_violation_rate": sum(1 for r in recs if r["judge"].get("금지_위반")) / n,
           "refusal_accuracy": (sum(1 for r in unans if r["label"] == "정답") / len(unans)) if unans else None, "n_unanswerable": len(unans),
           "citation_accuracy": cite_ok / cite_all if cite_all else None, "citations": cite_all,
           "withheld_rate": sum(1 for r in recs if r.get("mode") != "answer") / n,
           "sec_first_token": {"p50": _pct([r.get("sec_first_token") for r in recs], 50), "p95": _pct([r.get("sec_first_token") for r in recs], 95)},
           "sec_answer": {"p50": _pct([r.get("sec_answer") for r in recs], 50), "p95": _pct([r.get("sec_answer") for r in recs], 95)},
           "tokens": {"in": sum(r["tokens"]["in"] for r in recs), "out": sum(r["tokens"]["out"] for r in recs)}}
    out = {"meta": run.stage_meta({"answer_model": ans_llm.model, "verify_model": ver_llm.model, "judge_model": judge_llm.model, "k": k,
                                   "prompt_hash": run.prompt_hash(), "judge_prompt_hash": run.prompt_hash()}),
           "agg": agg, "questions": recs}
    path = run.new_result_path(f"answers_{run.combo}_{ans_llm.label}")
    from src.combo.stages import save_json
    save_json(path, out)
    print(f"답변 결과 저장: {path}\n  판정 {labels}, 요지 충족률 {agg['core_point_fill']}", flush=True)
    return out


def with_graph(searcher, gx, results, order, near_k):
    """검색 결과(인용 포함)에서 그래프로 더할 조를 찾아 첫 결과의 related에 붙인다. gx가 없으면 그대로."""
    if gx is None or not results:
        return results
    got = {M.node_key(it["doc_id"], it["article"]) for it in results} | {M.node_key(c["doc_id"], c["article"]) for it in results for c in it.get("cited", [])}
    added = gx.expand(got, set(order[:near_k]))
    results[0]["related"] = list(results[0].get("related") or []) + graph_entries(searcher, added)
    return results


def _pct(vals, p):
    vals = sorted(v for v in vals if v is not None)
    return round(vals[min(len(vals) - 1, int(round(p / 100 * (len(vals) - 1))))], 3) if vals else None
