"""역할 가드 (app/web_agent.guard): 승인·반려는 승인권자만, 공포(등록·공지)는 담당자, 일반 직원은 아무것도 못 한다."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("psycopg")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import web_agent as W  # noqa: E402


@pytest.mark.parametrize("role,action,ok", [
    ("editor", "draft", True), ("editor", "submit", True), ("editor", "decide", True), ("editor", "publish", True), ("editor", "scan", True),
    ("editor", "approve", False), ("editor", "reject", False),
    ("approver", "approve", True), ("approver", "reject", True),
    ("approver", "draft", False), ("approver", "decide", False), ("approver", "submit", False), ("approver", "publish", False),
    ("employee", "draft", False), ("employee", "approve", False), ("", "approve", False),
])
def test_role_matrix(role, action, ok):
    g = W.guard(role, action, "누군가", "")        # cid를 비우면 기안자 확인(DB)은 건너뛴다
    assert (g is None) == ok
    if not ok:
        assert ("허용되지 않는" in g["error"]) or (not role and "역할이 지정되지 않았습니다" in g["error"])
