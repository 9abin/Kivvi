from regcheck.leave_layer import LeaveLayer
from regcheck.topic_layer import TopicLayer, TopicSpec

UNITS = {
    "a:1": "제1조(육아휴직) ① 육아휴직 기간은 3년 이내로 한다. ② 육아휴직을 신청하는 직원은 신청서를 제출한다.",
    "a:2": "제2조(육아휴직 기간) 육아휴직의 기간은 3년 이내로 한다. 휴직 중 보수는 지급하지 않는다.",
    "a:3": "제3조(가족돌봄휴직) 가족돌봄휴직 기간은 90일 이내로 한다. 가족돌봄휴직을 신청한다.",
    "a:4": "제4조(휴직) 휴직의 기간 중에는 보수를 지급하지 않는다. 휴직 신청은 인사부서에 한다.",
    "a:5": "제5조(여비) 출장비는 휴직과 무관하게 지급한다.",
}


def test_leave_layer_attr_and_kind():
    layer = LeaveLayer(UNITS)
    out = layer.expand("a:1", '제1조제1항 중 "3년 이내"를 "2년 이내"로 한다.', UNITS["a:1"])
    assert out["a:2"] == ["LEAVE_ATTR"]
    assert "a:5" not in layer.assign   # 주제 낱말이 한 번뿐인 조는 제외


def test_common_unit_gets_common_path_not_attr():
    layer = LeaveLayer(UNITS)
    out = layer.expand("a:1", '제1조제1항 중 "3년 이내"를 "2년 이내"로 한다.', UNITS["a:1"])
    assert "LEAVE_COMMON" in out.get("a:4", [])


def test_other_topic_spec_changes_prefix():
    spec = TopicSpec(prefix="PAY", trigger="보수", common="보수(공통)", kinds={"기본급": ["기본급"]}, attrs={"율": ["1/2", "2/3"]})
    units = {"p:1": "기본급은 기본급의 1/2를 지급한다. 보수", "p:2": "기본급 지급률은 1/2이다. 보수 보수"}
    out = TopicLayer(spec, units).expand("p:1", '"1/2"를 "2/3"로', units["p:1"])
    assert out and all(v[0].startswith("PAY_") for v in out.values())
