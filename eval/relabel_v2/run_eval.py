"""평가셋 v2(bm = 개발, test = 시험)에서 파이프라인을 돌린다 (리포트 94). 스크래치패드 스크립트(build_pool_new · run_pipe_val)를 옮긴 것.
작업 파일은 out/relabel_v2/ (git 제외): pool_<세트>.json, pairs_<세트>.json, decide_scores.json(판단 모델 점수, 세트 공통).

  1) uv run python eval/relabel_v2/run_eval.py pool <bm|test>        # 검색 후보(규칙 + 도메인 그래프 + RAG)
  2) uv run python eval/relabel_v2/run_eval.py pairs <bm|test>       # 판단 모델 입력
  3) <kd_venv python> scripts/decide_score_pairs.py <kanana_decide 폴더> out/relabel_v2/pairs_<세트>.json out/relabel_v2/decide_scores.json
  4) uv run python eval/relabel_v2/run_eval.py pipe <bm|test> <출력.jsonl> [orders=2] [문항 수]   # 거르기 -> luna -> 답변
그다음 metrics.py rank, judge_solar.py로 잰다.

값 변경 평가 패치(CITES out 0.7, MENTIONS_TITLE out 0.6)는 리포트 92와 같다(eval_final_view.py와 같은 설정).
점수가 없는 규칙 후보는 남긴다(apply_decide). bm의 점수는 261110에 잰 것이다.
"""
import copy
import glob
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
WORK = ROOT / "out/relabel_v2"


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def load_regs():
    return {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}


def changes(d):
    return jl(ROOT / f"eval/relabel_v2/{d}/changes.jsonl")


def pool(d):
    import eval_recall_all as era
    from regcheck import kg as K
    from regcheck.cand_state import source_of
    from regcheck.graph_source import GraphSource
    from regcheck.impact_view import load_context, run
    regs = load_regs(); ctx = load_context(ROOT, regs); rag0 = era.build_rag(ctx); g = GraphSource.load(ROOT)
    by = {r["name"]: k for k, r in regs.items()}
    base = copy.deepcopy(K.ONTOLOGY["change_paths"])
    K.ONTOLOGY["change_paths"] = {k: list(v) for k, v in base.items()}
    K.ONTOLOGY["change_paths"]["값 변경"] = base["값 변경"] + [("CITES", "out", 0.7), ("MENTIONS_TITLE", "out", 0.6)]
    out = []
    for m in changes(d):
        s = m["수정"]
        r = run(ctx, regs, by[s["규정"]], s["개정문"], m["id"], rag=rag0, graph=g); cq = r["cq"]
        cands = r["view"]["all"] if not r["error"] else []
        out.append({"q": f"{d}:{m['id']}", "d": d, "kind": cq.kind,
                    "cands": [{"id": c.id, "tier": c.tier, "paths": c.paths, "score": c.score, "src": source_of(c.paths)} for c in cands]})
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / f"pool_{d}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(d, "문항", len(out), "문항당 후보", sum(len(p["cands"]) for p in out) / max(len(out), 1))


def pairs(d):
    from regcheck.collect import Candidate
    from regcheck.evidence import change_keys
    from regcheck.final_view import decide_inputs
    from regcheck.impact_view import analyze, load_context
    regs = load_regs(); ctx = load_context(ROOT, regs); by = {r["name"]: k for k, r in regs.items()}
    ch = {m["id"]: m for m in changes(d)}
    out = []
    for p in json.loads((WORK / f"pool_{d}.json").read_text(encoding="utf-8")):
        m = ch[p["q"].split(":")[1]]; s = m["수정"]
        cq = analyze(regs[by[s["규정"]]], s["개정문"], m["id"])
        cands = []
        for x in p["cands"]:
            c = Candidate(x["id"], x["score"]); c.paths = list(x["paths"]); c.tier = x["tier"]; cands.append(c)
        st = (cq.starts or [""])[0]
        for cid, state in decide_inputs(cands, cq.kind, s["개정문"], ctx.unit_text.get(st, ""), ctx.unit_text,
                                        keys_for=lambda cid: change_keys(cq, ctx.nodes, cid)).items():
            out.append({"key": f"{p['q']}|{cid}", "kind": cq.kind, "state": state})
    (WORK / f"pairs_{d}.json").write_text(json.dumps({"pairs": out}, ensure_ascii=False), encoding="utf-8")
    print("판단 모델 입력", len(out))


def pipe(d, out_f, orders=2, lim=None):
    from regcheck.answer_writer import make_writer, write_answers
    from regcheck.cand_state import build_states
    from regcheck.collect import Candidate
    from regcheck.impact_pipeline import DECIDE_TH, apply_decide, apply_luna, final_list, luna_items, pick_for_luna, rule_evidence
    from regcheck.impact_view import analyze, load_context
    from regcheck.luna_rerank import change_block, luna_call, rank_list
    regs = load_regs(); ctx = load_context(ROOT, regs); by = {r["name"]: k for k, r in regs.items()}
    pl = {p["q"]: p for p in json.loads((WORK / f"pool_{d}.json").read_text(encoding="utf-8"))}
    S = json.loads((WORK / "decide_scores.json").read_text(encoding="utf-8"))
    luna = luna_call(); writer = make_writer("gpt-5.6-luna")
    out = Path(out_f)
    done = {r["q"] for r in jl(out)} if out.exists() else set()
    lock = threading.Lock()

    def one(m):
        q = f"{d}:{m['id']}"; s0 = m["수정"]; rid = by[s0["규정"]]; amend = s0["개정문"]; p = pl[q]
        cq = analyze(regs[rid], amend, m["id"])
        states = build_states([Candidate(c["id"], c["score"], list(c["paths"]), c["tier"]) for c in p["cands"]])
        lv = {c["id"]: S[f"{q}|{c['id']}"]["lv"] for c in p["cands"] if f"{q}|{c['id']}" in S}
        apply_decide(states, lv, DECIDE_TH, cq.kind)
        rule_evidence(states, cq, ctx.nodes, ctx.unit_text)
        start = cq.starts[0] if cq.starts else None
        change_text = change_block(regs[rid], amend, start)
        picked = pick_for_luna(states)
        res = rank_list(change_text, luna_items(picked, ctx.nodes, ctx.unit_text), luna, orders=orders, seed=0) if picked else {"order": [], "picks": {}, "runs": [], "usage": []}
        if picked:
            apply_luna(picked, res)
        view = final_list(states)
        write_answers(view["rule"] + view["other"], amend, change_text, ctx.nodes, writer)
        rec = {"q": q, "kind": cq.kind, "change_text": change_text, "picked": [s.id for s in picked],
               "runs": [[x["id"] for x in r] for r in res["runs"]], "usage": res["usage"],
               "rule": [s.to_dict() for s in view["rule"]], "other": [s.to_dict() for s in view["other"]]}
        with lock, out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return q, len(view["rule"]), len(view["other"])

    todo = [m for m in changes(d) if f"{d}:{m['id']}" not in done][:lim]
    print("할 문항", len(todo), flush=True)
    with ThreadPoolExecutor(6) as ex:
        for r in ex.map(one, todo):
            print(r, flush=True)


if __name__ == "__main__":
    cmd, d = sys.argv[1], sys.argv[2]
    if cmd == "pool":
        pool(d)
    elif cmd == "pairs":
        pairs(d)
    elif cmd == "pipe":
        pipe(d, sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 2, int(sys.argv[5]) if len(sys.argv) > 5 else None)
