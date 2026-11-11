import sys
import unittest

sys.path.insert(0, "src")
from regcheck.kg import KG, build, moved_range  # noqa: E402


def art(rid, n, title, text, items=None):
    aid = f"{rid}:{n}"
    its = items or [{"id": aid + "-0-0-0", "path": [0, 0, 0], "norm": text}]
    return {"id": aid, "label": f"제{n}조", "title": title, "deleted": False, "norm": text, "items": its, "amend_notes": []}


def reg(rid, name, arts, tier="규정", addenda=None, appendices=None):
    return {"reg_id": rid, "name": name, "hierarchy": tier, "revised": "2026-01", "articles": arts, "addenda": addenda or [], "appendices": appendices or []}


def edge(src, tid, ttype="article", raw="제1조", status="ok", ctx="", sk="본문"):
    return {"src": src, "src_kind": sk, "raw": raw, "context": ctx, "target_type": ttype, "target_doc": "", "target_ref": {}, "target_reg": "",
            "target_id": tid, "status": status}


DEF_ITEMS = [{"id": "A:2-1-0-0", "path": [1, 0, 0], "norm": "“성과급”이라 함은 성과에 따라 지급하는 보수를 말한다."}]
REGS = {
    "A": reg("A", "직제규정", [art("A", 1, "목적", "목적"), art("A", 2, "용어의 정의", "정의", DEF_ITEMS), art("A", 3, "육아휴직", "육아휴직은 최대 2회"),
                         art("A", 4, "대상", "제3조에 따른다"), art("A", 5, "기타", "기타")],
             addenda=[{"date": "2026.1.1.", "text": "", "articles": [art("A", "부칙1:1", "적용례", "제3조의 개정 규정은 재직자에게 적용한다")]}]),
    "B": reg("B", "나지침", [art("B", 1, "육아휴직", "육아휴직은 최대 3회"), art("B", 2, "지급", "성과급을 지급한다 직제규정 제3조 준용"), art("B", 3, "기타", "무관")], tier="지침"),
    "C": reg("C", "직제지침", [art("C", 1, "시행", "직제규정을 시행한다")], tier="지침"),
}
EDGES = [
    edge("B:2-0-0-0", "A:3", raw="제3조", ctx="직제규정 제3조 준용"),
    edge("A:4-0-0-0", "A:3", raw="제3조"),
    edge("A:부칙1:1-0-0-0", "A:3", raw="제3조", sk="부칙"),
    edge("C:1-0-0-0", "A", ttype="regulation", raw="직제규정"),
]


