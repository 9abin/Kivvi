"""최종 화면(regcheck.final_view) 평가: 문항당 보이는 후보 수와 정답 유지율 (리포트 92).
  1) uv run python scripts/eval_final_view.py build <출력 폴더> val=<폴더> dom2=<폴더> bm=<폴더>
     -> <출력 폴더>/pool.json(문항별 후보 · 정답), pairs.json(판단 모델 입력)
  2) <kd_venv python> scripts/decide_score_pairs.py <모델 폴더> <출력 폴더>/pairs.json <출력 폴더>/scores.json
  3) uv run python scripts/eval_final_view.py eval <출력 폴더> [--dev val,dom2] [--test bm]
세트 폴더는 산출/changes.jsonl, 산출/labels_final.jsonl을 담는다 (eval_gabin_bench.py와 같음). 한석희 연결 표 층은 쓰지 않는다.
선택 규칙(미리 정함): 규칙 임계값은 개발 세트 규칙 정답 유지 90% 이상인 가장 높은 값, RAG 개수는 개발 세트 전체 유지율이 RAG 20개 때보다
3%p 이상 떨어지지 않는 가장 작은 값. 시험 세트는 고른 값으로 한 번만 잰다. 정답은 AI 초안이다.
"""
import copy
import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
from regcheck.collect import Candidate  # noqa: E402
from regcheck.final_view import decide_inputs, final_view, is_rule  # noqa: E402


def _jl(p: Path) -> dict:
    return {json.loads(l)["id"]: json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()}


def build(out: Path, sets: dict):
    import eval_recall_all as era
    from regcheck import kg as K
    from regcheck.impact_view import load_context, run
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    ctx = load_context(ROOT, regs); rag0 = era.build_rag(ctx)
    by = {r["name"]: k for k, r in regs.items()}
    lab2id = {(r["name"], a["label"]): a["id"] for r in regs.values() for a in r["articles"]}
    base = copy.deepcopy(K.ONTOLOGY["change_paths"])   # eval_gabin_bench.py와 같은 설정
    K.ONTOLOGY["change_paths"] = {k: list(v) for k, v in base.items()}
    K.ONTOLOGY["change_paths"]["값 변경"] = base["값 변경"] + [("CITES", "out", 0.7), ("MENTIONS_TITLE", "out", 0.6)]
    pool, pairs = [], []
    for name, d in sets.items():
        ch, lb = _jl(d / "산출/changes.jsonl"), _jl(d / "산출/labels_final.jsonl")
        for i, m in ch.items():
            s = m["수정"]
            gold = sorted({lab2id[(e["규정"], e["조"])] for e in lb[i]["영향"] if (e["규정"], e["조"]) in lab2id})
            r = run(ctx, regs, by[s["규정"]], s["개정문"], i, rag=rag0)
            cq = r["cq"]; q = f"{name}:{i}"
            cands = r["view"]["all"] if cq.kind != "분석 실패" else []
            pool.append({"q": q, "d": name, "gold": gold, "kind": cq.kind,
                         "cands": [{"id": c.id, "tier": c.tier, "paths": c.paths, "score": c.score} for c in cands]})
            st = (cq.starts or [""])[0]
            for cid, state in decide_inputs(cands, cq.kind, s["개정문"], ctx.unit_text.get(st, ""), ctx.unit_text).items():
                pairs.append({"key": f"{q}|{cid}", "kind": cq.kind, "state": state})
        print(name, "문항", len(ch), flush=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "pool.json").write_text(json.dumps(pool, ensure_ascii=False), encoding="utf-8")
    (out / "pairs.json").write_text(json.dumps({"pairs": pairs}, ensure_ascii=False), encoding="utf-8")
    print("판단 모델 입력", len(pairs))


def _cands(q):
    out = []
    for x in q["cands"]:
        c = Candidate(x["id"], x["score"]); c.paths = list(x["paths"]); c.tier = x["tier"]; out.append(c)
    return out


def measure(pool, S, names, th, n):
    nq = shown = keep = G = allk = rule_g = rule_k = 0
    for q in pool:
        if q["d"] not in names:
            continue
        nq += 1; g = set(q["gold"]); cs = _cands(q)
        lv = {c.id: S[f"{q['q']}|{c.id}"]["lv"] for c in cs if f"{q['q']}|{c.id}" in S}
        v = final_view(cs, lv, rule_th=th, rag_n=n)
        ids = {c.id for c in v["rule"] + v["rag"]}
        shown += len(ids); keep += len(g & ids); G += len(g); allk += len(g & {c.id for c in cs})
        rg = g & {c.id for c in cs if is_rule(c)}; rule_g += len(rg); rule_k += len(rg & {c.id for c in v["rule"]})
    return dict(nq=nq, per_q=shown / max(nq, 1), keep=keep, G=G, all=allk, rule_g=rule_g, rule_k=rule_k)


def evaluate(out: Path, dev: list, test: list):
    pool = json.loads((out / "pool.json").read_text(encoding="utf-8")); S = json.loads((out / "scores.json").read_text(encoding="utf-8"))
    miss = sum(1 for p in json.loads((out / "pairs.json").read_text(encoding="utf-8"))["pairs"] if p["key"] not in S)
    print(f"점수 없는 후보 {miss}개 (없으면 숨기지 않음)")
    f = lambda m: f"문항당 {m['per_q']:5.1f}개 | 전체 정답 {m['keep']}/{m['G']} ({m['keep'] / max(m['G'], 1):.0%}, 수집 전체 {m['all'] / max(m['G'], 1):.0%}) | 규칙 정답 {m['rule_k']}/{m['rule_g']}"  # noqa: E731
    print("\n## 개발", dev)
    th = None
    for t in (0.5, 1.0, 1.5):
        m = measure(pool, S, dev, t, 10**6); print(f"  규칙 ≥{t}: {f(m)}")
        if m["rule_k"] / max(m["rule_g"], 1) >= 0.9:
            th = t
    th = th or 0.5
    ref = measure(pool, S, dev, th, 20)["keep"] / max(measure(pool, S, dev, th, 20)["G"], 1)
    n_sel = 20
    for n in (5, 10, 15, 20):
        m = measure(pool, S, dev, th, n); print(f"  규칙 ≥{th}, RAG {n:2d}개: {f(m)}")
        if m["keep"] / max(m["G"], 1) >= ref - 0.03 and n < n_sel:
            n_sel = n
    print(f"선택: 규칙 ≥{th}, RAG {n_sel}개")
    print("\n## 시험 (한 번)", test)
    print(f"  {f(measure(pool, S, test, th, n_sel))}")
    print(f"  (거르기 전: {f(measure(pool, S, test, -1, 10**6))})")


if __name__ == "__main__":
    cmd, out = sys.argv[1], Path(sys.argv[2])
    if cmd == "build":
        build(out, {a.split("=", 1)[0]: Path(a.split("=", 1)[1]) for a in sys.argv[3:] if "=" in a})
    else:
        opt = lambda k, d: sys.argv[sys.argv.index(k) + 1].split(",") if k in sys.argv else d  # noqa: E731
        evaluate(out, opt("--dev", ["val", "dom2"]), opt("--test", ["bm"]))
