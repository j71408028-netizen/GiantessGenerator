#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 git tag 打出本平台发行包与源码归档（v1.0.0 起各版本复用）。

设计目标：**一次发布只依赖一个 tag**。工作副本是否脏、当前在哪个分支、那个 tag
里是什么目录结构，都不影响结果——脚本在临时目录里为 tag 建一份干净 worktree，
按 **该 tag 自己的** ``requirements.txt`` 装依赖并构建。因此同一份脚本既能打
v1.0.0（重构前的扁平旧结构、无 numpy/webview），也能打之后任何版本。

产物统一落在 ``dist/release/``：::

    GiantessGenerator-<version>-<os>-<arch>.zip   运行时包（PyInstaller onedir 整目录）
    GiantessGenerator-<version>-src.zip           源码归档（剔除作者本机数据与缓存）
    RELEASE-NOTES-<version>.md                    发布说明（tag 注记 + 构建环境 + 产物清单）
    SHA256SUMS.txt                                上述产物的 SHA256

用法::

    python build/release/package.py --tag v1.0.0
    python build/release/package.py --tag v1.0.0 --backend nuitka   # 换 Nuitka 后端对照
    python build/release/package.py --tag v1.0.0 --skip-build       # 只出源码归档
    python build/release/package.py --tag v1.0.0 --keep             # 保留暂存目录排错
    python build/release/package.py                                 # 自动取 HEAD 上的 tag

打包后端：默认 ``pyinstaller``（onedir）；``--backend nuitka`` 走 Nuitka ``--standalone``。
两者产物结构相似（一个目录：可执行文件 + 依赖），产物名带 ``-nuitka`` 后缀以示区分，
便于同一 tag 打出两版做体积 / 启动对照。

平台说明：两种后端都**不做交叉编译**——Windows / macOS / Linux 的运行时包必须各自
在对应系统上打；本脚本自动识别当前平台并据此命名产物。也就是说同一个 tag 的完整
发布 = 在三个系统上各跑一次本脚本，把 dist/release/ 里的文件汇总到一起。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 产品名（决定 exe / app / 目录名）。
DEFAULT_NAME = "GiantessGenerator"

#: 入口文件候选（按顺序取第一个存在的）。
ENTRY_CANDIDATES = ("main.py",)

#: 需要 ``--collect-all`` 的第三方包（按 **import 名** 写）。实际只对**该版本装得上**
#: 的生效，所以同一份清单能同时适配 v1.0.0（无 numpy/webview）与更新版本。
COLLECT_ALL_IMPORTS = (
    "customtkinter", "dearpygui", "PIL", "openai",
    "networkx", "graphviz", "numpy", "webview",
)

#: 随包发布的只读资源目录（存在才收）。用户数据（data/user、data/archives）**不进包**：
#: 打包版首次启动时会自行在系统用户数据目录里生成。
DATA_DIRS = ("assets", "data/packs", "data/static")

#: 图标候选（按顺序取第一个存在的）。Windows 用 .ico；macOS 要 .icns，缺省时退回
#: PyInstaller 默认图标（本仓暂无 .icns）。
ICON_CANDIDATES = ("assets/icons/icon.ico", "assets/icon.ico")

#: 打包后端。``pyinstaller`` 产出 onedir 目录（默认）；``nuitka`` 产出 standalone 目录。
BACKENDS = ("pyinstaller", "nuitka")

#: Nuitka 构建时注入的引导入口文件名（写进临时 worktree，不随源码归档发布）。
NUITKA_ENTRY_NAME = "_gg_nuitka_entry.py"

#: Nuitka 语义补丁源码。Nuitka **不会**像 PyInstaller 那样设置 ``sys.frozen`` /
#: ``sys._MEIPASS``，而本应用的 ``paths.py`` 依赖这两个属性判断"是否打包运行"
#: 并定位只读资源；不打补丁会退化成"源码运行"分支，用户数据会被写进安装目录。
#: 这里在任何应用模块被导入之前补齐语义，使 Nuitka 包与 PyInstaller 包表现一致。
NUITKA_ENTRY_SOURCE = '''\
"""构建期注入的 Nuitka 引导入口（仅随 Nuitka 包发布，不进入仓库）。"""
import os
import sys

sys.frozen = True                                                  # noqa: B010
sys._MEIPASS = os.path.dirname(os.path.abspath(sys.executable))    # noqa: SLF001

import main  # noqa: E402

if __name__ == "__main__":
    main.main()
'''


