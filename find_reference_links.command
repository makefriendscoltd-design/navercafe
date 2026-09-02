#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
exec .venv312/bin/python reference_link_routine.py "$@"
