"""오타 검사 확인 규칙 (src/regcheck/spell.py verify): 모델이 지어내거나 범위 밖으로 지적한 것을 거른다."""
from regcheck.spell import verify

T = "민원 통제자는 민원 서류를 잡수한 후 「민원 처리에 관한 법률」 제9조에 따라 이송하여야 한다."


def test_keeps_small_real_fix():
    assert verify(T, {"wrong": "잡수한", "right": "접수한"})


def test_drops_text_not_in_sentence_and_spacing_only():
    assert not verify(T, {"wrong": "접수하여", "right": "접수하며"})
    assert not verify(T, {"wrong": "민원 서류를", "right": "민원서류를"})


def test_drops_names_numbers_and_rewrites():
    assert not verify(T, {"wrong": "민원 처리에", "right": "민원처리에"})          # 「」 안 법령명
    assert not verify(T, {"wrong": "제9조", "right": "제10조"})                   # 숫자
    assert not verify(T, {"wrong": "이송하여야", "right": "보내야"})              # 표현 바꾸기 (글자 차이 큼)
