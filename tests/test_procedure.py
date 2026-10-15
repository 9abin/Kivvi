"""개정 절차 안내 (src/regcheck/procedure.py): 이사회 의결 대상은 이사회규정 원문에서 읽고, 취업규칙만 근로기준법 제94조 단계가 붙는다."""
from regcheck.procedure import board_list, steps

BOARD_TEXT = """제3조(심의ㆍ의결사항) ① 이사회는 정관 제36조 각 호의 사항을 심의ㆍ의결한다.
  ② 정관 제36조제8호에 따라 이사회의 의결이 필요하다고 정하는 사항은 다음 각 호와 같다.
  9. 출연ㆍ출자에 관한 사항
  10. 다음 각 목의 규정의 제정, 개정, 폐지에 관한 사항. 다만, 관계 법령 및 정부의 지침 개정에 따라 개정, 폐지하는 경우는 제외한다.
    가. 「이사회규정」
    다.  삭제 <2025.08.27.>
    라. 「취업규칙」
    마. 「인사규정」
  11. 고문 및 자문위원의 위촉, 예우 등에 관한 사항"""


def regs():
    def reg(name, text=""):
        return {"reg_id": name, "name": name, "articles": [{"label": "제3조", "text": text}] if text else []}
    return {"1": reg("이사회규정", BOARD_TEXT), "2": reg("취업규칙"), "3": reg("정보공개지침"), "4": reg("인사규정")}


def test_board_list_read_from_regulation_text():
    names, exception = board_list(regs())
    assert names == ["이사회규정", "취업규칙", "인사규정"]          # 삭제된 목(다.)은 이름이 없어 빠진다
    assert "관계 법령" in exception and "제외" in exception


def test_steps_by_regulation():
    r = regs()
    plain = {s["step"]: s for s in steps(r["3"], r)}
    assert "확정: 대표이사 결재" in plain and "근로자 의견 청취·동의" not in plain
    board = {s["step"]: s for s in steps(r["4"], r)}
    assert "확정: 이사회 심의·의결" in board and board["확정: 이사회 심의·의결"]["status"] == "해당"
    labor = [s["step"] for s in steps(r["2"], r)]
    assert "근로자 의견 청취·동의" in labor and "확정: 이사회 심의·의결" in labor


def test_law_driven_change_flags_board_exception_for_confirmation():
    r = regs()
    s = {x["step"]: x for x in steps(r["4"], r, law_driven=True)}["확정: 이사회 심의·의결"]
    assert s["status"] == "확인 필요" and "제외" in s["note"]
