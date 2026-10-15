"""개정 절차 안내 (고도화 5): 이 개정안이 거쳐야 할 내부 절차를 우리 규정 원문에서 읽어 단계로 보여 준다.

근거는 모두 이 회사 규정 원문이다 (법률 DB의 근로기준법 제94조는 취업규칙만).
  입안·협의   내규관리규정 제20조①: 업무담당부서장이 내규안을 작성, 다른 부서와 관련되면 관련 부서와 협의
  조정        내규관리규정 제21조①: 내규담당부서장·준법감시인과 협의 (법령등과의 저촉·중복, 분류·형식·내용)
  확정        내규관리규정 제22조①: 대표이사 결재. 이사회규정 제3조제10호 각 목의 규정은 이사회 심의·의결
              (단서: 관계 법령·정부 지침 개정에 따른 개정·폐지는 제외 — 이 개정이 거기 해당하는지는 담당 부서가 판단)
              제22조②: 정부 승인이 필요한 내규안은 위 절차 뒤 정부 승인으로 확정
  등록·공지   내규관리규정 제23조: 확정된 내규안을 등록·공지 (내규명, 입안부서, 개정일, 시행일)
  시행·주지   제24조(부칙에 따로 없으면 확정일부터 시행), 제5조(각 부서장이 지체 없이 직원에게 주지)
  취업규칙    근로기준법 제94조①: 근로자 과반수 노조(없으면 근로자 과반수)의 의견을 들어야 하고, 불리한 변경은 동의를 받아야 함
판정이 아니라 안내다. 이사회 의결 대상 여부는 「이사회규정」 제3조②10호의 목록에서 읽고, 목록에 없는 규정은 그렇다고만 알린다.
같은 원문이 바뀌면 안내도 따라 바뀐다 (목록을 코드에 적어 두지 않음).
"""

from __future__ import annotations

import re

RE_NAME = re.compile(r"「([^」]+)」")


def board_list(regs: dict[str, dict]) -> tuple[list[str], str]:
    """이사회 의결 대상 규정 이름 목록과 단서 문장 (이사회규정 제3조제2항제10호). 원문에서 읽는다"""
    reg = next((r for r in regs.values() if r["name"] == "이사회규정"), None)
    if not reg:
        return [], ""
    art = next((a for a in reg["articles"] if a["label"] == "제3조"), None)
    if not art:
        return [], ""
    t = art["text"]
    i = t.find("규정의 제정, 개정, 폐지에 관한 사항")
    if i < 0:
        i = t.find("제정, 개정, 폐지")
    seg = t[i: t.find("11.", i)] if i >= 0 else ""
    seg = re.sub(r"<[^<>]*(?:삭제|개\s*정)[^<>]*>", "", seg)
    exception = re.search(r"다만[^.]*제외한다\.?", seg)
    names = [m.group(1) for m in RE_NAME.finditer(seg)]
    # 「삭제」된 목(다. 삭제 <2025.08.27.>)은 이름이 없어 자연히 빠진다
    return list(dict.fromkeys(names)), exception.group(0) if exception else ""


def steps(reg: dict, regs: dict[str, dict], *, law_driven: bool = False) -> list[dict]:
    """[{step, who, basis, note, status}]  status: 필수 / 해당 / 확인 필요 / 해당 없음.
    law_driven: 이 개정이 관계 법령 개정을 반영하는 것인가 (법령명 정비 등) — 이사회 의결 제외 단서와 관련"""
    name = reg["name"]
    board, exception = board_list(regs)
    on_board = any(re.sub(r"\s", "", name) == re.sub(r"\s", "", b) for b in board)
    out = [
        {"step": "입안", "who": "업무담당부서장", "basis": "내규관리규정 제20조①", "status": "필수",
         "note": "내규안을 작성한다. 다른 부서와 관련되면 관련 부서와 협의한다."},
        {"step": "협의·조정", "who": "내규담당부서장, 준법감시인", "basis": "내규관리규정 제21조①", "status": "필수",
         "note": "법령·고시·정관·다른 내규와의 저촉 또는 중복 여부, 분류·형식·내용의 적부를 협의한다. (이 도구의 영향 카드가 그 자료)"},
    ]
    if on_board:
        note = f"「{name}」은 이사회규정 제3조제2항제10호의 의결 대상 규정이다."
        status = "해당"
        if law_driven:
            note += f" 다만 {exception or '관계 법령·정부 지침 개정에 따른 개정은 제외된다.'} 이 개정이 그에 해당하는지는 담당 부서가 판단한다."
            status = "확인 필요"
        out.append({"step": "확정: 이사회 심의·의결", "who": "이사회", "basis": "내규관리규정 제22조①, 이사회규정 제3조제2항제10호",
                    "status": status, "note": note})
    else:
        out.append({"step": "확정: 대표이사 결재", "who": "대표이사", "basis": "내규관리규정 제22조①", "status": "해당",
                    "note": f"「{name}」은 이사회규정 제3조제2항제10호의 목록(이름이 적힌 규정)에 없다. "
                            "다만 그 목록의 「중소벤처기업부고시에 따라 승인받아야 하는 규정」, 「이사회가 중요하다고 인정하는 규정」은 이름으로 알 수 없어 아래 확인 단계를 본다."})
    out.append({"step": "정부 승인·이사회 인정 여부 확인", "who": "업무담당부서", "basis": "내규관리규정 제22조②, 이사회규정 제3조제2항제10호 하·거목",
                "status": "확인 필요",
                "note": "법령 등에 따라 정부의 승인이 필요한 내규안이면 위 확정 뒤 정부 승인으로 확정한다(업무담당부서가 진행). "
                        "필요한지 불분명하면 이사회의 결정에 따른다."})
    if name == "취업규칙":
        out.append({"step": "근로자 의견 청취·동의", "who": "사용자(회사)", "basis": "근로기준법 제94조①②", "status": "해당",
                    "note": "근로자 과반수로 조직된 노동조합이 있으면 그 노동조합, 없으면 근로자 과반수의 의견을 듣는다. "
                            "근로자에게 불리하게 변경하면 그 동의를 받아야 한다. 신고할 때는 그 의견을 적은 서면을 첨부한다. "
                            "개정이 불리한 변경인지는 이 도구가 판단하지 않는다(담당 부서·노무 확인)."})
    out += [
        {"step": "등록·공지", "who": "내규담당부서장", "basis": "내규관리규정 제23조", "status": "필수",
         "note": "확정된 내규안을 사내인터넷망 내규 게시판에 등록하고 공지한다. 공지에는 내규명, 입안부서, 개정일, 시행일을 적는다."},
        {"step": "시행·주지", "who": "각 부서장", "basis": "내규관리규정 제24조, 제5조", "status": "필수",
         "note": "부칙에 따로 정하지 않으면 확정된 날부터 시행한다. 각 부서장은 개정 내용을 지체 없이 직원에게 주지시킨다."},
    ]
    return out


def for_changeset(reg_id: str, regs: dict[str, dict], kind: str = "") -> list[dict]:
    """개정안 한 건의 절차. kind == 'lawname'(법령명 정비)이면 법령 개정 반영으로 보고 이사회 단서를 알린다"""
    return steps(regs[reg_id], regs, law_driven=(kind == "lawname"))
