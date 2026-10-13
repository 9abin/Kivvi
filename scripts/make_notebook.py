"""notebooks/01-pipeline-walkthrough.ipynb 생성기. (md, code) 순서 목록을 그대로 셀로 만든다.

  uv run python scripts/make_notebook.py notebooks/01-pipeline-walkthrough.ipynb
노트북을 고칠 때는 이 파일을 고치고 다시 생성·실행한다 (노트북을 직접 고치면 다음 생성 때 사라짐).
"""
import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
C = []  # (kind, source)


def md(s):
    C.append(("markdown", s.strip("\n")))


def code(s):
    C.append(("code", s.strip("\n")))


# ─────────────────────────────────────────────────────────────
md("""
# 조항 수정 에이전트: 지금까지 만든 흐름 따라가기

작성 2026-10-07 · 대상 코드 `src/regcheck/` · 관련 리포트 `docs/reports/261006-1428-07` ~ `261006-1744-11`

이 노트북은 **새 기능을 만들지 않는다.** 이미 만든 코드를 한 단계씩 불러서, 데이터가 어떻게 바뀌는지 눈으로 확인한다.
셀은 위에서부터 순서대로 실행한다. 한 셀은 한 가지 일만 한다.

```
원문 파일(HWP·HWPX·PDF)
  ① 추출      extract.py     파일 → 문단 줄 목록
  ② 파싱      parse.py       줄 → 본문 조·항·호·목 / 부칙 / 별표 / 별지
  ③ 정규화    normalize.py   원문은 그대로, 비교용 norm을 따로
  ④ 메타데이터 build.py       규정ID·위계·개정일·조문 ID
  ⑤ 인용 추출  citations.py   조문 → 인용 대상, 대상이 실제로 있는지 확인
  ⑥ 정답 대조  eval/          1주차 사람 검토와 비교
  ⑦ 관련 조항 표 relations.py  근거·출처·신뢰도·검증 상태를 붙여 저장, 조회
  ⑧ (선택) 벡터 vectors.py     표현이 다른 같은 내용 찾기
  ⑨ 메타데이터 대조            「제35조에 따른 행동강령책임관」 ↔ 제35조 제목 '교육'
```
""")

md("""
## 0. 준비

노트북 위치(`notebooks/`)에서 저장소 루트를 찾고, `src/`를 import 경로에 넣는다.
커널은 프로젝트 가상환경(`.venv`)을 쓴다. 설치: `uv sync --group notebook` (벡터 절은 `--extra vector`도 필요).
""")
code("""
from pathlib import Path
import sys, json, collections
""")
code("""
ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
ROOT
""")
code("""
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
""")
code("""
import pandas as pd
pd.set_option("display.max_colwidth", 80)
""")
code("""
DATA = ROOT / "data" / "한국벤처투자_내부규정"
OUT = ROOT / "out"
""")

# ─────────────────────────────────────────────────────────────
md("""
## 1. 원문 파일

규정마다 폴더가 하나다. 폴더 이름 끝의 숫자가 ALIO 규정 번호이고, 이것을 **규정ID**로 쓴다.
""")
code("""
folders = sorted(p for p in DATA.iterdir() if p.is_dir())
len(folders)
""")
code("""
[f.name for f in folders[:5]]
""")
code("""
exts = collections.Counter(p.suffix.lower() for f in folders for p in f.iterdir())
exts
""")
md("예시로 **퇴직금지급규정**을 따라간다. 1주차 F1(「인사규정 제6장」)과 F2(「제5조」) 사례가 들어 있다.")
code("""
folder = next(f for f in folders if f.name.startswith("퇴직금지급규정"))
folder.name
""")
code("""
src_file = next(p for p in folder.iterdir() if p.suffix.lower() in (".hwp", ".hwpx", ".pdf"))
src_file.name
""")

