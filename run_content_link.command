#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x .venv312/bin/python ]]; then
  echo "[오류] Python 3.12 환경이 없습니다."
  exit 1
fi

.venv312/bin/python content_workflow_preflight.py --runtime --json

echo "[중단] 이 레거시 단일 실행기는 3채널 품질·독립 발행 계약을 보장하지 않습니다."
echo "유튜브 링크는 이 Orca 작업방에 요청해 AGENTS.md의 독립 Task 흐름으로 처리하세요."
exit 2
