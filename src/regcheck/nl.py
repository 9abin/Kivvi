"""자연어 요청 → 개정문 (로컬 LLM, Ollama). LLM은 문안만 쓰고, 맞는지는 우리 적용기가 판정한다.

흐름: 대상 규정·후보 조문 찾기(규칙) → LLM에 개정문 문법·예시·조문 원문을 주고 개정문만 쓰게 함 → 적용기로 검증
     → 실패하면 적용기 오류 메시지를 돌려주고 다시 쓰게 함(최대 3회) → 성공하면 연쇄 수정 에이전트로.
폐쇄망 가정: 로컬 모델만 (AGENTS §4). 개정 초안은 민감 정보라 외부 API를 쓰지 않는다.
"""

from __future__ import annotations

import json
import re
import urllib.request

from .amend import AmendError, build_changeset, marker

OLLAMA = "http://localhost:11434/api/chat"
MAX_TRIES = 3

RULES = """너는 한국 공공기관 내규를 고치는 보조자다. 설명·인사말 없이 아래 (A) 또는 (B) 중 하나만 출력한다.

(B) 문구를 고치는 요청이면: 바뀐 조문 전체를 원문 형식 그대로 다시 쓴다. 바꿀 곳만 바꾸고 나머지 글자는 원문과 똑같이 둔다.
   예) 제8조(특별공로금) ① 퇴직하는 … 지급할 수 있다.
       ② 제1항의 규정에 따른 특별공로금은 대표이사의 승인을 거쳐야 한다.

(A) 조를 삭제·신설하거나 번호를 옮기는 요청이면: 아래 형식의 개정문을 쓴다.

허용 형식 (한 줄에 한 문장, 문장 끝은 "한다.")
1. 문구 바꾸기: 제30조제2항 중 "최대 2회"를 "최대 3회"로 한다.
   - 여러 개: 제9조 중 "제7조"를 "제8조"로, "제10조"를 "제11조"로 한다.
   - 따옴표 안 옛 문구는 아래 조문 원문에서 글자 그대로 복사한다 (띄어쓰기까지). 바꿀 곳만 짧게.
   - 항이 있는 조는 제N조제M항으로 정확히 가리킨다. 호는 제N조제M항제K호.
2. 문구 지우기: 제5조제2항 중 "및 지방자치단체"를 삭제한다.
3. 조·항 삭제: 제37조를 삭제한다. / 제12조제3항을 삭제한다.
4. 조 전체를 새로 쓰기: 제8조를 다음과 같이 한다.  (다음 줄부터 "제8조(제목) ① …" 본문)
5. 신설하며 뒤 번호 밀기: 제7조부터 제20조까지를 각각 제8조부터 제21조까지로 하고, 제6조의 다음에 제7조를 다음과 같이 신설한다.
   (다음 줄에 "제7조(제목) 내용")
6. 제목 바꾸기: 제4조의 제목 중 "위임"을 "세부사항"으로 한다.
조사: 받침 있으면 을/으로, 없으면 를/로.
예) 요청 "직제규정 제6조 다음에 홍보팀 조문을 넣고 뒤 번호를 밀어줘. 내용 '홍보팀을 둔다.'" →
제7조부터 제20조까지를 각각 제8조부터 제21조까지로 하고, 제6조의 다음에 제7조를 다음과 같이 신설한다.
제7조(홍보팀) 홍보팀을 둔다."""


def _post(model: str, messages: list[dict], timeout: int = 180) -> str:
    if model.startswith("gpt-"):   # 외부 모델 비교용 (평가 전용: 공개 규정 문장만 보냄). 운영 기본은 로컬 모델
        from openai import OpenAI
        from .db import load_env
        r = OpenAI(api_key=load_env().get("OPENAI_API_KEY"), timeout=timeout).chat.completions.create(
            model=model, messages=messages, reasoning_effort="low", max_completion_tokens=3000)
        return r.choices[0].message.content or ""
    body = {"model": model, "messages": messages, "stream": False, "think": False,
            "options": {"temperature": 0, "num_ctx": 8192}}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))["message"]["content"]