# ─────────────────────────────────────────────────────────────
md("""
## 2. 추출 (`extract.py`)

HWP는 압축된 레코드 묶음이다. 본문 문단은 `PARA_TEXT` 레코드(UTF-16 글자열)에 들어 있다.
""")
code("""
from regcheck import extract as ex
""")
code("""
raw_paras = ex.extract_hwp(src_file)
len(raw_paras)
""")
code("""
raw_paras[:6]
""")
md("""
### 2-1. 왜 제어 문자를 규격대로 처리했나

HWP 문단 안에는 표·머리말 같은 컨트롤이 **8글자 묶음**(코드 + 데이터 6 + 코드)으로 끼어 있다.
제어 코드만 지우면 데이터 6글자가 깨진 한자처럼 남는다. 아래는 그 "단순한 방법"을 일부러 재현한 것이다.
""")
code("""
import olefile, zlib, struct, re

def naive_first_paras(path, n=3):
    \"\"\"제어 문자만 지우는 단순한 방법 (비교용)\"\"\"
    ole = olefile.OleFileIO(str(path))
    data = zlib.decompress(ole.openstream("BodyText/Section0").read(), -15)
    out = []
    for tag, body in ex._hwp_records(data):
        if tag == ex.HWPTAG_PARA_TEXT:
            out.append(re.sub(r"[\\x00-\\x1f]", "", body.decode("utf-16le", "ignore")))
        if len(out) == n:
            break
    return out
""")
code("""
naive_first_paras(src_file)
""")
code("""
raw_paras[:3]   # 규격대로 처리한 결과: 깨진 한자가 없다
""")
md("`clean()`은 빈 줄, PDF 쪽번호(「- 5 -」)를 빼고 줄 목록으로 만든다. `extract()`는 형식(HWP·HWPX·PDF)에 맞는 추출기를 고른 뒤 `clean()`까지 한다.")
code("""
lines = ex.extract(src_file)
len(lines)
""")
code("""
lines[:12]
""")

# ─────────────────────────────────────────────────────────────
md("""
## 3. 파싱 (`parse.py`)

줄을 하나씩 읽으며 **지금 문서의 어느 구역인지**(머리 → 본문 → 부칙 → 별표·별지)를 기억하는 상태 기계다.
""")
code("""
from regcheck.parse import parse
""")
code("""
parsed = parse(lines)
""")
code("""
parsed.title, parsed.history[:3]
""")
code("""
len(parsed.articles), len(parsed.addenda), len(parsed.appendices)
""")
code("""
[a.label for a in parsed.articles]
""")
md("### 3-1. 조문 하나 보기: 제3조 (F1 사례)")
code("""
art3 = next(a for a in parsed.articles if a.label == "제3조")
art3.title, art3.chapter
""")
code("""
print(art3.text)
""")
md("항·호·목은 `(항, 호, 목)` 경로로 나뉜다. 없는 단계는 0. `삭제 <…>`인 호는 `deleted=True`.")
code("""
pd.DataFrame([{"경로": it.path, "삭제": it.deleted, "본문": it.text} for it in art3.items])
""")
md("### 3-2. 부칙과 별표")
code("""
[(ad.date, len(ad.articles)) for ad in parsed.addenda][:5]
""")
code("""
[(ap.kind, ap.number, ap.title) for ap in parsed.appendices]
""")
md("""
### 3-3. 상태 기계가 막아 주는 것

부칙에도 「제1조(시행일)」이 있고, 계약서형 별지에도 「제1조(목적)」이 있다.
이걸 본문과 섞으면 "제1조가 여러 개"가 된다. 작은 예시로 확인한다.
""")
code("""
sample = \"\"\"테스트규정
제1조(목적) 목적을 정한다.
제2조(정의) ① 용어는 다음과 같다.
  1. 갑
  2. 삭제 <2021.02.03.>
부      칙 <2020.01.01.>
제1조(시행일) 시행한다.
【별지 제1호 서식】
임대차계약서
제1조(목적) 이 계약은 임대차를 목적으로 한다.\"\"\".splitlines()
""")
code("""
p = parse(sample)
[a.label for a in p.articles]          # 본문 조는 2개뿐
""")
code("""
[a.label for a in p.addenda[0].articles]  # 부칙의 제1조는 따로
""")
code("""
p.appendices[0].text                     # 계약서의 제1조는 별지 텍스트에 남는다
""")

