"""고정 평가셋(가빈 val 30건 · 최종 100건) 후보 재현율: 경로(규칙 · RAG · 한석희 연결 표)별과 합친 값, 상위 10 · 20 · 50 · 전체.
  uv run python scripts/eval_gabin_bench.py val=<val_set_30 폴더> bm=<benchmark_100 폴더> [--links <연결 표 JSONL>]
폴더 안 산출/changes.jsonl, 산출/labels_final.jsonl을 읽는다 (가빈 브랜치 feature/benchmark-sets의 eval/val_set_30, eval/benchmark_100).
지표: 정답 쌍 중 후보 안(상위 k)에 든 비율 (정답 쌍 합산). 정답 0개 문항은 재현율에서 빼고 후보 수만 본다. 검색만 잰다 (판정 없음).
경로별 단독 순서: 규칙 · 한석희는 후보 전체 순서(단계 · 점수)에서 그 경로의 근거가 있는 것만, RAG는 질의 결과 순위. 고유 = 다른 경로가 못 찾고 그 경로만 찾은 쌍.
정답은 AI 초안(사람 검토 전). val은 개발용, 최종 100건은 파서 문법을 고치는 데 한 번 쓰였다(2026-11-10) — 봉인으로 보지 않는다.
"""
import collections
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
import eval_recall_all as era  # noqa: E402
from regcheck import kg as K  # noqa: E402
from regcheck.impact_view import load_context, run  # noqa: E402
from regcheck.linked_layer import LinkLayer, item_texts  # noqa: E402

KS = (10, 20, 50, "전체")


def lane_of(path: str) -> str:
    b = path.split(":")[0]
    return "RAG" if b in ("RAG", "RAG_NEW") else ("한석희" if b.startswith("HYUJIK") else "규칙")


def main():
    args = [a for a in sys.argv[1:] if "=" in a and not a.startswith("--")]
    sets = {a.split("=", 1)[0]: Path(a.split("=", 1)[1]) for a in args}
    links_path = sys.argv[sys.argv.index("--links") + 1] if "--links" in sys.argv else None
    if not sets:
        sys.exit(__doc__)
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    ctx = load_context(ROOT, regs); rag0 = era.build_rag(ctx)
    by = {r["name"]: k for k, r in regs.items()}
    lab2id = {(r["name"], a["label"]): a["id"] for r in regs.values() for a in r["articles"]}
    links = LinkLayer(links_path, hierarchy=True, item_text=item_texts(regs)) if links_path else False
    base = copy.deepcopy(K.ONTOLOGY["change_paths"])   # 값 변경에서 바깥 방향 간선(CITES out · MENTIONS_TITLE out)도 따라간다 (리포트 88)
    K.ONTOLOGY["change_paths"] = {k: list(v) for k, v in base.items()}
    K.ONTOLOGY["change_paths"]["값 변경"] = base["값 변경"] + [("CITES", "out", 0.7), ("MENTIONS_TITLE", "out", 0.6)]
    lanes = ["규칙", "RAG"] + (["한석희"] if links else [])
    for name, d in sets.items():
        ch = {json.loads(l)["id"]: json.loads(l) for l in (d / "산출/changes.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}
        lb = {json.loads(l)["id"]: json.loads(l) for l in (d / "산출/labels_final.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}
        Q, zero, fails, unmapped, sizes = [], [], 0, 0, []
        for i, m in ch.items():
            s = m["수정"]; gold = set()
            for e in lb[i]["영향"]:
                u = lab2id.get((e["규정"], e["조"]))
                if u:
                    gold.add(u)
                else:
                    unmapped += 1
            rec = []

            def rag(q, k, rec=rec):
                out = rag0(q, k); rec.append(out); return out
            r = run(ctx, regs, by[s["규정"]], s["개정문"], i, rag=rag, leave=links)
            ok = r["cq"].kind != "분석 실패"; fails += not ok
            allc = r["view"]["all"] if ok else []
            sizes.append(len(allc))
            if not gold:
                zero.append(len(allc)); continue
            L = {"합침": [x.id for x in allc]}
            for ln in ("규칙", "한석희"):
                if ln in lanes:
                    L[ln] = [x.id for x in allc if any(lane_of(p) == ln for p in x.paths)]
            L["RAG"] = [u for u in (rec[0] if (ok and rec) else []) if u not in set(r["cq"].starts or [])]
            Q.append((gold, L))
        G = sum(len(g) for g, _ in Q)
        print(f"\n## {name}: 문항 {len(ch)} (정답 있는 {len(Q)}), 정답 쌍 {G}, 개정문 분석 실패 {fails}, 연결 못한 정답 {unmapped}, 정답 0개 문항 후보 평균 {sum(zero) / max(len(zero), 1):.0f}, 문항당 후보 평균 {sum(sizes) / max(len(sizes), 1):.1f}")
        print(f"{'경로':6s} " + " ".join(f"{'@' + str(k) if k != '전체' else '전체':>6s}" for k in KS) + " | 이 경로만 찾은 쌍")
        for ln in lanes + ["합침"]:
            row = []
            for k in KS:
                h = sum(len(g & set(L[ln] if k == "전체" else L[ln][:k])) for g, L in Q)
                row.append(f"{h / G:6.0%}")
            uniq = ""
            if ln != "합침":
                oth = lambda L: set().union(*[set(L[x]) for x in lanes if x != ln])  # noqa: E731
                uniq = f" | {sum(len(g & set(L[ln]) - oth(L)) for g, L in Q) / G:5.1%}"
            print(f"{ln:6s} " + " ".join(row) + uniq)


if __name__ == "__main__":
    main()
