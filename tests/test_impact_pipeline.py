"""후보 state · 출처별 경로 · luna 목록 리랭킹 · 답변 검사 (리포트 93). 모델 · 네트워크 없이 돈다."""
import sys
import unittest

sys.path.insert(0, "src")
from regcheck.answer_writer import write_answers  # noqa: E402
from regcheck.cand_state import build_states, source_of  # noqa: E402
from regcheck.collect import Candidate  # noqa: E402
from regcheck.graph_source import add_graph  # noqa: E402
from regcheck.impact_pipeline import apply_decide, apply_luna, final_list, pick_for_luna, screen  # noqa: E402
from regcheck.luna_rerank import change_block, merge, rank_list  # noqa: E402


def cand(cid, tier, *paths):
    c = Candidate(cid, 1.0); c.paths = list(paths); c.tier = tier
    return c


def reg():
    items3 = [{"id": "R:3-1", "label": "①", "text": "직원은 신청서를 제출한다.", "norm": "직원은 신청서를 제출한다."}]
    items4 = [{"id": "R:4-1", "label": "①", "text": "제3조에 따른 신청은 7일 이내 처리한다.", "norm": "제3조에 따른 신청은 7일 이내 처리한다."}]
    return {"reg_id": "R", "name": "가 지침", "articles": [
        {"id": "R:3", "label": "제3조", "title": "신청", "text": "제3조(신청) ① 직원은 신청서를 제출한다.", "norm": "제3조(신청) ① 직원은 신청서를 제출한다.", "items": items3},
        {"id": "R:4", "label": "제4조", "title": "처리", "text": "제4조(처리) ① 제3조에 따른 신청은 7일 이내 처리한다.", "norm": "제4조(처리) ① 제3조에 따른 신청은 7일 이내 처리한다.", "items": items4}]}


class StateTest(unittest.TestCase):
    def test_source_is_fixed_by_paths(self):
        self.assertEqual(source_of(["CITES_in", "RAG"]), "rule")
        self.assertEqual(source_of(["GRAPH_definition_use", "RAG"]), "graph")
        self.assertEqual(source_of(["SCOPED_specific_reference_in"]), "graph")
        self.assertEqual(source_of(["RAG"]), "rag")

    def test_route_and_cut(self):
        st = build_states([cand("A", "확정급", "CITES_in"), cand("B", "참고", "RAG"), cand("C", "참고", "RAG"),
                           cand("D", "참고", "GRAPH_regulation_delegation_in"), cand("E", "참고", "RAG")])
        self.assertEqual([s.route for s in st], ["decide", "luna", "luna", "luna", "luna"])
        picked = pick_for_luna(st, n_rag=2, n_graph=5)
        self.assertEqual([s.id for s in picked], ["B", "C", "D"])
        self.assertEqual(st[4].rerank, {"by": "cut", "kept": False})

    def test_default_cut_graph_first_and_graph_only_edges(self):
        # RAG가 찾은 것 중 그래프도 찾은 F를 먼저. 그래프만 찾은 것은 위임 · 언급 연결(G, H)만 넘기고 D(정의 사용)는 뺀다
        st = build_states([cand("B", "참고", "RAG"), cand("D", "참고", "GRAPH_definition_use"),
                           cand("F", "참고", "RAG", "GRAPH_definition_use"), cand("E", "참고", "RAG_NEW"),
                           cand("G", "참고", "GRAPH_regulation_delegation_in"), cand("H", "참고", "SCOPED_specific_reference_out")])
        picked = pick_for_luna(st)
        self.assertEqual([s.id for s in picked], ["F", "B", "E", "G", "H"])
        self.assertEqual(st[1].rerank, {"by": "cut", "kept": False})
        self.assertIn("위임 · 언급 연결 아님", st[1].log[-1])
        self.assertEqual([s.id for s in pick_for_luna(build_states([cand(x, "참고", "RAG") for x in "PQR"]), n_rag=2)], ["P", "Q"])

    def test_decide_keeps_confirmed_and_unscored(self):
        st = build_states([cand("A", "확정급", "CITES_in"), cand("B", "확인 권장", "USES_TERM"), cand("C", "참고", "CO_MENTION")])
        apply_decide(st, {"A": 0.0, "B": 0.1})
        self.assertEqual([s.rerank["kept"] for s in st], [True, False, True])
        # 정의 변경이면 「정의가 바뀌는 용어를 씀」은 점수가 낮아도 남긴다
        st = build_states([cand("B", "확인 권장", "USES_TERM"), cand("C", "확인 권장", "MENTIONS_TITLE")])
        apply_decide(st, {"B": 0.1, "C": 0.1}, 0.7, "정의 변경")
        self.assertEqual([s.rerank["kept"] for s in st], [True, False])
        self.assertEqual(st[0].rerank["by"], "bypass_uses_term")

    def test_first_screen_keeps_order_and_appends_confirmed_beyond_n(self):
        # 261112: 첫 화면은 합친 순서로 n개 + n 밖의 확정급, 나머지는 「더 보기」(지우지 않음)
        rule = build_states([cand("A", "확인 권장", "CO_MENTION"), cand("B", "참고", "CO_MENTION"), cand("C", "확정급", "CITES_in")])
        other = build_states([cand("D", "참고", "RAG"), cand("E", "참고", "RAG")])
        v = screen(rule, other, n=2)
        self.assertEqual([s.id for s in v["first"]], ["A", "B", "C"])
        self.assertEqual([s.id for s in v["more"]], ["D", "E"])
        self.assertEqual([s.id for s in screen(rule, other, n=10)["first"]], ["A", "B", "C", "D", "E"])


