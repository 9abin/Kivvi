"""답변 뒤 누락 검사 (검색_설계.md 13절, config.GAP_CHECK, 2026.10.15).

답을 쓴 뒤에 "답이 달라지거나 조건이 붙을 수 있는데 근거에 없는 조문"을 찾아 답 끝에 "※ 확인 필요"로 붙인다.
결론은 바꾸지 않는다 (근거 없이 판정하지 않는 규칙). 재료는 두 곳에서 온다.

1. 되묻기(self-ask): 모델에게 질문·답변·근거·버린 후보 제목(·질문자)을 주고 "되묻는 질문" 최대 3개를 만들게 한 뒤,
   그 질문으로 검색해 지금 근거에 없는 조문을 모은다. 공가(건강검진 질문)처럼 다른 규정에 흩어진 보충 조문,
   "기밀 사항인 경우"처럼 질문과 말이 다른 단서가 대상이다. 모델이 지어낸 조건은 검색에서 조문이 안 나오면 그냥 버려진다.
2. 버린 후보 되짚기: 리랭크 6위 이하(Searcher.last_reranked)에서 근거에 안 들어간 조 중, 단서 문장(다만·제외·한하여·비밀·금지…)이
   질문의 명사와 겹치는 것. 검색 없이 공짜다.

두 재료를 합쳐 모델에게 한 번 묻는다: "이 조문 중 답의 결론을 바꾸거나 조건·예외·보충을 더하는 것이 있나?" → 있으면 안내문.
비용: LLM 2회 + 검색 1~3회. 지금은 답변 뒤에 차례로 돈다 (화면용으로 뒤에서 돌리려면 Searcher 호출에 잠금이 필요하다).
"""
import json
import re

from config import GAP_CHECK_MAX_NEW, GAP_CHECK_MAX_NOTES, GAP_CHECK_MAX_Q, GAP_CHECK_PER_Q
from src.search.search import _PROVISO, _article_base, strip_marks

PROBE_PROMPT = """너는 사내 규정 질의응답의 보조 검사기다. 직원의 질문, 시스템이 쓴 답변, 그 답변의 근거 조문, 그리고 검색이 버린 다른 후보 조문의 제목이 주어진다.
답변이 달라지거나 조건이 붙을 수 있는데 지금 근거에는 없는 사항을 찾기 위해, 규정에서 다시 찾아볼 **되묻는 질문**을 만든다.

규칙:
- 질문자가 같이 알아야 할 조건·예외·기한·승인·다른 제도(예: 건강검진 질문이면 "검진 받는 시간은 공가 처리되나", 출장 질문이면 "휴일 이동 시간은 근무로 인정되나")를 생각한다.
- 버린 후보 제목에 질문과 관련된 조건(기밀, 승인, 예외, 제한)이 보이면 그걸 되묻는다.
- 이미 근거에 답이 있는 것, 답변이 이미 말한 것은 되묻지 않는다.
- 질문자 정보가 있고 질문이 본인에 대한 것이면 질문자의 신분(공무직, 임원 등)에 맞는 규정을 되묻는다. 질문이 다른 사람에 대한 것이면 질문자 정보를 쓰지 않는다.
- 되묻는 질문은 규정 원문에 쓰일 법한 용어로, 한 문장씩, 최대 3개. 없으면 빈 목록.
출력은 JSON 하나만: {{"질문": ["...", "..."]}}"""

JUDGE_PROMPT = """너는 사내 규정 질의응답의 보조 검사기다. 직원의 질문, 시스템 답변, 그리고 답변 근거에 **없던** 조문 몇 개가 주어진다.
답변에 꼭 덧붙여야 할 조문만 고른다. **기본은 "해당 없음"이다.** 대부분의 조문은 질문과 관련은 있어도 덧붙일 필요가 없다.

덧붙이는 경우는 셋뿐이다:
1. 그 조문 때문에 답변의 결론이 달라진다 (예: 답은 "가능"인데 조문이 그 상황을 금지·제한).
2. 질문한 **바로 그 상황**에 적용되는 조건·예외·기한·승인 절차가 조문에 있는데 답변이 그걸 빠뜨렸다.
3. 질문자가 모르면 손해를 보는 보충 사항이 조문에 있고 답변에 없다 (예: 법정 건강검진 시간은 공가 처리).

해당 없음으로 보는 경우:
- 조문이 질문과 주제만 같고 묻는 상황에는 적용되지 않는다 (다른 절차, 다른 단계, 일반 원칙·목적·정의).
- 조문이 다른 대상(임원, 공무직, 해외근무자, 외부기관)에 대한 것이고 질문자는 그 대상이 아니다.
- 답변이 이미 같은 내용을 말했다.
- 조문이 "참고로 알아 두면 좋은" 정도다.

출력: 해당하는 조문이 있으면 그 조문이 **질문 상황에 대해** 무엇을 정하는지 한 문장(40자 안팎)으로, 최대 2개. 추측·조언은 쓰지 않는다.
출력은 JSON 하나만: {{"보완": [{{"번호": 1, "문장": "...", "이유": "결론변경|조건누락|보충"}}]}} (해당 없으면 {{"보완": []}})"""

_JSON = re.compile(r"\{.*\}", re.S)
_EXTRA_PROVISO = re.compile(r"비밀|기밀|금지|승인|허가|신고|사전")


def _parse(raw, key):
    m = _JSON.search(raw or "")
    if not m:
        return []
    try:
        v = json.loads(m.group(0)).get(key, [])
    except json.JSONDecodeError:
        return []
    return v if isinstance(v, list) else []


