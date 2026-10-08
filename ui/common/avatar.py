# -*- coding: utf-8 -*-
"""头像框：固定尺寸的圆角底 + 角色头像，缺图时退回占位字形。

**为什么要单独一个控件**：同一个角色的头像会在三处出现——介绍卡（档案主形象
56）、介绍条编辑行、聊天标题栏（后两处是紧凑位）。旧实现是三个互不相识的
``CTkLabel`` + 一份"把同一张 CTkImage 挨个 configure 到它们上面"的双 label
hack（``getattr(self, 'edit_avatar_label', None)``），于是：

- 尺寸只写在控件的 ``width/height`` 上，而图是 56 像素的——**图会把控件撑大**，
  写 48 也不生效，各处靠肉眼对齐；
- 占位字形、底色、清空图片这类细节每处各写一遍。

``AvatarFrame`` 把这两处收成一个控件：**尺寸由框说了算**（``set_image`` 按
本框尺寸打包 CTkImage），占位字形随尺寸缩放，图对象自己持有引用。

尺寸口径见 :data:`PROFILE_AVATAR_SIZE` / :data:`COMPACT_AVATAR_SIZE`；位图的
裁剪与分辨率封顶在 :func:`core.imaging.prepare_avatar`。
"""

import customtkinter as ctk

from ui.common.ctk_image import clear_ctk_label_image, format_avatar
from ui.common.theme import FB_CHIP_BG, FB_MUTED

#: 头像框尺寸（逻辑像素），按用途分两档——社交软件里"列表 / 标题栏头像"普遍在
#: 32~40，只有名片、个人主页那类头像砖才到 48~56：
#: - ``PROFILE_AVATAR_SIZE``：介绍卡（档案主形象），维持 56；
#: - ``COMPACT_AVATAR_SIZE``：编辑行与聊天标题栏这类紧凑位，取 36——比 40 更贴
#:   嵌入观感（面板本身只有左栏宽），比 32 更能看清五官；两侧都是 24 高的按钮，
#:   36 的框与它们同排不喧宾。
#: 圆角默认取半，即正圆。
PROFILE_AVATAR_SIZE = 56
COMPACT_AVATAR_SIZE = 36


class AvatarFrame(ctk.CTkLabel):
    """固定尺寸的圆角头像框（缺图时显示占位字形）。

    用法::

        frame = AvatarFrame(parent, size=COMPACT_AVATAR_SIZE)
        frame.set_image(pil_img)     # 任意 PIL 图，框内自行裁剪/缩放
        frame.set_image(None)        # 等价于 clear_image()
    """

    def __init__(self, master, size: int = COMPACT_AVATAR_SIZE,
                 corner_radius: int = None, placeholder: str = "👤", **kwargs):
        self._size = int(size)
        self._placeholder = placeholder
        # 占位字形随框缩放（旧实现固定 24，用在 36 的框里会顶到边）
        self._placeholder_font = ("Segoe UI", max(11, int(self._size * 0.45)))
        kwargs.setdefault("fg_color", FB_CHIP_BG)
        kwargs.setdefault("text_color", FB_MUTED)
        super().__init__(
            master, text=placeholder, width=self._size, height=self._size,
            corner_radius=self._size // 2 if corner_radius is None else corner_radius,
            font=self._placeholder_font, **kwargs)
        # 注意名字不能叫 ``_image``：CTkLabel 用那个属性登记自己显示的图与缩放
        # 回调（configure(image=…) 会去里面 remove_configure_callback），占用
        # 它会让贴图直接抛 ValueError。
        self._ctk_image = None

    @property
    def size(self) -> int:
        return self._size

    @property
    def ctk_image(self):
        """当前显示的 CTkImage（没有头像时为 None），供调用方/自检查看。"""
        return self._ctk_image

    def set_image(self, pil_img):
        """显示头像；传 None（或打包失败）退回占位字形。"""
        if pil_img is None:
            self.clear_image()
            return
        try:
            image = format_avatar(pil_img, self._size)
        except Exception:
            self.clear_image()
            return
        self._ctk_image = image      # 保引用：CTkImage 被回收后控件会变空白
        self.configure(image=image, text="")

    def clear_image(self):
        """清空头像，回到占位字形。"""
        self._ctk_image = None
        clear_ctk_label_image(self)
        try:
            self.configure(text=self._placeholder, font=self._placeholder_font)
        except Exception:
            pass