# ─────────────────────────────────────────────────────────────
md("""
## 4. 정규화 (`normalize.py`)

**원문(`text`)은 절대 바꾸지 않는다.** 검토 화면의 근거와 신·구조문대비표의 "현행" 칸에 그대로 써야 하기 때문이다.
비교·검색용으로만 `norm`을 따로 만든다.
""")
code("""
from regcheck.normalize import normalize, amend_marks, compact, index_header
""")
code("""
t = art3.items[0].text
t
""")
code("""
normalize(t)      # 개정 표시 제거, 따옴표·가운뎃점 통일, 공백 정리
""")
code("""
amend_marks(t)    # 지운 개정 표시는 따로 보관
""")
code("""
compact("퇴 직 금 지 급 규 정") == compact("퇴직금지급규정")   # 띄어쓰기 무시 비교
""")
md("HWP 머리말(내규집 목차 번호 + 문서명)은 본문이 아니다. 하지만 다른 규정 이름이 들어간 경우가 있어 지우지 않고 따로 본다.")
code("""
lines[0], index_header(lines[0])
""")

# ─────────────────────────────────────────────────────────────
md("""
## 5. 메타데이터와 조문 ID (`build.py`)

`build()`는 ②~④를 묶고 규정ID·위계·개정일·출처·조문 ID를 붙여 JSON 한 덩어리로 만든다.
""")
code("""
from regcheck.build import build, _sources, hierarchy, revision_date
""")
code("""
reg = build(folder, _sources(DATA))
list(reg.keys())
""")
code("""
{k: reg[k] for k in ("reg_id", "name", "hierarchy", "revised", "headers", "header_mismatch")}
""")
md("머리말이 「퇴직**급**지급규정」으로 오타가 나 있어 `header_mismatch`에 걸린다 (원문 표기 오류 후보, 원문은 그대로 둠).")
code("""
hierarchy("취업규칙"), hierarchy("임직원행동강령"), hierarchy("한국벤처투자 정관")
""")
md("조문 ID (임시안): 본문 조 `규정ID:조`, 항·호·목 `규정ID:조-항-호-목`, 부칙 `규정ID:부칙N:조`, 별표·별지 `규정ID:별표1`.")
code("""
[a["id"] for a in reg["articles"][:6]]
""")
code("""
[it["id"] for it in reg["articles"][2]["items"]]
""")

# ─────────────────────────────────────────────────────────────
md("""
## 6. 90건 전체

`scripts/parse_regs.py --all`이 만들어 둔 `out/parsed/*.json`을 읽는다. 없으면 다시 만든다 (1~2분).
""")
code("""
REBUILD = False
""")
code("""
from regcheck.build import build_all
if REBUILD or not list((OUT / "parsed").glob("[0-9]*.json")):
    build_all(DATA, OUT / "parsed")
""")
code("""
regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (OUT / "parsed").glob("[0-9]*.json"))}
len(regs)
""")
code("""
summary = pd.DataFrame([{
    "규정": r["name"], "위계": r["hierarchy"], "개정": r["revised"],
    "조": len(r["articles"]), "부칙": len(r["addenda"]),
    "별표": sum(x["kind"] == "별표" for x in r["appendices"]),
    "별지": sum(x["kind"] == "별지" for x in r["appendices"]),
} for r in regs.values()]).sort_values("조", ascending=False)
summary.head(10)
""")
code("""
summary["조"].sum(), summary["위계"].value_counts().to_dict()
""")
md("""
### 6-1. 전체 청킹 결과 (90건)

청크는 두 가지다.
- **조문 구조**: 조 → 항 → 호 → 목. 인용 점검처럼 "제4조 제6호가 있나"를 볼 때 쓴다 (`out/parsed/`)
- **검색용 청크**: 조의 **항 하나**(호·목 포함)에 「규정명 > 제N조(제목)」을 붙인 것. 벡터·키워드 검색에 쓴다 (`vectors.build_chunks`)

부칙·별지·삭제 조는 검색용 청크에서 뺀다. 조문이 없는 윤리헌장은 전문 1개 + 「하나, …」 항목마다 1개로 만든다 (김가빈 수동 청크와 같은 나눔).
""")
code("""
from regcheck.vectors import build_chunks
chunks = build_chunks(list(regs.values()))
len(chunks), len({c["reg_id"] for c in chunks})
""")
code("""
items_total = sum(len(a["items"]) for r in regs.values() for a in r["articles"])
addenda_total = sum(len(r["addenda"]) for r in regs.values())
apps = collections.Counter(x["kind"] for r in regs.values() for x in r["appendices"])
{"본문 조": int(summary["조"].sum()), "항·호·목": items_total, "부칙": addenda_total,
 "별표": apps["별표"], "별지": apps["별지"], "검색용 청크": len(chunks)}
""")
code("""
per_reg = pd.DataFrame(collections.Counter(c["reg_name"] for c in chunks).items(), columns=["규정", "청크"])
per_reg = per_reg.merge(summary[["규정", "조"]], on="규정").sort_values("청크", ascending=False)
per_reg.head(10)
""")
code("""
per_reg.tail(5)      # 청크가 가장 적은 규정 (윤리헌장 5개 포함)
""")
code("""
print(chunks[0]["id"]); print(chunks[0]["text"])
""")
code("""
[c["id"] for c in chunks if c["reg_name"] == "한국벤처투자 윤리헌장"]
""")
md("참고: 김가빈 챗봇 DB의 색인 대상은 4,465개(조·항 청크, 부칙·삭제 제외). 같은 나눔 원칙이라 규모가 거의 같다. 정확한 대조는 DB 덤프를 받은 뒤 한다.")

