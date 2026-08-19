"""跨平台资源与用户数据路径工具。"""
import os
import shutil
import sys


APP_NAME = "GiantessGenerator"
APP_VERSION = "1.0.0 preview"


def resource_root() -> str:
    """返回随源码或 PyInstaller 包发布的只读资源根目录。

    - 源码运行：返回本文件所在目录（仓库根）；
    - PyInstaller 打包：返回资源目录；macOS .app 为 Contents/Resources。
    """
    if getattr(sys, "frozen", False):  # PyInstaller
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


def project_root() -> str:
    """兼容旧调用：返回应用资源根目录。"""
    return resource_root()


def _packaged_data_parent() -> str:
    """返回打包应用可写用户数据目录的父级。"""
    if sys.platform.startswith("darwin"):
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", APP_NAME)
    if sys.platform.startswith("win"):
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)
    return os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), APP_NAME)


def data_dir() -> str:
    """返回数据目录。

    源码运行保持使用项目内的 ``data/``；打包应用则使用系统可写目录，避免
    Finder / App Translocation 将数据写入只读的 ``.app`` 资源目录。
    """
    if getattr(sys, "frozen", False):
        return os.path.join(_packaged_data_parent(), "data")
    return os.path.join(resource_root(), "data")


def assets_dir() -> str:
    """返回资源目录（默认为 <项目根>/assets）。"""
    return os.path.join(resource_root(), "assets")


def names_dir() -> str:
    """返回静态姓名表目录（默认为 <数据目录>/static/names）。"""
    return os.path.join(data_dir(), "static", "names")


def presets_dir() -> str:
    """返回静态身材表目录（默认为 <数据目录>/static/presets）。"""
    return os.path.join(data_dir(), "static", "presets")


def personalities_dir() -> str:
    """返回静态性格表目录（默认为 <数据目录>/static/personalities）。"""
    return os.path.join(data_dir(), "static", "personalities")


def worlds_dir() -> str:
    """返回世界包目录（默认为 <数据目录>/worlds）。"""
    return os.path.join(data_dir(), "worlds")


def _bootstrap_packaged_data() -> None:
    """首次启动打包应用时，初始化可写用户数据与内置内容包。"""
    if not getattr(sys, "frozen", False):
        return
    target = data_dir()
    packs_target = os.path.join(target, "packs")
    packs_source = os.path.join(resource_root(), "data", "packs")
    if not os.path.exists(packs_target) and os.path.isdir(packs_source):
        os.makedirs(target, exist_ok=True)
        shutil.copytree(packs_source, packs_target)
    static_target = os.path.join(target, "static")
    static_source = os.path.join(resource_root(), "data", "static")
    if not os.path.exists(static_target) and os.path.isdir(static_source):
        os.makedirs(target, exist_ok=True)
        shutil.copytree(static_source, static_target)
    os.makedirs(os.path.join(target, "user"), exist_ok=True)
    os.makedirs(os.path.join(target, "archives"), exist_ok=True)


def ensure_cwd() -> str:
    """准备用户数据并将进程当前工作目录切换到数据目录的父级。

    应用启动时调用一次即可，使历史上所有基于相对路径（如 "data"）的代码
    在任何平台、任何启动方式下都保持一致。返回项目根目录。
    """
    _bootstrap_packaged_data()
    root = os.path.dirname(data_dir())
    try:
        os.makedirs(root, exist_ok=True)
        os.chdir(root)
    except OSError:
        pass
    return root
