"""HWP(5.x) 본문 추출. OLE 컨테이너의 BodyText 레코드에서 문단 텍스트(PARA_TEXT, tag 67)를 읽는다."""
import struct
import zlib

import olefile

PARA_TEXT = 67
# 확장 제어문자(인라인 개체: 표·그림·책갈피·각주/미주·자동번호 등). 이 코드 뒤에는 그 개체를
# 가리키는 부가정보 7칸(WCHAR)이 더 따라오며, 이 부가정보는 글자가 아니다. 제어문자만 지우고
# 부가정보를 그대로 두면, 그 값이 우연히 인쇄 가능한 코드에 걸릴 때 "灳瑣"처럼 깨진 글자로
# 남는다(2026.10.12 발견: 이해충돌 방지제도 운영지침, 전산업무지침 등).
_EXT_CTRL = set(range(1, 24)) - {10, 13}  # 10=줄바꿈, 13=문단끝만 글자 하나짜리 특례
# 그 밖의 단일 제어문자(줄바꿈 0x0a, 탭 0x09는 글자로 남긴다)
_SINGLE_STRIP = set(range(0, 9)) | set(range(0x0b, 0x20))


def _decode_para_text(raw):
    """PARA_TEXT 레코드 글자 단위(WCHAR)를 읽어 제어문자와 그 부가정보를 걷어낸 글자만 돌려준다."""
    n = len(raw) // 2
    units = struct.unpack_from(f"<{n}H", raw)
    chars = []
    j = 0
    while j < n:
        u = units[j]
        if u in _EXT_CTRL:
            j += 8  # 제어문자 1 + 부가정보 7칸(WCHAR) 전체를 건너뜀
            continue
        if u in _SINGLE_STRIP:
            j += 1
            continue
        if 0xD800 <= u <= 0xDBFF and j + 1 < n and 0xDC00 <= units[j + 1] <= 0xDFFF:
            code = 0x10000 + (u - 0xD800) * 0x400 + (units[j + 1] - 0xDC00)
            chars.append(chr(code))
            j += 2
            continue
        chars.append(chr(u))
        j += 1
    return "".join(chars)


def extract_hwp(path):
    ole = olefile.OleFileIO(path)
    try:
        header = ole.openstream("FileHeader").read()
        compressed = header[36] & 1
        sections = sorted(
            [d for d in ole.listdir() if d[0] == "BodyText"],
            key=lambda d: int(d[1][7:]),
        )
        out = []
        for s in sections:
            data = ole.openstream(s).read()
            if compressed:
                data = zlib.decompress(data, -15)
            i = 0
            while i < len(data):
                h = struct.unpack_from("<I", data, i)[0]
                tag = h & 0x3FF
                size = (h >> 20) & 0xFFF
                i += 4
                if size == 0xFFF:
                    size = struct.unpack_from("<I", data, i)[0]
                    i += 4
                if tag == PARA_TEXT:
                    out.append(_decode_para_text(data[i:i + size]))
                i += size
        return "\n".join(out)
    finally:
        ole.close()
