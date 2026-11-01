#!/bin/bash
# 온톨로지 전체 추출 (규정 2,027조). 세션과 상관없이 돈다. 끊기면 다시 실행하면 이어서 한다 (형식 실패 조는 다시 묻는다).
#   powershell: Start-Process "C:\Program Files\Git\bin\bash.exe" -ArgumentList "scripts/run_full_extract.sh" -WindowStyle Hidden
# 로그: eval/onto_full/run.log
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
mkdir -p eval/onto_full
{
  echo "== 시작 $(date '+%F %T')"
  [ -f eval/onto_full/scope.json ] || python -I scripts/onto_build.py scope eval/onto_full --all
  python -I scripts/onto_build.py extract eval/onto_full
  echo "== 끝 $(date '+%F %T')"
} >> eval/onto_full/run.log 2>&1
