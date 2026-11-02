"""검토 화면 (PoC 필수 ②): 기준선 이슈와 개정안(대비표·영향)을 담당자가 확인하고 결정을 남긴다.

  uv sync --extra vector --extra db --group notebook --group app
  uv run streamlit run app/review_app.py
결정은 eval/review_log.jsonl에 덧붙이기만 한다 (regcheck.review). 원문·규정 DB는 바꾸지 않는다.
자동 수락 방지: 기본 선택 없음, 결정은 상세를 연 뒤에만, 일괄 수락 없음 (리포트 261006-1414-06).
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from regcheck.amend import AmendError, build_changeset, to_html  # noqa: E402
from regcheck.review import STATES, ReviewLog, key_baseline, key_impact, key_row  # noqa: E402

st.set_page_config(page_title="조항 점검 검토", layout="wide")

CATN = {"breaks": "깨짐", "new_conflicts": "새 충돌 후보", "resolved": "해소 후보", "review": "확인 필요",
        "law_check": "법령 대조", "undeterminable": "판단 불가", "info": "참고"}
CSS = """<style>
.cmp{border-collapse:collapse;width:100%;table-layout:fixed}.cmp caption{display:none}
.cmp th,.cmp td{border:1px solid rgba(128,128,128,.45);padding:6px 9px;vertical-align:top;width:50%;line-height:1.7}
.cmp th{letter-spacing:.3em;text-align:center}
u{text-decoration-thickness:2px;text-underline-offset:3px}mark{padding:0 2px}
.art{border:1px solid rgba(128,128,128,.35);border-radius:8px;padding:10px 12px;line-height:1.75;white-space:pre-wrap}
</style>"""
st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------- 데이터

def jl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


@st.cache_data
def load_static():
    from parse_regs import assigned_names
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    found = jl(ROOT / "out" / "findings" / "findings.jsonl")
    rels = jl(ROOT / "out" / "relations" / "relations.jsonl")
    edges = jl(ROOT / "out" / "edges" / "edges_all.jsonl")
    runs = sorted((ROOT / "eval" / "runs").glob("*-score.json"))
    gold, matched = {}, set()
    if runs:
        sc = json.loads(runs[-1].read_text(encoding="utf-8"))
        matched = set(sc["summary"].get("정답 연결 finding", []))
        for res in sc["summary"]["원장별"].values():
            for it in res["항목"]:
                if it["fid"]:
                    gold.setdefault(it["fid"], []).append(it["id"])
    assigned = set(assigned_names())
    for f in found:
        f.setdefault("priority", "높음")
        f["gold"] = ", ".join(gold.get(f["fid"], [])) or ("정답 연결" if f["fid"] in matched else "")
        f["scope"] = "정답 연결" if f["gold"] else ("확인 대기" if f["reg_name"] in assigned else "범위 밖")
        f["key"] = key_baseline(f)
    return regs, found, rels, edges


def load_changes() -> dict[str, dict]:
    out = {}
    for p in sorted((ROOT / "out" / "changes").glob("*/changeset.json")):
        cs = json.loads(p.read_text(encoding="utf-8"))
        chk = p.parent / "check.json"
        cs["check"] = json.loads(chk.read_text(encoding="utf-8")) if chk.exists() else None
        out[cs["id"]] = cs
    return out


@st.cache_resource
def vector_backend():
    import os
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from regcheck.vectors import Index, load_model
    index = Index(ROOT / "out" / "vector" / "BAAI__bge-m3")
    return load_model(index.model_name), index


@st.cache_resource
def impact_context():
    """영향 후보 탭용 그래프 · 문구 색인. out/kg가 없으면 None (scripts/build_kg.py)."""
    from regcheck.impact_view import load_context
    return load_context(ROOT, load_static()[0])


regs, found, rels, edges = load_static()
log = ReviewLog()
names = {k: v["name"] for k, v in regs.items()}


def article_of(reg_id: str, label: str, unit: str = "본문") -> dict | None:
    r = regs.get(reg_id)
    if not r or unit != "본문":
        return None
    return next((a for a in r["articles"] if a["label"] == label), None)


def unit_html(src_id: str, quote: str = "", context: str = "") -> str:
    """본문이 아닌 단위(별지·별표·부칙)의 전체 문안. 문맥 조각(앞뒤 60자)만 보이면 문장이 잘려 보인다."""
    from regcheck.amend import marker
    from regcheck.normalize import normalize
    r = regs.get(src_id.split(":")[0])
    text = ""
    if r:
        aid = src_id.split("-")[0]
        ap = next((x for x in r["appendices"] if x["id"] == aid), None)
        if ap:
            text = (f"{ap['kind']} {ap['number']} {ap['title']}".strip() + "\n" + normalize(ap["text"])).strip()
        else:
            for ad in r["addenda"]:
                a = next((x for x in ad["articles"] if x["id"] == aid), None)
                if a:
                    head = f"부칙 <{ad['date']}> {a['label']}" + (f"({a['title']})" if a["title"] else "")
                    text = head + "\n" + "\n".join(marker(it["path"]) + it["norm"] for it in a["items"])
                    break
    if not text:
        return f'<div class="art">{html.escape(context)}</div>'
    body = html.escape(text)
    q = html.escape(quote.strip("「」")) if quote else ""
    if q and q in body:
        body = body.replace(q, f"<mark>{q}</mark>")
    return f'<div class="art" style="max-height:420px;overflow:auto">{body}</div>'


def art_html(a: dict, para: int = 0, quote: str = "") -> str:
    from regcheck.amend import marker
    lines = [f"<b>{html.escape(a['label'])}{'(' + html.escape(a['title']) + ')' if a['title'] else ''}</b>"]
    for it in a["items"]:
        t = html.escape(marker(it["path"]) + it["norm"])
        if quote and (not para or it["path"][0] in (para, 0)) and html.escape(quote) in t:
            t = t.replace(html.escape(quote), f"<mark>{html.escape(quote)}</mark>")
        lines.append(t)
    return '<div class="art">' + "<br>".join(lines) + "</div>"


def decision_form(key: str, kind: str, ref: dict, reviewer: str, form_key: str) -> None:
    cur = log.state(key, kind)
    st.caption(f"현재 상태: **{cur}**")
    with st.form(form_key, clear_on_submit=True):
        choice = st.radio("결정", STATES[kind][1:], index=None, horizontal=True, key=form_key + "r")
        comment = st.text_input("의견 (근거·메모)", key=form_key + "c")
        ok = st.form_submit_button("결정 저장", disabled=not reviewer.strip())
        if ok:
            if not choice:
                st.warning("결정을 고르세요.")
            else:
                log.decide(key, kind, choice, reviewer, comment, ref)
                st.success(f"저장: {cur} → {choice}")
                st.rerun()
    hist = log.history(key)
    if hist:
        st.dataframe(pd.DataFrame([{"일시": h["at"], "검토자": h["reviewer"], "이전": h["prev"], "결정": h["state"], "의견": h["comment"]}
                                   for h in hist]), hide_index=True, width="stretch")


# ---------------------------------------------------------------- 사이드바

with st.sidebar:
    st.header("조항 점검 검토")
    page = st.radio("화면", ["기준선 이슈", "개정안 검토", "검토 현황"], label_visibility="collapsed")
    reviewer = st.text_input("검토자", key="reviewer", help="결정 기록에 남는 이름. 비우면 저장할 수 없습니다.")
    if not reviewer.strip():
        st.info("검토자 이름을 넣어야 결정을 저장할 수 있습니다.")
    st.caption("담당자 검토용 표시입니다. 오류 확정이 아닙니다. 원문은 바꾸지 않습니다.")
    if st.button("데이터 다시 읽기", help="점검을 다시 돌린 뒤 (build_findings·score) 누르면 새 결과를 읽습니다. 결정 기록은 그대로입니다."):
        load_static.clear()
        st.rerun()


# ---------------------------------------------------------------- 기준선 이슈

if page == "기준선 이슈":
    st.subheader("기준선 이슈")
    st.caption("최초 전수 점검 결과. 정답 목록 밖 결과는 오탐이 아니라 확인 대기입니다.")
    c1, c2, c3, c4, c5 = st.columns([1.2, 1.4, 1, 1, 1.6])
    scope = c1.selectbox("범위", ["확인 대기", "정답 연결", "범위 밖", "전체"])
    types = sorted({f["type"] for f in found})
    typ = c2.selectbox("종류", ["전체"] + types)
    pri = c3.selectbox("우선순위", ["전체", "높음", "낮음"])
    stt = c4.selectbox("상태", ["전체"] + STATES["baseline"])
    regq = c5.selectbox("규정", ["전체"] + sorted({f["reg_name"] for f in found}))
    rows = [f for f in found
            if (scope == "전체" or f["scope"] == scope) and (typ == "전체" or f["type"] == typ)
            and (pri == "전체" or f["priority"] == pri) and (regq == "전체" or f["reg_name"] == regq)]
    for f in rows:
        f["state"] = log.state(f["key"], "baseline")
    if stt != "전체":
        rows = [f for f in rows if f["state"] == stt]
    st.caption(f"{len(rows)}건")
    df = pd.DataFrame([{"상태": f["state"], "ID": f["fid"], "규정": f["reg_name"],
                        "위치": f"{f['unit']} {f['article']}" + (f" {f['para']}항" if f["para"] else ""),
                        "인용": f["quote"], "종류": f["type"], "우선": f["priority"], "정답": f["gold"]} for f in rows])
    sel = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                       height=320) if rows else None
    picked = sel.selection.rows if sel else []
    if not picked:
        st.info("목록에서 한 줄을 고르면 상세와 결정 칸이 나옵니다.")
    else:
        f = rows[picked[0]]
        st.divider()
        left, right = st.columns([1.35, 1])
        with left:
            st.markdown(f"#### {f['reg_name']} {f['unit']} {f['article']}" + (f" {f['para']}항" if f["para"] else ""))
            st.markdown(f"**{f['type']}** · 우선순위 {f['priority']} · 방법 {f['method']} · {f['fid']}"
                        + (f" · 정답 {f['gold']}" if f["gold"] else ""))
            qd = f["quote"] if f["quote"].startswith("「") else f"「{f['quote']}」"
            st.markdown(f"**인용** {qd}  \n**설명** {f['note'] or '-'}")
            if f.get("suggest"):
                st.markdown(f"**참고 수정안** 「{f['suggest']}」 (자동 반영 안 함)")
            a = article_of(f["reg_id"], f["article"], f["unit"])
            st.markdown(art_html(a, f["para"], f["quote"]) if a else unit_html(f.get("src_id", ""), f["quote"], f["context"]),
                        unsafe_allow_html=True)
            aid = a["id"] if a else None
            if aid:
                outs = [r for r in rels if r["src_article"] == aid]
                ins = [r for r in rels if r.get("target") == aid]
                with st.expander(f"관련 조항 — 인용함 {len(outs)} · 인용받음 {len(ins)}"):
                    def lbl(x):
                        if not x or ":" not in x:
                            return x or ""
                        rid, rest = x.split(":", 1)
                        return f"{names.get(rid, rid)} {rest}"
                    st.dataframe(pd.DataFrame(
                        [{"방향": "인용함", "상대": r["target"] if r["target_type"] == "law" else lbl(r["target"]), "근거": r["evidence"],
                          "상태": r["check_status"], "검증": r["verification"]} for r in outs]
                        + [{"방향": "인용받음", "상대": lbl(r["src_article"]), "근거": r["evidence"], "상태": r["check_status"],
                            "검증": r["verification"]} for r in ins]), hide_index=True, width="stretch")
        with right:
            st.markdown("#### 결정")
            st.caption("오류 확인 = 원문에 실제 문제가 있음 / 오탐 = 우리 해석이 틀림 / 판단 불가 = 담당 부서 확인 필요")
            decision_form(f["key"], "baseline", {"fid": f["fid"], "reg_id": f["reg_id"], "article": f["article"],
                                                 "para": f["para"], "quote": f["quote"], "type": f["type"]},
                          reviewer, "bf_" + f["key"])


# ---------------------------------------------------------------- 개정안 검토

elif page == "개정안 검토":
    st.subheader("개정안 검토")
    st.caption("개정문을 규정 사본에 적용한 참고 초안입니다. 승인은 개정이 아니며, 규정은 공포된 개정본의 시행일에만 바뀝니다.")
    with st.expander("새 개정안 넣기"):
        rname = st.selectbox("규정", sorted(names.values()))
        cid = st.text_input("개정안 ID", value="draft")
        text = st.text_area("개정문", height=120,
                            placeholder='제30조제2항 중 "2회"를 "3회"로 한다.\n제37조를 삭제한다.\n제6조의 다음에 제7조를 다음과 같이 신설한다.\n제7조(제목) ① ...')
        use_vec = st.checkbox("의미 유사 후보도 찾기 (모델 로드, 처음 10~20초)", value=False)
        if st.button("적용하고 점검", disabled=not text.strip() or not re.fullmatch(r"[\w-]+", cid)):
            reg = next(r for r in regs.values() if r["name"] == rname)
            try:
                cs, _ = build_changeset(reg, text, cid)
            except AmendError as e:
                st.error(f"적용 못함 — {e}")
            else:
                from regcheck.change_check import run as run_check
                cdir = ROOT / "out" / "changes" / cid
                cdir.mkdir(parents=True, exist_ok=True)
                (cdir / "changeset.json").write_text(json.dumps(cs, ensure_ascii=False, indent=1), encoding="utf-8")
                from regcheck.lawdb import DEFAULT as LAW_DB, LawDB
                fnd = run_check(cs, regs, rels, edges, vector=vector_backend() if use_vec else None,
                                lawdb=LawDB(LAW_DB) if LAW_DB.exists() else None)
                (cdir / "check.json").write_text(json.dumps({"id": cid, "found": fnd, "score": None}, ensure_ascii=False, indent=1),
                                                 encoding="utf-8")
                st.session_state["change"] = cid
                st.success(f"{cid}: 조문 {len(cs['rows'])}개 변경, 영향 {sum(f['category'] != 'info' for f in fnd)}건")
    changes = load_changes()
    if not changes:
        st.info("개정안이 없습니다. 위에서 넣거나 scripts/amend.py 로 만드세요.")
        st.stop()
    ids = list(changes)
    cur_id = st.selectbox("개정안", ids, index=ids.index(st.session_state.get("change", ids[0])) if st.session_state.get("change") in ids else 0,
                          format_func=lambda i: f"{i} · {changes[i]['reg_name']}" + (f" · {changes[i]['title']}" if changes[i].get("title") else ""))
    cs = changes[cur_id]
    with st.expander(f"개정문 ({len(cs['sentences'])}문장)"):
        st.code(cs["amendment"], language=None)
    tab1, tab2, tab3 = st.tabs([f"신·구조문대비표 ({len(cs['rows'])})", "영향 (변경안 점검)", "영향 후보 (그래프 · 근거 카드)"])
    with tab1:
        st.caption("행마다 수락·거절합니다. 밑줄 = 바뀐 부분. 일괄 수락은 없습니다.")
        for i, r in enumerate(cs["rows"]):
            k = key_row(cur_id, r)
            c1, c2 = st.columns([3, 1])
            with c1:
                st.markdown(to_html([r], ""), unsafe_allow_html=True)
            with c2:
                st.markdown(f"**{r['old_label'] or '(신설)'} → {r['new_label']}**")
                decision_form(k, "row", {"change": cur_id, "row": r["new_label"]}, reviewer, f"rf_{k}")
    with tab2:
        chk = cs.get("check")
        if not chk:
            st.info(f"점검 전: uv run python scripts/check_change.py --change {cur_id}")
        else:
            if chk.get("score"):
                parts = [f"{CATN.get(c, '영향 없음')} {sum(x['result'] in ('탐지', '통과') for x in rows_)}/{len(rows_)}"
                         for c, rows_ in chk["score"].items() if rows_]
                st.caption("시나리오 기대 결과 대조 (독립 평가 아님): " + " · ".join(parts))
            items = [x for x in chk["found"] if x["category"] != "info"]
            cats = [c for c in CATN if any(x["category"] == c for x in items)]
            for c in cats:
                group = [x for x in items if x["category"] == c]
                st.markdown(f"##### {CATN[c]} ({len(group)})")
                for x in group:
                    k = key_impact(cur_id, x)
                    state = log.state(k, "impact")
                    title = (f"[{state}] {x['reg_name']} {x.get('unit', '')} {x.get('article', '')}"
                             + (f" {x['para']}항" if x.get("para") else "") + f" 「{(x.get('quote') or '')[:30]}」 · {x.get('priority', '')} · {x['path']}")
                    with st.expander(title):
                        if x.get("quote"):
                            st.markdown(f"**문구** {x['quote'] if x['quote'].startswith('「') else '「' + x['quote'] + '」'}")
                        st.markdown(f"**이유** {x['reason']}")
                        if x.get("suggest"):
                            st.markdown(f"**참고 수정안** 「{x['quote']}」 → 「{x['suggest']}」")
                        if x.get("counterpart", {}).get("items"):
                            # 바뀐 항만 전체 문안으로, 바뀐 부분 밑줄 (대비표와 같은 방식)
                            from regcheck.amend import _segments
                            st.markdown(f"**바뀐 조** ({x.get('target', '')})")
                            rows_ = []
                            for d in x["counterpart"]["items"]:
                                so, sn = _segments(d["old"], d["new"])
                                cell = lambda segs: "".join(f"<u>{html.escape(t)}</u>" if ch else html.escape(t) for t, ch in segs)  # noqa: E731
                                rows_.append(f"<tr><td>{d['marker']} {cell(so)}</td><td>{d['marker']} {cell(sn)}</td></tr>")
                            st.markdown('<table class="cmp"><tr><th>현 행</th><th>개 정 안</th></tr>' + "".join(rows_) + "</table>",
                                        unsafe_allow_html=True)
                        a = article_of(x["reg_id"], x.get("article", ""), x.get("unit", "본문"))
                        q = x.get("quote", "") if x["path"] in ("인용받음", "문서 인용") else ""
                        if a:
                            st.markdown(art_html(a, x.get("para") or 0, q), unsafe_allow_html=True)
                        elif x.get("context"):
                            st.markdown(unit_html(x.get("src", ""), x.get("quote", ""), x["context"]), unsafe_allow_html=True)
                        decision_form(k, "impact", {"change": cur_id, "category": c, "reg_id": x["reg_id"],
                                                    "article": x.get("article"), "quote": x.get("quote")}, reviewer, f"if_{k}")

    with tab3:
        st.caption("후보를 확정하거나 충돌을 판정하지 않습니다. 단계는 경로별로 잰 정밀도로 나눈 것이고, 결정은 담당자가 합니다. 라벨 기준 평가는 탐색적 결과입니다(리포트 78).")
        ctx = impact_context()
        if ctx is None:
            st.info("그래프가 없습니다: uv run python scripts/build_kg.py")
        else:
            use_rag = st.checkbox("의미 유사 후보도 찾기 (모델 로드, 처음 10~20초)", value=False, key=f"rag_{cur_id}")
            from regcheck.impact_view import make_rag, run as impact_run
            rag = None
            if use_rag:
                model, index = vector_backend()
                rag = make_rag(model, index)
            res = impact_run(ctx, regs, cs["reg_id"], cs["amendment"], cur_id, rag=rag)
            if res["error"]:
                st.warning(f"개정문을 분석하지 못했습니다: {res['error']}")
            else:
                cq = res["cq"]
                view = res["view"]
                st.markdown(f"**수정 종류** {cq.kind} · {cq.evidence}")
                show_all = st.checkbox(f"전체 후보 보기 (소음이 많은 경로 후보 {view['hidden']}개 포함)", value=False, key=f"all_{cur_id}")
                if show_all:
                    from regcheck.evidence import card as make_card
                    cards = [make_card(c, cq, ctx.nodes, ctx.unit_text) for c in view["all"]]
                else:
                    cards = res["cards"]
                st.caption(f"후보 {len(cards)}개 (기본 화면: 의미 검색 30 + 쓸모 있는 경로의 그래프 후보 20까지). 숨긴 후보 {0 if show_all else view['hidden']}개.")
                for tier in ("확정급", "확인 권장", "참고"):
                    group = [c for c in cards if c["tier"] == tier]
                    if not group:
                        continue
                    st.markdown(f"##### {tier} ({len(group)})")
                    for c in group:
                        art = ctx.nodes.get(c["id"], {})
                        x = {"category": "graph:" + tier, "reg_id": art.get("reg", ""), "unit": None, "article": art.get("label", c["id"]), "para": None, "quote": c["quote"] or c["id"]}
                        k = key_impact(cur_id, x)
                        state = log.state(k, "impact")
                        with st.expander(f"[{state}] {names.get(art.get('reg', ''), '')} {c['target']} · {' / '.join(c['reasons'][:2])}"):
                            if c["quote"]:
                                st.markdown(f"**근거 문장** 「{c['quote']}」")
                            else:
                                st.markdown("**근거 문장** 없음 (규칙으로 뽑지 못함. 직접 확인)")
                            if c["replacement"]:
                                st.markdown(f"**참고 수정안** 「{c['replacement']['before']}」 → 「{c['replacement']['after']}」")
                            st.markdown("**경로** " + ", ".join(c["paths"]))
                            if c["needs_check"]:
                                st.caption("확인 필요: 확정급이 아니거나 근거 문장이 없습니다.")
                            a = article_of(art.get("reg", ""), art.get("label", ""))
                            if a:
                                st.markdown(art_html(a, 0, c["quote"] or ""), unsafe_allow_html=True)
                            decision_form(k, "impact", {"change": cur_id, "category": x["category"], "reg_id": x["reg_id"], "article": x["article"], "quote": c["quote"]},
                                          reviewer, f"gf_{k}")


# ---------------------------------------------------------------- 검토 현황

else:
    st.subheader("검토 현황")
    keys = {f["key"]: f for f in found}
    cnt = {}
    for f in found:
        s_ = log.state(f["key"], "baseline")
        cnt.setdefault(f["scope"], {}).setdefault(s_, 0)
        cnt[f["scope"]][s_] += 1
    st.markdown("##### 기준선 이슈 (범위별 상태)")
    st.dataframe(pd.DataFrame(cnt).fillna(0).astype(int).T, width="stretch")
    st.markdown("##### 최근 결정")
    if not log.rows:
        st.info("아직 결정이 없습니다.")
    else:
        st.dataframe(pd.DataFrame([{"일시": r["at"], "검토자": r["reviewer"], "대상": r["kind"], "이전": r["prev"], "결정": r["state"],
                                    "의견": r["comment"], "참조": json.dumps(r["ref"], ensure_ascii=False)[:90]}
                                   for r in reversed(log.rows[-200:])]), hide_index=True, width="stretch")
        confirmed = [keys[k] for k in {r["key"] for r in log.rows if r["kind"] == "baseline"} if k in keys
                     and log.state(k, "baseline") == "오류 확인" and not keys[k]["gold"]]
        st.markdown(f"##### 정답 원장 후보 — 오류 확인했지만 원장에 없는 것 {len(confirmed)}건")
        if confirmed:
            csv = pd.DataFrame([{k: f[k] for k in ("reg_id", "reg_name", "unit", "article", "para", "quote", "type", "note")}
                                for f in confirmed]).to_csv(index=False).encode("utf-8-sig")
            st.download_button("CSV로 받기 (원장 추가용)", csv, "ledger_candidates.csv", "text/csv")
    st.caption(f"결정 기록: {log.path.relative_to(ROOT) if log.path.is_relative_to(ROOT) else log.path} (덧붙이기만, git에 남김)")
