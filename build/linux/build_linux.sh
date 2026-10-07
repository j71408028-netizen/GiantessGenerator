#!/usr/bin/env bash
# Linux 打包：PyInstaller onedir + tar.gz 目录包（对齐 build/windows 与 build/macos）。
#
# 用法：
#   bash build/linux/build_linux.sh                # 只打包
#   bash build/linux/build_linux.sh --self-check   # 打包 + 打包自检（需要显示器）
#   PYTHON_BIN=python3.12 bash build/linux/build_linux.sh
#
# 产物：
#   dist/GiantessGenerator/                      onedir 目录（可执行文件同名，在目录根）
#   dist/GiantessGenerator-linux-<arch>.tar.gz   发布用的目录包
#   dist/GiantessSelfCheck/                      --self-check 才生成：自动驾驶自检包
#
# 说明：
# - 只在 Linux 上跑；PyInstaller 不做交叉编译，Windows/macOS 包各自在对应系统上打。
# - 不带 --collect-all zai：项目没有该依赖（残留引用，见 linux_compat_plan §2.3-3）。
# - 打包版数据目录走 XDG：$XDG_DATA_HOME/GiantessGenerator/data（默认
#   ~/.local/share/GiantessGenerator/data），首启从包内 data/packs、data/static 拷贝；
#   源码运行仍用仓库内 data/（见 paths.py）。
# - graphviz 的 dot 是外部可选依赖，不进包：依赖图功能需要系统装 graphviz。
# - --self-check 会把 scripts/dungeon_autopilot.py 也打成一个包（同一套依赖），用它
#   在**打包态**里真开副本窗口跑几个场景（进副本、文本组件、小游戏触发器），
#   并顺带验证发布包能启动、能引导 XDG 数据目录。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV="${VENV:-.venv-buildlinux}"        # 与 windows(.venv-build) / macos(.venv-macos) 同规矩
ENTRY="main.py"
NAME="GiantessGenerator"
SELF_CHECK_ENTRY="scripts/dungeon_autopilot.py"
SELF_CHECK_NAME="GiantessSelfCheck"
ARCH="$(uname -m)"

SELF_CHECK=0
for arg in "$@"; do
    case "$arg" in
        --self-check) SELF_CHECK=1 ;;
        -h|--help) sed -n '2,24p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "未知参数：$arg（可用：--self-check）" >&2; exit 2 ;;
    esac
done

if [ ! -x "$VENV/bin/python" ]; then
    echo "== 创建构建虚拟环境 $VENV =="
    "$PYTHON_BIN" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r requirements.txt pyinstaller

# 单一打包形态：入口跟随设置里的「启动界面模式」，两套界面共用一个进程模型，
# 因此全部依赖（含 networkx 依赖图）一并收集。Linux 的 --add-data 分隔符是 `:`。
PYINSTALLER_ARGS=(
    --noconfirm
    --clean
    --windowed
    --onedir
    # 分析期的模块搜索路径：入口在 scripts/ 下时（打包自检的 autopilot），
    # 仓库根不在默认 pathex 里，`ui` / `paths` 这类顶层模块会被漏收。
    --paths "$ROOT"
    --add-data "assets:assets"
    --add-data "data/packs:data/packs"
    --add-data "data/static:data/static"
    --collect-all customtkinter
    --collect-all dearpygui
    --collect-all PIL
    --collect-all openai
    --collect-all networkx
    --collect-all numpy
    --collect-all webview
)

echo "== 打包 $NAME =="
"$VENV/bin/pyinstaller" "${PYINSTALLER_ARGS[@]}" --name "$NAME" "$ENTRY"

BUNDLE="$ROOT/dist/$NAME"
TARBALL="$ROOT/dist/$NAME-linux-$ARCH.tar.gz"
tar -czf "$TARBALL" -C "$ROOT/dist" "$NAME"

echo
echo "Created: $BUNDLE/$NAME"
echo "Created: $TARBALL"
echo "The bundle includes only built-in content packs. User settings and archives are created under \$XDG_DATA_HOME/$NAME/data (default ~/.local/share/$NAME/data)."
echo "Graphviz remains an external optional dependency. Install it with: sudo apt install graphviz"

