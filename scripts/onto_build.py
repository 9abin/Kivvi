"""검색용 규정 온톨로지 구축. 스키마: reports/ontology-schema-retrieval-20261028.md

  set -a; . ./.env; set +a
  python scripts/onto_build.py scope <out_dir> [--terms 휴직,휴가]   # 범위: 용어가 나오는 조 + 그 조가 인용하는 조
      [--all]                                                       #   전체 구축: 항목이 있는 조 전부 (빈 폴더에서 시작한다)
      [--roots 휴가,휴직,근로시간단축]                               #   + 트리에서 이 개념과 하위 개념이 나오는 조 (한 바퀴 돈 뒤)
  python scripts/onto_build.py extract <out_dir> [--limit N]        # 조마다 규정문·개념·대상·언급 추출 (이어서. 형식 실패 조는 다시 묻는다)
                                                                    #   extract_raw.jsonl에 모델 원래 출력, extract_meta.json에 프롬프트 해시
  python scripts/onto_build.py stats <out_dir>                      # 추출 결과 요약
  python scripts/onto_build.py normalize <out_dir>                  # 3단계: 개념 이름 합치기
  python scripts/onto_build.py tree <out_dir>                       # 4단계: 상위 개념과 관계
  python scripts/onto_build.py relcheck <out_dir>                   # 4단계 보조: 관계를 원문과 대조해 엄격히 거름
  python scripts/onto_build.py parentcheck <out_dir>                # 4단계 보조: 상위 선을 엄격히 다시 판정해 거름
  python scripts/onto_build.py alias <out_dir>                     # 5단계: 다른 이름·일상어 별칭 (그 뒤 normalize를 다시 돌리면 다른 이름이 합치기 후보가 된다)
  python scripts/onto_build.py load <out_dir>                      # Neo4j 적재 (Item, Statement, Concept, Target)

범위·추출은 같은 규칙과 프롬프트를 모든 조에 쓴다. 평가 질문과 정답은 프롬프트에 넣지 않는다.
"""
import argparse
import collections
import hashlib
import json
import os
import re
import sys
import time

import psycopg
import requests

sys.stdout.reconfigure(encoding="utf-8")

FACETS = ["정의", "대상·자격", "요건·사유", "기간·시기·횟수", "금액·비율·한도", "절차·신청·승인", "비용부담", "의무·금지", "효과",
          "예외·특례", "조치·제재", "위임·근거"]
MODES = ["의무", "재량", "금지", "허용"]
KINDS = ["제도", "사항", "상황", "역할", "기구"]

PROMPT = f"""사내 규정의 조 하나를 항목(항·호) 단위로 읽고, 이 조가 정하는 사실을 규정문으로 뽑는다. 결과는 검색에 쓴다:
직원이 어떤 제도·사항의 어떤 면을 물으면, 그 제도에 대한 규정문을 가진 조를 모두 찾기 위한 것이다.

규정문 하나 = 한 항목이 한 개념에 대해 정하는 사실 하나. 항목마다 다음을 쓴다.
- "항목": 입력에 적힌 항목 번호 그대로 (예: "2", "2-1").
- "개념": 이 규정문이 말하는 제도·사항의 이름. 규정에 쓰인 공식 용어로, **제도 단위**로 쓴다 (예: "업무용 차량", "도서 대출", "보안점검", "기록물 폐기").
  항목 내용이나 조 제목을 이름으로 만들지 않는다 ("운전자 자격 제한" X -> "업무용 차량"). 한 조의 규정문은 보통 개념 1~2개로 모인다.
  제도의 신청·보고·신고·기한·서식·점검 같은 세부 사항은 개념이 아니라 그 제도의 "면"이다
  ("차량 운행일지 제출" X -> 개념 "업무용 차량", 면 "절차·신청·승인". "도서 대출 기한" X -> 개념 "도서 대출", 면 "기간·시기·횟수").
  조 제목이 "제도 이름 + 보고·신고·신청·제출·점검·관리"여도 개념은 앞의 제도 이름이다 ("대출도서 반납 신고" -> "도서 대출",
  "차량 이용자 운행 보고" -> "업무용 차량"). 사람 집단 + 행위("대출자 연체 통보", "운전자 사고 보고")도 그 제도("도서 대출", "업무용 차량")가 개념이다.
  "자산", "문서"처럼 너무 넓은 말은 그 항목이 정말 자산 전체를 말할 때만 쓴다.
  부서장·담당관 같은 직위는 개념이 아니다 (승인권자 등은 요지에 쓴다). 직위 자체의 지정·자격·직무를 정하는 조일 때만 "보안담당관 지정"처럼 제도로 쓴다.
  규정·지침·법령의 이름은 개념이 아니라 "위임처"에 쓴다.
  조문에 없는 제도 이름을 지어내지 않는다. 사유·종류를 나열한 호는 새 제도 이름이 아니라 그 조가 정하는 제도의 "요건·사유"다
  (예: "다음 사유로 대출 기간을 연장할 수 있다: 1. 예약자 없음 2. 연구 목적" -> 개념은 둘 다 "도서 대출", "연구목적도서대출"을 만들지 않는다).
- 한 제도(A)가 다른 제도(B)에 어떻게 반영되는지 정한 항목(예: 연체 기간은 대출 한도 산정에서 뺀다, 보안점검 결과는 접근권한 갱신에 반영한다)은
  "개념"=A, "면"="효과", "영향개념"=B로 쓴다. A를 언급으로만 두지 않는다.
- "운전직", "외부 방문자", "용역 직원"처럼 사람의 집단은 개념이 아니라 "대상"에 쓴다.
- "개념종류": {KINDS} 중 하나. 제도=도서 대출·업무용 차량 같은 제도, 사항=보존기간·대출 한도처럼 정해지는 대상, 상황=야간 출입·재난처럼 여러 제도가 걸리는 상황, 역할=보안담당관 같은 직위, 기구=위원회.
- "면": {FACETS} 중 하나.
  효과 = 이 개념이 다른 제도(평가, 지급, 기간 산정, 한도 등)에 미치는 영향. "영향개념"에 그 다른 제도 이름을 쓸 수 있을 때만 효과다.
  "별도로 평가한다", "따로 정한다"는 효과가 아니다 (대상이면 대상·자격, 다른 규정에 맡기면 위임·근거).
  위임·근거 = 이 개념의 (일부) 사항을 다른 규정·지침·법령이 정한다고 한 것.
- "요지": 30자 안팎 한 줄. 양태와 어긋나지 않게 쓴다 ("할 수 있다"를 "의무"로 쓰지 않는다).
- "값원문": 숫자·기간·금액·비율이 있으면 원문 그대로 (없으면 "").
- "조건원문": 값이 적용되는 조건이 있으면 원문 그대로 (없으면 "").
- "양태": {MODES} 중 하나. "하여야 한다"=의무, "할 수 있다"=허용, "아니 된다·할 수 없다"=금지, 결정권자가 정함=재량.
- "대상": 누구에게 적용되는지 [{{"이름": "직원 집단", "역할": "포함|제외"}}]. 조문에 적힌 집단만. 전 직원이면 빈 배열.
  "(용역 직원 제외)", "~를 제외한 모든 직원", "~에게는 적용하지 아니한다"는 반드시 "제외"로 쓴다. 이름은 짧게 ("운전직", "외부 방문자", "계약직").
  조 제목이나 첫 항이 특정 집단에 대한 조이면("운전직의 복무", "외부 방문자는 다음을 지킨다") 그 조의 모든 규정문에 그 집단을 "포함"으로 붙인다.
  호마다 다시 적혀 있지 않아도 붙인다. 항목 문장이 "운전직에 대한 ~", "외부 방문자의 ~"처럼 특정 집단을 주어로 하면 그 집단을 "포함"으로 쓴다.
  "역할"은 "포함"과 "제외" 둘 중 하나만 쓴다.
- "위임처": 이 항목이 다른 규정·지침·법령에 따른다고 하면 [{{"이름": "규정 이름(「」 없이)", "조": "제N조 또는 빈 문자열"}}]. 없으면 빈 배열.
  면이 위임·근거가 아니어도 위임처를 쓴다 (예: "보존기간은 기록물관리지침의 기준표에 따른다").
- "구성": "A는 B, C, D로 구성한다·구분한다·한다"처럼 개념의 종류나 구성 요소를 나열하면 그 이름들 (면은 "정의"). 서식·신고서 이름, 서식의 기재 항목, 기간·횟수 값은 구성이 아니다. 없으면 빈 배열.
- "대상"에는 적용받는 사람 집단만 쓴다. 승인하는 직위(부서장, 승인권자 등)는 대상이 아니다.
- 다른 규정의 조를 가져다 쓰면("보안업무규정 제12조 각 호의 구역") 그것도 위임처에 쓴다 ({{"이름": "보안업무규정", "조": "제12조"}}).
- 법령도 같다: "「○○법」 제N조에 따른", "○○법 시행령에 의한"처럼 법령을 근거로 들면 위임처에 법령 이름과 조를 쓴다 (언급에도 같이 써도 된다).
값원문·조건원문·대상·위임처·구성·영향개념이 비면 그 칸은 쓰지 않는다 (출력을 짧게).
호는 머리말(상위 항의 본문)과 합쳐야 문장이 완성된다. 머리 항이 "다음 각 호와 같다"뿐이면 그 항에는 규정문을 만들지 않는다.
원칙과 단서가 한 항목에 있고 단서가 값을 바꾸면 규정문 둘로 쓴다.

규정문의 개념이 아닌 다른 제도의 이름이 항목에 나오면 "언급"에 쓴다 (규정문이 있는 항목도): [{{"항목": "3", "개념": "이름"}}].
조문에 없는 내용을 만들지 않는다. 출력은 줄바꿈·들여쓰기 없는 JSON 하나만 쓴다:
{{"규정문": [...], "언급": [...]}}

예 (차량관리지침, 평가와 무관한 예시):
입력: 제7조(차량의 사용) [1] 업무용 차량은 승용차, 승합차, 화물차로 구분한다. [2] 업무용 차량은 업무수행 목적으로만 사용하여야 한다. 다만, 운전직(계약직 제외)은 출퇴근에 사용할 수 있다. [3] 다음 각 호의 경우에는 총무부서장의 승인을 받아 휴일에 차량을 사용할 수 있다. [3-1] 재난 대응 [3-2] 총무부서장이 지정한 행사 [4] 차량 사고 처리에 관한 사항은 「안전관리규정」이 정한 바에 따른다. [5] 업무용 차량은 「자동차손해배상 보장법」 제5조에 따른 보험에 가입하여야 한다.
출력: {{"규정문":[{{"항목":"1","개념":"업무용 차량","개념종류":"제도","면":"정의","요지":"승용·승합·화물차로 구분","양태":"의무","구성":["승용차","승합차","화물차"]}},{{"항목":"2","개념":"업무용 차량","개념종류":"제도","면":"의무·금지","요지":"업무 목적으로만 사용","양태":"의무"}},{{"항목":"2","개념":"업무용 차량","개념종류":"제도","면":"예외·특례","요지":"운전직은 출퇴근 사용 가능","양태":"허용","대상":[{{"이름":"운전직","역할":"포함"}},{{"이름":"계약직","역할":"제외"}}]}},{{"항목":"3-1","개념":"업무용 차량","개념종류":"제도","면":"절차·신청·승인","요지":"재난 대응 시 휴일 사용은 총무부서장 승인","조건원문":"재난 대응","양태":"허용"}},{{"항목":"3-2","개념":"업무용 차량","개념종류":"제도","면":"절차·신청·승인","요지":"지정 행사 시 휴일 사용은 총무부서장 승인","조건원문":"총무부서장이 지정한 행사","양태":"허용"}},{{"항목":"4","개념":"차량 사고","개념종류":"사항","면":"위임·근거","요지":"사고 처리는 안전관리규정에 따름","양태":"의무","위임처":[{{"이름":"안전관리규정","조":""}}]}},{{"항목":"5","개념":"업무용 차량","개념종류":"제도","면":"의무·금지","요지":"자동차손해배상 보장법에 따른 보험 가입","양태":"의무","위임처":[{{"이름":"자동차손해배상 보장법","조":"제5조"}}]}}],"언급":[{{"항목":"3-1","개념":"재난 대응"}}]}}"""


