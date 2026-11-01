#!/bin/bash
set -euo pipefail

QUANTVIEW_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /bin/bash "$QUANTVIEW_ROOT/scripts/start_ui.sh"
