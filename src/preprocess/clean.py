"""추출 잔여물 제거. 깨진 제목 토큰, 서식 표지, 머리글 번호를 걷어낸다."""
import re

# 추출 과정에서 줄 앞·뒤에 붙는 깨진 글자 토큰 (원문 내용이 아님)
JUNK_TOKENS = [
    "捤獥汤捯慤桥潴景", "捤獥汤捯", "慤桥潴景", "湰灧慤桥潴景", "潴景", "桥潴景", "慤桥",
    "漠杳", "氠瑢", "汤捯", "楴䵴", "捤獥", "湰灧", "&lt;", "&gt;",
]


# 원문자와 모양만 비슷한 글자 (ⓛ, ➀~➉, ➊~➓). 그대로 두면 항으로 나뉘지 않아 다음 항이 앞 항에 합쳐진다
# (도서관리지침 제7조 「➃」, 연봉제운영지침 제10조, 정보보안관리지침 제9조·제14조). 2026.10.13
CIRCLED_10 = "①②③④⑤⑥⑦⑧⑨⑩"
LOOKALIKE = str.maketrans({"ⓛ": "①", **{chr(0x2780 + i): CIRCLED_10[i] for i in range(10)},
                           **{chr(0x278A + i): CIRCLED_10[i] for i in range(10)}})
# 줄 머리 「제l조」: 숫자 1 자리에 영문 소문자 l (보안업무지침 제1조가 조로 인식되지 않아 통째로 빠짐). 2026.10.13
L_FOR_ONE = re.compile(r"^([^\w\n]{0,8}제)l(?=\s*\d*\s*조)")
PAGE_NUMBER = re.compile(r"^[-–]\s*\d{1,3}\s*[-–]$")  # 쪽 번호 '- 5 -'


def clean_text(txt):
    out = []
    for line in txt.splitlines():
        s = L_FOR_ONE.sub(r"\g<1>1", line.translate(LOOKALIKE))
        for token in JUNK_TOKENS:
            s = s.replace(token, "")
        s = re.sub(r"^\s*\d+(?:-\d+)+\.?\s*[가-힣·\w ]+$", "", s)  # '3-3. 감사규정' 같은 머리글
        if PAGE_NUMBER.match(s.strip()):
            continue
        s = re.sub(r"[ \t　]+", " ", s).strip()
        if s:
            out.append(s)
    return "\n".join(out)
