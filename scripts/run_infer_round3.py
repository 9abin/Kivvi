"""3차 봉인 문항에 LLM 판단(P3)을 돌린다 (exp_infer_dev.py를 바꾸지 않고 라벨 위치만 바꿔서)."""
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts")); sys.path.insert(0, str(ROOT / "src"))
import eval_collect_dev as ecd  # noqa: E402

ecd.K = ROOT / "eval/impact_label_kit_r3/산출"
sys.argv = ["exp_infer_dev.py", "--prompt", "P3", "--splits", "봉인"]
runpy.run_path(str(ROOT / "scripts/exp_infer_dev.py"), run_name="__main__")
