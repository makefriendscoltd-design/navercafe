#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"
export NAVERCAFE_BROWSER_BACKEND=aside
PYTHON="$SCRIPT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

if ! "$PYTHON" - <<'PY'
from aside_browser import check_login
raise SystemExit(0 if check_login("naver").get("logged_in") else 1)
PY
then
  print -u2 -- "Aside 브라우저에서 네이버에 먼저 로그인한 뒤 다시 실행하세요."
  exit 2
fi

while true; do
  print -r -- "[$(date '+%Y-%m-%d %H:%M:%S')] === comment bot start (Aside) ===" >> comment_bot.log
  "$PYTHON" -u comment_bot.py >> comment_bot.log 2>&1 || true
  print -r -- "[$(date '+%Y-%m-%d %H:%M:%S')] === bot stopped, restarting in 30s ===" >> comment_bot.log
  sleep 30
done
