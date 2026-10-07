import json
import sqlite3

from regcheck.lawdb import LawDB, amendment_lines, law_changes


def make_db(path):
    con = sqlite3.connect(path)
    con.execute("create table documents (id text, requested_title text, api_title text, raw_json text)")
    laws = [
        ("eflaw:1", "개인정보 보호법", {
            "기본정보": {"시행일자": "20260911", "공포일자": "20260310", "제개정구분": "일부개정"},
            "개정문": {"개정문내용": [["개인정보 보호법 일부를 다음과 같이 개정한다.", '제23조제2항 중 "훼손되지"를 "훼손이 되지"로 한다.']]},
            "조문": {"조문단위": [
                {"조문여부": "조문", "조문번호": "23", "조문제목": "민감정보의 처리 제한", "조문시행일자": "20260911", "조문변경여부": "Y",
                 "조문이동이전": "000000", "조문이동이후": "000000", "조문내용": "제23조(민감정보의 처리 제한) …"},
                {"조문여부": "조문", "조문번호": "31", "조문제목": "개인정보 보호책임자의 지정 등", "조문시행일자": "20200101", "조문변경여부": "N"},
            ]}}),
        ("eflaw:2", "공공기록물 관리에 관한 법률 시행령", {"기본정보": {"시행일자": "20250101"}, "조문": {"조문단위": [
            {"조문여부": "조문", "조문번호": "26", "조문제목": "보존기간", "조문변경여부": "N"}]}}),
        ("eflaw:3", "의료법", {"기본정보": {"시행일자": "20261002", "제개정구분": "타법개정"},
            "개정문": {"개정문내용": [["⑦ 다른법 일부를 다음과 같이 개정한다. 제17조 중 \"가\"를 \"나\"로 한다.",
                                      "⑧ 의료법 일부를 다음과 같이 개정한다.", '제17조제1항 본문 중 "지방검찰청검사"를 "지방공소청 검사"로 한다.', "⑨ 생략"]]},
            "조문": {"조문단위": [{"조문여부": "조문", "조문번호": "17", "조문제목": "진단서 등", "조문변경여부": "Y", "조문시행일자": "20261002"}]}}),
    ]
    for did, title, j in laws:
        con.execute("insert into documents values (?,?,?,?)", (did, title, title, json.dumps({"법령": j}, ensure_ascii=False)))
    con.commit()
    con.close()
    return LawDB(path)


def test_find_with_alias_and_enforcement_decree(tmp_path):
    db = make_db(tmp_path / "law.sqlite3")
    assert db.find("개인정보보호법")["id"] == "eflaw:1"
    assert db.find("공공기록물법 시행령")["id"] == "eflaw:2"
    assert db.find("없는법") is None


def test_amendment_lines_only_this_law_part_for_other_law_amendment(tmp_path):
    db = make_db(tmp_path / "law.sqlite3")
    assert amendment_lines(db.find("의료법"), "17") == ['제17조제1항 본문 중 "지방검찰청검사"를 "지방공소청 검사"로 한다']
    assert amendment_lines(db.find("개인정보 보호법"), "23") == ['제23조제2항 중 "훼손되지"를 "훼손이 되지"로 한다']


def test_law_changes_flags_changed_article_newer_than_citing_article(tmp_path):
    db = make_db(tmp_path / "law.sqlite3")
    art = {"id": "9:5", "label": "제5조", "title": "x", "text": "", "amend_notes": [],
           "items": [{"id": "9:5-0-0-0", "path": [0, 0, 0], "norm": "", "amend_marks": ["<개정 2021.03.16.>"]}]}
    regs = {"9": {"reg_id": "9", "name": "가지침", "articles": [art], "addenda": [], "history": ["제정 2015. 01. 01."]}}
    edge = lambda raw: {"src": "9:5-0-0-0", "src_kind": "본문", "status": "law_pending", "target_doc": "개인정보 보호법", "raw": raw, "context": "…"}  # noqa: E731
    out, stats = law_changes(regs, [edge("제23조"), edge("제31조")], db)
    assert [o["law_article"] for o in out] == ["제23조"]                 # 제31조는 바뀌지 않음
    assert "2026.09.11" in out[0]["note"] and "훼손이 되지" in out[0]["note"]
    assert stats["조문 연결"] == 2


def test_same_title(tmp_path):
    db = make_db(tmp_path / "law.sqlite3")
    assert [(law["title"], k) for law, k, _ in db.same_title("진단서 등")] == [("의료법", "17")]