MODEL, OLLAMA = "qwen3:30b-a3b-instruct-2507-q4_K_M", "http://127.0.0.1:11434/api/chat"


def ask(system, user, num_ctx=12288, num_predict=3072, fmt=None, tries=5):
    """Ollama 기본 API로 직접 부른다: OpenAI 호환 경로는 num_ctx를 못 바꿔 기본 4096에서 긴 조가 잘린다.
    연결 끊김·시간 초과는 기다렸다가 다시 묻는다: 몇 시간짜리 실행이 Ollama 재시작 한 번에 멈추지 않게."""
    for k in range(tries):
        try:
            r = requests.post(OLLAMA, json={"model": MODEL, "stream": False, "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": num_predict},
                                            **({"format": fmt} if fmt else {}),
                                            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}, timeout=900)
            r.raise_for_status()
            return r.json()["message"]["content"]
        except requests.RequestException as e:
            if k == tries - 1:
                raise
            print(f"  모델 호출 실패, {30 * (k + 1)}초 뒤 다시: {e!r:.120}", flush=True)
            time.sleep(30 * (k + 1))


def pg(db):
    return psycopg.connect(host=os.environ.get("PG_HOST", "127.0.0.1"), port=os.environ.get("PG_PORT", "5432"), dbname=db,
                           user=os.environ["PG_USER"], password=os.environ["PG_PASSWORD"], connect_timeout=15,
                           options="-c default_transaction_read_only=on")


def read_items():
    """조 ID -> {문서, 라벨, 제목, 항목[(번호, 본문, 머리말)]}. 삭제된 항목은 뺀다."""
    with pg("regulations") as c:
        regs = dict(c.execute("select doc_id, title from documents").fetchall())
        rows = c.execute("select article_id, reg_id, label, title, item_id, hang, ho, mok, text, deleted from amend.chunk_detail "
                         "order by article_id, occ, hang, ho, mok").fetchall()
    arts = {}
    heads = {}
    for aid, reg, label, title, iid, hang, ho, mok, text, deleted in rows:
        if not re.fullmatch(r"\d+:\d+(?:의\d+)?", aid or ""):
            continue
        a = arts.setdefault(aid, {"reg": reg, "doc": regs.get(reg, reg), "label": label, "title": title, "items": []})
        text = (text or "").strip()
        if ho == 0 and mok == 0:
            heads[(aid, hang)] = text
        if deleted or not text:
            continue
        no = "-".join(str(x) for x in (hang, ho, mok) if x) or "0"
        head = heads.get((aid, hang), "") if ho else ""
        a["items"].append({"no": no, "id": iid, "text": text, "head": head})
    return arts


