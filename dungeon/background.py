import os
import threading
import time

import numpy as np
from PIL import Image
from PIL import ImageFilter
import dearpygui.dearpygui as dpg

from .dispatcher import _dispatch


_BYTE_TO_FLOAT = [value / 255.0 for value in range(256)]


def _rotate_safe(image, angle):
    """按对角线放大后旋转、再居中裁回原尺寸；透明边角用边缘色填充。

    旋转会引入透明边角（内容转出画布）。为让背景铺满时不留黑缝，先在
    对角线上扩充透明画布旋转（旋转不会丢失内容），裁回原尺寸后对透明
    像素用就近的非透明边缘色填充。
    """
    w, h = image.size
    diag = int((w * w + h * h) ** 0.5) + 1
    phase = Image.new("RGBA", (diag, diag), (0, 0, 0, 0))
    phase.paste(image, ((diag - w) // 2, (diag - h) // 2))
    rotated = phase.rotate(angle, resample=Image.BICUBIC)
    out = rotated.crop(((diag - w) // 2, (diag - h) // 2,
                        (diag - w) // 2 + w, (diag - h) // 2 + h))
    fill = _edge_tint(image)
    if out.getchannel("A").getextrema() != (255, 255) and fill is not None:
        alpha = out.getchannel("A")
        solid = Image.new("RGBA", out.size, fill)
        out = Image.composite(out, solid, alpha)
    return out


def _edge_tint(image):
    """取图像四边中间点的非透明平均色，用作旋转后透明边角的填充。"""
    w, h = image.size
    if w < 4 or h < 4:
        return None
    pts = [
        (w // 2, 1), (w // 2, h - 2), (1, h // 2), (w - 2, h // 2),
        (w // 2, h // 2),
    ]
    rs = gs = bs = 0
    cnt = 0
    for x, y in pts:
        r, g, b, a = image.getpixel((x, y))
        if a > 0:
            rs += r
            gs += g
            bs += b
            cnt += 1
    if not cnt:
        return None
    return (rs // cnt, gs // cnt, bs // cnt, 255)


class DungeonBackground:
    """副本背景图的加载、裁剪、滤镜和淡入淡出。"""

    def __init__(self, owner):
        self.owner = owner

    def change(self, image_path, smooth_transition=False, filter_effect=None,
               rotate_angle=None, blur_radius=None):
        owner = self.owner
        if not image_path:
            return
        full_path = self.resolve_path(image_path)
        if not full_path or not os.path.exists(full_path):
            print(f"背景加载失败: 图片文件不存在 -> {image_path}")
            return
        try:
            new_pil = Image.open(full_path).convert("RGBA")
            # 入口动态背景：先轻微旋转（按对角线放大后旋转再居中裁回原尺寸，
            # 避免旋转后四角露出透明），再模糊或滤镜
            if rotate_angle:
                new_pil = _rotate_safe(new_pil, rotate_angle)
            if blur_radius:
                new_pil = new_pil.filter(ImageFilter.GaussianBlur(blur_radius))
            elif filter_effect:
                new_pil = self.apply_filter(new_pil, filter_effect)
        except Exception as exc:
            print(f"图片加载错误: {exc}")
            return

        owner._bg_pil_full = new_pil
        width, height = owner._layout_w, owner._layout_h
        if owner._bg_pil_original is None or not smooth_transition:
            # 无过渡（首图/强制刷新）：在当前线程同步应用。走 Timer+队列时
            # 首图常因视口尚未稳定、revision 被后续刷新顶掉而延迟数秒才显示。
            resized = self.crop_and_resize(new_pil, width, height)
            self.apply_data(resized, self.pil_to_dpg(resized), width, height)
            return

        owner._bg_revision += 1
        revision = owner._bg_revision
        if owner._bg_resize_timer is not None:
            owner._bg_resize_timer.cancel()

        def fade_task():
            old_resized = owner._bg_pil_original
            new_resized = self.crop_and_resize(new_pil, width, height)
            if old_resized.size != (width, height):
                old_resized = old_resized.resize((width, height), Image.Resampling.LANCZOS)
            old_data = self.pil_to_dpg(old_resized)
            _dispatch.enqueue(owner._apply_prepared_bg, old_resized, old_data, width, height, revision)
            # 30 步 × 0.03s ≈ 0.9s 平滑淡入淡出（numpy 转换后每步开销很小，
            # 放慢过渡让切换更柔和；切换周期由轮播线程控制，不受影响）
            for step in range(30):
                if owner._closing or revision != owner._bg_revision:
                    return
                alpha = step / 29
                blended = Image.blend(old_resized, new_resized, alpha)
                _dispatch.enqueue(owner._set_bg_texture, self.pil_to_dpg(blended), width, height, revision)
                time.sleep(0.03)
            _dispatch.enqueue(owner._finish_bg_fade, new_resized, width, height, revision)

        threading.Thread(target=fade_task, daemon=True).start()

    def refresh(self, delay=0.08):
        owner = self.owner
        full = owner._bg_pil_full
        if full is None:
            return
        width, height = owner._layout_w, owner._layout_h
        if width <= 1 or height <= 1:
            return

        owner._bg_revision += 1
        revision = owner._bg_revision
        if owner._bg_resize_timer is not None:
            owner._bg_resize_timer.cancel()

        def prepare():
            if owner._closing or revision != owner._bg_revision:
                return
            resized = self.crop_and_resize(full, width, height)
            _dispatch.enqueue(
                owner._apply_prepared_bg,
                resized,
                self.pil_to_dpg(resized),
                width,
                height,
                revision,
            )

        owner._bg_resize_timer = threading.Timer(delay, prepare)
        owner._bg_resize_timer.daemon = True
        owner._bg_resize_timer.start()

    def apply_data(self, pil_img, dpg_data, width, height):
        # 尺寸一致时直接 set_value（接受 numpy float32，比整张重建快一个量级）；
        # 仅在尺寸变化（窗口 resize / 首次应用）时才重建纹理，
        # 注意 add_dynamic_texture 不接受 numpy，重建必须传 list。
        if dpg.does_item_exist("bg_texture"):
            cur = dpg.get_item_configuration("bg_texture")
            if cur.get("width") == width and cur.get("height") == height:
                dpg.set_value("bg_texture", dpg_data)
                self.owner._bg_pil_original = pil_img
                return
            dpg.delete_item("bg_texture")
            if dpg.does_alias_exist("bg_texture"):
                dpg.remove_alias("bg_texture")
        dpg.add_dynamic_texture(
            width=width,
            height=height,
            default_value=list(dpg_data),
            tag="bg_texture",
            parent="dungeon_texture_registry",
        )
        if dpg.does_item_exist("bg_image_item"):
            dpg.configure_item("bg_image_item", texture_tag="bg_texture", pmax=[width, height])
        self.owner._bg_pil_original = pil_img

    def resolve_path(self, image_path):
        owner = self.owner
        processed = str(image_path).lstrip("\\/")
        candidates = []
        if os.path.isabs(image_path):
            candidates.append(os.path.normpath(image_path))
        if owner.scenario_id and owner.scenario_repo:
            candidates.append(os.path.normpath(os.path.join(owner.scenario_repo.root, owner.scenario_id, processed)))
        candidates.extend([
            os.path.normpath(processed),
            os.path.normpath(os.path.join(os.path.abspath(os.path.dirname(__file__)), "..", processed)),
        ])
        for path in dict.fromkeys(candidates):
            if os.path.exists(path):
                return path
        return None

    @staticmethod
    def apply_filter(image, filter_effect):
        filters = {
            "blur": ImageFilter.BLUR, "contour": ImageFilter.CONTOUR,
            "detail": ImageFilter.DETAIL, "edge_enhance": ImageFilter.EDGE_ENHANCE,
            "edge_enhance_more": ImageFilter.EDGE_ENHANCE_MORE, "emboss": ImageFilter.EMBOSS,
            "find_edges": ImageFilter.FIND_EDGES, "sharpen": ImageFilter.SHARPEN,
            "smooth": ImageFilter.SMOOTH, "smooth_more": ImageFilter.SMOOTH_MORE,
        }
        return image.filter(filters[filter_effect.lower()]) if filter_effect.lower() in filters else image

    @staticmethod
    def crop_and_resize(image, width, height):
        if width <= 1 or height <= 1:
            return image
        target_ratio = width / height
        image_width, image_height = image.size
        if image_width / image_height > target_ratio:
            new_width = int(image_height * target_ratio)
            left = (image_width - new_width) // 2
            cropped = image.crop((left, 0, left + new_width, image_height))
        else:
            new_height = int(image_width / target_ratio)
            top = (image_height - new_height) // 2
            cropped = image.crop((0, top, image_width, top + new_height))
        return cropped.resize((width, height), Image.Resampling.LANCZOS)

    @staticmethod
    def pil_to_dpg(image):
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        # 全屏图约 2.5k×1.6k，逐像素 Python 推导需 ~1s；numpy 整块转换 ~30ms。
        arr = np.frombuffer(image.tobytes(), dtype=np.uint8).astype(np.float32)
        arr /= 255.0
        return arr
