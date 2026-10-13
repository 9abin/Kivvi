"""외부 법령 인용 연결 (data/manual/law_rag.sqlite3, 2026.10.12 추가).

내부규정이 외부 법령을 인용한 곳(refs.py가 유형="외부"로만 표시하던 것)을, 미리 수집된
법령 원문 DB(law_rag.sqlite3: 법률·시행령·시행규칙 232건, 조문 23,189개)의 실제 조문에
연결한다. 검색 대상을 늘리는 게 아니라, 이미 찾은 내부규정 청크가 인용하는 법 조문을
그대로 보여주는 것뿐이라 임베딩이 필요 없다 (이름+조 번호로 바로 찾아간다).

매칭 범위: refs.py가 법령명을 실제로 캡처한 경우만(cites의 "법령명" 필드가 있을 때).
"OO법 같은 단어로 끝난다"는 식의 느슷한 추정(LAW_NAME/EXT_WORD)만으로 "외부"가 된
경우는 법령명을 모르므로 연결하지 않는다 — 틀린 연결을 만드는 것보다 "외부"로 남기는
쪽이 안전하다 (검색_설계.md, 답변_설계.md의 "추측으로 연결하지 않는다" 원칙과 같다).
"""
import re
import sqlite3
import json

ARTICLE_KEY = re.compile(r"제(\d+)조(?:\s*의\s*(\d+))?")

# 내부규정이 법령 DB의 정식 명칭과 다르게 쓰는 경우(약칭, 오탈자). 실제로 그 법이 법령 DB에
# 있는 것을 확인한 경우만 넣는다(2026.10.12). 없는 법을 여기 넣어도 조 번호가 안 맞으면
# 그대로 "외부"로 남으므로 틀린 연결이 생기진 않지만, 확인 안 된 추측은 넣지 않는다.
LAW_ALIASES = {
    "개보법": "개인정보 보호법",
    "개인정보 보호법률": "개인정보 보호법",
    "부패방지 및 국민권익위위원회의 설치와 운영에 관한 법률": "부패방지 및 국민권익위원회의 설치와 운영에 관한 법률",
    "특정금융정보법": "특정 금융거래정보의 보고 및 이용 등에 관한 법률",
    "특금법": "특정 금융거래정보의 보고 및 이용 등에 관한 법률",
    "테러자금금지법": "공중 등 협박목적 및 대량살상무기확산을 위한 자금조달행위의 금지에 관한 법률",
    "국가계약법": "국가를 당사자로 하는 계약에 관한 법률",
    "지방계약법": "지방자치단체를 당사자로 하는 계약에 관한 법률",
    "소프트웨어산업 진흥법": "소프트웨어 진흥법",
    "공공기록물법": "공공기록물 관리에 관한 법률",
    "퇴직급여보장법": "근로자퇴직급여 보장법",
    "이해충돌방지법": "공직자의 이해충돌 방지법",
}


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def load(path):
    """(법령명 -> document_id, (document_id, 조번호) -> 본문) 두 인덱스를 돌려준다."""
    conn = sqlite3.connect(path)
    titles = {}
    documents = list(conn.execute("select id, requested_title, api_title, raw_json from documents"))
    for doc_id, requested, api, _ in documents:
        for t in (requested, api):
            if t:
                titles.setdefault(_norm(t), doc_id)
    for doc_id, _, _, raw in documents:
        previous = json.loads(raw).get("법령", {}).get("기본정보", {}).get("이전법령명")
        if isinstance(previous, str) and previous.strip():
            titles.setdefault(_norm(previous), doc_id)
    chunks = {}
    for doc_id, path_, content in conn.execute("select document_id, path, content from chunks"):
        m = re.match(r"^(\d+(?:의\d+)?)", path_ or "")
        if m:
            chunks[(doc_id, m.group(1))] = content
    conn.close()
    return titles, chunks


def article_key(ref_text):
    """'제15조제2항' 등에서 조 단위 키('15', '7의2')만 뽑는다. 항·호는 이 DB 단위가 아니라 버린다."""
    m = ARTICLE_KEY.search(ref_text)
    if not m:
        return None
    return m.group(1) + (f"의{m.group(2)}" if m.group(2) else "")


def resolve(law_name, ref_text, titles, chunks):
    """(document_id, 조번호, 본문) 또는 매칭 실패 시 None."""
    name = _norm(law_name)
    suffix = next((s for s in ("시행규칙", "시행령") if name.endswith(s)), "")
    base = name[:-len(suffix)] if suffix else name
    aliases = {_norm(k): _norm(v) for k, v in LAW_ALIASES.items()}
    doc_id = titles.get(name) or titles.get(aliases.get(base, base) + suffix)
    if not doc_id:
        return None
    key = article_key(ref_text)
    if key is None:
        return None
    body = chunks.get((doc_id, key))
    if body is None:
        return None
    return doc_id, key, body


def resolve_external(rows, law_db_path):
    """cites 중 유형="외부"이고 법령명이 있는 것만 시도해서 연결한다.
    연결되면 상태를 "외부연결"로 바꾸고 대상/본문을 채운다. 반환: (rows, 연결 건수)."""
    titles, chunks = load(law_db_path)
    n = 0
    for r in rows:
        for c in r["cites"]:
            if c["유형"] != "외부" or not c.get("법령명"):
                continue
            hit = resolve(c["법령명"], c["원문"], titles, chunks)
            if hit is None:
                continue
            doc_id, key, body = hit
            c["대상"] = f"{doc_id}_{key}"
            c["상태"] = "외부연결"
            c["외부본문"] = body
            n += 1
    return rows, n
