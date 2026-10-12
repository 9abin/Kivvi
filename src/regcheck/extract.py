"""원문 파일(HWP, HWPX, PDF)에서 문단 단위 텍스트를 뽑는다."""

from __future__ import annotations

import re
import struct
import zipfile
import zlib
from pathlib import Path

import olefile

HWPTAG_PARA_TEXT = 67
_CTRL = re.compile(r"[\x00-\x1f]")


def _hwp_records(data: bytes):
    i = 0
    while i + 4 <= len(data):
        header = struct.unpack("<I", data[i : i + 4])[0]
        tag, size = header & 0x3FF, (header >> 20) & 0xFFF
        i += 4
        if size == 0xFFF:
            size = struct.unpack("<I", data[i : i + 4])[0]
            i += 4
        yield tag, data[i : i + size]
        i += size


def extract_hwp(path: Path) -> list[str]:
    ole = olefile.OleFileIO(str(path))
    compressed = ole.openstream("FileHeader").read()[36] & 1
    sections = sorted(
        (e for e in ole.listdir() if e[0] == "BodyText"),
        key=lambda e: int(e[1].removeprefix("Section")),
    )
    paras: list[str] = []
    for entry in sections:
        data = ole.openstream(entry).read()
        if compressed:
            data = zlib.decompress(data, -15)
        for tag, body in _hwp_records(data):
            if tag == HWPTAG_PARA_TEXT:
                paras.append(_decode_para_text(body))
    ole.close()
    return paras


# HWP 5.0 규격: 제어 문자 중 아래 코드는 인라인·확장 컨트롤로 8 WCHAR(코드 + 데이터 6 + 코드)를 차지한다.
# 그냥 지우면 컨트롤 ID(예: 'tbl ', 'secd')가 뒤집힌 쓰레기 한자로 남는다.
_EIGHT_WCHAR = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}


def _decode_para_text(body: bytes) -> str:
    chars = struct.unpack(f"<{len(body) // 2}H", body[: len(body) // 2 * 2])
    kept: list[int] = []  # UTF-16 코드 단위 (서로게이트 쌍은 마지막에 한 번에 디코딩)
    i = 0
    while i < len(chars):
        c = chars[i]
        if c < 32:
            if c == 9:
                kept.append(0x20)
            elif c == 10:
                kept.append(0x0A)
            i += 8 if c in _EIGHT_WCHAR else 1
            continue
        kept.append(c)
        i += 1
    return struct.pack(f"<{len(kept)}H", *kept).decode("utf-16le", errors="replace")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _hwpx_paras(xml: str) -> list[str]:
    """문단(hp:p)을 문서 순서대로. 표 칸·쪽 머리말처럼 문단 안에 든 문단은 바깥 문단을 끊고 따로 한 줄씩 낸다.
    정규식(<hp:p>.*?</hp:p>)은 머리말·꼬리말 안쪽 문단의 끝 태그에서 멈춰 바깥 문단 본문을 통째로 잃었다 (문서관리규정 제45조 등)."""
    import xml.etree.ElementTree as ET

    out: list[str] = []

    def walk_p(p) -> None:
        buf: list[str] = []

        def visit(el) -> None:
            tag = _local(el.tag)
            if tag == "t":
                buf.append("".join(el.itertext()))
                return
            if tag == "p":
                out.append("".join(buf))
                buf.clear()
                walk_p(el)
                return
            for ch in el:
                visit(ch)

        for ch in p:
            visit(ch)
        out.append("".join(buf))

    def walk(el) -> None:
        for ch in el:
            if _local(ch.tag) == "p":
                walk_p(ch)
            else:
                walk(ch)

    walk(ET.fromstring(xml))
    return [s for s in out if s.strip()]


def extract_hwpx(path: Path) -> list[str]:
    paras: list[str] = []
    with zipfile.ZipFile(path) as z:
        names = sorted(
            (n for n in z.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n)),
            key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group()),
        )
        for name in names:
            paras.extend(_hwpx_paras(z.read(name).decode("utf-8")))
    return paras


def extract_pdf(path: Path) -> list[str]:
    from pypdf import PdfReader

    lines: list[str] = []
    for page in PdfReader(str(path)).pages:
        lines.extend((page.extract_text() or "").splitlines())
    return lines


def _unescape(s: str) -> str:
    return (
        s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
        .replace("&apos;", "'").replace("&amp;", "&")
    )


def clean(paras: list[str]) -> list[str]:
    out = []
    for p in paras:
        for line in p.split("\n"):
            line = _CTRL.sub("", line.replace(" ", " ").replace("\t", " ")).rstrip()
            if line.strip() and not re.fullmatch(r"\s*-\s*\d+\s*-\s*", line):  # PDF 쪽번호 "- 5 -"
                out.append(line)
    return out


def extract(path: Path) -> list[str]:
    ext = path.suffix.lower()
    if ext == ".hwp":
        paras = extract_hwp(path)
    elif ext == ".hwpx":
        paras = extract_hwpx(path)
    elif ext == ".pdf":
        paras = extract_pdf(path)
    else:
        raise ValueError(f"지원하지 않는 형식: {path}")
    return clean(paras)
