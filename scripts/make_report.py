"""1차 결과 확인 페이지 (로컬 HTML 한 장).

  uv run python scripts/make_report.py        → out/report/index.html (브라우저로 연다)
입력: out/parsed, out/vector, out/relations, out/findings, eval/runs의 최신 채점 기록, eval/ledger
먼저 build_findings.py → score.py 를 돌려 둔다. 규정 문구가 들어가므로 out/(git 제외)에만 둔다.
"""

import collections
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from parse_regs import assigned_names  # noqa: E402


def jl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    regs = [json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json")]
    chunks = sum(1 for _ in (ROOT / "out" / "vector" / "BAAI__bge-m3" / "chunks.jsonl").open(encoding="utf-8"))
    rels = jl(ROOT / "out" / "relations" / "relations.jsonl")
    found = jl(ROOT / "out" / "findings" / "findings.jsonl")
    score_path = sorted((ROOT / "eval" / "runs").glob("*-score.json"))[-1]
    score = json.loads(score_path.read_text(encoding="utf-8"))
    ledgers = {p.stem: {g["id"]: g for g in jl(p)} for p in (ROOT / "eval" / "ledger").glob("*.jsonl")}
    runs = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "runs").glob("*.json")}

    gold_of = collections.defaultdict(list)
    gold_rows = []
    for led, res in sorted(score["summary"]["원장별"].items(), key=lambda kv: kv[0] != "week1"):
        for it in res["항목"]:
            g = ledgers[led][it["id"]]
            if it["fid"]:
                gold_of[it["fid"]].append(it["id"])
            gold_rows.append({**it, "ledger": led, "basis": g.get("basis", ""), "handling": g.get("handling", ""),
                              "show": (g.get("expected") or {}).get("show", ""), "answer": g.get("answer", "")})
    assigned = set(assigned_names())
    names = {r["reg_id"]: r["name"] for r in regs}
    matched = set(score["summary"].get("정답 연결 finding", []))
    for f in found:
        # 대표로 연결된 정답 ID, 같은 정답에 함께 걸린 결과는 "정답 연결"로만 표시 (확인 대기에서 뺌, score.py와 같은 기준)
        f["gold"] = gold_of.get(f["fid"], []) or (["정답 연결"] if f["fid"] in matched else [])
        f["assigned"] = f["reg_name"] in assigned
        f.setdefault("priority", "높음")
    rel_rows = [{k: r[k] for k in ("src", "src_article", "target", "target_type", "kind", "evidence", "confidence",
                                  "verification", "check_status")} for r in rels]

    stats = {
        "규정": len(regs), "본문 조": sum(len(r["articles"]) for r in regs),
        "항·호·목": sum(len(a["items"]) for r in regs for a in r["articles"]),
        "검색용 청크": chunks, "관계": len(rels), "점검 결과": len(found),
    }
    data = {
        "made": datetime.now().isoformat(timespec="minutes"), "stats": stats,
        "score": {"run": score["run_id"], "code": score["code"], "total": score["summary"]["합계"],
                  "queue": score["summary"]["확인 대기"],
                  "ledgers": {k: {x: v[x] for x in ("정답", "채점 대상", "탐지", "높음", "제안 일치", "종류별", "분포")}
                              for k, v in sorted(score["summary"]["원장별"].items(), key=lambda kv: kv[0] != "week1")}},
        "changes": [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "out" / "changes").glob("*/changeset.json"))]
        if (ROOT / "out" / "changes").exists() else [],
        "gold": gold_rows, "found": found, "rels": rel_rows, "names": names,
        # 단계별 최신 실행 + 이번 채점
        "runs": [{"id": v["run_id"], "step": v["step"], "code": v["code"], "models": v["models"], "inputs": v["inputs"],
                  "summary": v["summary"] if v["step"] != "score" else v["summary"]["합계"]}
                 for v in [*{v["step"]: v for _, v in sorted(runs.items())}.values(), score]],
    }
    html = TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    out = ROOT / "out" / "report"
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(html, encoding="utf-8")
    print(f"→ {out / 'index.html'}  (점검 결과 {len(found)}, 정답 {len(gold_rows)}, 관계 {len(rels)})")


