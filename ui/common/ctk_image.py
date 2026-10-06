# -*- coding: utf-8 -*-
"""把 PIL 图片包装成 customtkinter 控件可直接显示的对象。

**为什么住在 ui**：``CTkImage`` 与控件配置都是界面层概念。若把它们和纯图像处理混在
一起下沉到 ``services``（原 ``services/image_service.py`` 就是这样），会把
``customtkinter`` 拖进服务层，直接触发 ``tests/check_import_graph`` 的 UI 框架禁令。
纯图像处理（裁剪 / 缩放 / base64）请改用 ``core/imaging.py``。

（2026-10-06 阶段 3.2.3 从 ``services/image_service.py`` 拆出界面部分。）
"""

from typing import Tuple

import customtkinter as ctk
from PIL import Image

from core import imaging


def pil_to_ctk(pil_img: Image.Image, size: Tuple[int, int]) -> ctk.CTkImage:
    return ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=size)


def format_avatar(pil_img: Image.Image) -> ctk.CTkImage:
    """裁剪为方形头像并返回可直接显示的 CTkImage"""
    size = imaging.AVATAR_HEIGHT
    if pil_img.size[1] > pil_img.size[0]:
        cropped = imaging.crop_top_square(pil_img)
    else:
        cropped = imaging.crop_center_square(pil_img)
    processed = cropped.resize((size, size), Image.Resampling.LANCZOS)
    return pil_to_ctk(processed, (size, size))


def format_image(pil_img: Image.Image) -> ctk.CTkImage:
    """按固定高度缩放并返回可直接显示的 CTkImage"""
    size = imaging.IMAGE_HEIGHT
    processed = imaging.resize_to_fixed_height(pil_img, size)
    return pil_to_ctk(processed, (processed.size[0], size))


def clear_ctk_label_image(label_widget):
    """清空 label 上的图片（CTkLabel 的 image 配置在其内部 _label 上）。"""
    if label_widget is None:
        return
    try:
        if hasattr(label_widget, '_label'):
            label_widget._label.configure(image="")
        label_widget.configure(image=None)
    except Exception:
        pass