class KGTest(unittest.TestCase):
    def setUp(self):
        self.nodes, self.rows = build(REGS, EDGES)
        self.kg = KG(self.nodes, self.rows)

    def types(self, t):
        return [r for r in self.rows if r["type"] == t]

    def test_cites_with_sub_and_supplement_source(self):
        c = {(r["src"], r["dst"]): r for r in self.types("CITES")}
        self.assertEqual(c[("B:2", "A:3")]["sub"], "준용")
        self.assertIn(("A:부칙1:1", "A:3"), c)
        self.assertEqual(self.nodes["A:부칙1:1"]["type"], "Supplement")

    def test_reg_level_citation_kept_separate(self):
        self.assertEqual([(r["src"], r["dst"]) for r in self.types("CITES_REG")], [("C:1", "A")])

    def test_term_defined_and_used(self):
        self.assertEqual(self.kg.terms_defined_in("A:2"), ["TERM:A:성과급"])
        users = {r["src"] for r in self.types("USES_TERM")}
        self.assertEqual(users, {"B:2"})  # 정의 조 자신은 사용 조가 아니다

    def test_same_title_cross_regulation(self):
        st = self.types("SAME_TITLE")
        self.assertEqual({(r["src"], r["dst"]) for r in st}, {("A:3", "B:1")})

    def test_supplements_pair_by_stem(self):
        # 직제규정 ↔ 직제지침 (줄기 「직제」)
        self.assertEqual([(r["src"], r["dst"]) for r in self.types("SUPPLEMENTS")], [("A", "C")])

    def test_impact_by_change_kind(self):
        val = self.kg.impact("A:3", "값 변경")
        self.assertEqual(set(val["candidates"]), {"B:2", "A:4", "A:부칙1:1", "B:1"})  # 인용받음 + 같은 제목
        self.assertTrue(any(x.startswith("SAME_TITLE") for x in val["candidates"]["B:1"]["paths"]))  # 값이 다르면 「SAME_TITLE:값다름」
        self.assertIn("B:2", val["candidates"])
        self.assertEqual(self.kg.impact("A:3", "표현 수정")["candidates"], {})  # 표현만 바꾸면 번지지 않는다
        dfn = self.kg.impact("A:2", "정의 변경")
        self.assertEqual(set(dfn["candidates"]), {"B:2"})

    def test_term_hint_limits_terms(self):
        self.assertEqual(self.kg.impact("A:2", "정의 변경", term_hint=[])["candidates"], {})

    def test_moved_range_from_amendment_text(self):
        regs = {"A": reg("A", "직제규정", [art("A", n, f"조{n}", "x") for n in range(1, 8)])}
        nodes, _ = build(regs, [])
        self.assertEqual(moved_range("제3조부터 제5조까지를 각각 제4조부터 제6조까지로 한다", "A", nodes), ["A:3", "A:4", "A:5"])
        self.assertEqual(moved_range("제3조를 삭제한다", "A", nodes), [])

    def test_impact_many_keeps_in_range_citers(self):
        regs = {"A": reg("A", "직제규정", [art("A", n, f"조{n}", "x") for n in range(1, 5)])}
        e = [edge("A:3-0-0-0", "A:4", raw="제4조")]  # 범위 안의 조가 범위 안의 다른 조를 인용
        nodes, rows = build(regs, e)
        kg = KG(nodes, rows)
        r = kg.impact_many(["A:3", "A:4"], "번호 이동")
        self.assertIn("A:3", r["candidates"])


if __name__ == "__main__":
    unittest.main()


class ChapterCiteTest(unittest.TestCase):
    """장 · 절 인용 -> CITES_CHAPTER (그 장 · 절의 조마다), 수정 종류 경로로 후보가 된다 (261111)."""

    def test_chapter_and_section(self):
        arts = [art("S", 1, "목적", "목적"), art("S", 2, "관리", "관리"), art("S", 3, "파기", "파기"), art("S", 4, "기타", "기타")]
        for a, (j, s) in zip(arts, [("제1장 총칙", ""), ("제2장 보안", "제1절 전자"), ("제2장 보안", "제2절 매체"), ("제3장 기타", "")]):
            a["jang"], a["jeol"] = j, s
        regs = {"S": reg("S", "보안지침", arts, tier="지침"), "T": reg("T", "전산지침", [art("T", 1, "도입", "보안지침 제2장제2절에 따라 조치한다"), art("T", 2, "운영", "보안지침 제2장에 따른다")], tier="지침")}
        e1 = edge("T:1-0-0-0", "", ttype="chapter", raw="제2장", status="missing_chapter", ctx="보안지침 제2장제2절에 따라 조치한다")
        e2 = edge("T:2-0-0-0", "S:장2", ttype="chapter", raw="제2장", ctx="보안지침 제2장에 따른다")
        for e in (e1, e2):
            e["target_ref"], e["target_reg"] = {"jang": 2}, "S"
        nodes, rows = build(regs, [e1, e2])
        got = sorted((r["src"], r["dst"]) for r in rows if r["type"] == "CITES_CHAPTER")
        self.assertEqual(got, [("T:1", "S:3"), ("T:2", "S:2"), ("T:2", "S:3")])
        res = KG(nodes, rows).impact("S:3", "값 변경")
        self.assertIn("CITES_CHAPTER_in", res["candidates"]["T:1"]["paths"])
