"""문서 메타데이터: 고유번호, 문서명, 위계, 개정 표기, 출처 URL."""
import json
import io
import os
import re

from config import DOWNLOAD_LIST, TIER_RULES
from src.extract import list_sources


def tier_of(title):
    if title == "한국벤처투자 정관":
        return "정관"
    for suffix, tier in TIER_RULES:
        if title.endswith(suffix):
            return tier
    return "기타"


def build_meta():
    dl = json.load(io.open(DOWNLOAD_LIST, encoding="utf-8"))
    source_by_name = {os.path.basename(r["path"]): r["source"] for r in dl}
    meta = {}
    for idx, name, _ in list_sources():
        base = re.sub(r"^\d+_", "", name.rsplit(".", 1)[0])
        title = re.sub(r"\s*\((?:[^()]*)\)\s*$", "", base).strip()
        rev = re.search(r"\(([^()]*(?:개정|제정)[^()]*)\)", name)
        meta[idx] = {
            "고유번호": idx,
            "문서명": title,
            "파일": name,
            "출처URL": source_by_name.get(name, ""),
            "개정표기": rev.group(1) if rev else "",
            "위계": tier_of(title),
        }
    return meta
