"""과거·시행일 질문 정책에 맞춘 평가셋을 만든다 (원본 answer_eval_100.jsonl은 그대로 둔다).

정책: 과거 기준·시행일·적용례를 물어도 현행 규정으로만 답하고 "현행 규정 기준" 안내를 붙인다 (generate.py 규칙 18).
  - 적용례·경과조치를 직접 묻는 문항(Q169, Q171, Q174)은 뺀다.
  - 현행 내용으로 답할 수 있는 문항(Q163, Q178, Q179, Q181)은 부칙·시행일 근거와 정답요지를 빼고 현행 내용만 남긴다.

실행 (저장소 루트에서): uv run python -m src.eval.make_current_set
"""
import json
import os
import re

from config import BASE_DIR

SRC = os.path.join(BASE_DIR, "eval", "answer_eval_100.jsonl")
DST = os.path.join(BASE_DIR, "eval", "answer_eval_100_current.jsonl")
DROP = {"Q169", "Q171", "Q174"}
NOTICE = "현행 규정 기준으로 답하고, 과거 기준이나 시행일은 담당 부서에 확인하라고 안내한다."
POINTS = {
    "Q163": ["현행 배우자 출산휴가는 20일(유급)이다.", NOTICE],
    "Q178": ["시차출퇴근제는 새로 도입되는 제도가 아니라 현행 유연근무제 운영지침에 이미 있는 제도다.",
             "출근 07:00~10:00, 퇴근 16:00~19:00 범위에서 30분 단위로 선택해 쓸 수 있다.", NOTICE],
    "Q179": ["2026년부터 새로 생기는 것이 아니라 현행 유연근무제 운영지침에 이미 2시간 단위 분할 사용이 있다.",
             "1일을 반일 또는 2시간(반반차) 단위로 분할할 수 있고 반차 2회 또는 반반차 4회가 연차 1일이다.", NOTICE],
    "Q181": ["사적이해관계자 신고·회피 신청은 별지 제1호서식을 작성하여 이해충돌방지담당관에게 서면으로 제출한다.", NOTICE],
}


def main():
    out = []
    for line in open(SRC, encoding="utf-8"):
        if not line.strip():
            continue
        q = json.loads(line)
        if q["id"] in DROP:
            continue
        if q["id"] in POINTS:
            q["근거"] = [e for e in q["근거"] if not re.match(r"^부칙", e.get("조항") or "")]
            q["정답요지"] = POINTS[q["id"]]
            q["비고"] = (q.get("비고") + " | " if q.get("비고") else "") + "현행 기준 정책: 부칙·시행일 근거와 정답요지를 뺐다."
        out.append(q)
    with open(DST, "w", encoding="utf-8") as f:
        for q in out:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    print(f"{len(out)}문항 저장: {DST}")


if __name__ == "__main__":
    main()