TEMPLATE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>조항 점검 1차 결과</title>
<style>
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1f;--mute:#6b6b70;--line:#e3e3e0;--acc:#2f5bd3;--ok:#1f8a4c;--warn:#b26a00;--bad:#c0392b;--chip:#eef1f8}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#151517;--card:#1f1f22;--fg:#ececee;--mute:#9a9aa2;--line:#33333a;--acc:#7c9cff;--ok:#4cc27e;--warn:#e0a040;--bad:#ff7a6b;--chip:#2a2d38}}
:root[data-theme="dark"]{--bg:#151517;--card:#1f1f22;--fg:#ececee;--mute:#9a9aa2;--line:#33333a;--acc:#7c9cff;--ok:#4cc27e;--warn:#e0a040;--bad:#ff7a6b;--chip:#2a2d38}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 -apple-system,"Segoe UI","Malgun Gothic",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 10px}.mute{color:var(--mute)}
.note{background:var(--chip);border-radius:8px;padding:10px 12px;margin:12px 0;font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.card b{display:block;font-size:22px}.card span{color:var(--mute);font-size:12px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:22px 0 8px;border-bottom:1px solid var(--line)}
.tabs button{background:none;border:0;border-bottom:2px solid transparent;padding:8px 10px;color:var(--mute);font:inherit;cursor:pointer}
.tabs button.on{color:var(--fg);border-color:var(--acc);font-weight:600}
.panel{display:none}.panel.on{display:block}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
select,input{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font:inherit;max-width:100%}
.tw{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{position:sticky;top:0;background:var(--card);color:var(--mute);font-weight:600;white-space:nowrap}
td.ctx{min-width:260px;max-width:420px;color:var(--mute)}.q{font-weight:600}
.tag{display:inline-block;padding:1px 7px;border-radius:999px;background:var(--chip);font-size:12px;white-space:nowrap}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.cmp{border-collapse:collapse;width:100%;table-layout:fixed;background:var(--card)}.cmp th,.cmp td{border:1px solid var(--line);padding:8px 10px;vertical-align:top;width:50%;font-size:13.5px;line-height:1.7}
.cmp th{letter-spacing:.3em;text-align:center}u{text-decoration-thickness:2px;text-underline-offset:3px;text-decoration-color:var(--acc)}
.count{color:var(--mute);font-size:12px;margin:4px 2px}
details{margin:6px 0}summary{cursor:pointer;color:var(--mute)}
pre{white-space:pre-wrap;font-size:12px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px}
</style></head><body><div class="wrap">
<h1>조항 점검 1차 결과</h1>
<div class="mute" id="meta"></div>
<div class="note">담당자 검토용 표시입니다. 오류 확정이 아닙니다. <b>정답 목록 밖 결과</b>는 오탐이 아니라 사람이 확인할 <b>확인 대기</b>입니다. 원문은 고치지 않았습니다.</div>
<div class="cards" id="cards"></div>
<div class="tabs" id="tabs"></div>
<section class="panel" id="p-score"></section>
<section class="panel" id="p-found"></section>
<section class="panel" id="p-cmp"></section>
<section class="panel" id="p-rel"></section>
<section class="panel" id="p-run"></section>
</div>
<script>
const D=__DATA__;
const $=s=>document.querySelector(s), esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const tag=(t,c="")=>`<span class="tag ${c}">${esc(t)}</span>`;
$("#meta").textContent=`생성 ${D.made} · 채점 실행 ${D.score.run} · 코드 ${D.score.code.commit}${D.score.code.uncommitted?" (미커밋 변경 있음)":""}`;
const t=D.score.total;
const cards=[...Object.entries(D.stats),["정답 재현율",`${t["탐지"]}/${t["채점 대상"]}`],["그중 우선순위 높음",`${t["높음"]}/${t["채점 대상"]}`],["확인 대기(담당 21건)",D.score.queue]];
$("#cards").innerHTML=cards.map(([k,v])=>`<div class="card"><b>${typeof v=="number"?v.toLocaleString():esc(v)}</b><span>${esc(k)}</span></div>`).join("");

const TABS=[["score","정답 대조"],["found","점검 결과"],["cmp","대비표"],["rel","관련 조항 조회"],["run","실행 기록"]];
$("#tabs").innerHTML=TABS.map(([k,v])=>`<button data-k="${k}">${v}</button>`).join("");
function show(k){document.querySelectorAll(".tabs button").forEach(b=>b.classList.toggle("on",b.dataset.k==k));document.querySelectorAll(".panel").forEach(p=>p.classList.toggle("on",p.id=="p-"+k));try{localStorage.setItem("tab",k)}catch(e){}}
$("#tabs").onclick=e=>{if(e.target.dataset.k)show(e.target.dataset.k)};
let first="score";try{first=localStorage.getItem("tab")||first}catch(e){}

// 정답 대조
const LN={week1:"1주차 검토(박형건)",gabin_doc:"가빈 정리본"};
const resCls=r=>r=="탐지"?"ok":r=="놓침"?"bad":"warn";
$("#p-score").innerHTML=Object.entries(D.score.ledgers).map(([k,v])=>`
<h2>${esc(LN[k]||k)} <span class="mute">재현율 ${v["탐지"]}/${v["채점 대상"]} · 높음 ${v["높음"]} · 수정 제안 일치 ${v["제안 일치"][0]}/${v["제안 일치"][1]}</span></h2>
<div class="bar">${Object.entries(v["종류별"]).map(([t,x])=>tag(`${t} ${x["탐지"]}/${x["정답"]}`)).join("")}</div>
<details><summary>원장 분포 (대표성 점검)</summary><pre>${esc(JSON.stringify(v["분포"],null,1))}</pre></details>
<div class="tw"><table><tr><th>ID</th><th>규정</th><th>조</th><th>인용</th><th>종류</th><th>결과</th><th>기대 출력</th><th>정답</th><th>근거</th></tr>
${D.gold.filter(g=>g.ledger==k).map(g=>`<tr><td>${esc(g.id)}</td><td>${esc(D.names[g.reg_id]||g.reg_id)}</td><td>${esc(g.article)}</td><td class="q">${esc(g.quote)}</td><td>${tag(g.type)}</td>
<td class="${resCls(g.result)}">${esc(g.result)}${g.fid?` <a href="#" data-f="${g.fid}">${g.fid}</a>`:""}</td><td>${esc(g.show)}</td><td>${esc(g.answer)}${g.suggest_ok===false?' <span class="warn">(제안 다름)</span>':g.suggest_ok?' <span class="ok">(제안 일치)</span>':""}</td><td class="ctx">${esc(g.basis)}</td></tr>`).join("")}
</table></div>`).join("")+`<div class="note">탐지(낮음) = 벡터 1단계로만 잡혀 우선순위가 낮은 것. 가빈 정리본의 대상 불일치 3건은 조정 과정에서 본 사례라 독립 평가가 아닙니다. 독립 평가는 가빈 corrections 155건이 들어온 뒤 합니다.</div>`;
document.addEventListener("click",e=>{const f=e.target.dataset&&e.target.dataset.f;if(f){e.preventDefault();show("found");$("#fq").value=f;$("#fs").value="";renderF();}});

// 점검 결과
const uniq=a=>[...new Set(a)].sort();
$("#p-found").innerHTML=`<div class="bar">
<select id="fs"><option value="">전체 범위</option><option value="queue">확인 대기 (담당 21건, 정답 밖)</option><option value="gold">정답과 연결된 것</option><option value="assigned">담당 21건</option></select>
<select id="ft"><option value="">모든 종류</option>${uniq(D.found.map(f=>f.type)).map(x=>`<option>${esc(x)}</option>`).join("")}</select>
<select id="fp"><option value="">모든 우선순위</option><option>높음</option><option>낮음</option></select>
<select id="fu"><option value="">모든 단위</option>${uniq(D.found.map(f=>f.unit)).map(x=>`<option>${esc(x)}</option>`).join("")}</select>
<select id="fr"><option value="">모든 규정</option>${uniq(D.found.map(f=>f.reg_name)).map(x=>`<option>${esc(x)}</option>`).join("")}</select>
<input id="fq" placeholder="검색 (문구, ID)" size="18"></div><div class="count" id="fc"></div>
<div class="tw"><table id="ftab"></table></div>`;
function renderF(){
 const s=$("#fs").value,ty=$("#ft").value,p=$("#fp").value,u=$("#fu").value,r=$("#fr").value,q=$("#fq").value.trim();
 const rows=D.found.filter(f=>(!s||(s=="queue"?f.assigned&&!f.gold.length:s=="gold"?f.gold.length:f.assigned))&&(!ty||f.type==ty)&&(!p||f.priority==p)&&(!u||f.unit==u)&&(!r||f.reg_name==r)
  &&(!q||[f.fid,f.quote,f.context,f.note,f.reg_name,f.article].join(" ").includes(q)));
 $("#fc").textContent=`${rows.length}개 / 전체 ${D.found.length}개`;
 $("#ftab").innerHTML=`<tr><th>ID</th><th>규정</th><th>위치</th><th>인용</th><th>종류</th><th>우선</th><th>설명</th><th>문맥</th><th>정답</th><th>방법</th></tr>`+
 rows.slice(0,400).map(f=>`<tr><td>${f.fid}</td><td>${esc(f.reg_name)}</td><td>${esc(f.unit)} ${esc(f.article)}${f.para?" "+f.para+"항":""}</td><td class="q">${esc(f.quote)}</td>
 <td>${tag(f.type)}</td><td class="${f.priority=="높음"?"":"mute"}">${esc(f.priority)}</td><td>${esc(f.note)}${f.suggest?`<br><span class="ok">제안: ${esc(f.suggest)}</span>`:""}</td>
 <td class="ctx">${esc(f.context)}</td><td>${f.gold.length?`<span class="ok">${esc(f.gold.join(", "))}</span>`:f.assigned?'<span class="warn">확인 대기</span>':'<span class="mute">범위 밖</span>'}</td><td class="mute">${esc(f.method)}</td></tr>`).join("")
 +(rows.length>400?`<tr><td colspan="10" class="mute">앞 400개만 표시. 조건을 좁히세요.</td></tr>`:"");
}
["fs","ft","fp","fu","fr"].forEach(i=>$("#"+i).onchange=renderF);$("#fq").oninput=renderF;

// 대비표
const cell=lines=>lines.map(ln=>"<div>"+ln.map(([t,c])=>c?`<u>${esc(t)}</u>`:esc(t)).join("")+"</div>").join("");
$("#p-cmp").innerHTML=D.changes.length?`<div class="note">개정문을 규정 <b>사본</b>에 적용한 참고 초안입니다. 원문은 바꾸지 않았고, 승인은 개정이 아닙니다. 밑줄 = 바뀐 부분. 바뀌지 않은 항·호는 (생 략)/(현행과 같음)으로 줄입니다.
새 개정문: <code>uv run python scripts/amend.py --reg 규정명 --text "…"</code></div>
<div class="bar"><select id="cs">${D.changes.map((c,i)=>`<option value="${i}">${esc(c.id)} · ${esc(c.reg_name)}${c.title?" · "+esc(c.title):""}</option>`).join("")}</select></div><div id="cout"></div>`:`<p class="mute">아직 없음. scripts/amend.py 로 만든다.</p>`;
function renderC(){const c=D.changes[+$("#cs").value];const moved=Object.entries(c.label_map);
 $("#cout").innerHTML=`<h2>「${esc(c.reg_name)}」 ${esc(c.title||c.id)}</h2>
 <details open><summary>개정문 (${c.sentences.length}문장)</summary><pre>${esc(c.amendment)}</pre></details>
 ${moved.length?`<div class="note">번호 변경·삭제 ${moved.length}개 조문: ${moved.slice(0,8).map(([k,v])=>esc(D.names[k.split(":")[0]]?art(k):k)+" → "+esc(v||"삭제")).join(", ")}${moved.length>8?" …":""}. 이 조문을 인용하는 곳은 다음 단계(변경안 점검)에서 찾습니다.</div>`:""}
 <div class="tw"><table class="cmp"><tr><th>현 행</th><th>개 정 안</th></tr>${c.rows.map(r=>`<tr><td>${cell(r.old)}</td><td>${cell(r.new)}</td></tr>`).join("")}</table></div>`;}
if(D.changes.length){$("#cs").onchange=renderC;}

// 관련 조항 조회
$("#p-rel").innerHTML=`<div class="note">조항 하나를 바꾸면 함께 봐야 할 조항입니다. 미리 계산해 둔 관련 조항 표(인용 기반)를 조회합니다. 같은 기준(값)·예외·정의-사용 관계는 아직 없습니다.</div>
<div class="bar"><select id="rr">${Object.entries(D.names).sort((a,b)=>a[1].localeCompare(b[1])).map(([k,v])=>`<option value="${k}">${esc(v)}</option>`).join("")}</select>
<input id="ra" placeholder="조 (예: 제37조)" size="14"></div><div id="rout"></div>`;
const art=id=>{const m=/^(\d+):(?:(부칙\d*):)?(\d+)(?:의(\d+))?/.exec(id||"");return m?`${m[2]?m[2]+" ":""}제${m[3]}조${m[4]?"의"+m[4]:""}`:id};
const lbl=id=>{const r=(id||"").split(":")[0];return (D.names[r]||r)+" "+(id&&id.includes(":")?art(id):"")};
function renderR(){
 const reg=$("#rr").value,a=$("#ra").value.replace(/\s/g,"");
 const mine=x=>x&&x.startsWith(reg+":")&&(!a||art(x)==a||art(x).endsWith(" "+a));
 const out=D.rels.filter(r=>mine(r.src_article)), inb=D.rels.filter(r=>r.target&&(mine(r.target)||(!a&&r.target==reg)));
 const tb=(rows,dir)=>rows.length?`<div class="tw"><table><tr><th>${dir=="out"?"이 조항이 인용":"이 조항을 인용하는 곳"}</th><th>관계</th><th>근거 문구</th><th>확실성</th><th>검증</th><th>상태</th></tr>${rows.slice(0,200).map(r=>`<tr><td>${esc(dir=="out"?(r.target_type=="law"?r.target:lbl(r.target)):lbl(r.src_article))}</td><td>${tag(r.kind)}</td><td class="ctx">${esc(r.evidence)}</td><td>${esc(r.confidence)}</td><td class="${r.verification=="의심"?"warn":""}">${esc(r.verification)}</td><td class="${r.check_status=="ok"?"mute":"bad"}">${esc(r.check_status)}</td></tr>`).join("")}</table></div>`:`<p class="mute">없음</p>`;
 $("#rout").innerHTML=`<h2>인용함 (${out.length})</h2>${tb(out,"out")}<h2>인용받음 (${inb.length})</h2>${tb(inb,"in")}`;
}
$("#rr").onchange=renderR;$("#ra").oninput=renderR;
const pre=Object.entries(D.names).find(([k,v])=>v=="인사규정");if(pre)$("#rr").value=pre[0];

// 실행 기록
$("#p-run").innerHTML=`<div class="note">결과마다 실행 ID(run)가 붙어 있고, 어느 코드·입력·모델로 나왔는지는 아래 기록(out/runs, eval/runs)에서 찾습니다.</div>`+
 D.runs.map(r=>`<details open><summary><b>${esc(r.id)}</b> · 코드 ${esc(r.code.commit)}${r.code.uncommitted?" (미커밋)":""}</summary><pre>${esc(JSON.stringify({step:r.step,models:r.models,inputs:r.inputs,summary:r.summary},null,1))}</pre></details>`).join("");

renderF();renderR();if(D.changes.length)renderC();show(first);
</script></body></html>"""

if __name__ == "__main__":
    main()