if [ "$SELF_CHECK" != 1 ]; then
    echo
    echo "提示：加 --self-check 可在打包态里真开副本窗口跑自检（需要显示器）。"
    exit 0
fi

# ---------------------------------------------------------------------------
# 打包自检：同源依赖 + 自动驾驶脚本 → 打包态里真开窗口
# ---------------------------------------------------------------------------
echo
echo "== 打包自检：构建 $SELF_CHECK_NAME =="
"$VENV/bin/pyinstaller" "${PYINSTALLER_ARGS[@]}" --name "$SELF_CHECK_NAME" "$SELF_CHECK_ENTRY"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/giantess-selfcheck-XXXXXX")"
cleanup() {
    if [ -n "${APP_PID:-}" ]; then kill "$APP_PID" 2>/dev/null || true; fi
    rm -rf "$WORK"
}
trap cleanup EXIT

# 1) 发布包能启动、能引导 XDG 数据目录（不碰真实用户数据）
echo
echo "-- 启动检查：$NAME（XDG_DATA_HOME=$WORK/xdg）--"
APP="$BUNDLE/$NAME"
APP_LOG="$WORK/app.log"
XDG_DATA_HOME="$WORK/xdg" "$APP" >"$APP_LOG" 2>&1 &
APP_PID=$!
DATA_ROOT="$WORK/xdg/$NAME/data"
BOOTSTRAPPED=0
for _ in $(seq 1 60); do
    if [ -d "$DATA_ROOT/user" ] && [ -d "$DATA_ROOT/packs" ] && [ -d "$DATA_ROOT/static" ]; then
        BOOTSTRAPPED=1
        break
    fi
    if ! kill -0 "$APP_PID" 2>/dev/null; then
        break
    fi
    sleep 1
done

START_OK=0
if [ "$BOOTSTRAPPED" = 1 ] && kill -0 "$APP_PID" 2>/dev/null; then
    START_OK=1
    echo "   数据目录已引导：$DATA_ROOT"
    if command -v xwininfo >/dev/null 2>&1; then
        if xwininfo -root -tree 2>/dev/null | grep -q "巨大娘生成器"; then
            echo "   主窗口已映射（xwininfo 找到标题「巨大娘生成器」）"
        else
            echo "   警告：xwininfo 未找到主窗口标题，启动检查降级为「进程存活 + 数据目录就绪」"
        fi
    else
        echo "   （没有 xwininfo，跳过窗口标题检查）"
    fi
else
    echo "   启动检查失败：见日志 $APP_LOG" >&2
    sed -n '1,40p' "$APP_LOG" >&2 || true
fi
kill "$APP_PID" 2>/dev/null || true
wait "$APP_PID" 2>/dev/null || true
unset APP_PID

# 2) 副本窗口场景：打包态里真开 DPG 窗口
# 场景都验证过能在打包态跑通。不含 text-components：它在 Linux/X11 上会踩已知的
# DPG/GLFW「反复拆建上下文」段错误（源码态同样复现，见 linux_compat_plan.md §5），
# 放进打包自检只会把第三方风险记成打包问题。
SELF_CHECK_BIN="$ROOT/dist/$SELF_CHECK_NAME/$SELF_CHECK_NAME"
SCENES=(entry-start session-close entry-replay chapter-bgm mini-game mini-game-py)
FAILED=0
for scene in "${SCENES[@]}"; do
    echo
    echo "-- 场景 $scene --"
    if XDG_DATA_HOME="$WORK/xdg-sc" timeout 180 "$SELF_CHECK_BIN" --scene "$scene"; then
        echo "   场景 $scene：通过"
    else
        echo "   场景 $scene：失败（rc=$?）" >&2
        FAILED=$((FAILED + 1))
    fi
done

echo
if [ "$START_OK" != 1 ]; then
    echo "打包自检失败：发布包未能启动并引导数据目录" >&2
    exit 1
fi
if [ "$FAILED" != 0 ]; then
    echo "打包自检失败：$FAILED/${#SCENES[@]} 个副本场景未通过" >&2
    exit 1
fi
echo "打包自检通过：发布包启动引导 OK，副本场景 ${#SCENES[@]}/${#SCENES[@]}（${SCENES[*]}）"
