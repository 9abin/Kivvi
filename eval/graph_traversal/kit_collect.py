"""12차 1단계(신규 평가셋 30건용): RAG + PR #11 규칙 그래프(문구·수정 종류별 질의·전이 규칙 포함 전체 후보 수집) + PR #12 도메인 그래프의 후보를 모은다. API 없음.
평가셋
  PR12 80문항: 문항 개정 항목마다 개정문을 만들어(scripts/four_domain_parse.py 파싱 + eval_four_domain.py의 amend_text) 후보 수집(impact_view.run)에 넣는다.
  PR #11 도메인 한정: 시작 조가 도메인 안이고 정답도 도메인 안인 쌍만 남긴 문항(I01, I02, I04, I05).
RAG는 로컬 bge-m3(규정 조 항 단위 색인, 이미 만들어 둔 out/vector/BAAI__bge-m3). 질의 벡터는 transformers로 계산한다(CLS, 정규화 = sentence-transformers bge-m3와 같음).
사용(Python 3.11, torch+transformers+numpy+scipy): python fullsys_collect.py <scratchpad> <pr12data>
결과: 작업 폴더 fullsys.pkl = {"sets": {세트: [케이스]}, "lists": {세트: {케이스id: {"앵커별": [...], ...}}}}"""
import sys, os, re, json, glob, pickle, collections
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
NS = Path(sys.argv[1]); D = Path(sys.argv[2])
sys.path.insert(0, str(NS / "kgcode" / "src")); sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
import numpy as np, torch
from transformers import AutoModel, AutoTokenizer
from regcheck.impact_view import ImpactContext, run, make_rag
from regcheck.vectors import Index

ROOT = Path("C:/Users/Admin/Downloads/kvic_pr11")
regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
G = pickle.load(open(NS / "kg.pkl", "rb"))
ctx = ImpactContext.from_parts(regs, G["nodes"], G["rows"])
index = Index(ROOT / "out/vector/BAAI__bge-m3")


class Enc:
    def __init__(self):
        self.tok = AutoTokenizer.from_pretrained("BAAI/bge-m3")
        self.m = AutoModel.from_pretrained("BAAI/bge-m3", dtype=torch.float16).to("cuda").eval()

    @torch.no_grad()
    def encode(self, texts, normalize_embeddings=True):
        b = self.tok(texts, padding=True, truncation=True, max_length=512, return_tensors="pt").to("cuda")
        v = self.m(**b).last_hidden_state[:, 0].float()
        return torch.nn.functional.normalize(v, dim=-1).cpu().numpy()


rag = make_rag(Enc(), index)


def amend_text(op):                                   # scripts/eval_four_domain.py 와 같음
    label = op["label"]
    if op["type"] == "move":
        t = op["to"]
        if label.startswith("제") and t.startswith("제"):
            return f"{label}부터 {label}까지를 각각 {t}부터 {t}까지로 한다."
        return f"{label}를 {t}로 한다."
    parts = op["id"].split(":")[1].split("-")
    ho, hang = (int(parts[2]) if len(parts) > 2 else 0), (int(parts[1]) if len(parts) > 1 else 0)
    where = label if label.startswith("제") else label.replace("별지제", "별지 제")
    if label.startswith("제"):
        if hang: where += f"제{hang}항"
        if ho: where += f"제{ho}호"
    return f'{where} 중 "{op["old"]}"를 "{op["new"]}"로 한다.'


def collect_one(reg, txt, cid):
    out = {}
    for mode, rg in (("full", rag), ("graph", None)):          # full = 문구·그래프·의미 검색, graph = 의미 검색 없이
        try:
            r = run(ctx, regs, reg, txt, cid, rag=rg)
        except Exception as e:   # noqa: BLE001
            out[mode] = None; out["err"] = repr(e)[:80]; continue
        if r["cq"].kind == "분석 실패":
            out[mode] = None; out["err"] = r["cq"].error[:80]; continue
        out["kind"] = r["cq"].kind; out["starts"] = list(r["cq"].starts or [])
        out[mode] = [(c.id, round(c.score, 4), list(c.paths)) for c in sorted(r["view"]["all"], key=lambda c: -c.score)]
    return out


import kit_cases
cases, dropped = kit_cases.load(NS)
sets = {"4도메인 신규 30건": cases}
lists = {"4도메인 신규 30건": {}}
for n, c in enumerate(cases):
    a = c["anchors"][0][0]
    lists["4도메인 신규 30건"][c["id"]] = [{"op": 0, "article": a, **collect_one(a.split(":")[0], c["amend"], c["id"])}]
    if n % 5 == 0: print("신규", n, len(cases), flush=True)
errs = [(cid, v[0].get("err")) for cid, v in lists["4도메인 신규 30건"].items() if not v[0].get("full")]
print("후보 수집 실패:", errs)
pickle.dump({"sets": sets, "lists": lists}, open(NS / "fullsys_kit.pkl", "wb"))
print("끝", len(cases), "건, 정답", sum(len(c["wanted"]) for c in cases), "쌍")
