#!/usr/bin/env bash
set -euo pipefail

# Run on macOS only. PyInstaller builds native binaries and cannot create a
# usable .app bundle from Windows or Linux.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"

ENTRY="main.py"
NAME="GiantessGenerator"

"$PYTHON_BIN" -m venv .venv-macos
source .venv-macos/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

# 单一打包形态：入口跟随设置里的「启动界面模式」，两套界面共用一个进程模型，
# 因此全部依赖（含 networkx 依赖图）一并收集。
pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "$NAME" \
  --add-data "assets:assets" \
  --add-data "data/packs:data/packs" \
  --add-data "data/static:data/static" \
  --collect-all customtkinter \
  --collect-all dearpygui \
  --collect-all PIL \
  --collect-all openai \
  --collect-all networkx \
  --collect-all numpy \
  --collect-all webview \
  "$ENTRY"

echo "Created: $ROOT/dist/$NAME.app"
echo "The bundle includes only built-in content packs. User settings and archives are created under ~/Library/Application Support/GiantessGenerator/."
echo "Graphviz remains an external optional dependency. Install it with: brew install graphviz"
