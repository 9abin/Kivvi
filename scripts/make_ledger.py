"""정답 원장 만들기: 1주차 검토(박형건, 담당 21건) + 김가빈 검토 정리본의 사례.

  uv run python scripts/make_ledger.py
출력: eval/ledger/week1.jsonl, eval/ledger/gabin_doc.jsonl
가빈 corrections.json(155건)은 scripts/import_corrections.py 로 따로 가져온다.

원장 한 건 = 원문의 문제 1건. 위치는 원문에 고정(규정ID·단위·원문 조·항·인용 원문)하고, 파이프라인 ID는 연결 정보로만 둔다.
처리: 고칠 수 있음 | 판단 가능 | 판단 불가   (1주차 F·J·X 분류)
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.findings import locate  # noqa: E402

OUT = ROOT / "eval" / "ledger"

# 1주차 §C (검토결과_노션용.md) + 최종_문제목록 F2. 정답 대상은 1주차 수정안 그대로.
WEEK1 = {
    "C1": ("인용_대상없음", "판단 가능", "", "임직원행동강령 제2조는 제5호까지, 괴롭힘 정의 없음 (J5)"),
    "C2": ("인용_대상불일치", "고칠 수 있음", "제36조", "제35조는 교육, 행동강령책임관은 제36조"),
    "C3": ("인용_대상불일치", "고칠 수 있음", "제13조", "제9조는 당사자 간 해결, 비밀유지 서약은 제13조⑤"),
    "C5": ("인용_대상불일치", "고칠 수 있음", "제14조", "제7조는 정치인 등의 부당한 요구, 영리 금지는 제14조 3호"),
    "C7": ("규정명_불일치", "고칠 수 있음", "휴직자 복무관리 지침", "정식 명칭"),
    "C8": ("규정명_불일치", "고칠 수 있음", "성희롱·성폭력 예방 운영지침", "정식 명칭"),
    "C9": ("인용_대상불일치", "고칠 수 있음", "제6조", "제4조는 차별금지, 채용결격사유는 제6조"),
    "C10a": ("인용_대상없음", "판단 불가", "", "정관에 제42조의2 없음, 집행간부 근거 조문 소멸 (X1)"),
    "C10b": ("인용_대상없음", "판단 불가", "", "직제규정에 제3조의2 없음 (X1)"),
    "C11": ("인용_제목불일치", "고칠 수 있음", "채용결격사유", "제6조 제목은 채용결격사유"),
    "C12": ("인용_대상불일치", "고칠 수 있음", "제7장", "제6장은 휴직, 퇴직은 제7장"),
    "C13": ("인용_대상없음", "고칠 수 있음", "제26조", "정관에 제39조의2 없음, 경영성과협약은 제26조"),
    "C22": ("인용_대상없음", "고칠 수 있음", "제71조", "회계규정은 제77조까지, 결산서는 제71조"),
    "C23": ("규정명_불일치", "고칠 수 있음", "직무전결규정", "정식 명칭"),
    "F2a": ("인용_대상불일치", "고칠 수 있음", "제6조", "제5조는 퇴직연금, 임원 퇴직금은 제6조"),
    "F2b": ("인용_대상불일치", "고칠 수 있음", "제6조", "제5조는 퇴직연금, 임원 퇴직금은 제6조"),
}

# 김가빈 검토 정리본(2026-10-06 공유)의 사례. 담당 21건 밖이라 우리 기준을 맞추는 데 쓰지 않은 데이터
GABIN = [
    ("23536:1-0-0-0", "제21조", "인용_대상불일치", "고칠 수 있음", "제22조", "성과급지급지침: 급여규정 제21조 → 제22조(성과급)"),
    ("23536:2-1-3-0", "제39조의2", "인용_대상없음", "고칠 수 있음", "제34조", "성과급지급지침: 인사규정 제39조의2 → 제34조(직위해제)"),
    ("23492:7-1-2-0", "제13조제2항", "인용_대상불일치", "고칠 수 있음", "제4조", "복지·후생규정 제7조: 제13조② → 제4조②"),
    ("55120:26-6-0-0", "제19조제2항", "인용_대상불일치", "고칠 수 있음", "제18조", "인권경영이행지침 제26조⑥: 제19조②~제21조 → 제18~20조"),
    ("23949:72-0-0-0", "제128조", "인용_대상없음", "고칠 수 있음", "제71조", "회계규정 제72조 (1주차 C22와 같은 건)"),
    ("24002:9-2-0-0", "②", "항번호_중복", "고칠 수 있음", "③", "노사협의회규정 제9조 두 번째 ②"),
    ("23482:9-5-0-0", "⑤", "항번호_중복", "판단 불가", "", "복지후생 운영지침 제9조 ⑤ 두 번, 자녀수당 문구가 섞여 승인본 확인 필요"),
]


# 판단 가능·판단 불가의 기대 출력 (가이드라인 6-1절)
EXPECTED = {
    # 1주차 J5 판정: 행동강령 인용 부분은 해당 조문 없음을 표시, 근로기준법 부분은 유효, 취업규칙 제48조①로 보충 안내
    "C1": {"show": "대상 없음 표시 + 보충 안내", "keep": "근로기준법 제76조의2 부분은 유효", "supplement": "취업규칙 제48조①"},
    "C10a": {"show": "판단 불가", "ask": "집행간부 제도가 현재 운영 중인가, 근거 조문은 어디인가"},
    "C10b": {"show": "판단 불가", "ask": "집행간부 제도가 현재 운영 중인가, 근거 조문은 어디인가"},
    "G07": {"show": "판단 불가", "ask": "제9조 ⑤가 두 번인 승인본 확인 (자녀수당 문구 혼입)"},
}


def expected_for(gid, handling, answer):
    if gid in EXPECTED:
        return EXPECTED[gid]
    return {"show": "수정 제안", "fix": answer} if handling == "고칠 수 있음" else {}


def record(gid, src, quote, kind, handling, answer, basis, source, judge, date):
    return {"id": gid, **locate(src), "quote": quote, "type": kind, "handling": handling,
            "answer": answer, "expected": expected_for(gid, handling, answer), "basis": basis,
            "source": source, "judge": judge, "date": date, "link": {"ours": src}}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cases = json.loads((ROOT / "eval" / "week1_citations.json").read_text(encoding="utf-8"))["cases"]
    week1 = [record(c["id"], c["src"], c["raw"], *WEEK1[c["id"]], "1주차 검토", "박형건", "2026-10-05") for c in cases]
    gabin = [record(f"G{i + 1:02d}", src, q, k, h, a, b, "김가빈 검토 정리본", "김가빈", "2026-10-06")
             for i, (src, q, k, h, a, b) in enumerate(GABIN)]
    for name, rows in (("week1", week1), ("gabin_doc", gabin)):
        (OUT / f"{name}.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        print(f"{name}: {len(rows)}건 → {OUT / (name + '.jsonl')}")


if __name__ == "__main__":
    main()
