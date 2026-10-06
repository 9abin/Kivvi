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
    return rows


def extract_one(path):
    ext = path.lower().rsplit(".", 1)[1]
    if ext == "hwp":
        return extract_hwp(path)
    if ext == "hwpx":
        return extract_hwpx(path)
    return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)


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