def cmd_scope(a):
    terms = a.terms.split(",")
    arts = read_items()
    pat = re.compile("|".join(map(re.escape, terms)))
    if a.all:  # 전체 구축: 항목이 있는 조 전부
        terms = ["(전체)"]
        hit = {k for k, v in arts.items() if v["items"]}
    else:
        hit = {k for k, v in arts.items() if any(pat.search(i["text"]) for i in v["items"]) or pat.search(v["title"] or "")}
    with pg("regulations") as c:
        edges = c.execute("select src_article, target from amend.edge where kind='인용' and target_type='article'").fetchall()
    old = os.path.join(a.out, "scope.json")
    if a.roots and os.path.exists(os.path.join(a.out, "tree.json")):
        # 개념 기준 넓히기: 글자만으로 고르면 공가처럼 "휴가"라는 말 없이 휴가의 한 종류를 정한 조가 빠진다.
        # 이미 만든 트리에서 뿌리 개념과 그 하위 개념을 모으고, 그 이름(합쳐진 다른 표기 포함)이 나오는 조를 더한다.
        rep = json.load(open(os.path.join(a.out, "normalize.json"), encoding="utf-8"))["rep"]
        tree = remap_tree(json.load(open(os.path.join(a.out, "tree.json"), encoding="utf-8")), rep)
        kids = collections.defaultdict(set)
        for c_, p in tree["parents"].items():
            kids[p].add(c_)
        core, todo = set(a.roots.split(",")), list(a.roots.split(","))
        while todo:
            for k in kids.get(todo.pop(), ()):
                if k not in core:
                    core.add(k)
                    todo.append(k)
        names = {norm(n) for n, r in rep.items() if r in core and len(norm(n)) >= 2}
        by_concept = {k for k, v in arts.items() if any(n in norm(v["title"] or "") + "".join(norm(i["text"]) for i in v["items"]) for n in names)}
        print(f"뿌리 {a.roots} 아래 개념 {len(core)}개 -> 그 이름이 나오는 조 {len(by_concept)}개, 글자 규칙에 없던 조 {len(by_concept - hit)}개")
        hit |= by_concept
    cited = {t for s, t in edges if s in hit and t in arts} - hit
    scope = {"terms": terms, "roots": a.roots, "hit": sorted(hit), "cited": sorted(cited)}
    if os.path.exists(old):
        prev = json.load(open(old, encoding="utf-8"))
        print(f"이전 범위 {len(prev['hit']) + len(prev['cited'])}개 -> {len(hit | cited)}개")
    os.makedirs(a.out, exist_ok=True)
    json.dump(scope, open(os.path.join(a.out, "scope.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    docs = {arts[k]["doc"] for k in hit | cited}
    print(f"용어가 나오는 조 {len(hit)}개 + 인용으로 들어오는 조 {len(cited)}개 = {len(hit | cited)}개, 문서 {len(docs)}건, "
          f"항목 {sum(len(arts[k]['items']) for k in hit | cited)}개")


def render(a):
    lines = [f"{a['doc']} {a['label']}({a['title']})"]
    for it in a["items"]:
        head = f" (머리말: {it['head'][:350]})" if it["head"] else ""   # 150자로 자르면 머리말 151개의 조건이 잘린다 (전체 최대 322자)
        lines.append(f"[{it['no']}]{head} {it['text'][:600]}")
    return "\n".join(lines)


def near_facet(f):
    """면 이름이 조금 다르면("효과·처우", "절차") 12개 중 가까운 하나로 받는다. 하나로 정해지지 않으면 그대로 둔다(버려진다)."""
    # 휴직·파견 "처우"(급여·근속·승진에 미치는 영향)는 효과다.
    # 전체 추출에서 12개에 맞는 칸이 없어 모델이 지어낸 면(위원회 구성·담당자 역할 약 100개 등)도 버리지 않고 가까운 칸으로 옮긴다.
    # "목적"(제1조)은 그대로 버린다. 면은 검색 문장에 안 들어가므로 칸이 조금 어색해도 검색에는 영향이 없다
    f = {"처우": "효과", "영향": "효과", "역할": "정의", "구성": "정의", "기구": "정의", "허용": "의무·금지", "재량": "의무·금지",
         "가중치": "금액·비율·한도", "보존기간": "기간·시기·횟수", "보관": "기간·시기·횟수", "적용 범위": "대상·자격"}.get(f.strip(), f)
    parts = [p for p in re.split(r"[·ㆍ,/\s]+", f) if p]
    cand = {x for x in FACETS if any(p in x.split("·") for p in parts)}
    return cand.pop() if len(cand) == 1 else f


def fix_braces(s):
    """위임처 목록을 닫은 직후의 괄호 버릇을 바로잡는다. 다시 물어도 같은 조에서 같은 자리가 틀린다 (전체 추출에서 19조, 6번씩).
    `]},"{"항목"`·`]},"항목"`·`]},"면"` → 다음 규정문을 여는 `{`가 빠지거나 자리가 틀림.
    `]},"면"`도 앞 규정문에 붙이지 않고 새 규정문으로 연다: 붙이면 같은 키가 두 번 생겨 앞 규정문의 면·요지가 덮인다 (점검 3에서 3번 모두 새 규정문이었다).
    빠진 항목·개념은 parse()가 앞 규정문에서 물려받는다."""
    s = re.sub(r'\]\},\s*"?\{\s*"?항목"', ']},{"항목"', s)
    s = re.sub(r'\]\},"(항목|개념|면|요지|양태|값원문|조건원문|대상|구성|영향개념|개념종류)"', r']},{"\1"', s)
    return re.sub(r'"\}\},\{"항목"', '"},{"항목"', s)   # 규정문 안에 객체 값은 없으므로 `"}},{`는 `}`가 하나 더 들어간 것이다


SHARED = ("항목", "개념", "개념종류", "면", "요지", "양태")


def split_dup_keys(pairs):
    """한 객체 안에 같은 키가 다시 나오면 새 규정문으로 나눈다. json.loads는 뒤엣것만 남겨 앞 값이 조용히 사라진다
    (점검 3: 59번. 값원문 "5배수 이내"·"10배수 이내" 중 하나만 남는 식).
    새 조각은 그 키가 처음 나온 자리 앞까지만 물려받는다: {…, 값원문 A, 조건원문 a, 값원문 B, 조건원문 b} → (A, a), (B, b).
    앞 조각을 통째로 복사하면 (B, a)처럼 값과 조건이 엇갈린다. 머리 칸(SHARED)만 다른 조각에서 채우고 값 칸은 섞지 않는다."""
    parts, unsure = [[]], False
    for k, v in pairs:
        ks = [x for x, _ in parts[-1]]
        if k in ks:
            parts.append(parts[-1][: ks.index(k)])
            # 물려받는 앞부분에 값 칸이 있으면 "값 A, 값 B, 조건 a, 조건 b"처럼 쓴 것이라 순서로는 짝을 알 수 없다 (23926:36의2)
            unsure |= any(x in ("값원문", "조건원문", "영향개념") for x, _ in parts[-1])
        parts[-1].append((k, v))
    if len(parts) == 1:
        return dict(parts[0])
    ds = [dict(p) for p in parts]
    if unsure:
        for d in ds:
            d["짝불확실"] = True
    for i, d in enumerate(ds):
        for k in SHARED:
            if k not in d:
                v = next((o[k] for o in ds[:i][::-1] + ds[i + 1:] if k in o), None)
                if v is not None:
                    d[k] = v
    return {"__나눔__": ds}


def parse(raw, nos, first=False):
    """first: 제1조. 제1조의 목적 문장("공정한 감사업무 수행")은 어느 질문에나 조금씩 걸려 관련 조 칸을 차지하므로 버린다.
    본문 조의 목적 문장("사원증: 효율적인 복무·보안 관리")은 제도가 왜 있는지 답하므로 살린다."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        d = json.loads(m.group(0), object_pairs_hook=split_dup_keys)
    except AttributeError:
        return None
    except ValueError:   # 맞는 JSON은 건드리지 않고, 틀렸을 때만 고쳐서 한 번 더 읽는다
        try:
            d = json.loads(fix_braces(m.group(0)), object_pairs_hook=split_dup_keys)
        except ValueError:
            return None
    if "__나눔__" in d:   # 맨 바깥에서 "규정문"이 두 번 나온 경우: 두 목록을 이어 붙인다 (나뉜 쪽은 앞 목록을 물려받으므로 같은 목록은 한 번만)
        lists = []
        for p in d["__나눔__"]:
            if not any(p.get("규정문") is x for x in lists):
                lists.append(p.get("규정문") or [])
        d = {"규정문": [x for L in lists for x in L], "언급": d["__나눔__"][-1].get("언급") or []}
    out = {"규정문": [], "언급": [], "버림": 0, "버린것": []}
    prev = {}
    stmts = [p for s in d.get("규정문") or [] for p in (s["__나눔__"] if isinstance(s, dict) and "__나눔__" in s else [s])]
    for s in stmts:
        # 괄호를 고친 자리에서 항목·개념이 빠지기도 한다. 모델이 바로 앞 규정문을 이어 쓰던 중이라 앞 규정문 것을 물려받는다
        if isinstance(s, dict) and prev:
            for k in ("항목", "개념"):
                if s.get(k) is None:
                    s[k] = prev[k]
        if isinstance(s, dict) and s.get("면") not in FACETS:
            if s.get("면") in ("허용", "재량"):   # 양태를 면 칸에 잘못 넣은 경우: "2천만원 미만은 수의계약 가능"
                s["양태"] = s["면"]
            orig, s["면"] = s.get("면"), near_facet(str(s.get("면") or ""))
            if s["면"] not in FACETS and not (orig == "목적" and first):   # 옮길 칸이 없는 드문 면("공개", "용도" 등 20여 종)도 내용은 살리고 "기타"로 둔다
                s["면원문"], s["면"] = orig, "기타"
        why = ("형식" if not isinstance(s, dict) else "항목 번호" if str(s.get("항목")) not in nos
               else "개념 없음" if not str(s.get("개념", "")).strip() else "면" if s.get("면") not in FACETS + ["기타"] else None)
        if why:
            out["버림"] += 1
            out["버린것"].append({"이유": why, **({k: s.get(k) for k in ("항목", "개념", "면", "요지")} if isinstance(s, dict) else {})})
            continue
        s["항목"] = str(s["항목"])
        s["양태"] = s.get("양태") if s.get("양태") in MODES else None
        s["개념종류"] = s.get("개념종류") if s.get("개념종류") in KINDS else None
        s["대상"] = [t for t in (s.get("대상") or []) if isinstance(t, dict) and t.get("이름")]
        for t in s["대상"]:  # 역할은 포함·제외만 받는다. "승인", "요구" 같은 값은 포함으로 두고 원래 값을 남긴다
            if t.get("역할") not in ("포함", "제외"):
                out["버린것"].append({"이유": "대상 역할", "항목": s["항목"], "개념": s.get("개념"), "면": s.get("면"), "요지": f"{t.get('이름')}: {t.get('역할')}"})
                t["역할"] = "포함"
        s["위임처"] = [{"이름": str(t["이름"]).strip("「」 "), "조": str(t.get("조") or "")} for t in (s.get("위임처") or [])
                      if isinstance(t, dict) and t.get("이름")]
        s["구성"] = [str(x) for x in (s.get("구성") or []) if isinstance(x, str) and x.strip()]
        if s in out["규정문"]:
            continue   # 모든 칸이 같은 규정문만 지운다. 항목·개념·요지만 비교하면 값원문·대상·영향개념이 다른 규정문까지 지워진다 (점검 3: 49개 중 36개)
        out["규정문"].append(s)
        prev = s
    for m_ in d.get("언급") or []:
        if isinstance(m_, dict) and str(m_.get("항목")) in nos and m_.get("개념"):
            out["언급"].append({"항목": str(m_["항목"]), "개념": str(m_["개념"])})
    return out


def save_atomic(path, obj):
    """임시 파일에 쓴 뒤 바꿔 넣는다: 저장 도중에 끊겨도 extract.json이 깨지지 않아 이어하기가 멈추지 않는다."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def cmd_extract(a):
    scope = json.load(open(os.path.join(a.out, "scope.json"), encoding="utf-8"))
    arts = read_items()
    ids = scope["hit"] + scope["cited"]
    if a.only:
        ids = a.only.split(",")
    if a.limit:
        ids = ids[: a.limit]
    path = os.path.join(a.out, "extract.json")
    done = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    # 어떤 프롬프트·모델로 뽑았는지 남긴다. 이어 돌릴 때 프롬프트가 바뀌었으면 멈춘다 (한 파일에 두 프롬프트 결과가 섞이지 않게)
    meta_path = os.path.join(a.out, "extract_meta.json")
    meta = {"model": MODEL, "prompt_sha": hashlib.sha1(PROMPT.encode("utf-8")).hexdigest()[:12], "num_ctx": 16384, "num_predict": 8192}
    if os.path.exists(meta_path) and done:
        old = json.load(open(meta_path, encoding="utf-8"))
        if old.get("prompt_sha") != meta["prompt_sha"] and not a.only:
            raise SystemExit(f"extract.json은 다른 프롬프트({old.get('prompt_sha')})로 뽑은 것이다. 새 폴더에서 시작한다.")
    json.dump({**meta, "started": time.strftime("%Y-%m-%d %H:%M:%S")}, open(meta_path, "w", encoding="utf-8"), ensure_ascii=False)
    raw_log = open(os.path.join(a.out, "extract_raw.jsonl"), "a", encoding="utf-8")   # 모델 원래 출력 (왜 버렸는지 나중에 본다)
    t0 = time.time()
    for n, aid in enumerate(ids, 1):
        # 이어하기: 끝난 조는 건너뛰되, 형식 실패로 비어 있는 조(버림 -1)는 다시 묻는다. --only는 지정한 조를 다시 뽑는다
        if (aid in done and done[aid].get("버림") != -1 and not a.only) or not arts[aid]["items"]:
            continue
        nos = {i["no"] for i in arts[aid]["items"]}
        res = None
        for k in range(2):   # 형식이 틀리면 한 번 더
            raw = ask(PROMPT, render(arts[aid]), num_ctx=16384, num_predict=8192)   # 36항목 조는 출력이 약 5,800토큰이라 6,144는 빠듯하다
            raw_log.write(json.dumps({"조": aid, "시도": k + 1, "raw": raw}, ensure_ascii=False) + "\n")
            raw_log.flush()
            res = parse(raw, nos, first=aid.endswith(":1"))
            if res is not None:
                break
        done[aid] = res or {"규정문": [], "언급": [], "버림": -1, "버린것": []}
        if n % 10 == 0:
            save_atomic(path, done)
            print(f"  {n}/{len(ids)} ({time.time() - t0:.0f}초)", flush=True)
    save_atomic(path, done)
    raw_log.close()
    print(f"추출 완료 {len(done)}개 조, 형식 실패 {sum(1 for v in done.values() if v.get('버림') == -1)}개, "
          f"버린 규정문 {sum(max(v.get('버림', 0), 0) for v in done.values())}개")


def cmd_reparse(a):
    """모델에 다시 묻지 않고 extract_raw.jsonl에 남은 출력을 최근 것부터 다시 읽는다. 기본은 형식 실패로 비어 있는 조(버림 -1)만,
    --all은 모든 조 (읽기 규칙을 바꾼 뒤). 최근 출력부터 읽으므로 이미 채워진 조는 그때 쓴 출력을 그대로 다시 읽는다."""
    path = os.path.join(a.out, "extract.json")
    done = json.load(open(path, encoding="utf-8"))
    arts = read_items()
    raws = collections.defaultdict(list)
    for line in open(os.path.join(a.out, "extract_raw.jsonl"), encoding="utf-8"):
        r = json.loads(line)
        if r["조"] in done and (a.all or done[r["조"]].get("버림") == -1):
            raws[r["조"]].append(r["raw"])
    fixed = []
    for aid, outs in raws.items():
        nos = {i["no"] for i in arts[aid]["items"]}
        res = next((p for p in (parse(o, nos, first=aid.endswith(":1")) for o in reversed(outs)) if p is not None), None)
        if res is not None:
            done[aid] = res
            fixed.append(aid)
    if a.all:
        reparse_supplement(a.out)
    merge_supplement(a.out, done)
    fix_targets(a.out, done)
    canon_concepts(done)
    apply_modes(a.out, done)   # 다시 읽으면 양태가 추출 때 값으로 돌아가므로 modecheck 결과를 다시 덮는다
    save_atomic(path, done)
    print(f"다시 읽어 채운 조 {len(fixed)}개" + ("" if a.all else f": {','.join(fixed)}"))
    print(f"남은 형식 실패 {sum(1 for v in done.values() if v.get('버림') == -1)}개")


def fix_targets(out, done):
    """대상 "제외"를 원문과 대조해 고친 목록(target_fix.json)을 덮는다. 사물(잡지·전자문서)이나 "A 대신 B"를 제외로 읽은 것은 지우고,
    금지가 걸리는 쪽(외부인·비인가자)을 제외로 읽은 것은 포함으로 바꾼다. 키는 (조, 항목, 요지, 대상 이름)."""
    path = os.path.join(out, "target_fix.json")
    if not os.path.exists(path):
        return
    fix = {(f["조"], f["항목"], f["요지"], f["이름"]): f["처리"] for f in json.load(open(path, encoding="utf-8"))}
    for aid, v in done.items():
        for s in v["규정문"]:
            keep = []
            for t in s["대상"]:
                act = fix.get((aid, s["항목"], s.get("요지"), t["이름"]))
                if act == "포함":
                    t["역할"] = "포함"
                if act != "삭제":
                    keep.append(t)
            s["대상"] = keep


CONCEPT_KEY = re.compile(r"[\s·ㆍ・]")
DETAIL_TAIL = re.compile(r"(관리|처리|절차|결과|보고|신청|기준|방법|사유|운영|지급|대상|기간|요건|제한|평가|심사|결과보고|승인|범위|시기|한도|접수절차|지급시기)$")
NOT_PARENT = {"대표이사", "본부장"}   # "대표이사 평가"는 대표이사라는 직위의 세부가 아니다


def canon_concepts(done):
    """개념 이름 정리. 1) 띄어쓰기·가운뎃점만 다른 이름(직원 채용 / 직원채용, 41묶음)은 가장 많이 쓴 이름 하나로 맞추고 원래 이름은 개념원문에 둔다.
    2) "제도 + 세부"로 지은 이름(민원 처리 / 채용절차)은 앞부분이 따로 개념으로 있으면 그 이름을 상위개념에 단다. 합치지는 않는다:
    합치기(MERGE_PROMPT)는 세부를 합치지 말라고 하므로, 대신 위로 잇는다. 위원회("인사위원회")는 세부가 아니라 기구라서 뺀다."""
    st = [s for v in done.values() for s in v["규정문"]]
    for s in st:   # 다시 실행해도 같은 결과가 나오게 원래 이름에서 시작한다
        s["개념"] = s.pop("개념원문", s["개념"])
        s.pop("상위개념", None)
    count = collections.Counter(s["개념"] for s in st)
    group = collections.defaultdict(list)
    for n in count:
        group[CONCEPT_KEY.sub("", n)].append(n)
    rep = {n: max(ns, key=lambda x: (count[x], x)) for ns in group.values() for n in ns}
    for s in st:
        if rep[s["개념"]] != s["개념"]:
            s["개념원문"], s["개념"] = s["개념"], rep[s["개념"]]
    for s in st:
        k = CONCEPT_KEY.sub("", s["개념"])
        m = DETAIL_TAIL.search(k)
        base = k[: m.start()] if m else ""
        if len(base) >= 2 and base in group and base != k and rep[group[base][0]] not in NOT_PARENT:
            s["상위개념"] = rep[group[base][0]]


def supplement_targets(arts, done):
    """규정문이 하나도 없는 본문 항목. 목록 머리말("다음 각 호와 같다"), 제1조, 삭제된 항목은 뺀다 (점검 3의 기준)."""
    todo = {}
    for aid, v in done.items():
        if aid.endswith(":1"):
            continue
        have = {s["항목"] for s in v["규정문"]}
        miss = []
        for it in arts[aid]["items"]:
            t = it["text"].strip()
            if it["no"] in have or not t or re.match(r"^\s*[<\[]?삭제", t):
                continue
            if len(t) < 90 and "다만" not in t and re.search(r"다음\s*각\s*호|다음과\s*같|각\s*호의", t):
                continue
            miss.append(it["no"])
        if miss:
            todo[aid] = miss
    return todo


def reparse_supplement(out):
    """supplement.json을 supplement_raw.jsonl에서 지금 읽기 규칙으로 다시 만든다 (본 추출의 reparse --all과 같은 이유)."""
    path = os.path.join(out, "supplement_raw.jsonl")
    if not os.path.exists(path):
        return
    raws = collections.defaultdict(list)
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        raws[r["조"]].append(r)
    supp = {}
    for aid, rs in raws.items():
        res = next((p for p in (parse(r["raw"], set(r["항목"]), first=aid.endswith(":1")) for r in reversed(rs)) if p is not None), None)
        supp[aid] = [{**s, "보충": True} for s in (res or {"규정문": []})["규정문"]]
    save_atomic(os.path.join(out, "supplement.json"), supp)


def merge_supplement(out, done):
    path = os.path.join(out, "supplement.json")
    if not os.path.exists(path):
        return
    for aid, sts in json.load(open(path, encoding="utf-8")).items():
        if aid in done:
            done[aid]["규정문"] += [s for s in sts if s not in done[aid]["규정문"]]


def cmd_supplement(a):
    """규정문이 없는 본문 항목만 다시 뽑는다 (점검 3: 항목의 약 1%가 실제 누락, 23678:2 직무관련자 정의 호 11개 등).
    같은 PROMPT를 쓰고, 사용자 메시지에 "이 항목들만" 을 덧붙인다. 원래 출력은 supplement_raw.jsonl, 읽은 결과는 supplement.json.
    reparse가 supplement.json을 다시 합치므로 읽기 규칙을 바꿔도 보충분이 사라지지 않는다. 보충 규정문에는 "보충": true를 단다."""
    path = os.path.join(a.out, "extract.json")
    done = json.load(open(path, encoding="utf-8"))
    arts = read_items()
    spath = os.path.join(a.out, "supplement.json")
    supp = json.load(open(spath, encoding="utf-8")) if os.path.exists(spath) else {}
    todo = {k: v for k, v in supplement_targets(arts, done).items() if k not in supp}
    print(f"보충할 조 {len(todo)}개, 항목 {sum(len(v) for v in todo.values())}개", flush=True)
    raw_log = open(os.path.join(a.out, "supplement_raw.jsonl"), "a", encoding="utf-8")
    t0 = time.time()
    for n, (aid, miss) in enumerate(todo.items(), 1):
        note = ("\n\n이 조에서 다음 항목은 아직 규정문이 없다. **이 항목들만** 규정문을 뽑는다. 다른 항목은 이미 뽑았으니 뽑지 않는다: "
                + ", ".join(f"[{x}]" for x in miss))
        res = None
        for k in range(2):
            raw = ask(PROMPT, render(arts[aid]) + note, num_ctx=16384, num_predict=8192)
            raw_log.write(json.dumps({"조": aid, "시도": k + 1, "항목": miss, "raw": raw}, ensure_ascii=False) + "\n")
            raw_log.flush()
            res = parse(raw, set(miss), first=aid.endswith(":1"))
            if res is not None:
                break
        supp[aid] = [{**s, "보충": True} for s in (res or {"규정문": []})["규정문"]]
        if n % 10 == 0 or n == len(todo):
            save_atomic(spath, supp)
            print(f"  {n}/{len(todo)} ({time.time() - t0:.0f}초) 규정문 {sum(len(v) for v in supp.values())}", flush=True)
    raw_log.close()
    save_atomic(spath, supp)
    merge_supplement(a.out, done)
    canon_concepts(done)
    apply_modes(a.out, done)
    save_atomic(path, done)
    print(f"보충 끝: 조 {len(supp)}개, 규정문 {sum(len(v) for v in supp.values())}개, 아직 빈 항목 {sum(len(v) for v in supplement_targets(arts, done).values())}개")


MODE_PROMPT = """사내 규정 조문과, 그 조문에서 뽑은 규정문 목록이다. 각 규정문의 양태를 **원문 문장**에 맞춰 다시 정한다.
- 의무: "하여야 한다", "한다", "~로 한다", "~에 따른다". 정의·구성·기준을 정한 문장도 의무다.
- 금지: "하여서는 아니 된다", "할 수 없다", "허용하지 아니한다", "~외에는 ~하지 못한다".
- 허용: "할 수 있다", "~가능". 어떤 사람·부서가 그렇게 해도 된다.
- 재량: 결정권자가 정하거나 판단에 맡긴 것. "~이 따로 정한다", "~하지 아니할 수 있다", "~을 거부할 수 있다".
요지의 말투가 아니라 그 규정문이 가리키는 원문 문장을 보고 정한다. 한 원문에 의무와 허용이 함께 있으면 그 규정문 요지가 가리키는 부분을 본다.

예시 원문
[1] 직원은 업무용 차량 사용을 신청할 수 있다.
[2] 운전자는 음주 후 차량을 운전하여서는 아니 된다.
[3] 차량관리부서장은 정비 중인 차량의 배차를 하지 아니할 수 있다.
[4] 차량 운행일지는 별지 제1호서식에 따른다.
예시 규정문: 1. [1] 업무용 차량: 차량 사용 신청 가능 / 2. [2] 음주운전: 음주 후 운전 금지 / 3. [3] 배차: 정비 중 차량 배차 거부 가능 / 4. [4] 운행일지: 서식은 별지 제1호
예시 답: {"양태": ["허용", "금지", "재량", "의무"]}

규정문 수와 같은 길이의 목록으로, JSON 한 줄만 답한다."""
MODE_CUE = re.compile(r"수 있|가능|아니 ?된|안 ?된|없다|없음|금지|불가|못한|못 한|아니한|아니할|않을|않는다|제한")


def mode_key(aid, s):
    return f"{aid}|{s['항목']}|{s['개념']}|{s.get('요지')}"


def apply_modes(out, done):
    path = os.path.join(out, "mode_check.json")
    if not os.path.exists(path):
        return
    fix = json.load(open(path, encoding="utf-8"))["바꿈"]
    for aid, v in done.items():
        for s in v["규정문"]:
            if mode_key(aid, s) in fix:
                s["양태"] = fix[mode_key(aid, s)]["후"]


def cmd_modecheck(a):
    """양태를 원문에 맞춰 다시 정한다. 추출 때 "조치 가능"을 의무로, "불리한 처분 가능"을 금지로 붙인 것이 약 1% 있었다.
    원문이나 요지에 "할 수 있다·아니 된다" 같은 말이 있거나 양태가 의무가 아닌 규정문만 묻는다 (나머지는 "~한다"라 의무가 맞다).
    결과는 mode_check.json에 따로 두고 extract.json에 덮는다. reparse도 같은 결과를 다시 덮는다."""
    path = os.path.join(a.out, "extract.json")
    done = json.load(open(path, encoding="utf-8"))
    arts = read_items()
    mpath = os.path.join(a.out, "mode_check.json")
    res = json.load(open(mpath, encoding="utf-8")) if os.path.exists(mpath) else {"끝난조": [], "실패조": [], "바꿈": {}}
    todo = []
    for aid, v in done.items():
        txt = {i["no"]: (i.get("head") or "") + " " + i["text"] for i in arts[aid]["items"]}
        if aid not in res["끝난조"] and any(MODE_CUE.search(s["요지"] or "") or MODE_CUE.search(txt.get(s["항목"], "")) or s["양태"] != "의무"
                                           for s in v["규정문"]):
            todo.append(aid)
    if a.limit:
        todo = todo[: a.limit]
    t0 = time.time()
    for n, aid in enumerate(todo, 1):
        st = done[aid]["규정문"]
        user = render(arts[aid]) + "\n\n규정문:\n" + "\n".join(f"{j}. [{s['항목']}] {s['개념']}: {s['요지']}" for j, s in enumerate(st, 1))
        got = None
        for _ in range(2):
            try:
                got = json.loads(ask(MODE_PROMPT, user, num_ctx=16384, num_predict=1024, fmt="json"))["양태"]
            except (ValueError, KeyError, TypeError):
                got = None
            if isinstance(got, list) and len(got) == len(st) and all(m in MODES for m in got):
                break
            got = None
        if got is None:
            res["실패조"].append(aid)
        else:
            for s, m in zip(st, got):
                if m != s["양태"]:
                    res["바꿈"][mode_key(aid, s)] = {"전": s["양태"], "후": m}
            res["끝난조"].append(aid)
        if n % 25 == 0 or n == len(todo):
            save_atomic(mpath, res)
            print(f"  {n}/{len(todo)} ({time.time() - t0:.0f}초) 바꿈 {len(res['바꿈'])}", flush=True)
    apply_modes(a.out, done)
    save_atomic(path, done)
    print(f"양태 점검 끝: 조 {len(res['끝난조'])}개, 실패 {len(res['실패조'])}개, 바꾼 규정문 {len(res['바꿈'])}개")


def cmd_stats(a):
    ex = json.load(open(os.path.join(a.out, "extract.json"), encoding="utf-8"))
    st = [s for v in ex.values() for s in v["규정문"]]
    print(f"조 {len(ex)}개, 규정문 {len(st)}개, 언급 {sum(len(v['언급']) for v in ex.values())}개, "
          f"형식 실패 조 {sum(1 for v in ex.values() if v.get('버림') == -1)}개, 버린 규정문 {sum(max(v.get('버림', 0), 0) for v in ex.values())}개")
    print("면:", collections.Counter(s["면"] for s in st).most_common())
    print("개념종류:", collections.Counter(s["개념종류"] for s in st).most_common())
    c = collections.Counter(s["개념"] for s in st)
    print(f"개념 이름 {len(c)}개. 많이 나온 것:", c.most_common(40))


MERGE_PROMPT = """사내 규정에서 뽑은 개념 이름 두 개와 각 개념에 대한 규정문 요지다. 둘이 **완전히 같은 제도를 다르게 부른 것**인지 판단한다.
같음(true)은 동의어, 약칭, 띄어쓰기·표기 차이뿐이다. 예: 공용차 = 업무용 차량, 보존년한 = 보존연한.
다음은 모두 false다.
- 한쪽이 다른 쪽의 한 종류: 차량 / 화물차, 기록물 / 전자기록물
- 같은 상위 아래 서로 다른 종류: 승용차 / 화물차, 일반문서 / 비밀문서
- 한쪽이 다른 쪽의 일부·세부 사항·절차·결과: 도서 대출 / 도서 대출 연장, 기록물 / 기록물 폐기, 차량 / 차량 정비
- 비슷하지만 다른 제도: 정보보안 / 개인정보보호, 출입증 / 방문증
애매하면 false. JSON 한 줄만: {"이유": "15자 안팎", "같음": true 또는 false, "대표": "같으면 더 공식적인 이름"}"""

def statements(ex):
    """(조 ID, 규정문) 목록. 영향개념이 있으면 그 이름도 개념 목록에 들어간다."""
    return [(aid, s) for aid, v in ex.items() for s in v["규정문"]]


def concept_cards(rows, canon=lambda x: x, k=3):
    cards = collections.defaultdict(list)
    for aid, s in rows:
        cards[canon(s["개념"])].append(f"{aid} {s['면']}: {s['요지']}")
        if s.get("영향개념"):
            cards[canon(s["영향개념"])].append(f"{aid} (영향받음) {s['요지']}")
    return {c: v[:k] for c, v in cards.items()}


def norm(s):
    return re.sub(r"[\s·ㆍ‧]", "", s)


def bigrams(x):
    x = norm(x)
    return {x[i:i + 2] for i in range(len(x) - 1)} or {x}


def embed_neighbors(cards, k):
    """이름+요지 임베딩의 가까운 이웃 k개 쌍. 글자가 안 겹치는 동의어(병가 / 인병휴가)를 후보에 올리려고 쓴다."""
    import numpy as np
    keys = sorted(cards)
    texts = [f"{c}: " + " / ".join(x.split(": ", 1)[-1] for x in cards[c]) for c in keys]
    r = requests.post("http://127.0.0.1:11434/api/embed", json={"model": "qwen3-embedding:4b-q8_0", "input": texts}, timeout=600)
    v = np.array(r.json()["embeddings"])
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    sim = v @ v.T
    np.fill_diagonal(sim, -1)
    return {tuple(sorted((keys[i], keys[j]))) for i in range(len(keys)) for j in np.argsort(-sim[i])[:k]}


def cmd_normalize(a):
    """3단계: 공백·가운뎃점 차이는 규칙으로 합치고, 글자가 겹치거나 임베딩이 가까운 이름 쌍만 모델에 같은 제도인지 묻는다."""
    ex = json.load(open(os.path.join(a.out, "extract.json"), encoding="utf-8"))
    rows = statements(ex)
    names = collections.Counter()
    for _, s in rows:
        names[s["개념"]] += 1
        if s.get("영향개념"):
            names[s["영향개념"]] += 1
    by_norm = collections.defaultdict(list)
    for n in names:
        by_norm[norm(n)].append(n)
    rep = {}
    for group in by_norm.values():
        top = max(group, key=lambda x: names[x])
        for n in group:
            rep[n] = top
    cards = concept_cards(rows, lambda x: rep[x], k=2)
    keys = sorted(cards)
    pairs = []
    for i, x in enumerate(keys):
        for y in keys[i + 1:]:
            bx, by = bigrams(x), bigrams(y)
            if len(bx & by) / len(bx | by) >= 0.3 or norm(x) in norm(y) or norm(y) in norm(x):
                pairs.append((x, y))
    n_char = len(pairs)
    pairs += sorted(embed_neighbors(cards, a.emb_k) - set(pairs))
    # 5단계 별칭이 있으면, 한 개념의 별칭이 다른 개념 이름과 같은 쌍도 후보로 (병가 / 인병휴가처럼 글자·임베딩 모두 먼 동의어).
    # 모델이 "병가"를 다른이름이 아니라 일상어 쪽에 넣기도 해서 둘 다 본다. 같은 제도인지는 MERGE_PROMPT가 정한다.
    apath = os.path.join(a.out, "aliases.json")
    if os.path.exists(apath):
        by = {norm(k): k for k in keys}
        for c, v in json.load(open(apath, encoding="utf-8")).items():
            for x in v["다른이름"] + v["일상어"]:
                o = by.get(norm(x))
                if o and c in cards and o != c and tuple(sorted((c, o))) not in pairs:
                    pairs.append(tuple(sorted((c, o))))
    # 지난 실행에서 이미 물어본 쌍은 그 판정을 다시 쓴다 (모델 호출이 쌍마다 몇 초씩 걸린다)
    old = os.path.join(a.out, "normalize.json")
    cache = {(p["A"], p["B"]): p for p in json.load(open(old, encoding="utf-8"))["pairs"]} if os.path.exists(old) else {}
    decisions = []
    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            k = parent[k]
        return k

    for x, y in pairs:
        card = lambda k: f"{k}\n  " + "\n  ".join(cards[k])  # noqa: E731
        if (x, y) in cache:
            d = cache[(x, y)]
        else:
            try:
                d = json.loads(ask(MERGE_PROMPT, f"A: {card(x)}\nB: {card(y)}", num_predict=100, fmt="json"))
            except ValueError:
                d = {}
        decisions.append({"A": x, "B": y, "같음": bool(d.get("같음")), "대표": d.get("대표"), "이유": d.get("이유")})
        # 연쇄로 큰 개념끼리 합쳐지지 않게: 이미 다른 이름과 합쳐진 이름은 다시 합치지 않는다 (한 번에 한 쌍만)
        if d.get("같음") and find(x) == x and find(y) == y:
            main = x if names[x] >= names[y] else y   # 대표는 규정문이 더 많은 이름 (모델이 고른 대표는 기록만)
            other = y if main == x else x
            parent[other] = main
    for n in rep:
        rep[n] = find(rep[n])
    json.dump({"rep": rep, "pairs": decisions}, open(os.path.join(a.out, "normalize.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"개념 이름 {len(names)}개 -> 규칙 후 {len(cards)}개 -> 후보 쌍 {len(pairs)}개(글자 {n_char}, 임베딩 {len(pairs) - n_char}) 중 같음 {sum(d['같음'] for d in decisions)}개 -> {len(set(rep.values()))}개")


PARENT_PROMPT = """사내 규정의 개념 하나와, 그 개념에 대한 규정문 요지, 그리고 상위 후보 이름들이다.
이 개념의 상위 개념을 하나 고른다. 상위는 "A는 B의 한 종류다"라는 문장이 규정 내용상 그대로 성립할 때만이다.
- 한국어 제도 이름은 보통 끝말이 상위다 (전자기록물 -> 기록물, 화물차 -> 차량). "이름 끝이 같은 후보"가 있으면 먼저 본다.
- 같은 상위 아래의 다른 종류(형제)는 상위가 아니다. 예: 화물차의 상위는 승용차가 아니라 차량이다.
- 절차·결과·부속 사항·관련 사항은 종류가 아니다. 예: 차량 정비는 차량의 한 종류가 아니다, 대출 연장은 도서 대출의 한 종류가 아니다.
- 이름에 같은 글자가 있어도 규정이 정한 성격(무엇을 면제하는지, 신분·지급이 어떻게 되는지)이 다르면 종류가 아니다.
- 요지에 근거가 보이는 경우에만 고른다. 후보 중에 맞는 것이 없으면, 규정에 흔히 쓰는 제도 이름 하나를 새로 쓸 수 있다. 그것도 아니면 null.
JSON 한 줄만: {"이유": "20자 안팎", "상위": "이름 또는 null"}"""

REL_PROMPT = """사내 규정의 항목 하나와 그 항목에 나오는 개념 이름들이다. 이 항목 문장이 **직접 정한** 개념 사이 관계만 쓴다.
종류 (A -> B 방향):
- 포함: A의 기간·일수가 B의 기간·일수에 산입된다. 예: "외부 위탁 보관 기간은 기록물 보존기간에 포함한다" -> A=외부 위탁 보관, B=기록물 보존기간. 종류 관계(A는 B의 한 종류)는 포함이 아니다.
- 선행: A를 다 쓰거나 마친 뒤에야 B를 쓸 수 있다. 예: "보안교육을 마친 뒤 정보시스템 접근권한을 신청한다" -> A=보안교육, B=정보시스템 접근권한.
- 대체: A 대신 B를 주거나, A를 허용하지 않을 때 B를 권한다. 예: "업무용 차량 대신 대중교통 실비를 줄 수 있다" -> A=업무용 차량, B=대중교통 실비.
  "공용 차량을 배정하지 않으면 카셰어링을 권하여야 한다" -> A=공용 차량, B=카셰어링.
- 준용: A에 B의 규정을 준용한다. 예: "외부 기관 도서 대출은 도서 대출 규정을 준용한다" -> A=외부 기관 도서 대출, B=도서 대출.
- 제외: A의 기간이 B에서 빠진다. 예: "수리 기간은 차량 운행일수에서 뺀다" -> A=차량 수리, B=차량 운행일수.
문장에 그 관계가 쓰여 있지 않으면 쓰지 않는다. 같은 항목에 나오기만 하는 것은 관계가 아니다. 대부분 항목은 빈 배열이다.
JSON 하나만: {"관계": [{"A": "이름", "B": "이름", "종류": "포함"}]}"""

REL_KINDS = ("포함", "선행", "대체", "준용", "제외")

REL_CHECK_PROMPT = """사내 규정 항목 하나와, 그 항목에서 뽑았다는 개념 관계 하나다. 이 항목 문장이 그 관계를 **글자로 직접** 말하는지 엄격히 판정한다.
관계 정의 (A -> B):
- 포함: A의 기간·일수를 B의 기간·일수에 산입한다고 쓰여 있다.
- 제외: A의 기간·일수나 A인 사람을 B(산정 기간·한도·지급·대상 등)에서 뺀다고 쓰여 있다.
- 선행: A를 마쳐야(다 써야) B를 쓸 수 있다고 쓰여 있다.
- 대체: A 대신 B를 주거나, A를 허용하지 않을 때 B를 권한다고 쓰여 있다.
- 준용: A에 대해 B(규정·제도)가 정한 바를 따른다고 쓰여 있다.
다음은 모두 false: 두 이름이 한 문장에 나열되기만 함, A가 B의 한 종류임, B가 문서 서식·신청서 항목임, 방향이 반대임, 종류가 다름.
JSON 한 줄만: {"이유": "20자 안팎", "맞음": true 또는 false}"""


def cmd_relcheck(a):
    """4단계 보조: tree.json의 관계를 하나씩 원문과 대조해 엄격히 다시 판정한다. 원래 목록은 relations_raw로 남긴다."""
    path = os.path.join(a.out, "tree.json")
    tree = json.load(open(path, encoding="utf-8"))
    raw = tree.get("relations_raw") or tree["relations"]
    arts = read_items()
    verdict, kept = {}, []
    for n, r in enumerate(raw, 1):
        key = (r["A"], r["B"], r["종류"], r["근거"], r["항목"])
        if key not in verdict:
            it = next(i for i in arts[r["근거"]]["items"] if i["no"] == r["항목"])
            text = (f"(머리말: {it['head'][:300]}) " if it["head"] else "") + it["text"][:800] + f"\n관계: {r['A']} -{r['종류']}-> {r['B']}"
            try:
                verdict[key] = bool(json.loads(ask(REL_CHECK_PROMPT, text, num_predict=100, fmt="json")).get("맞음"))
            except ValueError:
                verdict[key] = False
        if verdict[key]:
            kept.append(r)
        if n % 50 == 0:
            print(f"  {n}/{len(raw)}", flush=True)
    tree["relations_raw"], tree["relations"] = raw, kept
    json.dump(tree, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"관계 {len(raw)}개 -> 남음 {len(kept)}개 {collections.Counter(r['종류'] for r in kept)}")


PARENT_CHECK_PROMPT = """사내 규정의 개념 A와 B, 그리고 각 개념에 대한 규정문 요지다. "A는 B의 한 종류다"가 뜻으로 성립하는지 판정한다.
A가 B라는 제도·대상의 한 유형이면 true다. 요지에 "종류"라는 말이 직접 없어도 된다 (예: 화물차는 차량의 한 종류, 전자기록물은 기록물의 한 종류).
다음은 모두 false:
- A가 B의 절차·결과·사유·효과·부속 사항이다 (예: 차량 정비는 차량의 종류가 아니다, 연체 제재는 도서 대출의 종류가 아니다).
- A가 B를 받는 사람·기간에 대한 제한이거나 B와 함께 정해질 뿐이다.
- B가 A의 한 종류다 (방향이 반대).
- A와 B가 같은 상위 아래의 다른 종류다.
JSON 한 줄만: {"이유": "20자 안팎", "맞음": true 또는 false}"""


def cmd_parentcheck(a):
    """4단계 보조: tree.json의 상위 선을 하나씩 엄격히 다시 판정한다. 원래 목록은 parents_raw로 남긴다.
    새로 만든 상위 이름은 규정 본문에 낱말로 나오는 것만 받는다: 이름 끝을 잘라 만든 "등근로시간단축" 같은 이름을 막는다."""
    ex = json.load(open(os.path.join(a.out, "extract.json"), encoding="utf-8"))
    rep = json.load(open(os.path.join(a.out, "normalize.json"), encoding="utf-8"))["rep"]
    cards = concept_cards(statements(ex), lambda x: rep.get(x, x), k=3)
    path = os.path.join(a.out, "tree.json")
    tree = json.load(open(path, encoding="utf-8"))
    raw = tree.get("parents_raw") or tree["parents"]
    arts = read_items()
    texts = [norm(it["text"]) for aid in ex for it in arts[aid]["items"]]
    kids = collections.defaultdict(list)
    for c, p in raw.items():
        kids[p].append(c)

    def is_word(name):
        n = norm(name)
        return any(m.start() == 0 or not re.match(r"[가-힣]", t[m.start() - 1]) for t in texts for m in re.finditer(re.escape(n), t))

    def card(x):
        return "\n  ".join(cards[x]) if x in cards else "(규정문 없음. 하위로 올라온 개념: " + ", ".join(kids[x][:8]) + ")"

    kept, dropped = {}, []
    for n, (c, p) in enumerate(sorted(raw.items()), 1):
        ok = p in cards or is_word(p)
        if ok:
            try:
                ok = bool(json.loads(ask(PARENT_CHECK_PROMPT, f"A: {c}\n요지:\n  {card(c)}\nB: {p}\n요지:\n  {card(p)}", num_predict=100, fmt="json")).get("맞음"))
            except ValueError:
                ok = False
        if ok:
            kept[c] = p
        else:
            dropped.append(f"{c} -> {p}")
        if n % 50 == 0:
            print(f"  {n}/{len(raw)}", flush=True)
    tree["parents_raw"], tree["parents"] = raw, kept
    json.dump(tree, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"상위 선 {len(raw)}개 -> 남음 {len(kept)}개")
    print("  뺀 것:", "; ".join(dropped))


def item_concepts(ex, rep, arts=None):
    """항목 ID -> 그 항목의 규정문·영향개념·언급에 나온 대표 개념들.
    arts를 주면 본문에 개념 이름이 글자 그대로 나오는 것도 넣는다: 추출이 언급을 빠뜨린 경우(육아휴직 미사용 시 4년)를 메운다."""
    out = collections.defaultdict(set)
    if arts:
        names = {n: r for n, r in rep.items() if len(norm(n)) >= 2}
        for aid in ex:
            for it in arts[aid]["items"]:
                t = norm(it["text"])
                hit = {norm(n) for n in names if norm(n) in t}
                # "휴가"가 "연차유급휴가" 안에서만 나오면 빼고, 따로도 나오면 남긴다
                hit = {x for x in hit if t.count(x) > sum(t.count(y) for y in hit if x != y and x in y)}
                out[(aid, it["no"])] |= {r for n, r in names.items() if norm(n) in hit}
    for aid, v in ex.items():
        for s in v["규정문"]:
            out[(aid, s["항목"])].add(rep.get(s["개념"], s["개념"]))
            if s.get("영향개념"):
                out[(aid, s["항목"])].add(rep.get(s["영향개념"], s["영향개념"]))
        for m_ in v["언급"]:
            out[(aid, m_["항목"])].add(rep.get(m_["개념"], m_["개념"]))
    return out


def cmd_tree(a):
    """4단계: 상위 개념(BROADER)은 개념마다 임베딩 이웃을 후보로 하나씩 묻고, 관계는 개념이 둘 이상 나오는 항목마다 본문을 주고 묻는다."""
    ex = json.load(open(os.path.join(a.out, "extract.json"), encoding="utf-8"))
    rep = json.load(open(os.path.join(a.out, "normalize.json"), encoding="utf-8"))["rep"]
    cards = concept_cards(statements(ex), lambda x: rep.get(x, x), k=3)
    known = set(cards)
    near = collections.defaultdict(list)
    for x, y in sorted(embed_neighbors(cards, 8)):
        near[x].append(y)
        near[y].append(x)
    raw_parents = {}
    for n, c in enumerate(sorted(known), 1):
        tail = [x for x in known if x != c and norm(c).endswith(norm(x))]
        text = f"개념: {c}\n요지:\n  " + "\n  ".join(cards[c]) + "\n이름 끝이 같은 후보: " + (", ".join(tail) or "없음") + \
            "\n그 밖의 후보: " + ", ".join(x for x in near[c] if x not in tail)
        try:
            d = json.loads(ask(PARENT_PROMPT, text, num_predict=120, fmt="json"))
        except ValueError:
            d = {}
        raw_parents[c] = {"상위": d.get("상위"), "이유": d.get("이유")}
        if n % 20 == 0:
            print(f"  상위 {n}/{len(known)}", flush=True)
    parents = {c: v["상위"] for c, v in raw_parents.items() if v["상위"] not in (None, "", "null", c)}
    # 새로 만든 상위 이름은 하위가 둘 이상일 때만 남긴다: 하위 하나짜리 새 이름은 검색 확장에 보탬 없이 잡음만 는다
    kids = collections.Counter(parents.values())
    parents = {c: p for c, p in parents.items() if p in known or kids[p] >= 2}

    arts = read_items()
    rels = []
    multi = {k: v for k, v in item_concepts(ex, rep, arts).items() if len(v) >= 2}
    for n, ((aid, no), cs) in enumerate(sorted(multi.items()), 1):
        it = next((i for i in arts[aid]["items"] if i["no"] == no), None)
        if not it:
            continue
        text = f"{arts[aid]['doc']} {arts[aid]['label']}({arts[aid]['title']}) [{no}]" + (f" (머리말: {it['head'][:300]})" if it["head"] else "") + \
            f" {it['text'][:800]}\n개념: " + ", ".join(sorted(cs))
        try:
            d = json.loads(ask(REL_PROMPT, text, num_predict=300, fmt="json"))
        except ValueError:
            d = {}
        for r in d.get("관계") or []:
            if isinstance(r, dict) and r.get("A") in cs and r.get("B") in cs and r["A"] != r["B"] and r.get("종류") in REL_KINDS:
                rels.append({"A": r["A"], "B": r["B"], "종류": r["종류"], "근거": aid, "항목": no})
        if n % 20 == 0:
            print(f"  관계 {n}/{len(multi)}", flush=True)
    json.dump({"parents": parents, "relations": rels, "raw_parents": raw_parents}, open(os.path.join(a.out, "tree.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"개념 {len(known)}개, 상위가 있는 개념 {len(parents)}개, 새로 생긴 상위 {len(set(parents.values()) - known)}개, "
          f"관계 묻은 항목 {len(multi)}개 -> 관계 {len(rels)}개 {collections.Counter(r['종류'] for r in rels)}")


def acyclic(parents):
    """상위 선 중 순환을 만드는 것은 뺀다 (예: 휴가 -> 유급휴가 -> 휴가). 뺀 것은 출력한다."""
    kept = {}
    for c, p in sorted(parents.items()):
        x, seen = p, {c}
        while x in kept and x not in seen:
            seen.add(x)
            x = kept[x]
        if x in seen:
            print("  순환이라 뺀 상위 선:", c, "->", p)
            continue
        kept[c] = p
    return kept


ALIAS_PROMPT = """사내 규정의 개념 하나와 그 개념에 대한 규정문 요지다. 이 개념을 가리키는 다른 표현을 쓴다.
- "다른이름": 다른 규정·법령에서 같은 제도를 부르는 공식 이름이나 약칭 (예: 업무용 차량 -> 공용차량). 없으면 빈 배열.
- "일상어": 직원이 질문할 때 이 제도를 가리키려고 쓸 법한 말 3~5개. 줄임말, 흔한 속칭, 상황을 말하는 표현 (예: 도서 대출 -> "책 빌리기", "자료실 대여").
개념 이름을 그대로 반복하지 않는다. 한 종류(하위 제도)나 상위 제도의 이름은 쓰지 않는다. 조문에 없는 제도를 만들지 않는다.
JSON 한 줄만: {"다른이름": [...], "일상어": [...]}"""


def cmd_alias(a):
    """5단계: 대표 개념마다 다른 이름과 일상어 별칭. 평가 질문은 보지 않는다."""
    ex = json.load(open(os.path.join(a.out, "extract.json"), encoding="utf-8"))
    rep = json.load(open(os.path.join(a.out, "normalize.json"), encoding="utf-8"))["rep"]
    cards = concept_cards(statements(ex), lambda x: rep.get(x, x), k=3)
    path = os.path.join(a.out, "aliases.json")
    done = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    for n, c in enumerate(sorted(cards), 1):
        if c in done:
            continue
        try:
            d = json.loads(ask(ALIAS_PROMPT, f"개념: {c}\n요지:\n  " + "\n  ".join(cards[c]), num_predict=200, fmt="json"))
        except ValueError:
            d = {}
        clean = lambda xs: [str(x).strip() for x in (xs if isinstance(xs, list) else []) if str(x).strip() and norm(str(x)) != norm(c)][:5]  # noqa: E731
        done[c] = {"다른이름": clean(d.get("다른이름")), "일상어": clean(d.get("일상어"))}
        if n % 20 == 0:
            json.dump(done, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"  {n}/{len(cards)}", flush=True)
    json.dump(done, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"별칭: 개념 {len(done)}개, 다른이름 {sum(len(v['다른이름']) for v in done.values())}개, 일상어 {sum(len(v['일상어']) for v in done.values())}개")


def remap_tree(tree, rep):
    """4단계 뒤에 normalize를 다시 돌려 합쳐진 이름이 있으면 트리의 이름도 새 대표로 바꾼다. 자기 자신을 가리키게 된 선은 뺀다."""
    c = lambda x: rep.get(x, x)  # noqa: E731
    parents = {}
    for k, p in tree["parents"].items():
        if c(k) != c(p):
            parents.setdefault(c(k), c(p))
    rels = [{**r, "A": c(r["A"]), "B": c(r["B"])} for r in tree["relations"] if c(r["A"]) != c(r["B"])]
    return {**tree, "parents": parents, "relations": rels}


def cmd_load(a):
    """Neo4j에 항목·규정문·개념·대상과 간선을 올린다. 이 스크립트가 만든 노드만 지우고 다시 만든다."""
    from neo4j import GraphDatabase
    ex = json.load(open(os.path.join(a.out, "extract.json"), encoding="utf-8"))
    rep = json.load(open(os.path.join(a.out, "normalize.json"), encoding="utf-8"))["rep"]
    tree = remap_tree(json.load(open(os.path.join(a.out, "tree.json"), encoding="utf-8")), rep)
    arts = read_items()
    tag =os.path.basename(os.path.normpath(a.out))
    items, stmts, about, effect, target, mention = [], [], [], [], [], []
    for aid, v in ex.items():
        for it in arts[aid]["items"]:
            items.append({"id": f"{aid}-{it['no']}", "art": aid, "no": it["no"], "text": it["text"], "head": it["head"]})
        for i, s in enumerate(v["규정문"]):
            sid = f"{aid}#{i}"
            stmts.append({"id": sid, "item": f"{aid}-{s['항목']}", "props": {k: s.get(k) for k in ("면", "요지", "값원문", "조건원문", "양태")}})
            about.append({"s": sid, "c": rep.get(s["개념"], s["개념"])})
            if s.get("영향개념"):
                effect.append({"s": sid, "c": rep.get(s["영향개념"], s["영향개념"])})
            for t in s["대상"]:
                target.append({"s": sid, "t": t["이름"], "role": t.get("역할", "포함")})
        for m_ in v["언급"]:
            mention.append({"i": f"{aid}-{m_['항목']}", "c": rep.get(m_["개념"], m_["개념"])})
    driver = GraphDatabase.driver("bolt://127.0.0.1:17687", auth=None)
    with driver.session() as s:
        s.run("MATCH (n) WHERE n.onto = $t DETACH DELETE n", t=tag).consume()
        for lbl in ("Item", "Statement", "Concept", "Target"):
            s.run(f"CREATE CONSTRAINT {lbl.lower()}_id IF NOT EXISTS FOR (n:{lbl}) REQUIRE n.id IS UNIQUE").consume()
        s.run("UNWIND $r AS r MERGE (i:Item {id: r.id}) SET i += {onto: $t, no: r.no, text: r.text, head: r.head} "
              "WITH i, r MATCH (a:Article {id: r.art}) MERGE (i)-[:PART_OF]->(a)", r=items, t=tag).consume()
        s.run("UNWIND $r AS r MERGE (x:Statement {id: r.id}) SET x += r.props, x.onto = $t "
              "WITH x, r MATCH (i:Item {id: r.item}) MERGE (i)-[:DEFINES]->(x)", r=stmts, t=tag).consume()
        concepts = {r["c"] for r in about + effect + mention} | set(tree["parents"]) | set(tree["parents"].values())
        s.run("UNWIND $r AS r MERGE (c:Concept {id: r}) SET c.onto = $t", r=sorted(concepts), t=tag).consume()
        s.run("UNWIND $r AS r MATCH (x:Statement {id: r.s}) MATCH (c:Concept {id: r.c}) MERGE (x)-[:ABOUT]->(c)", r=about).consume()
        s.run("UNWIND $r AS r MATCH (x:Statement {id: r.s}) MATCH (c:Concept {id: r.c}) MERGE (x)-[:SCOPE]->(c)", r=effect).consume()
        s.run("UNWIND $r AS r MERGE (t:Target {id: r.t}) SET t.onto = $tag WITH t, r MATCH (x:Statement {id: r.s}) MERGE (x)-[:APPLIES_TO {role: r.role}]->(t)",
              r=target, tag=tag).consume()
        s.run("UNWIND $r AS r MATCH (i:Item {id: r.i}) MATCH (c:Concept {id: r.c}) MERGE (i)-[:MENTIONS]->(c)", r=mention).consume()
        s.run("UNWIND $r AS r MATCH (a:Concept {id: r.c}) MATCH (b:Concept {id: r.p}) MERGE (a)-[:BROADER]->(b)",
              r=[{"c": c, "p": p} for c, p in acyclic(tree["parents"]).items()]).consume()
        s.run("UNWIND $r AS r MATCH (a:Concept {id: r.A}) MATCH (b:Concept {id: r.B}) MERGE (a)-[x:RELATED {kind: r.종류}]->(b) SET x.evidence = r.근거",
              r=tree["relations"]).consume()
        print(s.run("MATCH (n) WHERE n.onto = $t RETURN labels(n)[0], count(*)", t=tag).values())
        print(s.run("MATCH (a)-[x]->(b) WHERE a.onto = $t AND type(x) <> 'PART_OF' RETURN type(x), count(*)", t=tag).values())
    driver.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scope", "extract", "reparse", "supplement", "modecheck", "stats", "normalize", "tree", "relcheck", "parentcheck", "alias", "load"])
    ap.add_argument("out")
    ap.add_argument("--terms", default="휴직,휴가")
    ap.add_argument("--roots", default="", help="scope: 이미 만든 트리에서 이 개념들과 하위 개념이 나오는 조도 범위에 넣는다 (쉼표 목록)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all", action="store_true", help="scope: 항목이 있는 조 전부 (전체 구축). reparse: 모든 조를 다시 읽음")
    ap.add_argument("--only", default="", help="조 ID 쉼표 목록만 다시 추출 (이미 있어도 덮어씀)")
    ap.add_argument("--emb-k", type=int, default=3, help="normalize: 개념마다 임베딩 이웃 몇 개를 후보로")
    a = ap.parse_args()
    {"scope": cmd_scope, "extract": cmd_extract, "reparse": cmd_reparse, "supplement": cmd_supplement, "modecheck": cmd_modecheck, "stats": cmd_stats, "normalize": cmd_normalize, "tree": cmd_tree, "relcheck": cmd_relcheck, "parentcheck": cmd_parentcheck, "alias": cmd_alias, "load": cmd_load}[a.cmd](a)
