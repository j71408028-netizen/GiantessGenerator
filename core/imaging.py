# -*- coding: utf-8 -*-
"""纯图像处理：PIL 读写、裁剪、缩放与 base64 编解码。

**为什么住在 core（而不是 services）**：``persistence/character_repo`` 生成头像缩略图
要用它，``services/preview`` 渲染身材剪影要用它，``ui`` 各面板也在展示前用它裁剪。把它
放在最底层（``core``，只依赖 ``infra``）是让这四个方向都能合法引用的唯一位置——与
``core/appearance.py`` 同理，别因为它「长得像工具」而挪回 ``services/``。

**边界**：本模块只依赖 ``PIL`` 与标准库，**不得** import ``tkinter`` /
``customtkinter`` / ``dearpygui``。把 PIL 图片包成 ``CTkImage``、或有副作用地清空某个
控件的图片，都属于界面层的事，那些函数在 ``ui/common/ctk_image.py``。

**由来**（2026-10-06 阶段 3.2.3）：本模块内容原先整体住在 ``services/image_service.py``，
里面纯图像与界面逻辑混在一起，导致两条守卫例外——
``persistence → services``（仓库层为做缩略图反向依赖服务层）与
``services → customtkinter``（服务层里混着 CTkImage 包装）。按「纯图像下移 core /
界面部分上移 ui」拆开后两条例外一并消除。
"""

import base64
import io
import os
import tempfile
from typing import Optional, Tuple

from PIL import Image

# 专业界面里头像 / 详情图的固定像素高。原本是 ImageService 的类属性，界面层按需引用。
AVATAR_HEIGHT = 56
IMAGE_HEIGHT = 180

#: 头像**母本**的像素上限：3 倍于档案主形象高度。界面里的头像框（档案卡 56、
#: 编辑行与聊天标题栏 36）都从这一份母本按自身尺寸降采样，母本留到这个分辨率
#: 就够 3 倍缩放清晰，同时不会把用户上传的大图整张扣在内存里。
AVATAR_SOURCE_HEIGHT = AVATAR_HEIGHT * 3


def crop_center_square(pil_img: Image.Image) -> Image.Image:
    w, h = pil_img.size
    size = min(w, h)
    left = (w - size) // 2
    top = (h - size) // 2
    return pil_img.crop((left, top, left + size, top + size))


def crop_top_square(pil_img: Image.Image) -> Image.Image:
    w, h = pil_img.size
    size = min(w, h)
    left = (w - size) // 2
    top = 0
    return pil_img.crop((left, top, left + size, top + size))


def prepare_avatar(pil_img: Image.Image,
                   cap: int = None) -> Image.Image:
    """把任意图片收成方形头像**母本**：竖图偏上裁、横图居中裁，超过上限再降采样。

    母本是界面头像来源的统一样子（见 ``ui.common.avatar.AvatarFrame``）：方形、
    分辨率封顶（默认 :data:`AVATAR_SOURCE_HEIGHT`），于是同一个角色的档案卡、
    编辑行、聊天标题栏可以各自按控件尺寸从它降采样——比"先压到某个控件的尺寸
    再被别的控件放大"清楚，也不会把原图整张留在内存里。

    不修改入参；返回新位图。
    """
    cap = cap or AVATAR_SOURCE_HEIGHT
    if pil_img.size[1] > pil_img.size[0]:
        square = crop_top_square(pil_img)      # 竖版头像偏上裁，别切掉脸
    else:
        square = crop_center_square(pil_img)
    if square.size[0] > cap:
        square = square.resize((cap, cap), Image.Resampling.LANCZOS)
    return square


def resize_to_fixed_height(pil_img: Image.Image, target_height: int) -> Image.Image:
    w_target = int(pil_img.size[0] * (target_height / pil_img.size[1]))
    return pil_img.resize((w_target, target_height), Image.Resampling.LANCZOS)


def resize_low_resolution(pil_img: Image.Image) -> Image.Image:
    """视纵横比降低纵向分辨率：横图360、方图540、竖图720。"""
    w, h = pil_img.size
    target_h = 180 * int(3 * w / h)
    if h <= target_h:
        return pil_img
    return resize_to_fixed_height(pil_img, target_h)


def load_from_path(path: str) -> Optional[Image.Image]:
    if path and os.path.exists(path):
        try:
            return Image.open(path)
        except Exception:
            pass
    return None


def load_from_base64(b64_str: str) -> Optional[Image.Image]:
    if not b64_str:
        return None
    try:
        if ',' in b64_str:
            b64_str = b64_str.split(',', 1)[1]
        img_data = base64.b64decode(b64_str)
        return Image.open(io.BytesIO(img_data))
    except Exception:
        return None


def file_to_base64(file_path: str) -> str:
    if not file_path or not os.path.exists(file_path):
        return ""
    try:
        with open(file_path, "rb") as f:
            return base64.b64encode(f.read()).decode('utf-8')
    except Exception:
        return ""


def base64_to_tempfile(b64_str: str, suffix: str = ".png") -> Optional[str]:
    try:
        if ',' in b64_str:
            b64_str = b64_str.split(',', 1)[1]
        img_data = base64.b64decode(b64_str)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(img_data)
        tmp.close()
        return tmp.name
    except Exception:
        return None


def crop_aspect(pil_img: Image.Image, ratio: float, offset: float = 0.5,
                orientation: Optional[str] = None) -> Image.Image:
    """
    ratio: 宽度 / 高度 (W / H) 的比例
    offset: 0.0 ~ 1.0 的滑动偏移量
    """
    w, h = pil_img.size

    # 计算以宽度为基准的裁剪尺寸
    crop_w = w
    crop_h = w / ratio

    # 若高度超出原图，则以高度为基准计算
    if crop_h > h:
        crop_h = h
        crop_w = h * ratio

    max_off_x = w - crop_w
    max_off_y = h - crop_h

    # 自适应选择余量较大的轴进行偏移调节
    if max_off_y > max_off_x:
        x = (w - crop_w) / 2
        y = max_off_y * offset
    else:
        x = max_off_x * offset
        y = (h - crop_h) / 2

    return pil_img.crop((int(x), int(y), int(x + crop_w), int(y + crop_h)))
