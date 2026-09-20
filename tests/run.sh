#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONIOENCODING=utf-8

bash -n "$ROOT/install/install.sh" "$ROOT/tests/test_install.sh"
node --check "$ROOT/automation/quickadd/rollup.js"
node --check "$ROOT/tests/test_rollup.js"

bash "$ROOT/tests/test_install.sh"
node "$ROOT/tests/test_rollup.js"
python "$ROOT/tests/test_skills.py"
python "$ROOT/tests/test_query.py"
python "$ROOT/tests/test_monitor.py"
python "$ROOT/tests/test_knowledge_delta.py"
python "$ROOT/tests/test_python_tools.py"
python "$ROOT/tests/test_paper_summarizer.py"

echo "All regression checks passed."
