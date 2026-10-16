"""전이 확인용 별도 평가셋 만들기 (eval/holdout/, 2026.10.15).

기존 평가셋(100·190·N-set 80)과 **규정·조문·질문 유형이 겹치지 않는** 질문 50개를 만든다. 답변 뒤 누락 검사(self-ask)·주제 링크
같은 장치가 개발에 쓴 100문항 밖에서도 효과가 있는지(전이) 보기 위한 세트다. 시스템 답변을 보기 전에 규정 원문에서 gold를 만들어 고정한다.

방법
1. 기존 세트가 gold로 쓴 조문과 그 규정 중 많이 쓰인 규정(복무·여비·취업규칙·인사·승진·행동강령·정보보안 등)을 뺀 나머지 규정에서 조문을 뽑는다.
2. 질문 유형은 기존 라벨(수치기준·잘못된전제·규정에없음·복수조문종합·단일조문사실·예외단서·개별사정·일부만답가능)에 없는 것만 쓴다:
   절차·순서 / 기한·시점 / 승인권자·담당부서 / 제출서류·양식 / 위반시제재 / 회사의무 / 조건부상황 / 적용대상범위.
3. 조문 본문(과 그 조가 인용하는 조문 하나)을 GPT에 주고 직원 말투의 질문·질문자·정답요지·근거 인용·금지내용을 JSON으로 받는다.
4. 인용 문구가 실제 조문에 있는지(공백 무시) 확인해 틀린 것은 버린다. 규정당 최대 3문항, 유형별로 고르게 50개를 남긴다.

실행 (저장소 루트, OPENAI 키 필요): uv run python -m scripts.make_holdout_set [--n 50] [--seed 7]
출력: eval/holdout/questions_holdout_50.jsonl (답변 평가 형식), questions_holdout_50_retrieval.jsonl (검색 정답), README.md
"""
import argparse
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict

from dotenv import load_dotenv

from config import BASE_DIR, OUT_DIR
from src.answer.generate import Generator

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

OUT_DIR_H = os.path.join(BASE_DIR, "eval", "holdout")
TYPES = ["절차·순서", "기한·시점", "승인권자·담당부서", "제출서류·양식", "위반시제재", "회사의무", "조건부상황", "적용대상범위"]
# 기존 세트가 많이 쓴 규정은 뺀다 (근거 상위 + 자주 나온 것)
SKIP_REGS = {"복무규정", "여비규정", "취업규칙", "인사규정", "승진지침", "정보보안관리지침", "임직원행동강령", "유연근무제 운영지침",
             "복지후생 운영지침", "회의비 지출에 관한 지침", "퇴직금지급규정", "법인카드 사용지침", "급여규정", "근무평정시행지침",
             "개인정보 보호 및 관리지침", "한국벤처투자 이해충돌 방지제도 운영지침", "직장 내 괴롭힘 예방 운영지침", "징계지침",
             "복지·후생규정", "성과급지급지침", "퇴직금중간정산 운영지침", "계약사무처리지침", "전문계약직 운영지침",
             "성희롱·성폭력 예방 운영지침"}
# 직원이 물을 만한 규정만 (자금세탁·정관·이사회·감사 등 기관 운영 규정은 뺀다)
PICK_REGS = {"사옥관리지침", "문서관리규정", "기록물관리규정", "공무직관리지침", "사택 대여 및 관리지침", "임직원교육훈련지침",
             "인장관리지침", "도서관리지침", "민원 처리지침", "홍보업무지침", "차량관리지침", "물품관리규정", "전화 녹음관리 및 운영지침",
             "임금피크제 운영지침", "임원 복무 등에 관한 지침", "해외 파견 업무처리 및 해외사무소 운영지침", "해외근무직원 보수·복지규정",
             "전산업무지침", "보안업무지침", "영상정보처리기기 관리 및 운영지침", "혁신제안제도 운영지침", "정보공개지침", "연구용역 관리지침",
             "직무전결규정", "직제규정", "노사협의회규정", "안전관리규정", "부정청탁 및 금품등 수수의 신고사무 처리지침",
             "공익신고 처리 및 신고자 보호 등에 관한 운영지침", "부패신고 처리 및 신고자 보호 등에 관한 운영지침", "직무청렴계약 운영지침",
             "서식관리지침", "휴직자 복무관리 지침", "상품권 관리지침", "업무추진비 및 판공비 지출에 관한 지침", "준법윤리경영지침",
             "인권경영이행지침", "윤리강령", "직원채용지침", "안전사고에 관한 문책 지침", "제수수료 및 수당지급 지침", "경영공시 관리지침",
             "내규관리규정", "직제지침"}