class LunaTest(unittest.TestCase):
    def items(self):
        return [{"id": "R:4", "name": "가 지침 제4조(처리)", "found_by": ["의미 검색으로 찾음"], "text": "제3조에 따른 신청은 7일 이내 처리한다."},
                {"id": "S:1", "name": "나 지침 제1조(목적)", "found_by": ["의미 검색으로 찾음"], "text": "이 지침은 목적을 정한다."}]

    def fake(self, answer_for):
        prompts = []

        def call(prompt):
            prompts.append(prompt)
            alias = {line.split("]")[0][1:]: line.split("] ")[1] for line in prompt.splitlines() if line.startswith("[C")}
            return {"selected": answer_for({v: k for k, v in alias.items()})}, {"input": 10, "output": 5}
        return call, prompts

    def test_aliases_map_back_and_quotes_are_checked(self):
        call, prompts = self.fake(lambda a: [
            {"id": a["가 지침 제4조(처리)"], "relation": "A", "source_quote": "신청서를 제출", "candidate_quote": "제3조에 따른 신청은", "reason": "신청 절차를 함께 확인할 필요가 있다."},
            {"id": "C9", "relation": "B", "source_quote": "", "candidate_quote": "", "reason": ""}])
        res = rank_list("개정문: 제3조를 삭제한다.\n개정 전 원문: 직원은 신청서를 제출한다.", self.items(), call)
        self.assertEqual(res["order"], ["R:4"])
        p = res["picks"]["R:4"]
        self.assertTrue(p["candidate_quote_ok"] and p["source_quote_ok"])
        self.assertEqual(p["relation"], "인용·준용·위임")
        self.assertIn("C1", prompts[0])
        self.assertNotIn("R:4", prompts[0])   # 원래 id는 넣지 않는다

    def test_made_up_quote_and_bad_reason_are_dropped(self):
        call, _ = self.fake(lambda a: [{"id": a["가 지침 제4조(처리)"], "relation": "C", "source_quote": "없는 구절입니다",
                                        "candidate_quote": "14일 이내 처리", "reason": "제9조 때문에 반드시 고쳐야 한다."}])
        p = rank_list("개정문: 제3조를 삭제한다.", self.items(), call)["picks"]["R:4"]
        self.assertFalse(p["candidate_quote_ok"] or p["source_quote_ok"])
        self.assertEqual(p["reason"], "")

    def test_merge_keeps_majority(self):
        mk = lambda i: {"id": i, "candidate_quote_ok": False}  # noqa: E731
        order, _ = merge([[mk("a"), mk("b")], [mk("b")], [mk("b"), mk("c")]], ["a", "b", "c"])
        self.assertEqual(order, ["b"])

    def test_apply_luna_keeps_only_verified_quotes(self):
        st = build_states([cand("R:4", "참고", "RAG"), cand("S:1", "참고", "RAG")])
        apply_luna(st, {"order": ["R:4"], "picks": {"R:4": {"relation": "인용·준용·위임", "candidate_quote": "가짜", "candidate_quote_ok": False,
                                                            "source_quote": "x", "source_quote_ok": True, "reason": "확인할 필요가 있다."}}})
        self.assertEqual(st[0].evidence["quote"], "")
        self.assertEqual(st[0].evidence["source_quote"], "x")
        self.assertFalse(st[1].rerank["kept"])
        self.assertEqual([s.id for s in final_list(st)["other"]], ["R:4"])

    def test_whole_article_deletion_is_visible(self):
        t = change_block(reg(), "제3조를 삭제한다.", "R:3")
        self.assertIn("(조 전체 삭제)", t)
        self.assertNotIn("(없음)", t)

    def test_renumber_is_not_shown_as_deletion(self):
        # 261112 Codex 지적: 개정 후 조를 옛 번호로 찾아 번호 이동이 「조 전체 삭제」로 보였다
        r = reg()
        r["articles"] = [a for a in r["articles"] if a["id"] == "R:3"]
        t = change_block(r, "제3조를 제4조로 한다.", "R:3")
        self.assertNotIn("조 전체 삭제", t)
        self.assertIn("번호 이동: 제3조 → 제4조", t)

    def test_insert_with_shift_shows_new_article_separately(self):
        # 신설하면서 뒤 조를 밀면, 옛 조와 새 조를 같은 조의 내용 변경으로 보이지 않는다
        t = change_block(reg(), "제4조 이하를 각각 한 조씩 뒤로 하고, 제3조의 다음에 제4조를 다음과 같이 신설한다.\n제4조(접수) 부서는 신청을 접수한다.", "R:4")
        self.assertIn("번호 이동: 제4조 → 제5조", t)
        self.assertIn("새로 들어가는 조: 가 지침 제4조(접수)", t)
        self.assertNotIn("바뀌는 부분 (개정 전)", t)


