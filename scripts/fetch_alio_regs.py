"""다른 공공기관의 내부규정을 ALIO에서 받는다 (기관 간 비교 측정용, 한 번 쓰는 스크립트).

  uv run python scripts/fetch_alio_regs.py --apba C0451 --name 창업진흥원
결과: data/others/{기관}/{규정명}_{ALIO 규정번호}/{파일번호}_{파일명} — KVIC 원문(data/한국벤처투자_내부규정)과 같은 모양이라 parse 그대로 쓴다
ALIO의 문서화되지 않은 내부 주소를 쓴다(alio-mcp README의 형식). 제품 기능이 아니라 일회성 측정이라 의존성으로 두지 않는다.
원문은 각 기관 저작물이라 data/ 아래(git 제외)에만 둔다. 요청 사이 0.5초 쉰다.
"""

import argparse
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://www.alio.go.kr"


def unpack(folder: Path) -> None:
    """확장자는 .hwp인데 실제로는 zip인 파일: 최근 5년 개정판 묶음이면 가장 최신판(앞 번호가 가장 큰 것)만 꺼내고 묶음은 .zip으로,
    HWPX(안에 Contents/)면 확장자만 .hwpx로 고친다. 파서는 폴더 안 파일 하나를 읽는다."""
    import zipfile
    for f in list(folder.iterdir()):
        if not f.is_file() or f.read_bytes()[:2] != b"PK":
            continue
        if f.suffix == ".zip" and any(p.suffix.lower() in (".hwp", ".hwpx", ".pdf") for p in folder.iterdir()):
            continue   # 이미 최신판을 꺼낸 묶음
        z = zipfile.ZipFile(f)
        names = z.namelist()
        if any(n.startswith("Contents/") for n in names):
            z.close()   # 윈도우는 열린 파일 이름을 못 바꾼다
            f.rename(f.with_suffix(".hwpx"))
            continue
        docs = [n for n in names if re.search(r"\.(hwpx?|pdf)$", n, re.I)]
        if docs:
            def key(n):
                base = n.rsplit("/", 1)[-1]
                d = re.search(r"(\d{4})년도?\s*(\d{1,2})월(?:\s*(\d{1,2})일)?", base)   # 「(2022년도 11월 16일 개정)」
                if d:
                    return (int(d.group(1)) * 10000 + int(d.group(2)) * 100 + int(d.group(3) or 0), base)
                d = re.search(r"\((\d{2})(\d{2})(\d{2})\)", base)   # 「(221228)」
                if d:
                    return (20000000 + int(d.group(1)) * 10000 + int(d.group(2)) * 100 + int(d.group(3)), base)
                m = re.match(r"(\d+)\.", base)   # 앞 번호만 있으면 번호 순
                return (int(m.group(1)) if m else -1, base)
            latest = max(docs, key=key)
            (folder / ("최신판_" + latest.rsplit("/", 1)[-1])).write_bytes(z.read(latest))
        z.close()
        if f.suffix != ".zip":
            f.rename(f.with_suffix(".zip"))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--apba", required=True, help="ALIO 기관 ID (예: C0451)")
    ap.add_argument("--name", required=True, help="폴더 이름으로 쓸 기관명")
    a = ap.parse_args()
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": BASE + "/"})
    out = ROOT / "data" / "others" / a.name
    out.mkdir(parents=True, exist_ok=True)
    rows, page = [], 1
    while True:
        d = s.post(f"{BASE}/item/itemReportListSusi.json", timeout=60,
                   json={"pageNo": page, "apbaId": a.apba, "apbaType": "", "reportFormRootNo": "21110", "search_word": "",
                         "search_flag": "title", "bid_type": "", "enfc_istt": ""}).json()["data"]
        rows += d["result"]
        if page >= d["page"]["totalPage"]:
            break
        page += 1
        time.sleep(0.5)
    print(f"{a.name}: 규정 {len(rows)}개", flush=True)
    got = 0
    for r in rows:
        title = re.sub(r'[\\/:*?"<>|]', "_", r["title"]).strip()
        folder = out / f"{title}_{r['idx']}"
        if folder.exists() and any(folder.iterdir()):
            got += 1
            continue
        html = s.get(f"{BASE}/item/itemBoard21110.do", timeout=60,
                     params={"apbaId": a.apba, "table_name": r["tableName"], "idx_name": r["idxName"], "idx": r["idx"],
                             "reportGbn": r["reportGbn"], "bid_type": r["bidType"]}).text
        # 상세 화면에는 과거 개정판이 모두 걸려 있다(예: 임직원 행동강령 2019~2024년 7개 판). 파일 번호가 가장 큰 것이 최신판
        links = []
        for m in re.finditer(r"rulefiledown\.json\?fileNo=(\d+)", html):
            after = re.sub(r"<[^>]+>", " ", html[m.end():m.end() + 400])
            nm = re.search(r"[^<>\"'/]{1,150}\.(?:hwpx?|pdf|docx|zip)", after, re.I)
            links.append((int(m.group(1)), nm.group(0).strip() if nm else f"{title}.bin"))
        if not links:
            print(f"  파일 없음: {r['title']}")
            continue
        file_no, fname = max(links)
        time.sleep(0.5)
        resp = s.get(f"{BASE}/download/rulefiledown.json", params={"fileNo": file_no}, timeout=120)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{file_no}_{re.sub(r'[\\/:*?\"<>|]', '_', fname)}").write_bytes(resp.content)
        unpack(folder)
        got += 1
        time.sleep(0.5)
    print(f"{a.name}: 받음 {got}/{len(rows)} → {out}")


if __name__ == "__main__":
    main()
