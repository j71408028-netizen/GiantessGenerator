"""副本编辑器共用的资源导入辅助。

背景图与结局图标都要走“选图 → 裁剪（可选）→ 存入副本目录 → 记相对路径”
这一套流程，触发器对话框与章节对话框共用这里的实现。
"""

import datetime
import os
import re
from tkinter import filedialog

import ui.common.dialogs
from ui.common.dialogs import ImageCropDialog

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")


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
        ui.common.dialogs.showerror("错误", f"图片加载失败: {e}")
        return ""
    cropped_path = crop_dlg.get_cropped_path()
    if not cropped_path:
        return ""  # 用户取消裁剪

    if not (scenario_id and scenario_repo):
        return cropped_path

    dungeon_dir = os.path.join(scenario_repo.root, scenario_id)
    if not os.path.exists(dungeon_dir):
        ui.common.dialogs.showerror("错误", f"副本目录不存在: {dungeon_dir}")
        return ""

    images_dir = os.path.join(dungeon_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    basename = os.path.basename(file_path)
    if os.path.splitext(basename)[1].lower() not in IMAGE_EXTS:
        basename += ".png"
    dest_path = os.path.join(images_dir, basename)
    if os.path.exists(dest_path):
        if not ui.common.dialogs.askyesno("文件已存在", f"图片 {basename} 已存在，是否覆盖？"):
            return ""
    try:
        save_cropped_image(cropped_path, dest_path)
    except Exception as e:
        ui.common.dialogs.showerror("错误", f"保存裁剪图片失败: {e}")
        return ""
    return os.path.relpath(dest_path, dungeon_dir)


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
        ui.common.dialogs.showerror("错误", f"图片加载失败: {e}")
        return ""
    if not (scenario_id and scenario_repo):
        ui.common.dialogs.showerror("错误", "无法解析副本目录，无法保存结局图标")
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
        ui.common.dialogs.showerror("错误", f"保存结局图标失败: {e}")
        return ""
    return os.path.relpath(dest_path, dungeon_dir).replace("\\", "/")
