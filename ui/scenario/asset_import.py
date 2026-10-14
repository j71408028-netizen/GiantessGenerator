"""副本编辑器共用的资源导入辅助。

背景图与结局图标都要走“选图 → 裁剪（可选）→ 存入副本目录 → 记相对路径”
这一套流程，触发器对话框与章节对话框共用这里的实现。

固定文本（章节字段 ``fixed_text``，见 ``dungeon/script_text``）走同一套素材
管理：正文落在 ``<方案>/texts/``，章节里只记相对路径。文本不需要裁剪，但需要
读回来做解析预览 / 内置编辑，因此读写 helper 也收在这里。
"""

import datetime
import os
import re
import shutil
from tkinter import filedialog

import ui.ctk.dialogs
from dungeon.audio.chapters import BGM_SUPPORTED_EXTS
from ui.ctk.dialogs import ImageCropDialog

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
#: 固定文本可用的后缀（导入时其它后缀会被补成 .txt）
TEXT_EXTS = (".txt", ".md")


def save_cropped_image(cropped_path: str, dest_path: str):
    """把裁剪结果按目标扩展名保存，并清理裁剪产生的临时文件。"""
    from PIL import Image as PILImage
    ext = os.path.splitext(dest_path)[1].lower()
    fmt_map = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG",
               ".bmp": "BMP", ".gif": "GIF", ".webp": "WEBP"}
    fmt = fmt_map.get(ext, "PNG")
    try:
        with PILImage.open(cropped_path) as img:
            if fmt in ("JPEG", "BMP", "GIF"):
                img.convert("RGB").save(dest_path, format=fmt)
            else:
                img.save(dest_path, format=fmt)
    finally:
        try:
            os.remove(cropped_path)
        except OSError:
            pass