#: 源码归档里剔除的路径前缀（作者本机数据与工具产物；其余未跟踪文件本就不在
#: ``git archive`` 里）。
SOURCE_EXCLUDE_PREFIXES = (
    "data/user/", "data/archives/",
    "developer_tools/_out/", "scripts/_out/",
)
#: 源码归档里剔除的文件后缀 / 目录名。
SOURCE_EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".bak", ".tmp")
SOURCE_EXCLUDE_DIRNAMES = {"__pycache__"}


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd, cwd=None) -> None:
    printable = " ".join(str(c) for c in cmd)
    log(f"  $ {printable}")
    subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None, check=True)


# --------------------------------------------------------------------------
# tag / worktree / 虚拟环境
# --------------------------------------------------------------------------

def resolve_tag(explicit: str | None) -> str:
    """确定要发布的 tag：显式给出优先，否则取 HEAD 上正好指着的 tag。"""
    if explicit:
        r = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{explicit}"],
            cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"找不到 tag：{explicit}（用 `git tag -l` 看看）")
        return explicit
    r = subprocess.run(
        ["git", "describe", "--tags", "--exact-match", "HEAD"],
        cwd=ROOT, capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    raise SystemExit("未指定 --tag，且 HEAD 不在任何 tag 上；请显式 `--tag vX.Y.Z`")


def add_worktree(work: Path, tag: str) -> Path:
    """为 tag 建一份独立 worktree（不动当前工作副本）。"""
    src = work / "src"
    # 上一次运行若用 --keep 保留了暂存目录，之后又有人手工删了目录，git 仍会保留
    # 该 worktree 的登记项，导致本次 `worktree add` 报 "already registered"。先 prune
    # 清掉失效登记，使脚本可重复运行。
    subprocess.run(["git", "worktree", "prune"], cwd=ROOT, capture_output=True, text=True)
    run(["git", "worktree", "add", "--detach", str(src), tag], cwd=ROOT)
    return src


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def make_venv(work: Path, python_bin: str) -> Path:
    venv = work / "venv"
    run([python_bin, "-m", "venv", str(venv)])
    return venv_python(venv)


def preflight_build_env(vpy: Path) -> None:
    """构建环境体检：虚拟环境里必须有 tkinter，否则立刻停。

    这个坑必须拦在**装依赖之前**：某些解释器发行版（便携版 / standalone 构建）
    不含 Tk，``python -m venv`` 照样成功、依赖也照装，直到用户双击打出来的包，
    才在启动瞬间炸 ``ModuleNotFoundError: No module named 'tkinter'``——本应用的
    界面正是 Tk，整个包等于废的。宁可在几十秒内大声失败。
    """
    probe = "import tkinter; print(tkinter.TkVersion)"
    r = subprocess.run([str(vpy), "-c", probe], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(
            "构建环境缺少 tkinter —— 本应用界面基于 Tk，打包用的解释器必须带 Tk。\n"
            "请换一个带 Tk 的解释器重试：\n"
            f"    python {Path(__file__).name} --tag <tag> --python <带 Tk 的 python>\n"
            f"（当前解释器：{sys.executable}）"
        )
    log(f"   构建环境 tkinter {r.stdout.strip()}")


def detect_collect_targets(vpy: Path) -> list[str]:
    """挑出该版本真正装上了的、需要 --collect-all 的包。"""
    lines = [
        "import importlib.util, json",
        f"names = {list(COLLECT_ALL_IMPORTS)!r}",
        "out = []",
        "for n in names:",
        "    try:",
        "        if importlib.util.find_spec(n):",
        "            out.append(n)",
        "    except Exception:",
        "        pass",
        "print(json.dumps(out))",
    ]
    r = subprocess.run([str(vpy), "-c", "\n".join(lines)],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------
# 构建
# --------------------------------------------------------------------------

def find_entry(src: Path) -> Path:
    for rel in ENTRY_CANDIDATES:
        if (src / rel).is_file():
            return src / rel
    raise SystemExit(f"在 {src} 找不到入口文件（候选：{', '.join(ENTRY_CANDIDATES)}）")


def find_icon(src: Path) -> Path | None:
    if os.name != "nt":          # Linux 忽略图标；macOS 需要 .icns，本仓暂无
        return None
    for rel in ICON_CANDIDATES:
        if (src / rel).is_file():
            return src / rel
    return None


def build_bundle(vpy: Path, src: Path, name: str, targets: list[str],
                 console: bool = False) -> Path:
    """用 PyInstaller onedir 构建，返回 dist/<name> 目录。"""
    venv_dir = vpy.parent.parent
    pyinstaller = venv_dir / ("Scripts/pyinstaller.exe" if os.name == "nt" else "bin/pyinstaller")
    sep = ";" if os.name == "nt" else ":"
    args = [
        pyinstaller, "--noconfirm", "--clean",
        "--console" if console else "--windowed", "--onedir",
        "--name", name,
        # 入口不在仓库根时（旧结构在根、将来未必）也能收全顶层模块。
        "--paths", str(src),
    ]
    icon = find_icon(src)
    if icon:
        args += ["--icon", str(icon)]
    for pkg in targets:
        args += ["--collect-all", pkg]
    for rel in DATA_DIRS:
        if (src / rel).is_dir():
            args += ["--add-data", f"{src / rel}{sep}{rel}"]
    args.append(find_entry(src))
    run(args, cwd=src)
    bundle = src / "dist" / name
    if not bundle.is_dir():
        raise SystemExit(f"构建结束但没找到产物目录：{bundle}")
    return bundle


def build_bundle_nuitka(vpy: Path, src: Path, name: str, version: str,
                        console: bool = False) -> Path:
    """用 Nuitka ``--standalone`` 构建，返回 ``dist/<name>.dist`` 目录。

    与 PyInstaller 版的关键差异：

    - 需要 C 编译器。Windows 上若无 MSVC / MinGW（且 Python ≥ 3.12 用不了
      ``--mingw64``），用 ``--zig`` 让 Nuitka 自动下载 Zig 作为 C 编译器，
      ``--assume-yes-for-downloads`` 免去交互确认。
    - 用注入的引导入口补齐 ``sys.frozen`` / ``sys._MEIPASS`` 语义（见
      ``NUITKA_ENTRY_SOURCE``），否则应用会误判为"源码运行"。
    - Tk 界面必须 ``--enable-plugin=tk-inter``，否则包里缺 Tcl/Tk 运行时而无法启动。
    """
    entry = src / NUITKA_ENTRY_NAME
    entry.write_text(NUITKA_ENTRY_SOURCE, encoding="utf-8")
    # Nuitka 的中间目录可达数百 MB；把它放在 worktree **之外**，源码树保持干净，
    # 也避免构建产物与 git worktree 管理文件相互干扰。
    out_dir = src.parent / "nuitka-out"
    out_dir.mkdir(parents=True, exist_ok=True)
    exe_name = f"{name}.exe" if os.name == "nt" else name
    args = [
        vpy, "-m", "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",   # 自动获取 Zig（本机无 MSVC/MinGW 时的 C 编译器）
        "--remove-output",              # 成功后清掉 *.build 中间目录
        "--enable-plugin=tk-inter",     # 界面是 Tk，必须带上 Tcl/Tk 运行时
        # 注意：Nuitka 的取值选项必须写成 ``--opt=value``，空格分隔会被判为缺参。
        f"--output-dir={out_dir}",
        f"--output-filename={exe_name}",
    ]
    if os.name == "nt":
        args += [
            "--enable-plugin=multiprocessing",  # 启动屏跑在独立子进程里
            "--windows-console-mode=%s" % ("force" if console else "disable"),
            f"--product-name={name}",
            f"--file-version={_dotted_version(version)}",
            f"--product-version={_dotted_version(version)}",
        ]
        icon = find_icon(src)
        if icon:
            args += [f"--windows-icon-from-ico={icon}"]
    for rel in DATA_DIRS:
        if (src / rel).is_dir():
            args += [f"--include-data-dir={src / rel}={rel}"]
    args.append(str(entry))
    run(args, cwd=src)
    dists = sorted(out_dir.glob("*.dist"), key=lambda p: p.stat().st_mtime)
    if not dists:
        raise SystemExit(f"构建结束但没找到 Nuitka 产物目录：{out_dir}/*.dist")
    return dists[-1]


def _dotted_version(version: str) -> str:
    """把 ``1.0.0`` 归一成 Nuitka 版本资源要求的四段式 ``1.0.0.0``。"""
    parts = [p for p in version.split(".") if p.isdigit()]
    while len(parts) < 4:
        parts.append("0")
    return ".".join(parts[:4])



# --------------------------------------------------------------------------
# 归档
# --------------------------------------------------------------------------

def zip_tree(root: Path, archive: Path, arc_prefix: str = "") -> int:
    """把 root 目录整棵树压成 zip，返回文件数。"""
    archive.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                zf.write(p, arc_prefix + p.relative_to(root).as_posix())
                count += 1
    return count


def prune_source(export: Path) -> list[str]:
    """剔除源码归档里的作者本机数据与缓存，返回被剔除的相对路径。"""
    removed: list[str] = []
    for p in sorted(export.rglob("*"), key=lambda x: len(x.parts), reverse=True):
        rel = p.relative_to(export).as_posix()
        drop = any(rel == pref.rstrip("/") or rel.startswith(pref)
                   for pref in SOURCE_EXCLUDE_PREFIXES)
        drop = drop or any(part in SOURCE_EXCLUDE_DIRNAMES for part in p.parts)
        drop = drop or (p.is_file() and p.suffix.lower() in SOURCE_EXCLUDE_SUFFIXES)
        if not drop:
            continue
        removed.append(rel)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
    return removed


def build_source_archive(tag: str, name: str, version: str, out: Path, work: Path):
    """用 git archive 导出 tag 的源码树，剔除本机数据后压成 zip。"""
    tar_path = work / "source.tar"
    run(["git", "archive", "--format=tar", "-o", str(tar_path), tag], cwd=ROOT)
    export = work / "export"
    if export.exists():
        shutil.rmtree(export)
    export.mkdir(parents=True)
    with tarfile.open(tar_path) as tf:
        try:
            tf.extractall(export, filter="data")
        except TypeError:        # Python < 3.12 没有 filter 参数
            tf.extractall(export)
    removed = prune_source(export)
    archive = out / f"{name}-{version}-src.zip"
    count = zip_tree(export, archive, arc_prefix=f"{name}-{version}/")
    return archive, count, removed


# --------------------------------------------------------------------------
# 校验和与发布说明
# --------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def platform_names() -> tuple[str, str]:
    system = platform.system().lower()
    if system.startswith("win"):
        plat = "windows"
    elif system == "darwin":
        plat = "macos"
    else:
        plat = "linux"
    machine = platform.machine().lower()
    arch = {
        "amd64": "x64", "x86_64": "x64",
        "arm64": "arm64", "aarch64": "arm64",
        "x86": "x86", "i386": "x86", "i686": "x86",
    }.get(machine, machine)
    return plat, arch


def tag_annotation(tag: str) -> str:
    r = subprocess.run(
        ["git", "for-each-ref", "--format=%(contents)", f"refs/tags/{tag}"],
        cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip()


def write_release_notes(out: Path, name: str, version: str, tag: str,
                        artifacts: list[Path]) -> Path:
    lines = [
        f"# {name} {version} 发布说明",
        "",
        f"- tag：`{tag}`",
        f"- 构建平台：{platform.platform()}",
        f"- Python：{platform.python_version()}",
        f"- 构建时间：{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}",
        "",
    ]
    annotation = tag_annotation(tag)
    if annotation:
        lines += ["## tag 注记", "", annotation, ""]
    lines += ["## 本次产物", ""]
    lines += [f"- `{a.name}`" for a in artifacts]
    lines.append("")
    path = out / f"RELEASE-NOTES-{version}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_checksums(out: Path, artifacts: list[Path]) -> Path:
    lines = [f"{sha256_file(a)}  {a.name}" for a in sorted(artifacts, key=lambda p: p.name)]
    path = out / "SHA256SUMS.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# 收尾
# --------------------------------------------------------------------------

def cleanup(work: Path, keep: bool) -> None:
    if keep:
        log(f"   保留暂存目录：{work}")
        return
    src = work / "src"
    if src.exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(src)],
                       cwd=ROOT, capture_output=True, text=True)
    subprocess.run(["git", "worktree", "prune"], cwd=ROOT, capture_output=True, text=True)
    shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="从 git tag 打出本平台发行包与源码归档")
    ap.add_argument("--tag", help="要发布的 tag（默认取 HEAD 上指着的 tag）")
    ap.add_argument("--name", default=DEFAULT_NAME, help=f"产品名（默认 {DEFAULT_NAME}）")
    ap.add_argument("--out", default=str(ROOT / "dist" / "release"),
                    help="产物目录（默认 dist/release）")
    ap.add_argument("--work", help="构建暂存目录（默认系统临时目录）")
    ap.add_argument("--python", default=sys.executable,
                    help="用于创建构建虚拟环境的解释器（默认当前解释器）")
    ap.add_argument("--backend", choices=BACKENDS, default="pyinstaller",
                    help="打包后端：pyinstaller（默认，onedir）或 nuitka（--standalone）")
    ap.add_argument("--wheelhouse", help="离线 wheel 目录：pip 改用 --no-index --find-links "
                                        "安装（受限/无网环境用；Nuitka 获取 Zig 也走它）")
    ap.add_argument("--skip-build", action="store_true", help="跳过二进制构建，只出源码归档")
    ap.add_argument("--skip-source", action="store_true", help="跳过源码归档")
    ap.add_argument("--console", action="store_true",
                    help="排错用：构建带控制台的包以看到启动期 traceback（正式发布不要用）")
    ap.add_argument("--keep", action="store_true", help="保留暂存目录（排错用）")
    args = ap.parse_args(argv)

    tag = resolve_tag(args.tag)
    version = tag.lstrip("vV")
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    if args.work:
        work = Path(args.work).resolve()
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
    else:
        work = Path(tempfile.mkdtemp(prefix=f"gg-release-{version}-"))

    log(f"== 发布 {tag}（版本号 {version}，后端 {args.backend}）==")
    log(f"   产物目录：{out}")
    log(f"   暂存目录：{work}")

    # 离线 wheel 仓库：把 pip 切到 --no-index --find-links，并通过环境变量把同一份
    # 设置透传给 Nuitka 派生的私有 pip（它据此离线获取 Zig 编译器）。
    pip_flags: list[str] = []
    if args.wheelhouse:
        wh = Path(args.wheelhouse).resolve()
        if not wh.is_dir():
            raise SystemExit(f"--wheelhouse 不是目录：{wh}")
        os.environ["PIP_NO_INDEX"] = "1"
        os.environ["PIP_FIND_LINKS"] = str(wh)
        pip_flags = ["--no-index", "--find-links", str(wh)]
        log(f"   离线 wheel 仓库：{wh}")

    artifacts: list[Path] = []
    try:
        if not args.skip_build:
            src = add_worktree(work, tag)
            vpy = make_venv(work, args.python)
            preflight_build_env(vpy)
            if pip_flags:
                log("   （离线模式：跳过 pip 自升级）")
            else:
                run([vpy, "-m", "pip", "install", "--upgrade", "pip"])
            if args.backend == "nuitka":
                run([vpy, "-m", "pip", "install", *pip_flags,
                     "-r", str(src / "requirements.txt"), "nuitka"])
                bundle = build_bundle_nuitka(vpy, src, args.name, version,
                                             console=args.console)
                suffix = "-nuitka"
            else:
                run([vpy, "-m", "pip", "install", *pip_flags,
                     "-r", str(src / "requirements.txt"), "pyinstaller"])
                targets = detect_collect_targets(vpy)
                log(f"   该版本可用依赖包：{', '.join(targets) or '（无）'}")
                bundle = build_bundle(vpy, src, args.name, targets, console=args.console)
                suffix = ""
            plat, arch = platform_names()
            archive = out / f"{args.name}-{version}-{plat}-{arch}{suffix}.zip"
            count = zip_tree(bundle, archive, arc_prefix=f"{args.name}/")
            log(f"[OK] 运行时包 {archive.name}（{count} 个文件，后端 {args.backend}）")
            artifacts.append(archive)

        if not args.skip_source:
            src_archive, count, removed = build_source_archive(
                tag, args.name, version, out, work)
            log(f"[OK] 源码归档 {src_archive.name}（{count} 个文件，"
                f"剔除 {len(removed)} 项：{'、'.join(removed) or '无'}）")
            artifacts.append(src_archive)

        notes = write_release_notes(out, args.name, version, tag, artifacts)
        artifacts.append(notes)
        artifacts.append(write_checksums(out, artifacts))
    finally:
        cleanup(work, args.keep)

    log("")
    log(f"== 完成：{len(artifacts)} 个文件在 {out} ==")
    for a in artifacts:
        log(f"   {a.name}")
    log("")
    log("提示：其他平台的运行时包需在对应系统上对本 tag 再跑一次")
    log("      （两种后端都不做交叉编译）；把各平台 dist/release/ 汇总即完整发布。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
