"""판단 모델(kanana_decide) 점수를 화면에서 부른다. 모델은 별도 가상환경에서 돌므로 scripts/decide_score_pairs.py를 하위 프로세스로 실행한다.

  lv = subprocess_scorer(root, reg, amendment, cid)   # IMPACT_DECIDE_PYTHON · IMPACT_DECIDE_MODEL이 없으면 None
  run_pipeline(..., lv=lv)                           # None이면 규칙 후보를 거르지 않는다(점수 없는 후보는 남긴다)

리뷰 앱(app/review_app.py)과 웹 편집 화면(app/web_edit.py)이 함께 쓴다(261113).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable

# 판단 모델은 GPU 메모리를 크게 쓰므로(약 7GB) 동시 요청이 와도 한 번에 하나씩 띄운다(261113 Codex 리뷰)
_GPU_LOCK = threading.Lock()


def subprocess_scorer(root: Path, reg: dict, amendment: str, cid: str, timeout: int = 300) -> Callable[[dict], dict] | None:
    python = os.environ.get("IMPACT_DECIDE_PYTHON")
    model = os.environ.get("IMPACT_DECIDE_MODEL")
    if not (python and model):
        return None

    from .change_query import analyze

    kind = analyze(reg, amendment, cid).kind

    def score(inputs: dict) -> dict:
        if not inputs:
            return {}
        with tempfile.TemporaryDirectory() as tmp:
            pairs, scores = Path(tmp) / "pairs.json", Path(tmp) / "scores.json"
            pairs.write_text(json.dumps({"pairs": [{"key": k, "kind": kind, "state": v}
                                                     for k, v in inputs.items()]}, ensure_ascii=False), encoding="utf-8")
            with _GPU_LOCK:
                subprocess.run([python, str(root / "scripts" / "decide_score_pairs.py"), model, str(pairs), str(scores)],
                               cwd=root, check=True, capture_output=True, timeout=timeout)
            return {k: v["lv"] for k, v in json.loads(scores.read_text(encoding="utf-8")).items()}

    return score