PROMPT = """너는 사내 규정 챗봇의 평가 문항을 만든다. 규정 조문 하나(와 그 조가 가리키는 조문 하나)가 주어진다.
직원이 실제로 물을 법한 **구어체 질문**을 지정된 유형으로 하나 만들고, 정답을 조문에서 그대로 뽑는다.

규칙
- 질문은 규정 이름이나 조 번호를 말하지 않는다. 상황을 한두 문장으로 말하는 직원 말투. 조문에 없는 말을 전제로 깔지 않는다.
- 유형 뜻: 절차·순서(어떤 순서로 무엇을 하나) / 기한·시점(언제까지, 언제부터) / 승인권자·담당부서(누구 승인·어느 부서) /
  제출서류·양식(무엇을 내나) / 위반시제재(어기면 어떻게 되나) / 회사의무(회사가 해 줘야 하는 것) / 조건부상황(~인 경우에는) / 적용대상범위(누구에게 적용되나)
- 정답요지: 조문으로 답할 수 있는 핵심 2~3개. 각 요지는 조문 내용을 그대로 담는다. 조문에 없는 내용은 쓰지 않는다.
- 근거: 요지의 출처. 인용은 조문 본문에서 **글자 그대로** 복사한 한 구절(20~80자). 가리키는 조문이 질문에 꼭 필요한 조건을 담고 있으면 그것도 근거와 요지에 넣는다(아니면 넣지 않는다).
- 금지내용: 이 질문에 대해 챗봇이 말하면 틀리는 내용 하나(조문과 반대되는 단정).
- 질문자: 직군·직책과 상황 한 줄.
- 조문이 질문을 만들기에 부적합하면(정의·목적·개정 절차처럼 직원 질문이 안 나오는 조) {{"부적합": true}}만 쓴다.
출력은 JSON 하나만:
{{"q": "...", "질문자": "...", "유형": "...", "정답요지": ["...", "..."], "근거": [{{"규정": "...", "조항": "제N조 제M항", "인용": "..."}}], "금지내용": ["..."]}}"""


def load_articles():
    rows = []
    for line in open(os.path.join(OUT_DIR, "chunks.jsonl"), encoding="utf-8"):
        r = json.loads(line)
        if r.get("상태") == "정상" and r["조항"].startswith("제") and len(r.get("본문", "")) >= 150:
            rows.append(r)
    return rows


