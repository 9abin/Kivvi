"""조·항 단위 청킹. 조 제목 인식, 가지번호·표기 정규화, 항 분리, 삭제 조 표시."""
import io
import json
import os
import re

from config import MANUAL_CHUNKS
from src.preprocess.clean import clean_text

CIRCLED = {c: i for i, c in enumerate("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳", 1)}

# 조 제목: 줄 맨 앞(잡문자 허용, 숫자·목록기호·원문자·괄호 제외) + 제N조[의/-N] (+ 'N조 의 N' 형태)
ARTICLE_HEADER = re.compile(
    r"(?:^|\n)[^\n가-힣0-9①-⑳.()]{0,8}?제(\d+)(?:\s*[의\-]\s*(\d+))?\s*조(?:\s*의\s*(\d+))?"
    r"(?=\s*[\(\s삭])",
    re.M,
)
# 표기 오류 '제9조2(…)' → 제9조의2 로 정규화
TYPO_HEADER = re.compile(r"(?:^|\n)[^\n가-힣0-9①-⑳.()]{0,8}?제(\d+)조(\d)(?=\s*\()", re.M)
BUJIK = re.compile(r"\n\s*(?:[^\n가-힣]{0,8})?부\s*칙")

DUP_NUMBERING = []  # (고유번호, 항 시작 문구, 원문 항번호, 검색용 항번호)


def split_articles(body):
    hits = []
    for m in ARTICLE_HEADER.finditer(body):
        k = m.group(2) or m.group(3) or "0"
        hits.append((m.start(), int(m.group(1)), int(k)))
    for m in TYPO_HEADER.finditer(body):
        hits.append((m.start(), int(m.group(1)), int(m.group(2))))
    hits.sort()
    arts = []
    for i, (start, n, k) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(body)
        arts.append((n, k, body[start:end].strip()))
    return arts


def split_paragraphs(seg, idx):
    """본문을 ①②… 항으로 나눈다. 줄 맨 앞의 원문자만 항 번호로 본다."""
    marks = [(m.start(1), CIRCLED[m.group(1)]) for m in re.finditer(r"(?:^|\n)[ \t]*([①-⑳])(?=\s|[가-힣])", seg)]
    # 줄바꿈 없이 이어진 항(예: '…다.② 보고책임자는…', '…회사③ …')도 찾는다.
    # 단, 줄 맨 앞 항과 합쳐서 번호가 1부터 순서대로 이어질 때만 사용하고, '①, ②' 같은 나열은 제외한다.
    inline = [(m.start(1), CIRCLED[m.group(1)])
              for m in re.finditer(r"(?<=\S)\s*([①-⑳])(?=\s|[가-힣])(?!\s*,)", seg)]
    combined = sorted(set(marks) | set(inline))
    if combined and [n for _, n in combined] == list(range(1, len(combined) + 1)):
        marks = combined
    if not marks:
        return [(1, seg)]
    res = []
    for i, (pos, no) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(seg)
        txt = seg[pos:end].strip()
        if len(re.sub(r"^[①-⑳]", "", txt).strip()) < 2:
            continue
        res.append((no, txt))
    if not res:
        return [(1, seg)]
    if res[0][0] != 1:
        head = seg[:marks[0][0]].strip()
        if head:
            res.insert(0, (1, head))
    # 중복(또는 역순) 항만 다음 번호로 바꾸고, 뒤의 정상 항 번호는 그대로 둔다
    fixed, used, last_orig = [], set(), 0
    for no, txt in res:
        if no in used or (no < last_orig and no != 1):
            new = max(used | {last_orig}) + 1
            DUP_NUMBERING.append((idx, txt[:30], no, new))
            fixed.append((new, txt))
            used.add(new)
        else:
            fixed.append((no, txt))
            used.add(no)
            last_orig = no
    return fixed


def build_chunks(meta, texts):
    """문서별 텍스트 → 청크 목록 (참조 cites는 아직 비어 있음)."""
    rows, seen = [], set()

    def dedupe(cid):
        base_id, n = cid, 2
        while cid in seen:
            cid = f"{base_id}_중복{n}"
            n += 1
        seen.add(cid)
        return cid

    for idx in sorted(meta):
        m = meta[idx]
        txt = clean_text(texts.get(idx, ""))
        parts = BUJIK.split(txt)
        body = parts[0]
        arts = split_articles(body)
        if not arts:
            rows.append({**m, "id": f"{idx}_전문", "조항": "전체", "상태": "정상",
                         "본문": body.strip(), "cites": []})
        for n, k, seg in arts:
            art = f"제{n}조" + (f"의{k}" if k else "")
            if re.search(r"삭제", seg[:40]) and len(seg) < 120:
                rows.append({**m, "id": dedupe(f"{idx}_{art}"), "조항": art, "상태": "삭제",
                             "본문": seg, "cites": []})
                continue
            paras = split_paragraphs(seg, idx)
            for pno, ptxt in paras:
                cid = f"{idx}_{art}" + (f"_{pno}항" if len(paras) > 1 or pno != 1 else "")
                rows.append({**m, "id": dedupe(cid),
                             "조항": art + (f" 제{pno}항" if len(paras) > 1 else ""),
                             "상태": "정상", "본문": ptxt, "cites": []})
        if len(parts) > 1:
            rows.append({**m, "id": dedupe(f"{idx}_부칙"), "조항": "부칙", "상태": "정상",
                         "본문": "부칙\n" + "\n".join(parts[1:]).strip(), "cites": []})
    return _merge_manual(rows)


def _merge_manual(rows):
    """수동 청크 파일이 있는 문서는 자동 청크를 대체한다 (예: 윤리헌장)."""
    if not os.path.exists(MANUAL_CHUNKS):
        return rows
    extra = [json.loads(l) for l in io.open(MANUAL_CHUNKS, encoding="utf-8") if l.strip()]
    manual_docs = {x["고유번호"] for x in extra}
    return [r for r in rows if r["고유번호"] not in manual_docs] + extra
