from regcheck.build import to_records
from regcheck.parse import parse
from regcheck.rename import build

T = """가규정
제1조(부기관장의 직무) ① 부기관장은 위원회 위원장이 된다.
② 비상임이사와 부기관장은 따로 정한다.
제2조(이사) 상임이사는 대표이사를 보좌한다."""


def regs():
    return {"1": {"reg_id": "1", "name": "가규정", "hierarchy": "규정", **to_records("1", parse(T.splitlines()))}}


def test_rename_auto_title_and_items():
    p = build(regs(), {"부기관장": "전무이사"})
    amend = p["packages"][0]["amendment"].splitlines()
    assert '제1조의 제목 중 "부기관장"을 "전무이사"로 한다.' in amend
    assert '제1조제1항 중 "부기관장"을 "전무이사"로 한다.' in amend
    assert p["packages"][0]["status"] == "ok" and p["packages"][0]["left_after"] == 0


def test_rename_inside_longer_word_goes_to_check():
    p = build(regs(), {"상임이사": "상근이사"})
    # 제1조② 「비상임이사」는 확인 필요, 제2조 「상임이사」(문장 첫머리)는 자동
    assert [(c["article"], c["token"]) for c in p["check"]] == [("제1조", "비상임이사")]
    assert p["packages"][0]["amendment"] == '제2조 중 "상임이사"를 "상근이사"로 한다.'