md("담당 21건(폴더 가나다순 「직장 내 괴롭힘 예방 운영지침」~「휴직자 복무관리 지침」)만 따로 표시해 둔다.")
code("""
from parse_regs import assigned_names
ASSIGNED = set(assigned_names())
len(ASSIGNED)
""")

# ─────────────────────────────────────────────────────────────
md("""
## 7. 인용 추출 (`citations.py`)

조문의 `norm`에서 「제N조」, 「「규정명」 제N조」, 「○○법 제N조」를 뽑아 **엣지**로 만들고, 대상이 실제로 있는지 확인한다.
판정은 하지 않는다. `status`는 구조적 사실만 적는다.
""")
code("""
from regcheck.citations import Registry, extract_edges, STATUS
""")
code("""
registry = Registry(list(regs.values()))   # 90건 규정명·약칭 사전
""")
code("""
pd.Series(STATUS, name="뜻")
""")
md("### 7-1. F3: 회계규정 제72조 「제128조」")
code("""
acc = next(r for r in regs.values() if r["name"] == "회계규정")
acc_edges = extract_edges(acc, registry)
len(acc_edges)
""")
code("""
[e for e in acc_edges if e.raw == "제128조"]
""")
code("""
acc["articles"][-1]["label"]     # 회계규정은 제77조까지
""")
md("### 7-2. 퇴직금지급규정의 인용 전체")
code("""
ret_edges = extract_edges(reg, registry)
""")
code("""
def edges_df(edges):
    return pd.DataFrame([{"출발": e.src, "표현": e.raw, "대상유형": e.target_type,
                          "대상문서": e.target_doc or "(자기)", "대상ID": e.target_id,
                          "상태": e.status, "설명": e.note} for e in edges])
""")
code("""
edges_df(ret_edges)
""")
md("""
- `24125:3-1-1-0` 「제6장」 → 인사규정 제6장(휴직…)이 **존재**하므로 `ok`. 내용이 퇴직이 아니라 휴직이라는 건 구조 검사로는 모른다 (9절).
- `24125:6-0-0-0` 「제39조의2」 → 정관에 없음 (1주차 C13).
- `24125:8-1-0-0` 「제5조」 → 자기 규정 제5조(퇴직연금)가 존재하므로 `ok` (F2, 9절).
""")
md("### 7-3. 다른 상태들의 예")
code("""
emp = next(r for r in regs.values() if r["name"] == "취업규칙")
emp_edges = extract_edges(emp, registry)
edges_df([e for e in emp_edges if e.status == "name_mismatch"])
""")
code("""
exe = next(r for r in regs.values() if r["name"] == "집행간부 운영규정")
edges_df([e for e in extract_edges(exe, registry) if e.status != "law_pending"])
""")
md("「제6조(채용제한)」처럼 인용에 제목을 붙인 경우 실제 제목과 비교한다 (`title_mismatch`).")
md("### 7-4. 약칭 풀이")
code("""
from regcheck.citations import _aliases
_aliases(next(r for r in regs.values() if r["name"] == "부정청탁 및 금품등 수수의 신고사무 처리지침"))
""")
code("""
list(registry.law_aliases.items())[:5]   # 다른 규정에서 정의한 약칭도 쓴다
""")