class GraphAndAnswerTest(unittest.TestCase):
    def test_add_graph_merges_paths(self):
        out = add_graph([cand("A", "참고", "RAG")], [("A", 0.9, "definition_use"), ("B", 0.8, "specific_reference_in"), ("Z", 0.7, "x")], known={"A", "B"})
        self.assertEqual([c.id for c in out], ["A", "B"])
        self.assertEqual(out[0].paths, ["RAG", "GRAPH_definition_use"])
        self.assertEqual(source_of(out[1].paths), "graph")

    def test_writer_output_is_checked(self):
        st = build_states([cand("R:4", "참고", "RAG")])
        st[0].evidence = {"by": "luna", "relation": "인용·준용·위임", "quote": "제3조에 따른 신청은", "source_quote": "", "reason": "확인할 필요가 있다."}
        nodes = {"R": {"name": "가 지침"}, "R:4": {"reg": "R", "label": "제4조", "title": "처리"}}
        write_answers(st, "제3조를 삭제한다.", "개정문: 가 지침 제3조를 삭제한다.", nodes, lambda p: "제4조는 제3조에 따른 신청을 다루므로 확인해 볼 만하다.")
        self.assertTrue(st[0].answer["checked"])
        write_answers(st, "제3조를 삭제한다.", "개정문: 가 지침 제3조를 삭제한다.", nodes, lambda p: "제17조 때문에 반드시 고쳐야 한다.")
        self.assertFalse(st[0].answer["checked"])
        self.assertEqual(st[0].answer["text"], "")


if __name__ == "__main__":
    unittest.main()
