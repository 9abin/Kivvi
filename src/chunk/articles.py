"""조 단위 청킹. 조 제목 인식, 가지번호·표기 정규화, 장·절 제목 분리, 삭제 조 표시.

청크는 조 하나다(항으로 나누지 않는다). 항 번호는 인용 대상 확인용으로 `항번호` 필드에만 남긴다.
장·절 제목 줄은 본문에서 떼어 그 아래 조들의 `장` 필드(예: "제2장 감사의 종류 > 제1절 정기감사")로 옮긴다."""
import io
import json
import os
import re

from config import ANNEX_OVERRIDES, MANUAL_CHUNKS
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
# 부칙 "제목 줄"만 잡는다: 뒤에 공백 다음 줄바꿈이나 괄호(공포일)만 와야 한다.
# "② 부칙 조문에는…", "4. 부칙: …"처럼 문장 중간이나 목록 설명으로 쓰인 "부칙"은 제외한다
# (내규관리규정처럼 부칙의 형식 자체를 설명하는 문서에서 조문이 통째로 삼켜지는 사고를 막는다).
BUJIK = re.compile(r"\n\s*(?:[^\n가-힣]{0,8})?부\s*칙[ \t]*(?=[\(<>\n]|$)")
# 부칙 뒤에 붙는 별표(표)·별지(서식) 제목 줄. 제목 줄만 잡고, "[별표3] … 적용한다."처럼 문장으로 끝나는 줄은 뺀다.
ANNEX_TITLE = re.compile(r"(?m)^[ \t]*(?:■[ \t]*)?[\[\(【<][ \t]*별[ \t]*(표|지)[^\]\)】>\n]{0,20}[\]\)】>][^\n]*")
ANNEX_SENTENCE_END = re.compile(r"다\.\s*$")
ANNEX_NUMBER = re.compile(r"별\s*표\s*(?:제\s*)?(\d+)(?:\s*호)?(?:\s*의\s*(\d+))?")

DUP_NUMBERING = []  # (고유번호, 항 시작 문구, 원문 항번호, 검색용 항번호)

# 장·절 제목 줄 (편 > 장 > 절 > 관). 조 경계가 아니라서 예전에는 바로 앞 조 청크 끝에 붙어 버렸다.
# 줄 전체가 "제N장 제목" 꼴인 줄만 잡는다. "제2장의 규정에 따라 …"처럼 뒤에 글자가 바로 붙거나
# 문장으로 끝나는 줄("…한다.")은 제목이 아니다.
HEADING = re.compile(r"[ \t]*제\s*(\d+)\s*(편|장|절|관)(?:[ \t][^\n]{0,60})?[ \t]*")
HEADING_LEVELS = ("편", "장", "절", "관")


def article_id(chunk_id):
    """예전 항 단위 ID('23583_제8조_2항')를 조 단위 ID('23583_제8조')로 바꾼다. 조 단위 ID는 그대로 둔다.
    평가 정답·보정 목록처럼 항 단위 시절에 적힌 ID를 지금 청크에 맞출 때 쓴다."""
    return re.sub(r"_\d+항(?=$|_)", "", chunk_id)


def _heading(line):
    """제목 줄이면 (단계, 이름), 아니면 None. 이름에서 <개정 …>, [개정 …] 표기와 겹친 공백을 뺀다."""
    m = HEADING.fullmatch(line)
    if not m or re.search(r"다\.\s*$", line):
        return None
    label = re.sub(r"\s+", " ", re.sub(r"<[^>]*>|\[[^\]]*\]", "", line)).strip()
    return m.group(2), label


def headings(body):
    """본칙의 장·절 제목 줄 목록: [(줄 시작 위치, 단계, 이름), ...]"""
    out, pos = [], 0
    for line in body.split("\n"):
        h = _heading(line)
        if h:
            out.append((pos, *h))
        pos += len(line) + 1
    return out


def section_path(heads, pos):
    """pos(조 시작 위치)에 걸리는 장·절 경로. 예: '제2장 감사의 종류 > 제1절 정기감사'. 없으면 빈 문자열.
    윗단계 제목이 새로 나오면 아랫단계는 비운다 (새 장이 시작되면 앞 장의 절은 끝난다)."""
    cur = {}
    for start, level, label in heads:
        if start > pos:
            break
        rank = HEADING_LEVELS.index(level)
        cur = {lv: v for lv, v in cur.items() if HEADING_LEVELS.index(lv) < rank}
        cur[level] = label
    return " > ".join(cur[lv] for lv in HEADING_LEVELS if lv in cur)


def strip_headings(seg):
    """조 본문에 끼어든 장·절 제목 줄을 뗀다."""
    return "\n".join(ln for ln in seg.split("\n") if not _heading(ln)).strip()


def split_articles(body):
    """[(조 번호, 가지 번호, 조 본문), ...]"""
    return [(n, k, seg) for _, n, k, seg in split_articles_at(body)]


def split_articles_at(body):
    """[(본칙 안 시작 위치, 조 번호, 가지 번호, 조 본문), ...]"""
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
        arts.append((start, n, k, body[start:end].strip()))
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


def split_annex(seg):
    """부칙 조각을 (부칙 글, [(종류, 제목 줄, 본문), ...])로 나눈다. 종류는 '표'(별표) 또는 '지'(별지)."""
    marks = [m for m in ANNEX_TITLE.finditer(seg) if not ANNEX_SENTENCE_END.search(m.group(0).rstrip())]
    if not marks:
        return seg, []
    blocks = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(seg)
        blocks.append((m.group(1), m.group(0), seg[m.start():end].strip()))
    return seg[:marks[0].start()], blocks


