"""외부 참조 목록: 규정이 근거로 드는 문서 중 내부 규정에도 법령 DB에도 없어 선을 잇지 못한 것.

출처는 온톨로지 위임처(모델)와 조문 본문의 위임 말투(규칙, onto_deleg.DELEG). 사람·기관("대표이사", "이사회"), 별표·별지,
"관계 법령"처럼 어느 문서인지 없는 이름은 뺀다. 현행 여부는 사람이 법제처 등에서 확인해 채운다.
"원문에 있음"이 아니오이면 모델이 지어낸 이름일 수 있다.

  set -a; . ./.env; set +a
  python scripts/ext_refs.py eval/onto_full reports/external-refs-20261030.csv
"""
import collections
import csv
import json
import os
import re
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
sys.stdout.reconfigure(encoding="utf-8")
from graph_law import LAW_DB  # noqa: E402
from graph_law_link import GENERIC_LAW  # noqa: E402
from graph_struct import pg, resolve_law  # noqa: E402
from onto_build import norm, read_items  # noqa: E402
from onto_deleg import DELEG, GENERIC, match_doc  # noqa: E402
from src.citations import law_lookup  # noqa: E402

DOC_LIKE = re.compile(r"(법|법률|령|규칙|규정|지침|기준|요령|매뉴얼|가이드라인|계획|고시|강령|헌장|세칙|훈령|예규|협약)$")
ANNEX = re.compile(r"별표|별지|서식|붙임")
# 어느 문서인지 없는 이름: "타 규정", "회사의 관련 규정", 규칙이 잡은 문장 조각("관하여 따로 규정")
# "법"류는 이름 전체일 때만 ("법인카드"를 잡지 않게), "관련"은 어디에 있든 (search로 쓴다)
VAGUE = re.compile(r"^(법|법령|법률|시행령|시행규칙|규정|지침|대통령령|부령)$|^(이 |본 |별도|해당|각종|항|관하여|관한|위한|위하여|있어서|경우|달리|따로|특별한|"
                   r"회사|당사|본사|제반|제규|타 |다른|관할 |글로벌|국제기준|국제법|국제관습법|란 |이란 |쉽게|누설|필요한|집행 전|감독당국|적절한|주재국|내부기준|"
                   r"업무지침$|업무규정$|운영지침$)|관련")


def trim(name):
    """규칙이 「」 없이 잡은 이름은 앞 문장이 붙어 온다 ("현지 출장경비는 당사 여비규정"). 조사로 끝나는 말이 나오면 거기서 끊는다."""
    keep = []
    for tok in reversed(name.split()):
        if keep and (re.search(r"[은는의을를이가에도와과며고]$", tok) or tok in ("당사", "회사", "이", "본", "및")):
            break
        keep.insert(0, tok)
    return " ".join(keep)


def kind(n):
    if re.search(r"(법|법률|시행령|시행규칙)$", n):
        return "법령 (법령 DB에 없음)"
    if re.search(r"(고시|훈령|예규)$", n) or re.search(r"^(국가|중소|금융|공공기관|기획재정부|행정안전부)", n):
        return "행정규칙·고시·정부 지침"
    if re.search(r"계획$", n):
        return "계획서 (매년 따로 만드는 문서)"
    if re.search(r"회계기준|매뉴얼|상장|어문|협약|감독규정|업무규정|고시|코스닥|소프트웨어|암호|공인기관|국민권익|프로그램", n):
        return "외부 기준·매뉴얼"
    return "내부 문서로 보이나 규정 목록에 없음 (옛 이름·미등록 추정)"


def main(onto, out):
    arts = read_items()
    ex = json.load(open(os.path.join(onto, "extract.json"), encoding="utf-8"))
    with pg("regulations") as c:
        titles = {norm(t): d for d, t in c.execute("select doc_id, title from documents").fetchall() if t}
    law_titles, _ = law_lookup.load(LAW_DB)
    refs = []   # (이름, 조, 출처)
    for aid, v in ex.items():
        for s in v["규정문"]:
            refs += [(t["이름"].strip("「」｢｣ "), aid, "모델") for t in s.get("위임처") or []]
    for aid, a in arts.items():
        for it in a["items"]:
            refs += [(m.group(1).strip() if m.group(1) else trim(m.group(2)), aid, "규칙") for m in DELEG.finditer(it["text"])]
    rows = collections.defaultdict(lambda: {"조": set(), "출처": set(), "원문": False})
    for name, aid, src in refs:
        n = name.strip()
        body = " ".join((i.get("head") or "") + i["text"] for i in arts[aid]["items"])
        # "「소프트웨어 보안약점 기준」([별표 2])"처럼 별표가 이름 밖 괄호에 붙으면 같은 규정의 별표다
        in_annex = re.search(re.escape(n) + r"[」｣]?\s*\(?\s*\[?\s*별표", body)
        if (len(norm(n)) < 3 or norm(n) in GENERIC or ANNEX.search(n) or in_annex or VAGUE.search(n) or GENERIC_LAW.match(n)
                or not DOC_LIKE.search(n) or match_doc(n, titles) or resolve_law(n, law_titles)[0]):
            continue
        r = rows[n]
        r["조"].add(aid)
        r["출처"].add(src)
        r["원문"] |= norm(n) in norm(body)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["분류", "이름", "인용 조 수", "인용 조 (규정명 조ID)", "출처", "원문에 있음", "현행 여부(사람이 채움)", "처리(사람이 채움)"])
        for n, r in sorted(rows.items(), key=lambda x: (kind(x[0]), -len(x[1]["조"]))):
            where = "; ".join(f"{arts[a]['doc']} {a}" for a in sorted(r["조"]))
            w.writerow([kind(n), n, len(r["조"]), where, "+".join(sorted(r["출처"])), "예" if r["원문"] else "아니오", "", ""])
    c = collections.Counter(kind(n) for n in rows)
    print(f"외부 참조 {len(rows)}개 {dict(c)}, 원문에 없음(모델이 지어냈을 수 있음) {sum(not r['원문'] for r in rows.values())}개 -> {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
