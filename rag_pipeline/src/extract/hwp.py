"""HWP(5.x) 본문 추출. OLE 컨테이너의 BodyText 레코드에서 문단 텍스트(PARA_TEXT, tag 67)를 읽는다."""
import re
import struct
import zlib

import olefile

PARA_TEXT = 67


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
                    t = data[i:i + size].decode("utf-16le", errors="ignore")
                    out.append(re.sub(r"[\x00-\x08\x0b-\x1f]", "", t))
                i += size
        return "\n".join(out)
    finally:
        ole.close()
