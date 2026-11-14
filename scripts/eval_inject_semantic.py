"""에이전트 평가 ① 오류 주입 — 규칙 밖 유형: 「존재하는 다른 조로 잘못 인용」 (의미 검사가 잡아야 하는 오류).

  uv run python scripts/eval_inject_semantic.py [--n 40] [--seed 1] [--rank 5]
같은 인용을 짝으로 시험한다
  원래   「제35조에 따른 행동강령책임관」 그대로 → 의미 검사가 통과시켜야 함 (걸리면 오탐)
  주입   같은 규정의 다른 조로 번호만 바꿈     → 의미 검사가 걸러야 함 (통과하면 놓침)
의미 검사 2단계와 같은 방법: 설명구(「…에 따른 X」의 X)를 리랭커로 대상 규정의 모든 조와 비교해, 인용한 조의 순위가 --rank 밖이면 후보.
설명구가 없는 인용은 이 방법의 대상이 아니다 (벡터 1단계 「확인 필요(낮음)」만 남음) — 그래서 대상 비율도 함께 보고한다.
출력: out/eval/inject_semantic_s{seed}.jsonl, 화면 표. GPU(리랭커) 사용.
"""

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from regcheck import runinfo  # noqa: E402
from semantic_check import RERANKER, article_text, descriptor, free_gpu, load_regs  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--rank", type=int, default=5)
    a = ap.parse_args()
    regs = load_regs()
    edges = [json.loads(l) for l in (ROOT / "out" / "edges" / "edges_all.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    pool = [e for e in edges if e["status"] == "ok" and e["target_type"] == "article" and e["src_kind"] == "본문"
            and e.get("target_reg") in regs]
    with_desc = [(e, d) for e in pool if (d := descriptor(regs, e))]
    rng = random.Random(a.seed)
    rng.shuffle(with_desc)
    picks = []
    for e, d in with_desc:
        tgt = regs[e["target_reg"]]
        src_article = e["src"].rsplit("-", 3)[0]
        live = [x for x in tgt["articles"] if not x["deleted"] and x["id"] != src_article]
        idx = next((i for i, x in enumerate(live) if x["id"] == e["target_id"]), None)
        if idx is None or len(live) < 8:
            continue
        far = [x for i, x in enumerate(live) if abs(i - idx) > 2]   # 바로 옆 조는 내용이 이어질 수 있어 뺀다
        picks.append({"edge": e, "desc": d, "wrong": rng.choice(far)["id"]})
        if len(picks) == a.n:
            break
    run = runinfo.start("eval_inject_semantic", [ROOT / "out" / "edges" / "edges_all.jsonl", ROOT / "out" / "parsed"],
                        models={"reranker": RERANKER}, params=vars(a))

    from sentence_transformers import CrossEncoder
    model = CrossEncoder(RERANKER, device="cuda", model_kwargs={"dtype": "float16"})
    res = []
    for p in picks:
        e = p["edge"]
        tgt = regs[e["target_reg"]]
        src_article = e["src"].rsplit("-", 3)[0]
        arts = [x for x in tgt["articles"] if not x["deleted"] and x["id"] != src_article]
        scores = model.predict([(p["desc"], article_text(x, 300)) for x in arts], batch_size=16, show_progress_bar=False)
        ranked = [x["id"] for _, x in sorted(zip(scores.tolist(), arts), key=lambda z: -z[0])]
        r_orig, r_wrong = ranked.index(e["target_id"]) + 1, ranked.index(p["wrong"]) + 1
        res.append({"src": e["src"], "src_name": e["src_name"], "raw": e["raw"], "target_doc": e["target_doc"] or "(자기 규정)",
                    "desc": p["desc"], "orig": e["target_id"], "wrong": p["wrong"], "n": len(arts),
                    "rank_orig": r_orig, "rank_wrong": r_wrong,
                    "orig_flagged": r_orig > a.rank, "wrong_flagged": r_wrong > a.rank})
    del model
    free_gpu()

    out = ROOT / "out" / "eval" / f"inject_semantic_s{a.seed}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in res), encoding="utf-8")
    n = len(res)
    caught = sum(x["wrong_flagged"] for x in res)
    fp = sum(x["orig_flagged"] for x in res)
    print(f"규칙 밖 오류 주입: 다른 조로 잘못 인용 (시드 {a.seed}, 기준 순위 > {a.rank})")
    print(f"  의미 검사 대상: 조 인용 {len(pool)}개 중 설명구 있는 인용 {len(with_desc)}개 ({len(with_desc) / len(pool):.0%})")
    print(f"  주입 {n}개: 탐지 {caught} ({caught / n:.0%}), 놓침 {n - caught}")
    print(f"  같은 인용 원래대로: 오탐 {fp} ({fp / n:.0%})")
    names = {x["id"]: f"{x['label']}({x['title']})" for r in regs.values() for x in r["articles"]}
    for x in res:
        if not x["wrong_flagged"] or x["orig_flagged"]:
            tag = "놓침" if not x["wrong_flagged"] else "오탐"
            print(f"  [{tag}] {x['src_name']} 「{x['raw']}에 따른 {x['desc']}」 원래 {names[x['orig']]} {x['rank_orig']}위 / "
                  f"바꾼 {names[x['wrong']]} {x['rank_wrong']}위 (조 {x['n']}개)")
    runinfo.finish(run, [out], {"대상 인용": len(pool), "설명구 있음": len(with_desc), "주입": n, "탐지": caught, "원래 인용 오탐": fp})


if __name__ == "__main__":
    main()
