#!/bin/bash
set -euo pipefail

QUANTVIEW_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QUANTVIEW_PYTHON="$QUANTVIEW_ROOT/.venv/bin/python"

if [[ ! -x "$QUANTVIEW_PYTHON" ]]; then
    printf '找不到项目运行环境：%s\n请先按照 README 安装依赖。\n' "$QUANTVIEW_PYTHON" >&2
    exit 1
fi

cd "$QUANTVIEW_ROOT"
export PYTHONPATH="$QUANTVIEW_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

printf '正在启动 QuantView2027\n地址：http://127.0.0.1:8517\n请保持此终端窗口运行；按 Control+C 停止服务。\n\n'
exec "$QUANTVIEW_PYTHON" -m streamlit run \
    "$QUANTVIEW_ROOT/src/quantview/ui/app.py" \
    --server.address=127.0.0.1 \
    --server.port=8517 \
    --server.headless=true \
    --browser.gatherUsageStats=false