# ─────────────────────────────────────────────────────────────
md("""
## 8. 담당 21건 전체 + 1주차 정답 대조

`eval/week1_citations.json`은 1주차 사람 검토(§C)에서 인용이 명시된 사례다.
`structural`은 구조 검사로 잡혀야 하는 것, `semantic`은 대상은 있는데 내용이 달라 의미 검사가 필요한 것.
""")
code("""
edges21 = []
for r in regs.values():
    if r["name"] in ASSIGNED:
        for e in extract_edges(r, registry):
            edges21.append({**e.to_dict(), "src_reg": r["reg_id"], "src_name": r["name"]})
len(edges21)
""")
code("""
pd.Series(collections.Counter(e["status"] for e in edges21)).sort_values(ascending=False)
""")
code("""
gold = json.loads((ROOT / "eval" / "week1_citations.json").read_text(encoding="utf-8"))["cases"]
len(gold)
""")
code("""
PROBLEM = {"missing_article", "missing_item", "missing_chapter", "missing_appendix",
           "deleted_target", "name_mismatch", "unknown_regulation", "title_mismatch"}
""")
code("""
rows = []
for c in gold:
    cand = [e for e in edges21 if e["src"] == c["src"] and c["raw"] in e["raw"]]
    hit = [e for e in cand if e["status"] in c.get("status", [])]
    rows.append({"ID": c["id"], "기대": c["expect"], "탐지": bool(hit),
                 "상태": (hit or cand or [{"status": "-"}])[0]["status"]})
week1 = pd.DataFrame(rows)
week1
""")
code("""
s = week1[week1["기대"] == "structural"]
f"구조 검사 대상 {len(s)}건 중 {s['탐지'].sum()}건 탐지"
""")

# ─────────────────────────────────────────────────────────────
md("""
## 9. 관련 조항 표 (`relations.py`)

영향 분석은 **수정할 때마다 찾지 않고, 미리 연결해 둔 표를 조회**한다.
관계마다 근거 문구·출처·신뢰도·검증 상태·원문 버전을 붙인다. ("그래프"는 이 표 + 역방향 조회를 말한다.)
""")
code("""
from regcheck.relations import from_edges, Related
""")
code("""
edges_all = []
for r in regs.values():
    for e in extract_edges(r, registry):
        edges_all.append({**e.to_dict(), "src_reg": r["reg_id"], "src_name": r["name"]})
len(edges_all)
""")
code("""
rels = [x.to_dict() for x in from_edges(edges_all, regs)]
len(rels)
""")
code("""
pd.DataFrame(rels)[["kind", "confidence", "verification"]].value_counts()
""")
code("""
pd.DataFrame(rels).iloc[0]
""")
md("### 9-1. 조회: 직제규정 제7조(주요조직)를 고치면 누가 영향을 받나")
code("""
related = Related(rels)
""")
code("""
jikje = next(r for r in regs.values() if r["name"] == "직제규정")
target = f"{jikje['reg_id']}:7"
res = related.lookup(target)
{k: len(v) for k, v in res.items()}
""")
code("""
pd.DataFrame([{"출발": r["src"], "표현": r["evidence"], "문맥": r["context"][:60]} for r in res["인용받음"]])
""")
md("""
### 9-2. 조회만으로는 못 찾는 것: J4 육아휴직 분할 횟수

취업규칙 제43조②(3회)와 인사규정 제30조②(2회)는 **서로 인용하지 않는다.** 그래서 관련 조항 표에는 없다.
아래 결과가 비어 있거나 위임(문서 단위)만 나오는 것을 확인한다. 이 빈칸을 채우는 것이 다음 단계(같은 대상·속성 관계)다.
""")
code("""
insa = next(r for r in regs.values() if r["name"] == "인사규정")
res30 = related.lookup(f"{insa['reg_id']}:30")
{k: len(v) for k, v in res30.items()}
""")
code("""
[(r["src"], r["evidence"]) for r in res30["규정 단위로 인용받음"]][:8]
""")

# ─────────────────────────────────────────────────────────────
md("""
## 10. (선택) 벡터: 표현이 다른 같은 내용

`uv sync --extra vector`와 `scripts/build_vectors.py`가 필요하다. GPU가 없으면 이 절은 건너뛴다.
벡터는 **영향 판단의 근거가 아니라**, 관계를 미리 만들 때 "표현이 다른 같은 개념"을 놓치지 않게 돕는 보조다.
""")
code("""
VEC_DIR = OUT / "vector" / "BAAI__bge-m3"
VEC_DIR.exists()
""")
code("""
try:
    from regcheck.vectors import Index, load_model, embed
    index = Index(VEC_DIR)
    model = load_model()
    VECTOR_OK = True
except Exception as err:
    VECTOR_OK = False
    print("벡터 절 건너뜀:", err)
""")
code("""
def search(q, k=5):
    qv = embed(model, [q])[0]
    return [(round(s, 3), c["reg_name"], c["label"], c["title"]) for s, c in index.search(qv, k=k)]
""")
code("""
search("당연해직 사유") if VECTOR_OK else None    # 원문 용어는 '당연면직'
""")
code("""
search("육아휴직을 나누어 사용할 수 있는 횟수") if VECTOR_OK else None   # J4 두 조항이 함께 나오는지
""")