RE_HEAD = re.compile(r"^(제\s*\d+\s*조(?:\s*의\s*\d+)?)\s*\(([^)]*)\)\s*(.*)$")
RE_INSTR = re.compile(r"\s중\s|삭제한다|신설한다|다음과\s*같이\s*한다|까지로\s*(?:하고|한다)|로\s*한다$|으로\s*한다$")


def _is_instruction(line: str) -> bool:
    """개정 지시인가 (조문 본문 「…목적으로 한다.」와 구별). 제목 괄호 뒤에 바로 지시가 오면 지시."""
    m = RE_HEAD.match(line)
    if m:   # 「제24조(인병휴가) 중 "…"를 …」 → 지시, 「제1조(목적) 이 지침은 … 한다」 → 본문
        return bool(re.match(r"^(중\s|의\s*제목|을|를)", m.group(3)))
    return bool(re.match(r"^(?:종전\s*)?제\s*\d+\s*조", line)) and bool(RE_INSTR.search(line))


def clean_output(text: str) -> str:
    """코드 블록·설명 줄 제거. 개정 문장과 그 뒤 본문 줄만 남긴다."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"```[a-z]*", "", text)
    out, body = [], False
    for line in text.splitlines():
        s = line.strip().strip("*").strip()
        if not s:
            continue
        if _is_instruction(s):
            s = RE_HEAD.sub(lambda m: f"{m.group(1)} {m.group(3)}", s)   # 「제24조(인병휴가) 중」 → 「제24조 중」
            out.append(s)
            body = "다음과 같이" in s
        elif body and (re.match(r"^제\s*\d+\s*조(?:의\s*\d+)?\s*\(", s) or re.match(r"^[①-⑳]|^\d+\.\s|^[가-하]\.\s", s)):
            out.append(s)
    return "\n".join(out)


def article_blocks(text: str) -> list[list[str]]:
    """출력에서 조문 본문 덩어리 (「제N조(제목) …」로 시작해 다음 지시·조문 머리 전까지)."""
    blocks, cur, attached = [], None, False
    for line in re.sub(r"```[a-z]*", "", text).splitlines():
        s = line.strip().strip("*").strip()
        if not s:
            continue
        if _is_instruction(s):
            cur, attached = None, "다음과 같이" in s   # 「…다음과 같이 신설한다」 뒤 본문은 그 지시의 것
            continue
        if RE_HEAD.match(s) and not attached:
            cur = [s]
            blocks.append(cur)
        elif cur is not None:
            cur.append(s)
    return blocks


PAIRS = {"(": ")", "「": "」", "『": "』", "[": "]", "<": ">", "“": "”", "‘": "’"}
QUANT = {"최대", "최소", "총", "연", "월", "주", "매년", "매월", "매주", "각", "약", "만"}   # 숫자 바로 앞에서 숫자와 한 덩어리로 읽히는 말


def _unmatched(s: str) -> tuple[int, int]:
    """(짝 없는 닫는 기호 수, 짝 없는 여는 기호 수). 큰따옴표는 홀수 개면 여는 쪽 하나로 본다."""
    close = opens = 0
    stack = []
    for ch in s:
        if ch in PAIRS:
            stack.append(PAIRS[ch])
        elif ch in PAIRS.values():
            if stack and stack[-1] == ch:
                stack.pop()
            else:
                close += 1
    opens = len(stack) + (s.count('"') % 2)
    return close, opens


def _span_pair(old: str, new: str) -> tuple[str, str] | None:
    """바뀐 부분을 (옛 문구, 새 문구)로. 개정문에 그대로 들어가므로 사람이 읽기 자연스럽게 넓힌다 (261013):
    낱말 경계까지 → 괄호·낫표 짝 맞추기 → 숫자 앞 수량어(최대·총 등) 포함 → 항 안에서 한 번만 나오도록 어절 단위로 넓히기.
    바뀐 곳 밖은 옛·새 문장이 같으므로 양쪽을 같은 글자 수만큼 넓힌다."""
    import difflib
    sm = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    ops = [o for o in sm.get_opcodes() if o[0] != "equal"]
    if not ops:
        return None
    i1, i2, j1, j2 = ops[0][1], ops[-1][2], ops[0][3], ops[-1][4]
    stop = " ,.「」()"
    while i1 > 0 and old[i1 - 1] not in stop and j1 > 0:
        i1 -= 1
        j1 -= 1
    while i2 < len(old) and old[i2] not in stop and j2 < len(new):
        i2 += 1
        j2 += 1

    def balance():
        nonlocal i1, i2, j1, j2
        for _ in range(len(old)):
            co, oo = _unmatched(old[i1:i2])
            cn, on = _unmatched(new[j1:j2])
            if (co or cn) and i1 > 0 and j1 > 0:        # 닫는 기호만 있음 → 왼쪽으로
                i1, j1 = i1 - 1, j1 - 1
            elif (oo or on) and i2 < len(old) and j2 < len(new):   # 여는 기호만 있음 → 오른쪽으로
                i2, j2 = i2 + 1, j2 + 1
            else:
                return

    balance()
    # 숫자로 시작하면 바로 앞 수량어를 붙인다: 「2회」 → 「최대 2회」
    if 1 < i1 < len(old) and old[i1].isdigit() and old[i1 - 1] == " ":   # i1 == len(old): 항 끝에 덧붙이는 개정 (자연어 확장 평가에서 8건 IndexError)
        k = i1 - 1
        while k > 0 and old[k - 1] not in stop:
            k -= 1
        if old[k:i1 - 1] in QUANT:
            j1 -= i1 - k
            i1 = k
    # 항 안에서 옛 문구가 한 번만 나오도록 어절 단위로 넓힌다
    while old.count(old[i1:i2]) > 1 and (i1 > 0 or i2 < len(old)):
        if i1 > 0:
            k = old.rfind(" ", 0, i1 - 1) + 1
            j1, i1 = j1 - (i1 - k), k
        if old.count(old[i1:i2]) > 1 and i2 < len(old):
            k = old.find(" ", i2 + 1)
            k = len(old) if k < 0 else k
            j2, i2 = j2 + (k - i2), k
        balance()
    return old[i1:i2].strip(), new[j1:j2].strip()


def blocks_to_amendment(reg: dict, blocks: list[list[str]]) -> str:
    """LLM이 다시 쓴 조문 → 개정문(규칙). 항 구성이 같으면 항별 문구 바꾸기, 다르면 「제N조를 다음과 같이 한다」."""
    from .agent import fix_sentence
    from .amend import _parse_body
    lines = []
    for block in blocks:
        m = RE_HEAD.match(block[0])
        label = re.sub(r"\s", "", m.group(1))
        old = next((a for a in reg["articles"] if a["label"] == label and not a.get("deleted")), None)
        if not old:
            continue
        no = re.match(r"제(\d+)조(?:의(\d+))?", label)
        try:
            new = _parse_body(block, int(no.group(1)), int(no.group(2) or 0))
        except Exception:
            continue
        oi = {tuple(it["path"]): it["norm"] for it in old["items"]}
        ni = {tuple(it["path"]): it["norm"] for it in new["items"]}
        if set(oi) != set(ni):
            lines.append(f"{label}를 다음과 같이 한다.")
            lines += block
            continue
        if new["title"] and new["title"] != old["title"] and not new["title"].startswith("임시"):
            if (pr := _span_pair(old["title"], new["title"])):
                lines.append(fix_sentence(f"{label}의 제목", [pr]))
        for path in sorted(oi):
            if oi[path] != ni[path] and (pr := _span_pair(oi[path], ni[path])):
                p, i, k = path
                unit = label + (f"제{p}항" if p else "") + (f"제{i}호" if i else "")
                lines.append(fix_sentence(unit, [pr]) if pr[1] else f'{unit} 중 "{pr[0]}"를 삭제한다.')
    return "\n".join(lines)


def intent_issues(request: str, cs: dict, reg: dict, cands: list[dict]) -> list[str]:
    """적용은 되지만 요청과 어긋난 초안을 잡는다 (문법이 맞아도 틀린 초안). 빈 목록이면 통과."""
    ops = [op for c in cs["changes"] for op in c["ops"]]
    kinds = {op["op"] for op in ops}
    issues = []
    if re.search(r"신설|새로\s*(넣|만들|두)|추가해", request) and "insert" not in kinds:
        issues.append("요청은 조문 신설인데 신설 문장이 없다. 「제X조의 다음에 제Y조를 다음과 같이 신설한다.」를 써라")
    if re.search(r"삭제|없애|폐지", request) and "delete" not in kinds and not any(op["op"] == "replace" and not op.get("new") for op in ops):
        issues.append("요청은 삭제인데 삭제 문장이 없다. 조 전체면 「제N조를 삭제한다.」 한 줄로 써라")
    if re.search(r"번호[를는]?\s*(하나씩\s*)?(밀|뒤로)", request) and "renumber" not in kinds:
        issues.append("요청은 뒤 조문 번호를 미는 것인데 번호 이동 문장이 없다")
    old_titles = {a["label"]: a["title"] for a in reg["articles"]}
    for c in cs["changes"]:
        if c["origin"] and any(op["op"] == "rewrite" for op in c["ops"]):
            new_title = next((r["new"] for r in cs["rows"] if r.get("origin") == c["origin"]), None)
            new_text = "".join(t for ln in (new_title or [])[:1] for t, _ in ln)
            if old_titles.get(c["old_label"]) and old_titles[c["old_label"]] not in new_text and old_titles[c["old_label"]] not in request:
                issues.append(f"기존 {c['old_label']}({old_titles[c['old_label']]}) 전체를 다른 내용으로 바꿨다. 새 조문이면 신설, 고치는 것이면 그 조만 문구 바꾸기")
    wanted = {a["label"] for a in cands[:1]}
    extra = [c["old_label"] for c in cs["changes"] if c["origin"] and c["old_label"] not in wanted
             and any(op["op"] not in ("renumber",) for op in c["ops"]) and c["old_label"] not in request]
    if extra and "insert" not in kinds:
        issues.append(f"요청하지 않은 {', '.join(extra)}도 바꿨다. 요청한 곳만 고쳐라")
    return issues


def find_regulation(regs: dict[str, dict], request: str) -> dict | None:
    """요청에 적힌 규정명 (가장 긴 이름 우선). 다른 규정 인용(「급여규정 제21조」)과 헷갈리지 않게 처음 나온 이름."""
    hits = []
    flat = request.replace(" ", "")   # 「조직성과평가 지침」처럼 띄어쓰기만 다른 이름도 같은 규정 (자연어 평가 D 말투 실패 원인의 일부, 261016)
    for r in regs.values():
        for n in {r["name"], r["name"].removeprefix("한국벤처투자 ")}:
            i = flat.find(n.replace(" ", ""))
            if i >= 0:
                hits.append((i, -len(n), r))
    return sorted(hits, key=lambda x: (x[0], x[1]))[0][2] if hits else None


def candidates(reg: dict, request: str, k: int = 3) -> list[dict]:
    """요청에 적힌 조 번호(규정명 바로 뒤 또는 단독) + 제목·본문 글자 겹침 상위."""
    def grams(s):
        s = re.sub(r"\s", "", s)
        return {s[i:i + 2] for i in range(len(s) - 1)}
    q = grams(request)
    named = set()
    for m in re.finditer(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?", request):
        before = request[max(0, m.start() - 8): m.start()]
        if re.search(r"(규정|지침|규칙|정관)\s*$", before) and reg["name"] not in before:
            continue   # 다른 규정의 조 (예: 「급여규정 제21조」)
        named.add(f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else ""))
    scored = []
    for a in reg["articles"]:
        if a.get("deleted"):
            continue
        text = a["title"] + " " + " ".join(it["norm"] for it in a["items"])
        # 요청에 적힌 조 제목(「결산보고 조항」)이 가장 강한 단서. 조 번호는 고칠 인용 번호(「제128조를 제71조로」)일 수도 있어 그다음
        title_hit = bool(a["title"]) and len(a["title"]) >= 2 and a["title"] in request
        s = len(q & grams(text)) / (len(q) or 1) + 20 * title_hit + 10 * (a["label"] in named)
        scored.append((s, a))
    return [a for _, a in sorted(scored, key=lambda x: -x[0])[:k]]


def article_text(a: dict) -> str:
    lines = [f"{a['label']}({a['title']})"]
    lines += [marker(it["path"]) + it["norm"] for it in a["items"]]
    return "\n".join(lines)


NEW_ART = re.compile(r"신설|새로\s*(?:넣|만들|두)|새\s*(?:조|조문|조항)|(?:조|조문|조항)\s*(?:을|를)?\s*추가")


def insert_anchor(request: str) -> tuple[int, str, str] | None:
    """조 신설 요청 → (그 뒤에 넣을 조 번호, 제목, 본문). 「제6조 다음에 X 조문을」, 「제7조 앞에 신설, 제목은 X, 내용은 Y」 모두 읽는다.
    본문이 없으면 빈 문자열(LLM이 채움). 위치나 제목을 못 읽으면 None."""
    if not NEW_ART.search(request):
        return None
    after = None
    m = re.search(r"제\s*(\d+)\s*조\s*(?:의\s*)?다음(?:으로|에)?", request)
    if m:
        after = int(m.group(1))
    else:
        m = re.search(r"제\s*(\d+)\s*조[^.\n]{0,80}?(?:바로\s*)?(?:앞|전)(?:으로|에)", request)   # 화면은 「규정명 제N조」를 요청 앞에 붙이므로 「이 조 바로 앞에」와 떨어져 있어도 읽는다
        if m and int(m.group(1)) >= 2:
            after = int(m.group(1)) - 1
    if after is None:
        return None
    title = None
    t = re.search(r"제목(?:은|:|을)\s*['\"「‘“]?([^'\"」’”,.\n]{2,30}?)['\"」’”]?\s*(?:[,.]|내용|이고|이며|으로|로|$)", request)
    if t:
        title = t.group(1).strip()
    else:
        t = re.search(r"다음(?:으로|에)?\s*(.+?)\s*(?:조문|조항|조)(?:을|를)?\s*(?:새로|신설)", request)
        if t:
            title = t.group(1).strip(" '\"「」")
    if not title or re.search(r"제\s*\d+\s*조|신설|앞에|다음|없이|해\s*주|해줘|넣어", title):   # 문장 일부를 제목으로 잘못 자른 경우는 규칙이 아니라 LLM·사람에게
        return None
    body = ""
    b = re.search(r"내용(?:은|:)\s*['\"「‘“](.+?)['\"」’”]", request)
    if not b:   # 따옴표 없는 본문은 문장만 있을 때 받는다. 「~라는 문장으로 해 주고」처럼 말투가 섞이면 규칙이 잘못 자르므로 LLM에 맡긴다
        b = re.search(r"내용(?:은|:)\s*(.+?)\s*(?:[.]\s*$|$)", request)
        if b and re.search(r"해\s*주|해줘|주세요|주시|으로\s*하|로\s*하|문장|넣어|밀어|[,]", b.group(1)):
            b = None
    if b:
        body = b.group(1).strip()
        if body and not body.endswith((".", "다")):
            body += "."
        elif body.endswith("다"):
            body += "."
    return after, title, body


def structural_plan(reg: dict, request: str, cands: list[dict]) -> str | None:
    """삭제·신설·번호 이동은 규칙으로 개정문을 만든다 (작은 LLM이 가장 약한 부분). 못 읽으면 None → LLM."""
    live = [a for a in reg["articles"] if not a.get("deleted")]
    titled = [a for a in live if a["title"] and len(a["title"]) >= 2 and a["title"] in request]
    named = re.findall(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?", request)
    # 조 전체 삭제: 「당연면직 조항을 삭제」, 「제37조를 삭제」 (문구 삭제 「"…"를 삭제」는 LLM)
    if re.search(r"(조항|조문|조)\s*(을|를)?\s*(통째로\s*)?(삭제|없애|폐지)", request) and '"' not in request and "'" not in request:
        target = titled[0] if len(titled) == 1 else (next((a for a in live if a["label"] == f"제{named[0][0]}조" + (f"의{named[0][1]}" if named[0][1] else "")), None)
                                                      if len(named) == 1 else None)
        if target:
            return f"{target['label']}를 삭제한다."
    # 신설: 「제6조 다음(으로|에) X 조문을 새로 넣고 (뒤 번호 밀어)… 내용은 '…'」
    anchor = insert_anchor(request)
    if anchor:
        after, title, body_text = anchor
        body = re.match(r"(.+)", body_text) if body_text else None
        nums = sorted({int(re.match(r"제(\d+)조", a["label"]).group(1)) for a in live})
        later = []   # 밀릴 조: 신설 자리부터 이어지는 번호까지만 (결번이 있으면 거기서 멈춘다. 전산업무지침 제23조 결번, 261016)
        n = after + 1
        while n in nums:
            later.append(n)
            n += 1
        lines = []
        if later:   # 신설 자리에 이미 조가 있으면 뒤 번호는 당연히 밀린다 (말하지 않아도 261016: 「제7조 앞에 신설」이 7B에서 실패)
            # 한 조만 밀리면 「제15조를 제16조로 하고」 (「제15조부터 제15조까지를 각각 …」은 법제 문장으로 어색, 261015 시연 리허설)
            move = (f"제{later[0]}조를 제{later[0] + 1}조로 하고, " if len(later) == 1 else
                    f"제{later[0]}조부터 제{later[-1]}조까지를 각각 제{later[0] + 1}조부터 제{later[-1] + 1}조까지로 하고, ")
            lines.append(move + f"제{after}조의 다음에 제{after + 1}조를 다음과 같이 신설한다.")
        elif not later or (after + 1) not in nums:
            lines.append(f"제{after}조의 다음에 제{after + 1}조를 다음과 같이 신설한다.")
        else:
            return None   # 빈 번호가 없는데 밀라는 말도 없음 → 가지 번호(제N조의2) 등 판단은 LLM·사람
        if not body:
            return "\n".join(lines) + f"\n제{after + 1}조({title}) "   # 본문은 LLM이 채움
        lines.append(f"제{after + 1}조({title}) {body.group(1).strip()}")
        return "\n".join(lines)
    return None


def draft(regs: dict[str, dict], request: str, model: str = "qwen2.5:7b", reg: dict | None = None) -> dict:
    """요청 → 검증된 개정문. {ok, reg_id, amendment, tries:[{output, error}], candidates}"""
    reg = reg or find_regulation(regs, request)
    if not reg:
        return {"ok": False, "error": "요청에서 규정명을 찾지 못함", "tries": []}
    cands = candidates(reg, request)
    # 뒤 번호를 밀라는데 밀릴 범위에 가지번호 조(제4조의2)가 있으면 적용기가 번호 이동을 표현할 수 없다 → LLM을 헛돌리지 않고 바로 안내
    # (261015 시연 리허설: 계약사무처리지침에서 3회 실패 뒤 원인과 다른 안내가 나감)
    anchor = insert_anchor(request)
    if anchor:
        after = anchor[0]
        nums = {int(re.match(r"제(\d+)조", a["label"]).group(1)) for a in reg["articles"] if not a.get("deleted") and "의" not in a["label"]}
        run, n = set(), after + 1
        while n in nums:   # 밀릴 조 = 신설 자리부터 이어지는 번호 (결번에서 멈춤)
            run.add(n)
            n += 1
        branch = [a["label"] for a in reg["articles"] if not a.get("deleted") and "의" in a["label"]
                  and int(re.match(r"제(\d+)조", a["label"]).group(1)) in run | {max(run, default=0) + 1}]
        if run and branch:
            return {"ok": False, "tries": [], "error": f"뒤 번호를 밀 범위에 가지번호 조({', '.join(branch[:3])})가 있어 번호 이동 개정문을 만들 수 없습니다. "
                    f"「제{after}조의2를 신설」처럼 가지번호로 넣거나, 번호를 어떻게 옮길지 직접 적어 주세요."}
    plan = structural_plan(reg, request, cands)
    if plan and not plan.endswith(") "):   # 본문까지 규칙으로 완성된 경우만 (본문이 없으면 LLM이 전체를 씀)
        try:
            cs, new = build_changeset(reg, plan, "nl")
            if not intent_issues(request, cs, reg, cands):
                return {"ok": True, "reg_id": reg["reg_id"], "reg_name": reg["name"], "amendment": plan, "changeset": cs,
                        "new_reg": new, "tries": [{"output": plan, "error": "", "by": "rule"}],
                        "candidates": [a["label"] for a in cands]}
        except AmendError:
            pass
    if plan and plan.endswith(") "):   # 위치·제목은 규칙이 정했고 본문만 비었다 → LLM에는 개정문 전체가 아니라 본문 한 문장만 쓰게 한다 (7B가 통째로 쓰면 실패: 신설 평가)
        body_tries = []
        for _ in range(2):
            try:
                body = _post(model, [{"role": "system", "content": "사내 규정 조문의 본문을 쓴다. 요청에서 새로 넣을 조문의 내용만 법제 문체(…한다.)로 한두 문장으로 쓴다. 조 번호·제목·설명·따옴표 없이 본문만."},
                                     {"role": "user", "content": request}]).strip().splitlines()[0].strip(" '\"「」")
            except (OSError, IndexError):
                break
            cand = plan + body
            try:
                cs, new = build_changeset(reg, cand, "nl")
                issues = intent_issues(request, cs, reg, cands)
                if issues:
                    raise AmendError("요청과 어긋남: " + " / ".join(issues))
                body_tries.append({"output": cand, "error": "", "by": "rule+llm"})
                return {"ok": True, "reg_id": reg["reg_id"], "reg_name": reg["name"], "amendment": cand, "changeset": cs,
                        "new_reg": new, "tries": body_tries, "candidates": [a["label"] for a in cands]}
            except AmendError as e:
                body_tries.append({"output": cand, "error": str(e)})
    toc = ", ".join(f"{a['label']}({a['title']})" for a in reg["articles"] if not a.get("deleted"))
    user = (f"요청: {request}\n\n대상 규정: 「{reg['name']}」\n조 목록: {toc}\n\n관련 조문 원문:\n"
            + "\n\n".join(article_text(a) for a in cands) + "\n\n개정문만 출력:")
    messages = [{"role": "system", "content": RULES}, {"role": "user", "content": user}]
    tries = []
    for _ in range(MAX_TRIES):
        raw = _post(model, messages)
        amend = clean_output(raw)
        blocks = article_blocks(raw)
        if blocks:   # (B) 조문을 다시 썼으면 우리가 비교해 개정문을 만든다 (지시문과 섞이면 지시문 + 비교 결과)
            from_blocks = blocks_to_amendment(reg, blocks)
            body_lines = {l for b in blocks for l in b}
            amend = "\n".join([l for l in amend.splitlines() if l not in body_lines] + ([from_blocks] if from_blocks else [])).strip()
        try:
            if not amend:
                raise AmendError("개정 문장이 없음 (형식에 맞는 「…한다.」 문장만 출력)")
            cs, new = build_changeset(reg, amend, "nl")
            issues = intent_issues(request, cs, reg, cands)
            if issues:   # 적용은 되지만 요청과 어긋남 → 다시 쓰게 함
                raise AmendError("요청과 어긋남: " + " / ".join(issues))
            tries.append({"output": amend, "error": ""})
            return {"ok": True, "reg_id": reg["reg_id"], "reg_name": reg["name"], "amendment": amend, "changeset": cs,
                    "new_reg": new, "tries": tries, "candidates": [a["label"] for a in cands]}
        except AmendError as e:
            tries.append({"output": amend or raw[:300], "error": str(e)})
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": f"적용기 오류: {e}\n원문 문구를 글자 그대로 복사하고 허용 형식만 써서 개정문을 다시 출력하라."}]
    return {"ok": False, "reg_id": reg["reg_id"], "reg_name": reg["name"], "tries": tries, "candidates": [a["label"] for a in cands]}
