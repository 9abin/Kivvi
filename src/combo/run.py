"""조합 실험 실행기.

  python -m src.combo.run configs/combo/<설정>.toml                 # ① 바꿔 쓰기 → ② 후보 → ③ 리랭크 → (선택) → 최종 근거·지표
  python -m src.combo.run configs/combo/*.toml                      # 여러 설정을 차례로 (중간 결과는 공유, 있으면 다시 계산하지 않음)
  python -m src.combo.run <설정> --stage cands                      # 해당 단계까지만
  python -m src.combo.run <설정> --stage answer                     # ⑥ 답변·채점 (주지표 상위 조합에만)
  python -m src.combo.run <설정> --set data.limit=10 --set select.enabled=true
  python -m src.combo.run <설정> --tag r2                           # 같은 설정 반복 (select, results 파일 이름에 _r2)
  python -m src.combo.run <설정> --force rerank                     # 해당 단계 캐시를 무시하고 다시 계산

DB는 환경 변수 PG_*(.env)로 고른다. 결과는 eval/combo/<평가셋 이름>/ 에 저장되고 덮어쓰지 않는다.
"""
import argparse
import os
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"

STAGES = ["rewrite", "cands", "rerank", "select", "final", "answer"]


def run_one(path, args):
    from src.combo.stages import Run, load_config
    cfg = load_config(path, args.set)
    if args.limit:
        cfg["data"]["limit"] = args.limit
    r = Run(cfg, run_tag=args.tag, force=args.force.split(",") if args.force else ())
    print(f"\n=== {cfg['_name']} | 조합 {r.combo} | 질문 {len(r.gold)}개 | DB {os.environ.get('PG_DATABASE')}", flush=True)
    last = args.stage if args.stage != "all" else "final"
    rew = r.stage_rewrite()
    if last == "rewrite":
        return
    cands = r.stage_cands(rew)
    if last == "cands":
        return
    rr = r.stage_rerank(cands, rew)
    if last == "rerank":
        return
    sel = r.stage_select(cands, rr) if r.S else None
    if last == "select":
        return
    if last in ("final", "answer"):
        if last == "final":
            r.stage_final(cands, rr, sel, rew)
        else:
            from src.combo.answer import stage_answer
            stage_answer(r, cands, rr, sel)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("configs", nargs="+")
    ap.add_argument("--stage", default="all", choices=["all"] + STAGES, help="어느 단계까지 (기본 all = 최종 근거·지표까지, 답변은 따로)")
    ap.add_argument("--set", action="append", default=[], help='설정 값 임시 덮어쓰기 (예: data.limit=10, select.enabled=true)')
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N문항만 (시험용)")
    ap.add_argument("--tag", default="", help="같은 설정을 반복할 때 붙이는 이름 (예: r2)")
    ap.add_argument("--force", default="", help="캐시를 무시할 단계 (쉼표로: rewrite,cands,rerank)")
    args = ap.parse_args()
    for c in args.configs:
        run_one(c, args)


if __name__ == "__main__":
    main()
