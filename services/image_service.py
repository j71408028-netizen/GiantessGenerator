import base64
import io
import os
import tempfile
from typing import Tuple, Optional

import customtkinter as ctk
from PIL import Image


class ImageService:
    avatar_height = 56
    image_height = 180

    @staticmethod
    def crop_center_square(pil_img: Image) -> Image:
        w, h = pil_img.size
        size = min(w, h)
        left = (w - size) // 2
        top = (h - size) // 2
        return pil_img.crop((left, top, left + size, top + size))

    @staticmethod
    def crop_top_square(pil_img: Image) -> Image:
        w, h = pil_img.size
        size = min(w, h)
        left = (w - size) // 2
        top = 0
        return pil_img.crop((left, top, left + size, top + size))

    @staticmethod
    def resize_to_fixed_height(pil_img: Image, target_height: int) -> Image:
        w_target = int(pil_img.size[0] * (target_height / pil_img.size[1]))
        return pil_img.resize((w_target, target_height), Image.Resampling.LANCZOS)

    @staticmethod
    def resize_low_resolution(pil_img: Image) -> Image:
        """视纵横比降低纵向分辨率：横图360、方图540、竖图720。"""
        w, h = pil_img.size
        target_h = 180 * int(3 * w / h)
        if h <= target_h:
            return pil_img
        return ImageService.resize_to_fixed_height(pil_img, target_h)

    @staticmethod
    def pil_to_ctk(pil_img: Image, size: Tuple[int, int]) -> ctk.CTkImage:
        return ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=size)

    @staticmethod
    def load_from_path(path: str) -> Optional[Image.Image]:
        if path and os.path.exists(path):
            try:
                return Image.open(path)
            except Exception:
                pass
        return None

    @staticmethod
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

    @staticmethod
    def file_to_base64(file_path: str) -> str:
        if not file_path or not os.path.exists(file_path):
            return ""
        try:
            with open(file_path, "rb") as f:
                return base64.b64encode(f.read()).decode('utf-8')
        except Exception:
            return ""

    @staticmethod
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

    @staticmethod
    def format_avatar(pil_img: Image.Image) -> ctk.CTkImage:
        """裁剪为方形头像并返回可直接显示的 CTkImage"""
        size = ImageService.avatar_height
        if pil_img.size[1] > pil_img.size[0]:
            cropped = ImageService.crop_top_square(pil_img)
        else:
            cropped = ImageService.crop_center_square(pil_img)
        processed = cropped.resize((size, size), Image.Resampling.LANCZOS)
        return ImageService.pil_to_ctk(processed, (size, size))

    @staticmethod
    def format_image(pil_img: Image.Image) -> ctk.CTkImage:
        """按固定高度缩放并返回可直接显示的 CTkImage"""
        size = ImageService.image_height
        processed = ImageService.resize_to_fixed_height(pil_img, size)
        return ImageService.pil_to_ctk(processed, (processed.size[0], size))

    @staticmethod
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

    @staticmethod
    def clear_ctk_label_image(label_widget):
        if label_widget is None:
            return
        try:
            if hasattr(label_widget, '_label'):
                label_widget._label.configure(image="")
            label_widget.configure(image=None)
        except Exception:
            pass
