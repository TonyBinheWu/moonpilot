#!/usr/bin/env bash
set -euo pipefail
lab_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
command -v uv >/dev/null || { echo '請先安裝 uv：https://docs.astral.sh/uv/getting-started/installation/'; exit 1; }
command -v ffmpeg >/dev/null || { echo '缺少 ffmpeg，請安裝 ffmpeg 後再執行。'; exit 1; }
command -v rsync >/dev/null || { echo '缺少 rsync，請安裝 rsync 後再執行。'; exit 1; }
uv sync --project "$lab_root/upstream" --frozen
uv pip install --python "$lab_root/upstream/.venv/bin/python" -r "$lab_root/requirements-ui.txt"
"$lab_root/upstream/.venv/bin/python" -m ipykernel install --user --name distill-spark --display-name '駕駛模型實驗室 · DGX Spark'
"$lab_root/upstream/.venv/bin/python" "$lab_root/lab.py" doctor
echo '啟動介面：upstream/.venv/bin/jupyter lab --ip=127.0.0.1 workflow.ipynb'
