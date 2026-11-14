"""온톨로지(kg.ONTOLOGY)와 지식 그래프(out/kg) 연결을 한 곳에서 보는 사이트를 만든다.
  uv run python scripts/build_ontology_site.py
입력: src/regcheck/kg.py의 ONTOLOGY, out/kg/stats.json, out/ontology_site/{flow,schema,trace}.html (archify로 만든 그림).
출력: out/ontology_site/index.html. 표와 수치는 코드와 그래프에서 읽는다(손으로 쓰지 않는다). 규정 본문은 넣지 않는다.
"""
import datetime
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")
from regcheck.kg import ONTOLOGY  # noqa: E402

OUT = ROOT / "out/ontology_site"
stats = json.loads((ROOT / "out/kg/stats.json").read_text(encoding="utf-8"))
e = html.escape

CLASS_KO = {"LawArticle": "법령 조", "Alias": "약칭", "Regulation": "규정", "Article": "본문 조", "Supplement": "부칙의 조", "Annex": "별표", "Form": "별지 서식", "Term": "정의된 용어", "Body": "위원회", "Org": "조직", "Law": "법령"}
DIR_KO = {"in": "이 조를 가리키는 조", "out": "이 조가 가리키는 조", "both": "양쪽", "term_in": "그 용어를 쓰는 조", "reg_in": "이 규정을 이름으로 인용한 조",
          "reg": "같은 규정의 짝", "share": "같은 조직을 언급한 조", "law_in": "그 법령을 인용한 조"}
FIRST = lambda s: (s.split(". ")[0].split("。")[0])[:90]   # noqa: E731

n_nodes = sum(stats["노드"].values()); n_edges = sum(stats["선"].values())
n_cls = len(ONTOLOGY["classes"]); n_rel = len(ONTOLOGY["relations"])
origin = {r: k for k, v in ONTOLOGY["origin"].items() for r in v}

SUP = ONTOLOGY.get("superclasses", {})
cls_rows = "".join(f"<tr><td><code>{e(c)}</code></td><td>{e(CLASS_KO.get(c, ''))}</td><td>{('<span class="pill p-old">' + e(SUP[c]) + '</span>') if c in SUP else ''}</td><td class='num'>{stats['노드'].get(c, 0):,}</td><td>{e(d)}</td></tr>" for c, d in ONTOLOGY["classes"].items())
for a, d in ONTOLOGY.get("abstract_classes", {}).items():
    members = [c for c, u in SUP.items() if u == a]
    cls_rows += f"<tr><td><code>{e(a)}</code> <span class='muted'>(추상)</span></td><td>조항 단위</td><td></td><td class='num'>{sum(stats['노드'].get(c, 0) for c in members):,}</td><td>{e(d)} · {e(' · '.join(members))}를 묶는다</td></tr>"
H = ONTOLOGY.get("regulation_hierarchy", {})
hier = " > ".join(H.get("order", []))
rel_rows = ""
for r, v in ONTOLOGY["relations"].items():
    rel_rows += (f"<tr><td><code>{e(r)}</code></td><td class='nowrap'>{e(v['from'].replace('|', ' · '))} → {e(v['to'].replace('|', ' · '))}</td><td class='num'>{stats['선'].get(r, 0):,}</td>"
                 f"<td><span class='pill {'p-old' if origin.get(r) == '기존 규칙' else 'p-new'}'>{e(origin.get(r, ''))}</span></td><td>{e(FIRST(v['meaning']))}</td></tr>")
path_rows = ""
for kind, paths in ONTOLOGY["change_paths"].items():
    chips = "".join(f"<span class='chip' title='{e(DIR_KO.get(d, d))}'><b>{e(r)}</b> <i>{e(DIR_KO.get(d, d))}</i> <em>{w}</em></span>" for r, d, w in paths) or "<span class='muted'>그래프 경로 없음 (문구 · 의미 검색 후보만)</span>"
    extra = ONTOLOGY.get("collect_extra_paths", {}).get(kind)
    if extra:
        chips += f"<div class='extra'>＋ {e(extra)}</div>"
    path_rows += f"<tr><td class='nowrap'><b>{e(kind)}</b></td><td>{chips}</td></tr>"
