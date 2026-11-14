"""법령 대조 카드 (app/web_law.py): 관련 법조문 고르기·숫자 짝짓기·같은 것을 세는지 확인. 법률 DB 없이 작은 가짜 법령으로."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import web_law as L  # noqa: E402

LAW = {"title": "가법", "id": "x", "조문": {
    "19": {"조문제목": "육아휴직", "항": [{"항내용": "① 사업주는 근로자가 자녀를 양육하기 위하여 육아휴직을 신청하는 경우 허용하여야 한다."},
                                      {"항내용": "② 육아휴직의 기간은 1년 이내로 한다."}]},
    "19의4": {"조문제목": "육아휴직의 사용형태", "항": [{"항내용": "① 근로자는 육아휴직을 3회에 한정하여 나누어 사용할 수 있다."}]},
    "20": {"조문제목": "벌칙", "조문내용": "제20조(벌칙) 위반하면 500만원 이하의 과태료를 부과한다."},
}}
OLD = "② 육아휴직 기간은 3년 이내로 하며, 분할하는 횟수는 최대 2회(나누어 사용하게 되는 휴직의 단위는 총 3회)로 한다."
NEW = OLD.replace("최대 2회", "최대 3회").replace("총 3회", "총 4회")


def test_label():
    assert L.label("19의4") == "제19조의4" and L.label("7") == "제7조"


def test_related_prefers_article_about_the_changed_phrase():
    focus, units = L.focus_of(OLD, NEW)
    assert units == {"회"}
    rel = L.related(LAW, OLD, prefer="19", focus=focus, units=units)
    assert rel[0]["article"] == "제19조의4"      # 제목이 같은 제19조가 아니라 분할 사용을 정한 조
    assert any(r["article"] == "제19조" for r in rel)


def test_compare_pairs_in_order_and_marks_only_same_thing():
    rel = L.related(LAW, OLD, focus=L.focus_of(OLD, NEW)[0], units={"회"})
    rows = L.compare([r["text"] for r in rel], OLD, NEW)
    assert [(r["old"], r["new"]) for r in rows] == [(["2"], ["3"]), (["3"], ["4"])]
    assert rows[0]["note"] == "개정안 값이 법령 조문의 값과 같아짐"
    assert rows[1]["note"] == ""      # 「휴직 단위 총 3회」는 법의 「3회 나누어 사용」과 세는 대상이 다르다


def test_compare_flags_value_moving_away_from_law():
    rows = L.compare(["출산전후휴가는 90일을 주어야 한다."], "① 직원은 출산휴가를 90일 사용할 수 있다.", "① 직원은 출산휴가를 120일 사용할 수 있다.")
    assert rows == [{"unit": "일", "law": ["90"], "old": ["90"], "new": ["120"], "note": "현행 값은 법령 조문에 있으나 개정안 값은 다름",
                     "context": "① 직원은 출산휴가를 90일 사용할 수 있다."}]