def proviso_sentences(body, nouns):
    """조 본문에서 단서·조건 문장 중 질문 명사와 하나라도 겹치는 것."""
    out = []
    for line in strip_marks(body or "").split("\n"):
        for p in re.split(r"(?<=다\.)\s+", line):
            if (_PROVISO.search(p) or _EXTRA_PROVISO.search(p)) and any(n in p for n in nouns):
                out.append(p.strip())
    return out


class GapChecker:
    def __init__(self, searcher, generator):
        self.searcher = searcher
        self.generator = generator

    def _used_keys(self, entries):
        return {(e["doc_id"], _article_base(e["article"])) for e in entries}

    def _discarded(self, entries):
        """리랭크 6위 이하 중 근거에 없는 조 (조 단위로 한 번씩, 리랭크 순서)."""
        used, seen, out = self._used_keys(entries), set(), []
        for h in self.searcher.last_reranked:
            key = (h["doc_id"], _article_base(h["article"]))
            if key in used or key in seen:
                continue
            seen.add(key)
            out.append(h)
        return out

    def follow_ups(self, question, answer, entries, discarded, asker=None):
        evidence = "\n".join(f"- {e['문서명']} {e['article']}: {re.sub(chr(10), ' ', strip_marks(e['body']))[:160]}" for e in entries[:8])
        titles = "\n".join(f"- {self.searcher.documents.get(h['doc_id'], {}).get('title') or h['doc_id']} "
                           f"{_article_base(h['article'])}: {re.sub(chr(10), ' ', h['body'])[:70]}" for h in discarded[:25])
        user = f"질문: {question}\n" + (f"질문자: {asker}\n" if asker else "")
        user += f"\n답변:\n{answer}\n\n근거:\n{evidence}\n\n검색이 버린 후보 제목:\n{titles}"
        qs = [q for q in _parse(self.generator._ask(PROBE_PROMPT, user), "질문") if isinstance(q, str) and q.strip()]
        return qs[:GAP_CHECK_MAX_Q]

    def search_new(self, follow_ups, entries):
        """되묻는 질문마다 검색해 근거에 없는 조만 모은다 (조 단위, 질문당 상위 GAP_CHECK_PER_Q개까지)."""
        used, out = self._used_keys(entries), []
        for fq in follow_ups:
            for h in self.searcher.search(fq, k=GAP_CHECK_PER_Q, expand_cites=False, expand_siblings=False):
                key = (h["doc_id"], _article_base(h["article"]))
                if key in used:
                    continue
                used.add(key)
                out.append({**h, "되묻기": fq})
        return out

    def discarded_with_provisos(self, question, discarded, entries):
        nouns = [t for t in self.searcher._query_tokens(question) if len(t) >= 2]
        used, out = self._used_keys(entries), []
        for h in discarded:
            sents = proviso_sentences(h["body"], nouns)
            if sents and (h["doc_id"], _article_base(h["article"])) not in used:
                out.append({**h, "단서": sents[:2]})
        return out

    def judge(self, question, answer, candidates):
        if not candidates:
            return []
        blocks = []
        for i, c in enumerate(candidates, start=1):
            title = c.get("문서명") or self.searcher.documents.get(c["doc_id"], {}).get("title") or c["doc_id"]
            body = " / ".join(c["단서"]) if c.get("단서") else re.sub(r"\s+", " ", strip_marks(c["body"]))[:500]
            blocks.append(f"[{i}] {title} {_article_base(c['article'])}\n{body}")
        user = f"질문: {question}\n\n답변:\n{answer}\n\n근거에 없던 조문:\n" + "\n\n".join(blocks)
        notes = []
        for item in _parse(self.generator._ask(JUDGE_PROMPT, user), "보완"):
            try:
                n = int(item.get("번호"))
            except (TypeError, ValueError, AttributeError):
                continue
            if 1 <= n <= len(candidates) and item.get("문장"):
                c = candidates[n - 1]
                title = c.get("문서명") or self.searcher.documents.get(c["doc_id"], {}).get("title") or c["doc_id"]
                notes.append({"규정": title, "조항": _article_base(c["article"]), "chunk_id": c["chunk_id"],
                              "문장": item["문장"].strip(), "이유": item.get("이유", ""),
                              "출처": "되묻기" if c.get("되묻기") else "버린후보"})
            if len(notes) >= GAP_CHECK_MAX_NOTES:
                break
        return notes

    def run(self, question, answer, entries, asker=None):
        """반환: {"되묻기": [...], "후보": [chunk_id...], "보완": [{"규정","조항","문장","출처"}...]}"""
        discarded = self._discarded(entries)
        follow_ups = self.follow_ups(question, answer, entries, discarded, asker)
        cands = self.search_new(follow_ups, entries)
        seen = {(c["doc_id"], _article_base(c["article"])) for c in cands}
        for c in self.discarded_with_provisos(question, discarded, entries):
            if (c["doc_id"], _article_base(c["article"])) not in seen:
                cands.append(c)
        cands = cands[:GAP_CHECK_MAX_NEW]
        notes = self.judge(question, answer, cands)
        return {"되묻기": follow_ups, "후보": [c["chunk_id"] for c in cands], "보완": notes}


def gap_notes(result):
    """답변 끝에 붙일 안내문. 부칙·정정·함께 조문 안내와 같은 자리."""
    return [f"※ 확인 필요: {n['규정']} {n['조항']} — {n['문장']}" for n in result.get("보완", [])]