def import_background_image(parent, scenario_repo, scenario_id) -> str:
    """选图并裁剪为 16:9 后存入副本 images 目录，返回相对副本目录的路径。

    用户取消、裁剪失败或副本目录不可用时返回空字符串；没有副本信息时
    返回裁剪后的临时路径（兼容旧版本行为）。
    """
    file_path = filedialog.askopenfilename(
        title="选择背景图片",
        filetypes=[("图片文件", "*.png *.jpg *.jpeg *.gif *.bmp *.webp")])
    if not file_path:
        return ""

    # 先弹出裁剪对话框（固定 16:9 比例）
    try:
        crop_dlg = ImageCropDialog(parent, file_path, mode=ImageCropDialog.MODE_BACKGROUND)
    except Exception as e:
        ui.ctk.dialogs.showerror("错误", f"图片加载失败: {e}")
        return ""
    cropped_path = crop_dlg.get_cropped_path()
    if not cropped_path:
        return ""  # 用户取消裁剪

    if not (scenario_id and scenario_repo):
        return cropped_path

    dungeon_dir = os.path.join(scenario_repo.root, scenario_id)
    if not os.path.exists(dungeon_dir):
        ui.ctk.dialogs.showerror("错误", f"副本目录不存在: {dungeon_dir}")
        return ""

    images_dir = os.path.join(dungeon_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    basename = os.path.basename(file_path)
    if os.path.splitext(basename)[1].lower() not in IMAGE_EXTS:
        basename += ".png"
    dest_path = os.path.join(images_dir, basename)
    if os.path.exists(dest_path):
        if not ui.ctk.dialogs.askyesno("文件已存在", f"图片 {basename} 已存在，是否覆盖？"):
            return ""
    try:
        save_cropped_image(cropped_path, dest_path)
    except Exception as e:
        ui.ctk.dialogs.showerror("错误", f"保存裁剪图片失败: {e}")
        return ""
    return os.path.relpath(dest_path, dungeon_dir)


def import_bgm_audio(parent, scenario_repo, scenario_id) -> str:
    """选音频文件并存入副本 ``audio`` 目录，返回相对副本目录的路径（正斜杠）。

    音频不像图片需要裁剪，拷进来即可；同名文件覆盖前会先问一句。
    用户取消、副本目录不可用或后缀不是常见音频时返回空字符串。
    """
    patterns = " ".join(f"*{ext}" for ext in BGM_SUPPORTED_EXTS)
    file_path = filedialog.askopenfilename(
        title="选择背景音乐",
        filetypes=[("音频文件", patterns), ("所有文件", "*.*")])
    if not file_path:
        return ""
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in BGM_SUPPORTED_EXTS:
        ui.ctk.dialogs.showerror(
            "错误", f"不支持的音频格式：{ext or '（无后缀）'}\n"
                    f"请用：{patterns}")
        return ""
    if not (scenario_id and scenario_repo):
        ui.ctk.dialogs.showerror("错误", "无法解析副本目录，无法保存音频文件")
        return ""

    dungeon_dir = os.path.join(scenario_repo.root, scenario_id)
    if not os.path.exists(dungeon_dir):
        ui.ctk.dialogs.showerror("错误", f"副本目录不存在: {dungeon_dir}")
        return ""

    audio_dir = os.path.join(dungeon_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    basename = os.path.basename(file_path)
    dest_path = os.path.join(audio_dir, basename)
    if os.path.exists(dest_path):
        if not ui.ctk.dialogs.askyesno("文件已存在", f"音频 {basename} 已存在，是否覆盖？"):
            return ""
    try:
        shutil.copy2(file_path, dest_path)
    except Exception as e:
        ui.ctk.dialogs.showerror("错误", f"保存音频文件失败: {e}")
        return ""
    return os.path.relpath(dest_path, dungeon_dir).replace("\\", "/")


def import_ending_icon(parent, scenario_repo, scenario_id) -> str:
    """选择 png 图标并存入副本 endings 目录，返回相对副本目录的路径（正斜杠）。"""
    file_path = filedialog.askopenfilename(
        title="选择结局图标",
        filetypes=[("PNG 图片", "*.png"), ("图片文件", "*.png *.jpg *.jpeg *.bmp")])
    if not file_path:
        return ""
    from PIL import Image as PILImage
    try:
        with PILImage.open(file_path) as img:
            img.load()
    except Exception as e:
        ui.ctk.dialogs.showerror("错误", f"图片加载失败: {e}")
        return ""
    if not (scenario_id and scenario_repo):
        ui.ctk.dialogs.showerror("错误", "无法解析副本目录，无法保存结局图标")
        return ""

    dungeon_dir = os.path.join(scenario_repo.root, scenario_id)
    endings_dir = os.path.join(dungeon_dir, "endings")
    os.makedirs(endings_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    base = os.path.splitext(os.path.basename(file_path))[0]
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", base)[:40] or "ending"
    dest_path = os.path.join(endings_dir, f"{ts}_{safe}.png")
    try:
        with PILImage.open(file_path) as img:
            if img.mode in ("RGBA", "LA", "P"):
                img.convert("RGBA").save(dest_path, "PNG")
            else:
                img.convert("RGB").save(dest_path, "PNG")
    except Exception as e:
        ui.ctk.dialogs.showerror("错误", f"保存结局图标失败: {e}")
        return ""
    return os.path.relpath(dest_path, dungeon_dir).replace("\\", "/")


# ---------------- 固定文本（fixed_text） ----------------

def _scenario_dir(scenario_repo, scenario_id) -> str:
    """方案目录；方案 / 仓库不可用时返回空串。"""
    if not (scenario_repo and scenario_id):
        return ""
    try:
        return scenario_repo.scenario_dir(scenario_id) or ""
    except Exception:
        return ""


def script_text_abs_path(scenario_repo, scenario_id, rel_path) -> str:
    """把相对方案目录的固定文本路径解析成绝对路径（解析不出来返回空串）。"""
    rel = str(rel_path or "").strip().replace("\\", "/")
    if not rel:
        return ""
    if os.path.isabs(rel):
        return os.path.normpath(rel)
    base = _scenario_dir(scenario_repo, scenario_id)
    if not base:
        return ""
    return os.path.normpath(os.path.join(base, rel))


def read_script_text(scenario_repo, scenario_id, rel_path):
    """读取固定文本内容，返回 ``(内容|None, 失败原因)``。

    编码与 ``dungeon.script_text.load_script_file`` 同一套口径
    （utf-8 → gbk → utf-8 容错替换），保证「编辑器里看到的」和「运行时读到的」
    是同一份文本。
    """
    path = script_text_abs_path(scenario_repo, scenario_id, rel_path)
    if not path:
        return None, "无法解析方案目录"
    if not os.path.isfile(path):
        return None, "文件不存在"
    for encoding in ("utf-8", "gbk"):
        try:
            with open(path, "r", encoding=encoding) as handle:
                return handle.read(), ""
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            return None, str(exc)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(), ""
    except OSError as exc:
        return None, str(exc)


def write_script_text(scenario_repo, scenario_id, rel_path, text) -> bool:
    """把文本写回方案目录（UTF-8 + LF，自动建目录）；失败弹框并返回 False。"""
    path = script_text_abs_path(scenario_repo, scenario_id, rel_path)
    if not path:
        ui.ctk.dialogs.showerror("错误", "无法解析方案目录，无法保存文本")
        return False
    try:
        parent_dir = os.path.dirname(path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text or "")
    except OSError as exc:
        ui.ctk.dialogs.showerror("错误", f"保存文本失败：{exc}")
        return False
    return True


def import_script_text(parent, scenario_repo, scenario_id) -> str:
    """选一个现成文本拷入 ``<方案>/texts/``，返回相对方案目录的路径。

    文本不像图片需要裁剪，拷进来即可；同名文件覆盖前先问一句。
    用户取消、方案目录不可用时返回空字符串。
    """
    file_path = filedialog.askopenfilename(
        title="选择文本文件",
        filetypes=[("文本文件", "*.txt *.md"), ("所有文件", "*.*")])
    if not file_path:
        return ""
    base = _scenario_dir(scenario_repo, scenario_id)
    if not base:
        ui.ctk.dialogs.showerror("错误", "无法解析方案目录，无法保存文本文件")
        return ""
    texts_dir = os.path.join(base, "texts")
    try:
        os.makedirs(texts_dir, exist_ok=True)
    except OSError as exc:
        ui.ctk.dialogs.showerror("错误", f"创建 texts 目录失败：{exc}")
        return ""
    basename = os.path.basename(file_path)
    if os.path.splitext(basename)[1].lower() not in TEXT_EXTS:
        basename += ".txt"
    dest_path = os.path.join(texts_dir, basename)
    if os.path.exists(dest_path):
        if not ui.ctk.dialogs.askyesno("文件已存在", f"文本 {basename} 已存在，是否覆盖？"):
            return ""
    try:
        shutil.copy2(file_path, dest_path)
    except OSError as exc:
        ui.ctk.dialogs.showerror("错误", f"保存文本文件失败：{exc}")
        return ""
    return os.path.relpath(dest_path, base).replace("\\", "/")


def suggest_script_path(scenario_repo, scenario_id, chapter_name) -> str:
    """给还没有文本的章节起一个不冲突的默认路径 ``texts/<章节名>.txt``。"""
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_",
                  str(chapter_name or "").strip())[:40] or "chapter"
    base = _scenario_dir(scenario_repo, scenario_id)
    if not base:
        return f"texts/{safe}.txt"
    texts_dir = os.path.join(base, "texts")
    candidate = f"{safe}.txt"
    index = 1
    while os.path.exists(os.path.join(texts_dir, candidate)):
        index += 1
        candidate = f"{safe}_{index}.txt"
    return f"texts/{candidate}"