outside = "".join(f"<li><code>{e(k)}</code> {e(v)}</li>" for k, v in ONTOLOGY["rules_outside_graph"].items())
now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>조항 관계 온톨로지와 지식 그래프</title>
<style>
:root{--bg:#f6f4ef;--panel:#ffffff;--ink:#1c2230;--sub:#5a6376;--line:#dcd6c8;--accent:#3b4cca;--accent2:#0d8a6a;--warn:#b86a00;--chip:#eef0fb;--code:#f0ece1}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#12151c;--panel:#1a1f2a;--ink:#e9ecf3;--sub:#9aa4b8;--line:#2b3242;--accent:#8c9bff;--accent2:#48d1a5;--warn:#f0b35a;--chip:#232a3d;--code:#222838}}
:root[data-theme="dark"]{--bg:#12151c;--panel:#1a1f2a;--ink:#e9ecf3;--sub:#9aa4b8;--line:#2b3242;--accent:#8c9bff;--accent2:#48d1a5;--warn:#f0b35a;--chip:#232a3d;--code:#222838}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 "Pretendard","Noto Sans KR","Malgun Gothic",system-ui,sans-serif;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
.wrap{max-width:1120px;margin:0 auto;padding-block:28px 72px}
nav{display:flex;gap:6px;flex-wrap:wrap;align-items:center;position:sticky;top:0;background:var(--bg);padding:10px 0;z-index:5;border-bottom:1px solid var(--line)}
nav a{color:var(--sub);text-decoration:none;font-size:14px;padding:5px 10px;border-radius:99px}nav a:hover{background:var(--chip);color:var(--ink)}
nav .sp{flex:1}nav button{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:99px;padding:5px 12px;font:inherit;font-size:13px;cursor:pointer}
header{padding:44px 0 18px}
.kicker{font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:700}
h1{font-size:clamp(30px,5vw,48px);line-height:1.15;margin:10px 0 14px;text-wrap:balance;letter-spacing:-.02em}
.lead{max-width:760px;color:var(--sub);font-size:18px;text-wrap:pretty}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:26px 0 8px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.stat .n{font-size:30px;font-weight:800;letter-spacing:-.02em;font-variant-numeric:tabular-nums}.stat .l{color:var(--sub);font-size:13px}
.stat.a .n{color:var(--accent)}.stat.b .n{color:var(--accent2)}
section{margin-top:54px}h2{font-size:26px;margin:0 0 6px;letter-spacing:-.01em}.sec-sub{color:var(--sub);margin:0 0 18px;max-width:760px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.tabs button{border:1px solid var(--line);background:var(--panel);color:var(--sub);padding:8px 14px;border-radius:10px;font:inherit;font-size:14px;cursor:pointer}
.tabs button[aria-selected="true"]{background:var(--accent);border-color:var(--accent);color:#fff}
.frame{background:var(--panel);border:1px solid var(--line);border-radius:16px;overflow:hidden}
.frame iframe{display:block;width:100%;height:min(94vh,1000px);min-height:720px;border:0;background:var(--panel)}
.frame .bar{display:flex;gap:10px;justify-content:space-between;align-items:center;padding:10px 14px;border-top:1px solid var(--line);font-size:13px;color:var(--sub);flex-wrap:wrap}
.frame .bar a{color:var(--accent)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px 20px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px;min-width:620px}
th{text-align:left;color:var(--sub);font-weight:600;font-size:12px;letter-spacing:.04em;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}tr:last-child td{border-bottom:0}
td.num{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}.nowrap{white-space:nowrap}td.nowrap{white-space:normal;min-width:190px}
code{background:var(--code);padding:1px 6px;border-radius:6px;font-size:.88em;font-family:ui-monospace,Consolas,monospace}
.pill{font-size:12px;padding:2px 9px;border-radius:99px;white-space:nowrap}.p-old{background:var(--chip);color:var(--accent)}.p-new{background:color-mix(in srgb,var(--accent2) 16%,transparent);color:var(--accent2)}
.chip{display:inline-flex;gap:6px;align-items:baseline;background:var(--chip);border-radius:8px;padding:3px 9px;margin:2px 6px 2px 0;font-size:13px}
.chip i{font-style:normal;color:var(--sub)}.chip em{font-style:normal;color:var(--accent);font-weight:700;font-variant-numeric:tabular-nums}
.extra{margin-top:6px;color:var(--warn);font-size:13px}.muted{color:var(--sub)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.grid .card h3{margin:0 0 6px;font-size:16px}.grid .card p,.grid .card li{color:var(--sub);font-size:14px;margin:4px 0}.grid ul{padding-left:18px;margin:6px 0 0}
.honest{border-left:4px solid var(--warn)}
footer{margin-top:60px;color:var(--sub);font-size:13px;border-top:1px solid var(--line);padding-top:16px}
@media (max-width:640px){.frame iframe{min-height:520px}}
</style></head><body>
<div class="wrap">
<nav><a href="#top">처음</a><a href="#diagrams">그림으로 보기</a><a href="#classes">클래스</a><a href="#relations">관계</a><a href="#paths">수정 종류별 경로</a><a href="#limits">한계</a>
<span class="sp"></span><button id="theme" type="button">테마 바꾸기</button></nav>
<header id="top"><div class="kicker">조항 수정 에이전트 · 영향 후보 점검</div>
<h1>온톨로지는 약속이고,<br>지식 그래프는 그 약속에 맞춰 담은 사실이다</h1>
<p class="lead">규정 90건의 조, 부칙, 별표, 서식과 그 사이의 인용 · 용어 · 언급을 규칙만으로 그래프에 담고, 개정 종류마다 그 그래프에서 따라갈 길을 정한다. 이 페이지는 두 가지가 어떻게 이어지는지 한곳에서 보여 준다.</p>
<div class="stats">
<div class="stat a"><div class="n">@@n_cls@@ · @@n_rel@@</div><div class="l">온톨로지 클래스 · 관계</div></div>
<div class="stat b"><div class="n">@@n_nodes@@</div><div class="l">그래프 노드</div></div>
<div class="stat b"><div class="n">@@n_edges@@</div><div class="l">그래프 선</div></div>
<div class="stat"><div class="n">@@n_kinds@@</div><div class="l">개정 종류 (경로표 행)</div></div>
</div></header>

<section id="diagrams"><h2>그림으로 보기</h2>
<p class="sec-sub">네 장면이다. 전체 흐름에서 온톨로지가 어디에 쓰이는지 보고, 스키마를 보고, 실제 한 건이 그래프를 따라가는 모습을 보고, 법령 조와 약칭이 더해진 부분을 본다.</p>
<div class="tabs" role="tablist">
<button role="tab" aria-selected="true" data-src="flow.html">① 전체 흐름</button>
<button role="tab" aria-selected="false" data-src="schema.html">② 온톨로지 스키마</button>
<button role="tab" aria-selected="false" data-src="trace.html">③ 한 건 따라가기</button>
<button role="tab" aria-selected="false" data-src="law.html">④ 법령 조와 약칭</button></div>
<div class="frame"><iframe id="fr" src="flow.html" title="다이어그램"></iframe>
<div class="bar"><span id="cap">온톨로지가 그래프를 만들 때와 후보를 모을 때, 두 번 쓰인다. 노드를 눌러 연결을 따라가 볼 수 있다.</span><a id="open" href="flow.html" target="_blank" rel="noopener">새 탭에서 열기</a></div></div>
</section>

<section id="classes"><h2>클래스 · 그래프에 실제로 있는 노드</h2>
<p class="sec-sub">온톨로지의 클래스 @@n_cls@@종과 그래프의 노드 수. 조항 단위(본문 조 · 부칙 · 별표 · 서식)는 같은 관계를 공유한다.</p>
<div class="card"><table><thead><tr><th>클래스</th><th>뜻</th><th>상위</th><th>노드</th><th>설명</th></tr></thead><tbody>@@cls_rows@@</tbody></table></div>
<p class="sec-sub" style="margin-top:12px">규정에는 위계가 있다: <b>@@hier@@</b> (@@hier_src@@). 그래프의 <code>Regulation</code> 노드가 이 위계를 속성(tier)으로 갖는다.</p></section>

<section id="relations"><h2>관계 · 그래프에 실제로 있는 선</h2>
<p class="sec-sub">관계 @@n_rel@@종, 어디에서 어디로 이어지는지와 선 수. <span class="pill p-old">기존 규칙</span>은 인용 표와 규칙 레지스트리에서, <span class="pill p-new">신규</span>는 이 그래프를 만들며 더한 관계다.</p>
<div class="card"><table><thead><tr><th>관계</th><th>출발 → 도착</th><th>선</th><th>출처</th><th>뜻</th></tr></thead><tbody>@@rel_rows@@</tbody></table></div></section>

<section id="paths"><h2>수정 종류별 경로 · 온톨로지가 쓰이는 곳</h2>
<p class="sec-sub">개정 종류마다 어떤 관계를 어느 방향으로 따라갈지 <code>change_paths</code>에 정해 둔다. 뒤의 숫자는 경로 가중치(다른 평가에서 잰 경로별 정밀도에서 온 값)다.</p>
<div class="card"><table><thead><tr><th>개정 종류</th><th>따라가는 관계 · 방향 · 가중치</th></tr></thead><tbody>@@path_rows@@</tbody></table></div></section>

<section id="limits"><h2>이 그래프가 하지 않는 것</h2>
<div class="grid">
<div class="card honest"><h3>판정하지 않는다</h3><p>그래프의 선은 문서에 적힌 사실이거나 규칙이 낸 후보일 뿐이다. 영향이 있는지, 충돌인지는 담당자가 판단한다.</p></div>
<div class="card honest"><h3>평가는 탐색 결과다</h3><p>정답 라벨은 AI 초안이고 현업 확인이 없다. 새 문항에서 후보 찾기는 높게 나왔지만(6차 98%, 정의 변경이 많았음) 순서 정렬의 개선은 구분되지 않았다.</p></div>
<div class="card"><h3>그래프에 넣지 않은 규칙</h3><ul>@@outside@@</ul></div>
<div class="card"><h3>알려진 빈틈</h3><ul><li>법령 조 노드는 본문이 조 번호까지 인용한 것만 있다 (법률 DB의 전체 조문이 아니다)</li><li>동의어는 규정이 정의한 약칭뿐이다. 일반 동의어 사전은 전문가 없이 만들지 않았다</li><li>위임받은 규정의 하위 조 · 별표</li><li>표현 수정(띄어쓰기 정정 등)의 후보</li><li>개정 이력과 시행 시점이 그래프에 없다</li></ul></div>
</div></section>

<footer>생성 @@now@@ · 수치는 <code>out/kg/stats.json</code>, 표는 <code>src/regcheck/kg.py</code>의 <code>ONTOLOGY</code>에서 읽었다 · 규정 본문은 싣지 않았다 · 그림은 archify로 만들었다 · 자세한 평가는 <code>docs/reports/261106-1500-83-metrics-summary.md</code></footer>
</div>
<script>
(function(){
  var fr=document.getElementById('fr'),op=document.getElementById('open'),cap=document.getElementById('cap');
  var caps={'flow.html':'온톨로지가 그래프를 만들 때와 후보를 모을 때, 두 번 쓰인다. 노드를 눌러 연결을 따라가 볼 수 있다.',
    'schema.html':'클래스는 노드, 관계는 선이다. 선 옆 숫자는 실제 그래프의 선 수다.',
    'law.html':'본문이 조 번호까지 인용한 법령 조와, 규정이 정의한 약칭이 법령 노드로 모이는 방식이다.',
    'trace.html':'정의 조를 삭제하는 개정 한 건이 그래프의 정의 → 용어 → 사용처 경로를 따라 후보가 되는 과정이다.'};
  document.querySelectorAll('.tabs button').forEach(function(b){b.addEventListener('click',function(){
    document.querySelectorAll('.tabs button').forEach(function(x){x.setAttribute('aria-selected','false')});
    b.setAttribute('aria-selected','true');var s=b.getAttribute('data-src');fr.src=s;op.href=s;cap.textContent=caps[s];});});
  var root=document.documentElement,tb=document.getElementById('theme');
  try{var t=localStorage.getItem('theme');if(t)root.setAttribute('data-theme',t);}catch(e){}
  tb.addEventListener('click',function(){
    var cur=root.getAttribute('data-theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
    var nx=cur==='dark'?'light':'dark';root.setAttribute('data-theme',nx);try{localStorage.setItem('theme',nx);}catch(e){}});
})();
</script></body></html>
"""

vals = dict(n_cls=n_cls, n_rel=n_rel, n_nodes=f"{n_nodes:,}", n_edges=f"{n_edges:,}", n_kinds=len(ONTOLOGY["change_paths"]),
            cls_rows=cls_rows, hier=hier, hier_src=H.get("source", ""), rel_rows=rel_rows, path_rows=path_rows, outside=outside, now=now)
page = PAGE
for k, v in vals.items():
    page = page.replace(f"@@{k}@@", str(v))
(OUT / "index.html").write_text(page, encoding="utf-8")
print("작성", OUT / "index.html", len(page), "자 | 노드", n_nodes, "선", n_edges, "클래스", n_cls, "관계", n_rel)