def squash(s):
    return re.sub(r"\s+", "", s or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--tries", type=int, default=90)
    ap.add_argument("--hard", action="store_true",
                    help="조문 용어를 피한 일상어 질문 + 가리키는 조문의 조건을 꼭 포함 (검색이 어려운 판, HH 번호)")
    args = ap.parse_args()
    random.seed(args.seed)
    os.makedirs(OUT_DIR_H, exist_ok=True)

    used = set(json.load(open(os.path.join(BASE_DIR, "eval", "tmp", "used_gold_ids.json"), encoding="utf-8")))
    rows = load_articles()
    by_id = {r["id"]: r for r in rows}
    pool = [r for r in rows if r["문서명"] in PICK_REGS and r["문서명"] not in SKIP_REGS and r["id"] not in used]
    random.shuffle(pool)
    print(f"후보 조문 {len(pool)}개 ({len({r['문서명'] for r in pool})}개 규정)")

    g = Generator()
    kept, per_reg, per_type, tried = [], Counter(), Counter(), 0
    for r in pool:
        if len(kept) >= args.n or tried >= args.tries:
            break
        if per_reg[r["문서명"]] >= 3:
            continue
        qtype = min(TYPES, key=lambda t: (per_type[t], random.random()))  # 가장 적은 유형부터
        linked = next((by_id[c["대상"]] for c in r.get("cites", []) if c.get("상태") == "정상연결" and c.get("대상") in by_id
                       and c["대상"] != r["id"]), None)
        user = f"유형: {qtype}\n\n[조문] {r['문서명']} {r['조항']}\n{r['본문']}"
        if args.hard:
            user = ("[어려운 판 규칙] 질문에는 조문의 명사·제도 이름을 그대로 쓰지 말고 직원이 일상에서 쓰는 말로 바꿔 쓴다 "
                    "(예: '사택 대여' → '회사가 집 구해 주는 거', '임차보증금' → '전세금', '복직' → '다시 출근'). "
                    "가리키는 조문이 있으면 그 조문의 조건·예외를 요지와 근거에 반드시 포함한다.\n\n") + user
        if linked:
            user += f"\n\n[이 조가 가리키는 조문] {linked['문서명']} {linked['조항']}\n{linked['본문'][:900]}"
        tried += 1
        raw = g._ask(PROMPT, user)
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            continue
        try:
            d = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if d.get("부적합") or not d.get("q") or not d.get("근거"):
            continue
        # 인용 검증: 규정·조가 조문 또는 가리키는 조문과 같고, 인용이 본문에 있어야 한다
        ok, ids = True, []
        for e in d["근거"]:
            src = r if squash(e.get("규정")) == squash(r["문서명"]) and e.get("조항", "").startswith(r["조항"]) else (
                linked if linked and squash(e.get("규정")) == squash(linked["문서명"]) and e.get("조항", "").startswith(linked["조항"]) else None)
            if src is None or squash(e.get("인용")) not in squash(src["본문"]):
                ok = False
                break
            ids.append(src["id"])
        if not ok or not d.get("정답요지"):
            print(f"  버림(인용 불일치): {r['문서명']} {r['조항']}")
            continue
        qid = f"{'HH' if args.hard else 'H'}{len(kept)+1:03d}"
        kept.append({"id": qid, "q": d["q"], "유형": qtype, "질문자": d.get("질문자", ""), "답가능여부": "가능",
                     "근거": d["근거"], "정답요지": d["정답요지"], "금지내용": d.get("금지내용", []), "출처조": sorted(set(ids)),
                     "비고": f"holdout 2026.10.15 생성, 원천 {r['문서명']} {r['조항']}"})
        per_reg[r["문서명"]] += 1
        per_type[qtype] += 1
        print(f"{qid} [{qtype}] {r['문서명']} {r['조항']} ← {d['q'][:50]}")

    stem = f"questions_holdout_{'hard_' if args.hard else ''}{args.n}"
    with open(os.path.join(OUT_DIR_H, f"{stem}.jsonl"), "w", encoding="utf-8") as f:
        for q in kept:
            f.write(json.dumps({k: v for k, v in q.items() if k != "출처조"}, ensure_ascii=False) + "\n")
    with open(os.path.join(OUT_DIR_H, f"{stem}_retrieval.jsonl"), "w", encoding="utf-8") as f:
        for q in kept:
            f.write(json.dumps({"id": q["id"], "q": q["q"], "유형": q["유형"], "정답": q["출처조"],
                                "정답묶음": [[a] for a in q["출처조"]]}, ensure_ascii=False) + "\n")
    print(f"\n{len(kept)}문항 저장 (시도 {tried}). 유형: {dict(per_type)}\n규정: {dict(per_reg)}")


if __name__ == "__main__":
    main()