ANNEX_SPLIT_OVER = 1000   # 별표가 이보다 길면 조각으로 나눈다 (리랭커가 긴 글을 읽는 시간, 검색 단위)
ANNEX_PIECE = 800


def split_long_annex(text):
    """긴 별표를 줄 단위로 ANNEX_PIECE자 안팎 조각으로 나눈다. 둘째 조각부터 맨 위에 별표 제목을 다시 붙인다."""
    if len(text) <= ANNEX_SPLIT_OVER:
        return [text]
    lines = text.split("\n")
    title = lines[0].strip()
    pieces, cur = [], ""
    for ln in lines:
        if cur and len(cur) + len(ln) + 1 > ANNEX_PIECE:
            pieces.append(cur.strip())
            cur = f"{title} (계속)\n"
        cur += ln + "\n"
    if cur.strip():
        pieces.append(cur.strip())
    return pieces


def load_annex_overrides():
    """추출 때 칸이 깨진 별표를 원본 표로 복원한 글. {"문서ID_별표N": {"text": 복원 글, "keep_from": 이 낱말부터는 원문 유지}}"""
    if not os.path.exists(ANNEX_OVERRIDES):
        return {}
    with io.open(ANNEX_OVERRIDES, encoding="utf-8") as f:
        return json.load(f)


def annex_rows(meta_row, idx, blocks, dedupe):
    """별표는 번호별 청크(색인 대상), 별지 서식은 문서당 청크 1개(색인 제외)로 만든다."""
    overrides = load_annex_overrides()
    merged = []
    for kind, title, text in blocks:
        if kind == "지" and merged and merged[-1][0] == "지":
            merged[-1] = ("지", merged[-1][1], merged[-1][2] + "\n" + text)
        else:
            merged.append((kind, title, text))
    rows = []
    for kind, title, text in merged:
        if kind == "지":
            rows.append({**meta_row, "id": dedupe(f"{idx}_별지"), "조항": "별지서식", "상태": "정상",
                         "본문": text, "cites": []})
            continue
        m = ANNEX_NUMBER.search(title)
        art = "별표" + (m.group(1) + (f"의{m.group(2)}" if m.group(2) else "") if m else "")
        status = "삭제" if re.search(r"삭제", text[:40]) and len(text) < 120 else "정상"
        fix = overrides.get(f"{idx}_{art}")
        if fix:
            keep = fix.get("keep_from")
            tail = text[text.index(keep):] if keep and keep in text else ""
            text = fix["text"].rstrip() + ("\n" + tail if tail else "")
        pieces = split_long_annex(text)
        for k, piece in enumerate(pieces, start=1):
            cid = f"{idx}_{art}" if len(pieces) == 1 else f"{idx}_{art}_{k:02d}"
            rows.append({**meta_row, "id": dedupe(cid), "조항": art, "상태": status,
                         "본문": piece, "cites": []})
    return rows


def is_deletion_only(text):
    """항 본문이 번호와 삭제 표기뿐이면 True. 예: '④ 삭제 <2025.04.17.>', '② (삭제) <삭제 2015.06.25.>', '① <삭제>'.
    남은 글이 조금이라도 있으면 살아 있는 항이므로 False다."""
    rest = re.sub(r"<[^>]*>|\([^)]*\)|\[[^\]]*\]", "", text)   # <개정·삭제 날짜>, (삭제), [2014.12.29. 개정]
    rest = re.sub(r"[①-⑳]|삭제", "", rest)               # 항 번호, '삭제' 글자
    return bool(re.search(r"삭제", text)) and not re.search(r"[가-힣A-Za-z0-9]", rest)


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
        heads = headings(body)
        arts = split_articles_at(body)
        if not arts:
            rows.append({**m, "id": f"{idx}_전문", "조항": "전체", "상태": "정상", "장": "", "항번호": [],
                         "본문": strip_headings(body), "cites": []})
        for pos, n, k, seg in arts:
            art = f"제{n}조" + (f"의{k}" if k else "")
            seg = strip_headings(seg)
            base = {**m, "id": dedupe(f"{idx}_{art}"), "조항": art, "장": section_path(heads, pos)}
            if re.search(r"삭제", seg[:40]) and len(seg) < 120:
                rows.append({**base, "상태": "삭제", "항번호": [], "본문": seg, "cites": []})
                continue
            paras = split_paragraphs(seg, idx)
            numbered = len(paras) > 1 or paras[0][0] != 1
            # 항이 모두 '④ 삭제 <날짜>' 꼴이면 조 전체가 삭제된 것과 같다
            dead = numbered and all(is_deletion_only(t) for _, t in paras)
            rows.append({**base, "상태": "삭제" if dead else "정상",
                         "항번호": [no for no, _ in paras] if numbered else [],
                         "본문": seg, "cites": []})
        if len(parts) > 1:
            heads, blocks = [], []
            for p in parts[1:]:
                head, b = split_annex(p)
                heads.append(head)
                blocks += b
            bujik = "\n".join(parts[1:]) if not blocks else "\n".join(h.strip() for h in heads if h.strip())
            rows.append({**m, "id": dedupe(f"{idx}_부칙"), "조항": "부칙", "상태": "정상",
                         "본문": "부칙\n" + bujik.strip(), "cites": []})
            rows += annex_rows(m, idx, blocks, dedupe)
    return _merge_manual(rows)


def _merge_manual(rows):
    """수동 청크 파일이 있는 문서는 자동 청크를 대체한다 (예: 윤리헌장)."""
    if not os.path.exists(MANUAL_CHUNKS):
        return rows
    extra = [json.loads(l) for l in io.open(MANUAL_CHUNKS, encoding="utf-8") if l.strip()]
    manual_docs = {x["고유번호"] for x in extra}
    return [r for r in rows if r["고유번호"] not in manual_docs] + extra
