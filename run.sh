#!/bin/bash
# =============================================
#  2026 FIFA World Cup Desktop Overlay
#  一键安装和启动脚本
# =============================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "⚽ 2026世界杯桌面悬浮窗"
echo "========================"

# Step 1: 优先使用 WorkBuddy 管理的 Python 3.13
# （避免被系统 Python 3.14 劫持导致 PyQt5 崩溃）
WB_PYTHON="$HOME/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
if [ -x "$WB_PYTHON" ]; then
    PYTHON="$WB_PYTHON"
else
    # 回退：找 python3 / python
    if command -v python3 &>/dev/null; then
        PYTHON=$(command -v python3)
    elif command -v python &>/dev/null; then
        PYTHON=$(command -v python)
    else
        echo "❌ 未找到 Python3，请先安装 Python 3.8+"
        echo "   macOS: brew install python3"
        echo "   或访问: https://www.python.org/downloads/"
        exit 1
    fi
fi

echo "📦 Python: $PYTHON ($($PYTHON --version 2>&1))"

# Step 2: Create virtual environment (if not exists)
if [ ! -d "$VENV_DIR" ]; then
    echo "🔧 创建虚拟环境..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

# Step 3: Activate and install dependencies
source "$VENV_DIR/bin/activate"
echo "📥 安装依赖..."
pip install --quiet PyQt5 requests

# Step 4: Run
echo "🔥 启动应用..."
echo "   提示: 右键菜单可设置 | 拖动标题栏移动窗口 | Esc 退出"
echo ""
cd "$SCRIPT_DIR"
python main.py
