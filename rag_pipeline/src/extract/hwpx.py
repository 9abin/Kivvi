"""HWPX(zip + XML) 본문 추출. 머리글·바닥글(장식선 포함) 객체는 건너뛴다."""
import re
import zipfile
import xml.etree.ElementTree as ET

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


def extract_hwpx(path):
    z = zipfile.ZipFile(path)
    out = []
    for name in sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)):
        root = ET.fromstring(z.read(name))

        def walk(el):
            if el.tag in (HP + "footer", HP + "header"):
                return
            if el.tag == HP + "t" and el.text:
                out.append(el.text)
            if el.tag == HP + "p":
                out.append("\n")
            for child in el:
                walk(child)
                # 줄바꿈·탭 같은 태그 뒤에 이어지는 글자(꼬리 텍스트)도 빠뜨리지 않는다
                if child.tail and child.tail.strip():
                    out.append(child.tail)

        walk(root)
    return re.sub(r"\n{2,}", "\n", "".join(out))
