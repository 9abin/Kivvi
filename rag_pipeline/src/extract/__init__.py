"""원문 파일 목록과 텍스트 추출."""
import glob
import io
import os
import re

from pypdf import PdfReader

from config import SOURCE_ROOT, TEXT_DIR
from src.extract.hwp import extract_hwp
from src.extract.hwpx import extract_hwpx


def list_sources():
    """(고유번호, 파일명, 전체경로) 목록. 폴더명 끝 숫자가 고유번호다."""
    rows = []
    for f in sorted(glob.glob(os.path.join(SOURCE_ROOT, "*", "*"))):
        if not f.lower().endswith((".hwp", ".hwpx", ".pdf")):
            continue
        folder, name = os.path.split(f)
        idx = folder.rsplit("_", 1)[1]
        rows.append((idx, name, f))
    # 같은 고유번호에 hwp·hwpx가 있으면 함께 놓인 pdf(보조 확인용 변환본)는 원본으로 보지 않는다.
    has_native = {idx for idx, name, _ in rows if not name.lower().endswith(".pdf")}
    return [r for r in rows if not (r[1].lower().endswith(".pdf") and r[0] in has_native)]


def extract_one(path):
    ext = path.lower().rsplit(".", 1)[1]
    if ext == "hwp":
        return extract_hwp(path)
    if ext == "hwpx":
        return extract_hwpx(path)
    text = "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    # 정보보안관리지침(23753) 표 안 일부 글자가 폰트 매핑이 깨져 가운뎃점(·)이 "ž"로
    # 추출된다(2026.10.12 발견, "자료수ž발신" → "자료 수·발신"). 같은 문서에 가운뎃점이
    # 169번 정상 추출된 것과 대조해 확인했다. 이 PDF들에서 "ž"가 정상적으로 쓰일 일은
    # 없으므로 가운뎃점으로 되돌린다.
    return text.replace("ž", "·")


def extract_all():
    os.makedirs(TEXT_DIR, exist_ok=True)
    report = []
    for idx, name, path in list_sources():
        try:
            txt, status = extract_one(path), "ok"
        except Exception as e:  # 실패 파일은 보고서에 남긴다
            txt, status = "", f"ERROR {type(e).__name__}: {e}"
        io.open(os.path.join(TEXT_DIR, idx + ".txt"), "w", encoding="utf-8").write(txt)
        report.append((idx, name, status, len(txt)))
    return report
