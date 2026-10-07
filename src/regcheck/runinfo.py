"""실행 기록 (역추적·재현용).

결과 파일을 만들 때마다 실행 기록 하나를 남긴다: 어느 코드(git 커밋)·입력(파일 해시)·모델·설정으로 나왔는지.
결과 행에는 실행 ID(`run`)만 붙이고, 자세한 내용은 기록 파일에서 찾는다 (행마다 반복하지 않음).
  생성물 기록: out/runs/{run_id}.json   (out/과 함께 다시 만들 수 있음)
  채점 기록:   eval/runs/{run_id}.json  (git에 남겨 점수 변화를 추적)
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def code_version() -> dict:
    """커밋과 미커밋 변경 여부. 변경이 있으면 결과를 그 커밋으로 정확히 재현할 수 없다는 표시."""
    changed = _git("status", "--porcelain", "--", "src", "scripts")
    return {"commit": _git("rev-parse", "--short", "HEAD") or "없음", "uncommitted": bool(changed)}


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    if path.is_dir():
        for p in sorted(path.rglob("*")):
            if p.is_file():
                h.update(str(p.relative_to(path)).encode())
                h.update(p.read_bytes())
    else:
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


def _rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def start(step: str, inputs: list[Path] = (), models: dict | None = None, params: dict | None = None) -> dict:
    now = datetime.now()
    return {
        "run_id": f"{now:%y%m%d-%H%M%S}-{step}",
        "step": step,
        "at": now.isoformat(timespec="seconds"),
        "code": code_version(),
        "inputs": {_rel(p): file_hash(p) for p in inputs if p.exists()},
        "models": models or {},
        "params": params or {},
    }


def finish(run: dict, outputs: list[Path] = (), summary: dict | None = None, where: str = "out") -> Path:
    run["outputs"] = {_rel(p): file_hash(p) for p in outputs if p.exists()}
    run["summary"] = summary or {}
    d = ROOT / where / "runs"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{run['run_id']}.json"
    path.write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
