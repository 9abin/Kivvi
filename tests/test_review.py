import pytest

from regcheck.review import ReviewLog, key_baseline


F = {"reg_id": "1", "unit": "본문", "article": "제3조", "para": 2, "quote": "제5조", "type": "인용_대상없음", "fid": "F00001"}


def test_key_is_stable_across_runs():
    assert key_baseline(F) == key_baseline({**F, "fid": "F09999"})
    assert key_baseline(F) != key_baseline({**F, "para": 3})


def test_append_only_last_wins(tmp_path):
    log = ReviewLog(tmp_path / "log.jsonl")
    k = key_baseline(F)
    assert log.state(k, "baseline") == "신규"
    log.decide(k, "baseline", "오탐", "박형건", "부칙 옛 번호")
    log.decide(k, "baseline", "오류 확인", "박형건")
    again = ReviewLog(tmp_path / "log.jsonl")
    assert again.state(k, "baseline") == "오류 확인"
    assert [h["prev"] for h in again.history(k)] == ["신규", "오탐"]


def test_requires_reviewer_and_valid_state(tmp_path):
    log = ReviewLog(tmp_path / "log.jsonl")
    with pytest.raises(ValueError):
        log.decide("x", "baseline", "오탐", " ")
    with pytest.raises(ValueError):
        log.decide("x", "row", "오탐", "박형건")
