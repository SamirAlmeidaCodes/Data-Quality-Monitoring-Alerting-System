#!/usr/bin/env bash
# Scan pinned requirements for known vulnerabilities.
# Uses pip-audit. safety check -r requirements.txt is an alternative.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -x .venv/bin/python ]]; then
  PY=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  PY=python
fi

"$PY" -m pip install --disable-pip-version-check --quiet pip-audit
"$PY" -m pip_audit --requirement requirements.txt --requirement requirements-dev.txt
