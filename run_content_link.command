#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x .venv312/bin/python ]]; then
  echo "[오류] Python 3.12 환경이 없습니다."
  exit 1
fi

if [[ $# -gt 0 ]]; then
  youtube_url="$1"
else
  printf '유튜브 링크: '
  IFS= read -r youtube_url
fi

if [[ -z "${youtube_url}" ]]; then
  echo "[오류] 유튜브 링크가 비어 있습니다."
  exit 1
fi

# YouTube 링크 한 개는 카페·카드뉴스·실제 쇼츠 3종 모두 검증 후 바로 발행한다.
# 세 채널 중 하나라도 provider 완료 확인이 없으면 비정상 종료한다.
exec .venv312/bin/python youtube_cardnews_pipeline.py "$youtube_url" \
  --publish-policy immediate \
  --youtube-expected-channel "나민수 AI"