# ─────────────────────────────────────────────────────────────
md("""
## 11. 메타데이터 대조: 대상 불일치 후보 (GPU 불필요)

인용 바로 뒤의 **설명구**(「제35조에 따른 **행동강령책임관**」)를 뽑아, 인용된 조문의 **제목**에 그 단어가 있는지 본다.
없으면 "대상 불일치 후보". 제목에 그 단어가 있는 다른 조문을 대안 후보로 보여 준다. 판정은 사람이 한다.
""")
code("""
from semantic_check import descriptor
""")
code("""
c2 = next(e for e in edges21 if e["src"] == "40209:5-4-0-0" and e["raw"] == "제35조")
c2["target_doc"], c2["target_id"], c2["status"]
""")
code("""
desc = descriptor(regs, c2)
desc
""")
code("""
target_reg = regs[c2["target_reg"]]
cited = next(a for a in target_reg["articles"] if a["id"] == c2["target_id"])
cited["label"], cited["title"]
""")
code("""
compact(desc) in compact(cited["title"])     # 제목에 '행동강령책임관'이 없다 → 후보
""")
code("""
[(a["label"], a["title"]) for a in target_reg["articles"] if compact(desc) in compact(a["title"])]
""")
md("1주차 수정안도 제36조다. 같은 방식으로 담당 21건의 설명구 있는 인용을 모두 본다.")
code("""
rows = []
for e in edges21:
    if e["target_type"] != "article" or e["status"] not in ("ok", "title_mismatch") or not e["target_id"]:
        continue
    d = descriptor(regs, e)
    if not d:
        continue
    t = next((a for a in regs[e["target_reg"]]["articles"] if a["id"] == e["target_id"]), None)
    if t:
        rows.append({"출발": e["src"], "인용": e["raw"], "설명구": d, "대상 제목": t["title"],
                     "제목에 있음": compact(d) in compact(t["title"])})
meta = pd.DataFrame(rows)
meta[~meta["제목에 있음"]]
""")
md("단어가 완전히 같지 않아도 겹치는 경우(「퇴직금」 vs 「퇴직연금제도」 등)가 있어, 실제 기준은 두 글자 단위 겹침 비율로 다듬는다 (리포트 261006-1603-10, 대화 기록).")

# ─────────────────────────────────────────────────────────────
md("""
## 12. 정리

| 단계 | 상태 | 이 노트북 절 |
|---|---|---|
| 추출·파싱·정규화·메타데이터 | 90건 완료 | 2~6 |
| 인용 추출·구조 검사 | 1주차 구조 대상 11/11 | 7~8 |
| 관련 조항 표 | 인용·문서·법령·별표 관계, 표본 정확도 측정 중 | 9 |
| 같은 대상·속성(값), 정의-사용, 예외 관계 | **미착수** (9-2의 빈칸) | — |
| 대상 불일치 후보 | 메타데이터 대조 + reranker 보조 | 11 |
| 개정문 적용·신·구조문대비표·검토 화면·법령 연결 | **미착수** | — |

다음에 적용할 때는 9-2의 빈칸(J4)을 채우는 관계부터 붙이고, 같은 노트북 흐름에 절을 추가한다.
""")

# ─────────────────────────────────────────────────────────────
nb = {
    "cells": [
        {"cell_type": k, "metadata": {}, "source": s.splitlines(keepends=True),
         **({"outputs": [], "execution_count": None} if k == "code" else {})}
        for k, s in C
    ],
    "metadata": {
        "kernelspec": {"display_name": "Python 3 (MINI_CNS .venv)", "language": "python", "name": "python3"},
        "language_info": {"name": "python"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"{OUT}  셀 {len(C)}개 (코드 {sum(k == 'code' for k, _ in C)}개)")
