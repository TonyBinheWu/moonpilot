#!/usr/bin/env bash
set -euo pipefail
lab_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$lab_root"
export ORT_DISABLE_TELEMETRY=1
export HF_HUB_DISABLE_TELEMETRY=1
export WANDB_MODE=offline
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
if [[ ! -x upstream/.venv/bin/python ]]; then
  echo '找不到專案環境，請先執行 bash bootstrap.sh。'
  exit 1
fi
exec upstream/.venv/bin/python -m streamlit run webui.py --server.address=127.0.0.1 --server.port=8501 --server.headless=true
