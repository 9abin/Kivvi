from regcheck.build import to_records
from regcheck.parse import parse
from regcheck.vectors import build_chunks


def test_chunks_by_hang_with_heading_and_skip_deleted():
    text = (
        "규정\n제1조(목적) 목적을 정한다.\n제2조(정의) ① 용어는 다음과 같다.\n  1. 갑\n  2. 을\n"
        "  ② 그 밖의 용어는 따로 정한다.\n제3조 삭제\n부      칙 <2020.01.01.>\n제1조(시행일) 시행한다."
    )
    reg = {"reg_id": "9", "name": "테스트규정", "hierarchy": "규정", "revised": "2020-01", **to_records("9", parse(text.splitlines()))}
    chunks = build_chunks([reg])
    assert [c["id"] for c in chunks] == ["9:1#0", "9:2#1", "9:2#2"]  # 삭제 조·부칙 제외
    assert chunks[1]["text"] == "테스트규정 > 제2조(정의)\n용어는 다음과 같다. 갑 을"


def test_charter_preamble_chunks():
    text = "윤리헌장\n제정 2024.12.06.\n우리는 다짐한다.\n하나, 정직하다.\n하나, 공정하다.\n부      칙 <2024.12.06.>\n시행한다."
    reg = {"reg_id": "8", "name": "윤리헌장", "hierarchy": "기타", "revised": "2024-12", **to_records("8", parse(text.splitlines()))}
    chunks = build_chunks([reg])
    assert [c["id"] for c in chunks] == ["8:전문#0", "8:항목1#0", "8:항목2#0"]
    assert chunks[1]["text"] == "윤리헌장 > 항목1\n하나, 정직하다."
