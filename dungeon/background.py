import os
import threading
import time

from PIL import Image
from PIL import ImageFilter
import dearpygui.dearpygui as dpg

from .dispatcher import _dispatch


_BYTE_TO_FLOAT = [value / 255.0 for value in range(256)]


class DungeonBackground:
    """副本背景图的加载、裁剪、滤镜和淡入淡出。"""

    def __init__(self, owner):
        self.owner = owner

    def change(self, image_path, smooth_transition=False, filter_effect=None):
        owner = self.owner
        if not image_path:
            return
        full_path = self.resolve_path(image_path)
        if not full_path or not os.path.exists(full_path):
            print(f"背景加载失败: 图片文件不存在 -> {image_path}")
            return
        try:
            new_pil = Image.open(full_path).convert("RGBA")
            if filter_effect:
                new_pil = self.apply_filter(new_pil, filter_effect)
        except Exception as exc:
            print(f"图片加载错误: {exc}")
            return

        owner._bg_pil_full = new_pil
        width, height = owner._layout_w, owner._layout_h
        if owner._bg_pil_original is None or not smooth_transition:
            owner._refresh_background(delay=0)
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
            for step in range(15):
                if owner._closing or revision != owner._bg_revision:
                    return
                alpha = step / 14
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
        if dpg.does_item_exist("bg_texture"):
            dpg.delete_item("bg_texture")
        if dpg.does_alias_exist("bg_texture"):
            dpg.remove_alias("bg_texture")
        dpg.add_dynamic_texture(
            width=width,
            height=height,
            default_value=dpg_data,
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
        if owner.dungeon_id and owner.dungeon_repo:
            candidates.append(os.path.normpath(os.path.join(owner.dungeon_repo.root, owner.dungeon_id, processed)))
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
        return [_BYTE_TO_FLOAT[value] for value in image.tobytes()]
