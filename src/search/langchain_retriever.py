"""검색기를 LangChain 리트리버 형식으로 감싼다.

우리 고유 로직(정확 조회, 가중 RRF, 리랭킹, 필터, 형제 항·인용 확장)은 그대로 `Searcher`에 둔다.
이 파일은 LangChain의 `BaseRetriever` 인터페이스에 맞춰 결과를 `Document`로 바꾸는 얇은 어댑터다.
LangSmith로 추적하거나, 다른 LangChain 체인에 끼워 넣을 때 이 클래스를 쓴다.
"""
from typing import Any, List, Optional

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict

from src.search.search import Searcher

# 답변 단계가 쓰는 필드. Document.metadata에 그대로 담아서 되돌릴 수 있게 한다.
_META_KEYS = ["chunk_id", "doc_id", "문서명", "위계", "개정표기", "출처URL",
             "article", "조문상태", "정정안내", "score", "rerank", "match",
             "siblings", "cited", "related", "하위질문",   # related: 주제 링크 확장 (검색_설계.md 12절)
             "hit_chunk"]  # 조 전체를 넘길 때 이 조를 올린 항 (화면 강조용, 261019)


def entry_to_document(e):
    meta = {k: e.get(k) for k in _META_KEYS}
    return Document(page_content=e["body"], metadata=meta)


def document_to_entry(d):
    e = dict(d.metadata)
    e["body"] = d.page_content
    return e


class RegulationRetriever(BaseRetriever):
    """Searcher를 감싼 LangChain 리트리버. k, filters는 생성 시점에 고정한다."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    searcher: Searcher
    k: int = 5
    filters: Optional[dict] = None
    rewritten: Optional[Any] = None  # 규정 용어로 바꿔 쓴 검색어 (후보 찾기에만 더한다). 바꿔쓰기를 동시에 돌리면 Future
    plan: Optional[list] = None  # 하위 질문 목록 (Generator.plan_queries). 있으면 rewritten 대신 이것으로 검색한다
    rewritten_future: Optional[Any] = None  # rewritten을 GPT가 만드는 중일 때 (Searcher.search 참고)

    def _get_relevant_documents(self, query, *, run_manager: CallbackManagerForRetrieverRun) -> List[Document]:
        if self.plan:
            results = self.searcher.search_plan(self.plan, k=self.k, filters=self.filters)
        else:
            results = self.searcher.search(query, k=self.k, filters=self.filters, rewritten=self.rewritten,
                                           rewritten_future=self.rewritten_future)
        return [entry_to_document(e) for e in results]
