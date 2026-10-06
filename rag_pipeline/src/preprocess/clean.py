"""추출 잔여물 제거. 깨진 제목 토큰, 서식 표지, 머리글 번호를 걷어낸다."""
import re

# 추출 과정에서 줄 앞·뒤에 붙는 깨진 글자 토큰 (원문 내용이 아님)
JUNK_TOKENS = [
    "捤獥汤捯慤桥潴景", "捤獥汤捯", "慤桥潴景", "湰灧慤桥潴景", "潴景", "桥潴景", "慤桥",
    "漠杳", "氠瑢", "汤捯", "楴䵴", "捤獥", "湰灧", "&lt;", "&gt;",
]


def clean_text(txt):
    out = []
    for line in txt.splitlines():
        s = line
        for token in JUNK_TOKENS:
            s = s.replace(token, "")
        s = re.sub(r"^\s*\d+(?:-\d+)+\.?\s*[가-힣·\w ]+$", "", s)  # '3-3. 감사규정' 같은 머리글
        s = re.sub(r"[ \t　]+", " ", s).strip()
        if s:
            out.append(s)
    return "\n".join(out)
