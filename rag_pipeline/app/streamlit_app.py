"""규정 질의응답 화면 (Streamlit).

실행 (rag_pipeline 폴더에서):
  uv run streamlit run app/streamlit_app.py
"""
import os
import sys

import streamlit as st

# 프로젝트 루트(rag_pipeline)를 import 경로에 넣는다
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.answer.pipeline import Assistant  # noqa: E402
from src.store.pgstore import PgStore  # noqa: E402

TIERS = ["전체", "정관", "규정", "지침", "헌장", "강령", "기타"]
STATE_BADGE = {"현행": "🟢 현행", "부칙": "🟡 부칙(현행 아님)", "삭제": "🔴 삭제"}
ROLE_LABEL = {"정확조회": "정확 조회", "검색": "검색", "인용": "인용 조문", "형제항": "같은 조의 다른 항"}


@st.cache_resource(show_spinner="검색 모델과 색인을 불러오는 중입니다...")
def get_assistant():
    return Assistant()


@st.cache_data(ttl=300)
def document_list():
    store = PgStore()
    docs = store.load_documents()
    store.close()
    return sorted(docs, key=lambda d: d["title"])


def build_filters(tier, doc_choice, min_year):
    filters = {}
    if tier != "전체":
        filters["tier"] = tier
    if doc_choice != "전체":
        filters["doc_ids"] = [doc_choice]
    if min_year:
        filters["min_year"] = min_year
    return filters or None


def show_entry(e):
    badge = STATE_BADGE.get(e["조문상태"], e["조문상태"])
    role = ROLE_LABEL.get(e.get("역할", ""), e.get("역할", ""))
    st.markdown(f"**{e['문서명']} {e['article']}** · {badge} · {role}")
    st.caption(f"개정: {e.get('개정표기') or '-'}" + (f" · [출처]({e['출처URL']})" if e.get("출처URL") else ""))
    if e.get("연결상태"):
        st.caption(f"인용 연결: {e['연결상태']} ({e.get('인용원문', '')})")
    st.text(e["body"])
    if e.get("정정안내"):
        st.info("정정 안내: " + " / ".join(e["정정안내"]))


st.set_page_config(page_title="규정 질의응답", page_icon="📘", layout="wide")
st.title("📘 한국벤처투자 내부규정 질의응답")
st.caption("답변은 검색된 규정 조문에서만 만듭니다. 근거가 없으면 확인되지 않는다고 답합니다.")

with st.sidebar:
    st.header("검색 범위")
    tier = st.selectbox("위계", TIERS)
    docs = document_list()
    doc_labels = ["전체"] + [f"{d['title']} ({d['doc_id']})" for d in docs]
    doc_choice = st.selectbox("규정", doc_labels)
    if doc_choice != "전체":
        doc_choice = doc_choice.rsplit("(", 1)[1].rstrip(")")
    min_year = st.number_input("개정연도 이상 (0이면 제한 없음)", min_value=0, max_value=2100, value=0, step=1)
    show_debug = st.checkbox("검색 과정 보기", value=False)

question = st.text_input("질문을 입력하세요", placeholder="예: 직위해제 기간 동안 급여는 얼마인가요?")
ask = st.button("질문하기", type="primary", disabled=not question.strip())

if ask:
    assistant = get_assistant()
    with st.spinner("규정을 찾아 답을 만드는 중입니다..."):
        result = assistant.ask(question.strip(), filters=build_filters(tier, doc_choice, min_year))

    st.subheader("답변")
    st.markdown(result["answer"])
    for w in result.get("warnings", []):
        st.warning(w)
    if result.get("model"):
        st.caption(f"모델: {result['model']}")

    entries = result.get("entries", [])
    st.subheader(f"근거 ({len(entries)}개, {result['assembled']['chars']:,}자)")
    for i, e in enumerate(entries, start=1):
        with st.expander(f"[{i}] {e['문서명']} {e['article']} · {STATE_BADGE.get(e['조문상태'], e['조문상태'])}",
                         expanded=(i <= 2)):
            show_entry(e)

    if show_debug:
        st.subheader("검색 과정")
        for r in result.get("results", []):
            rr = f"{r['rerank']:.3f}" if r.get("rerank") is not None else "-"
            rrf = f"{r['score']:.4f}" if r.get("score") is not None else "-"
            st.write(f"- {r['문서명']} {r['article']} · 경로 {r['match']} · 리랭커 {rr} · RRF {rrf}")
