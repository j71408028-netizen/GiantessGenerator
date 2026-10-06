"""身材比例预览渲染：把部位参数画成正面少女剪影，用于角色头像。

从原版 ``ui/exploration/creation_params_dlg.py`` 中抽出：挂件版不再提供性格 /
身材的自定义对话框，但「未上传立绘时用身材预览图当头像」仍需要这套画法，
因此把它单独放在服务层，供 CreationService 与 ExplorationContext 调用。

**本模块只依赖 PIL，不依赖 tkinter**：绘制配方（``BodyPreviewPainter``）与具体画布
解耦，服务层唯一的实现是画到 PIL 图片的 ``BodyPreviewPilCanvas``。暗色模式经
``core.appearance`` 读取（该模块零依赖，故不存在 services -> ui 反向边）。

注意：本模块与 ``ui.exploration.creation_params_dlg`` 里的同款画法是两份。差异只有
一处——本模块在暗色模式下会对颜色做亮度衰减（``_PREVIEW_DARK_DIM``），专业版不做。
合并两份之前先决定这个衰减要不要保留。
"""

import math
from typing import List, Optional

from PIL import Image, ImageDraw

from core import appearance

# 立绘配色：与主题调色板（assets/theme/*.json 的 vars 段）逐值一致，这里写成
# 字面量是为了让服务层不必反向依赖 UI 层——``ui.common.theme`` 会连带导入
# customtkinter，而挂件界面是纯 tkinter，不该被它拖进来。改动一侧请同步另一侧。
VIEW_OUTLINE = "#2A2033"
VIEW_OUTLINE_DARK = "#332F2E"
VIEW_HAIR = "#2A2625"
VIEW_HAIR_SHADE = "#3B3635"
VIEW_SKIN = "#F8E6DE"
VIEW_SKIN_SHADE = "#F5E4DD"
VIEW_SKIN_LINE = "#D6BEAE"
VIEW_CLOTH = "#F0EDEB"
VIEW_NAVY = "#4A5366"
VIEW_NAVY_LINE = "#6B778D"
VIEW_EYE = "#7B7771"
VIEW_EYE_WHITE = "#E9E8E8"
VIEW_EYE_WHITE_LINE = "#DED6D6"
VIEW_PUPIL = "#615F5F"
VIEW_HIGHLIGHT = "#EAE8E8"
VIEW_MOUTH = "#B08587"
VIEW_BLUSH = "#E8CFCD"
VIEW_SHADOW = "#707476"
VIEW_CLOTH_DARK = "#232630"
VIEW_CLOTH_DARK_LINE = "#171921"
VIEW_WHITE_LINE = "#D1CDCA"
VIEW_SLEEVE = "#EAE7E5"
VIEW_STITCH = "#ACA8A6"

_CANVAS_W = 225     # 原 PresetCustomDialog.CANVAS_W：预览画布设计宽
_CANVAS_H = 400     # 原 PresetCustomDialog.CANVAS_H：预览画布设计高

# 暗色模式下身材预览的颜色衰减强度（0~1，越大整体越暗）
_PREVIEW_DARK_DIM = 0.15


def _preview_dim_color(color: str) -> str:
    """暗色模式预览取色：对越亮的通道应用越大的亮度衰减（纯黑不变）。

    返回仍为规范的 #RRGGBB；仅处理 7 位十六进制颜色，其余（"" 等）原样返回。
    """
    if not (isinstance(color, str) and len(color) == 7 and color[0] == "#"):
        return color

    def channel(v: int) -> int:
        t = v / 255.0
        return round(v * (1.0 - _PREVIEW_DARK_DIM * t))

    return "#{:02X}{:02X}{:02X}".format(*(channel(int(color[i:i + 2], 16))
                                          for i in (1, 3, 5)))


_PART_PARAMS = {
    "步长": ("stride_ratio", 0.50, 1.50, 0.005),
    "腿长": ("leg_ratio", 0.40, 0.60, 0.005),
    "臂长": ("arm_span_ratio", 0.30, 0.50, 0.005),
    "胸宽": ("chest_width_ratio", 0.20, 0.40, 0.005),
    "脚长": ("foot_length_ratio", 0.10, 0.20, 0.005),
    "脚踝高度": ("ankle_height_ratio", 0.03, 0.16, 0.005),
    "膝盖高度": ("knee_height_ratio", 0.20, 0.35, 0.005),
    "大腿直径": ("thigh_diameter_ratio", 0.10, 0.20, 0.005),
    "小臂直径": ("forearm_diameter_ratio", 0.03, 0.08, 0.005),
    "手掌长度": ("palm_length_ratio", 0.05, 0.16, 0.005),
    "食指长度": ("index_finger_ratio", 0.02, 0.09, 0.005),
    "食指直径": ("index_finger_diameter_ratio", 0.002, 0.020, 0.0001),
    "指缝宽度": ("finger_gap_ratio", 0.0005, 0.0100, 0.00005),
    "指纹宽度": ("fingerprint_width_ratio", 0.0001, 0.0020, 0.00001),
}



class BodyPreviewPainter:
    """身材预览的绘制配方：只描述「画什么」，不决定「画到哪」。

    全部绘制只经 6 个图元原语完成——``create_oval`` / ``create_polygon`` /
    ``create_line`` / ``delete`` / ``winfo_width`` / ``winfo_height``——由子类实现。
    本类**不依赖 tkinter**：服务层只有 ``BodyPreviewPilCanvas`` 一个实现（把图元画到
    PIL 图片），专业界面那份自带 tk 画布的拷贝在 ``ui/exploration/creation_params_dlg``。

    此前本类继承 ``tk.Canvas``，于是 ``import tkinter`` 被拖进服务层（守卫例外 #10）。
    但那个 tk 画布**全仓没有任何实例化点**——它只是绘制配方的宿主，因此去 tk 化即可，
    不必把类搬进界面层。
    """

    def __init__(self, width: int = 300, height: int = 410):
        self.preview_width = width
        self.preview_height = height
        self._current_values: Optional[dict] = None
        self._dark = appearance.is_dark()

    def update_values(self, values: dict):
        """更新参数并请求重绘。"""
        self._current_values = dict(values)
        self.refresh()

    def poly(self, points, fill, outline=VIEW_OUTLINE, width=1.5, **kwargs):
        return self.create_polygon(points, fill=fill, outline=outline, width=width,
                                   joinstyle="round", **kwargs)

    def line(self, points, fill, width, **kwargs):
        return self.create_line(points, fill=fill, width=width, smooth=True,
                                capstyle="round", joinstyle="round", **kwargs)

    # ---- 图元原语：由子类实现 ----
    def create_oval(self, *args, **kwargs):
        raise NotImplementedError

    def create_polygon(self, *args, **kwargs):
        raise NotImplementedError

    def create_line(self, *args, **kwargs):
        raise NotImplementedError

    def delete(self, *args):
        raise NotImplementedError

    def winfo_width(self) -> int:
        raise NotImplementedError

    def winfo_height(self) -> int:
        raise NotImplementedError

    # ==================== 绘制拆分主入口与子方法 ====================
    def refresh(self):
        """主绘制逻辑：负责计算全局坐标系并调用各部位绘制子方法。"""
        if not self._current_values:
            return

        self.delete("all")
        w = max(self.winfo_width(), 100)
        h = max(self.winfo_height(), 100)

        # 1. 地面基准线 (ground) 与固定总身高 (total_height)
        cx = w * 0.5
        ground = h - 30
        total_height = h * 0.82
        v = self._current_values

        # 2. 关键 y 坐标计算
        head_top = ground - total_height
        leg = v.get("leg_ratio", 0.5) * total_height
        hip_y = ground - leg

        upper_h = total_height - leg
        min_head_h = total_height / 8.5
        max_head_h = total_height / 6.5

        head_h = max(min_head_h, min(upper_h * 0.27, max_head_h))

        # 胸宽对头部宽度的微调联动
        chest_ratio = v.get("chest_width_ratio", 0.2)
        head_w_scale = 0.77 + (chest_ratio - 0.2) * 0.15
        head_w = head_h * head_w_scale

        neck_h = max((upper_h - head_h) * 0.05, 4.0)
        neck_y = head_top + head_h
        shoulder_y = neck_y + neck_h

        foot = v.get("foot_length_ratio", 0.1) * total_height * 0.6
        shoe_h = 18.0 + foot * 0.25
        ankle_y = ground - shoe_h

        knee_y = ground - (v.get("knee_height_ratio", 0.25) * total_height)
        knee_y = max(hip_y + leg * .32, min(knee_y, ankle_y - leg * .16))

        # 3. 关键宽度与构件长短计算
        thigh_ratio = v.get("thigh_diameter_ratio", 0.1)
        thigh_w = thigh_ratio * total_height * .72
        calf_w = thigh_w * .72
        bust_w = chest_ratio * total_height * .58

        # 使裙子基础宽度增加
        weakened_chest_ratio = 0.22 + (chest_ratio - 0.2) * 0.28

        # 当增大大腿直径时，下半身基准宽度会同步被略微撑大，从而放大裙子下摆
        thigh_expansion = max(0.0, (thigh_ratio - 0.1) * 0.35)
        lower_bust_w = (weakened_chest_ratio + thigh_expansion) * total_height * .64

        arm = v.get("arm_span_ratio", 0.3) * total_height

        # 配色定义
        colors = {
            "hair_base": VIEW_HAIR,
            "hair_shade": VIEW_HAIR_SHADE,
            "skin": VIEW_SKIN,
            "skin_shade": VIEW_SKIN_SHADE,
            "cloth_white": VIEW_CLOTH,
            "navy_base": VIEW_NAVY,
            "navy_line": VIEW_NAVY_LINE,
            "eye": VIEW_EYE
        }

        # 依次调用部件绘制函数
        self._draw_background(cx, ground)
        self._draw_back_hair(cx, head_top, head_h, head_w, neck_y, shoulder_y, colors)

        self._draw_arm_back_styled(cx, shoulder_y, bust_w, arm, colors, direction=-1)
        self._draw_arm_back_styled(cx, shoulder_y, bust_w, arm, colors, direction=1)

        self._draw_legs_and_shoes(cx, hip_y, knee_y, ankle_y, ground, lower_bust_w, thigh_w, calf_w, foot, colors)
        self._draw_outfit(cx, shoulder_y, hip_y, bust_w, lower_bust_w, colors)
        self._draw_head_and_face(cx, head_top, head_h, head_w, neck_y, shoulder_y, colors)
        self._draw_front_hair(cx, head_top, head_h, head_w, colors)

    def _draw_head_and_face(self, cx, head_top, head_h, head_w, neck_y, shoulder_y, colors):
        """绘制头部轮廓、脖子、眼睛、嘴巴与腮红（精简脖子基础宽度）。"""
        skull_top = head_top + head_h * 0.08

        # 减少脖子基础宽度
        neck_w_half = head_w * 0.21
        self.poly([cx - neck_w_half, neck_y - 5, cx + neck_w_half, neck_y - 5,
                   cx + neck_w_half * 1.05, shoulder_y + 18, cx - neck_w_half * 1.05, shoulder_y + 18], colors["skin"])
        self.create_oval(cx - head_w * .5, skull_top, cx + head_w * .5, skull_top + head_h * 1.0,
                         fill=colors["skin"], outline=VIEW_OUTLINE_DARK, width=1.5)

        eye_y = head_top + head_h * .62
        eye_w = max(9, head_w * .20)
        eye_h = max(6, head_h * .13)
        eye_offset = head_w * .21

        # 睫毛
        self.line([cx - eye_offset - eye_w, eye_y - eye_h * .46, cx - eye_offset + eye_w, eye_y - eye_h * .62],
                  colors["hair_base"], 1.5)
        self.line([cx + eye_offset - eye_w, eye_y - eye_h * .62, cx + eye_offset + eye_w, eye_y - eye_h * .46],
                  colors["hair_base"], 1.5)

        # 眼白
        self.create_oval(cx - eye_offset - eye_w / 2, eye_y - eye_h / 2, cx - eye_offset + eye_w / 2, eye_y + eye_h / 2,
                         fill=VIEW_EYE_WHITE, outline=VIEW_EYE_WHITE_LINE, width=1.5)
        self.create_oval(cx + eye_offset - eye_w / 2, eye_y - eye_h / 2, cx + eye_offset + eye_w / 2, eye_y + eye_h / 2,
                         fill=VIEW_EYE_WHITE, outline=VIEW_EYE_WHITE_LINE, width=1.5)

        # 虹膜
        pupil_w = eye_w * 0.56
        pupil_h = eye_h * 0.84
        self.create_oval(cx - eye_offset - pupil_w / 2, eye_y - pupil_h / 2, cx - eye_offset + pupil_w / 2, eye_y + pupil_h / 2,
                         fill=colors["eye"], outline="")
        self.create_oval(cx + eye_offset - pupil_w / 2, eye_y - pupil_h / 2, cx + eye_offset + pupil_w / 2, eye_y + pupil_h / 2,
                         fill=colors["eye"], outline="")

        # 瞳孔中心（加深）
        center_w = pupil_w * 0.6
        center_h = pupil_h * 0.6
        self.create_oval(cx - eye_offset - center_w / 2, eye_y - center_h / 2, cx - eye_offset + center_w / 2, eye_y + center_h / 2,
                         fill=VIEW_PUPIL, outline="")
        self.create_oval(cx + eye_offset - center_w / 2, eye_y - center_h / 2, cx + eye_offset + center_w / 2, eye_y + center_h / 2,
                         fill=VIEW_PUPIL, outline="")

        # 高光
        shine_size = 2.5
        self.create_oval(cx - eye_offset - pupil_w / 2, eye_y - pupil_h / 2, cx - eye_offset - pupil_w / 2 + shine_size, eye_y - pupil_h / 2 + shine_size,
                         fill=VIEW_HIGHLIGHT, outline="white", width=0.5)
        self.create_oval(cx + eye_offset - pupil_w / 2, eye_y - pupil_h / 2, cx + eye_offset - pupil_w / 2 + shine_size, eye_y - pupil_h / 2 + shine_size,
                         fill=VIEW_HIGHLIGHT, outline="white", width=0.5)

        # 嘴巴与腮红
        mouth_y = head_top + head_h * .87
        self.line([cx - head_w * .07, mouth_y, cx, mouth_y + 4.5, cx + head_w * .07, mouth_y], VIEW_MOUTH, 1.5)
        self.create_oval(cx - head_w * .43, head_top + head_h * .78, cx - head_w * .28, head_top + head_h * .87,
                         fill=VIEW_BLUSH, outline="")
        self.create_oval(cx + head_w * .28, head_top + head_h * .78, cx + head_w * .43, head_top + head_h * .87,
                         fill=VIEW_BLUSH, outline="")

    # ---------------- 拆分的具体部件绘制方法 ----------------

    def _draw_background(self, cx: float, ground: float):
        """绘制阴影地面背景。"""
        self.create_oval(cx - 90, ground - 15, cx + 90, ground + 10, fill=VIEW_SHADOW, outline="")

    def _draw_back_hair(self, cx, head_top, head_h, head_w, neck_y, shoulder_y, colors):
        """绘制后发部分。"""
        self.create_polygon([
            cx - head_w * .48, head_top + head_h * .60,
            cx - head_w * .65, head_top + head_h * .88,
            cx - head_w * .72, neck_y + 20,
            cx - head_w * .68, shoulder_y + 30,
            cx - head_w * .62, shoulder_y + 58,
            cx - head_w * .45, shoulder_y + 53,
            cx - head_w * .29, shoulder_y + 47,
            cx, neck_y + 38,
            cx + head_w * .29, shoulder_y + 47,
            cx + head_w * .45, shoulder_y + 53,
            cx + head_w * .62, shoulder_y + 58,
            cx + head_w * .68, shoulder_y + 30,
            cx + head_w * .72, neck_y + 20,
            cx + head_w * .65, head_top + head_h * .88,
            cx + head_w * .48, head_top + head_h * .60
        ], fill=colors["hair_base"], outline="", smooth=True, splinesteps=16)

        for side in (-1, 1):
            self.create_polygon([
                cx + side * head_w * .48, head_top + head_h * .60,
                cx + side * head_w * .58, head_top + head_h * .92,
                cx + side * head_w * .62, neck_y + 23,
                cx + side * head_w * .55, shoulder_y + 10,
                cx + side * head_w * .48, shoulder_y + 35,
                cx + side * head_w * .38, shoulder_y + 33,
                cx + side * head_w * .29, shoulder_y + 32,
                cx + side * head_w * .31, neck_y + 22
            ], fill=colors["hair_shade"], outline="", smooth=True, splinesteps=16)

    def _draw_arm_back_styled(self, cx, shoulder_y, bust_w, arm, colors, direction):
        """通用方法：绘制背到身后的手臂，强化肘关节外侧轮廓感，精准对称且无变型相交。

        :param direction: -1 代表图像左臂，1 代表图像右臂
        """
        # 1. 骨骼基准关键点（肩部作为原点锚点，起点向内向上移动）
        rs = (cx + direction * bust_w * 0.60, shoulder_y + 28)

        # 比例与臂长设定
        len_upper = arm * 0.40   # 大臂实际几何长度
        len_fore = arm * 0.34    # 小臂实际几何长度

        # 大臂方向与小臂方向的单位化向量（保留原有自然的背手姿势角度）
        dir_u_len = math.hypot(0.22, 0.35)
        u_dx = direction * (0.22 / dir_u_len) * len_upper
        u_dy = (0.35 / dir_u_len) * len_upper
        re = (rs[0] + u_dx, rs[1] + u_dy)  # 肘部坐标

        dir_f_len = math.hypot(-0.6, 0.28)
        f_dx = direction * (-0.6 / dir_f_len) * len_fore
        f_dy = (0.28 / dir_f_len) * len_fore
        rw = (re[0] + f_dx, re[1] + f_dy)  # 手腕坐标

        v = self._current_values or {}
        forearm_ratio = v.get("forearm_diameter_ratio", 0.05)
        h = max(self.winfo_height(), 100)
        total_height = h * 0.82

        w_avg = forearm_ratio * total_height * 0.65
        w_elbow = w_avg / 0.85
        w_wrist = 0.65 * w_elbow
        w_shoulder = 1.25 * w_elbow

        # 偏移辅助函数：根据向量 (p1->p2) 计算外侧(+1)和内侧(-1)的点
        def get_offset_pt(p1, p2, width, side_sign):
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            length = math.hypot(dx, dy) or 1.0
            nx = -dy / length * (width / 2.0) * direction * side_sign
            ny = dx / length * (width / 2.0) * direction * side_sign
            return (p1[0] + nx, p1[1] + ny)

        # 2. 独立计算大臂段与小臂段两侧的边界点 (+1 为外侧，-1 为内侧)
        s_outer = get_offset_pt(rs, re, w_shoulder, 1)
        s_inner = get_offset_pt(rs, re, w_shoulder, -1)

        # 肘部外侧关键点：计算一个略微向外凸出的“肘尖”控制点
        e_outer_u = get_offset_pt(re, rs, w_elbow, -1)
        e_outer_l = get_offset_pt(re, rw, w_elbow, 1)
        # 结合两段法线方向，向外侧额外延伸 1.5 像素，形成明确的肘尖结构
        elbow_tip_x = (e_outer_u[0] + e_outer_l[0]) * 0.5 + direction * 1.5
        elbow_tip_y = (e_outer_u[1] + e_outer_l[1]) * 0.5
        elbow_tip = (elbow_tip_x, elbow_tip_y)

        # 肘部内侧关键点（保持平滑过渡）
        e_inner_u = get_offset_pt(re, rs, w_elbow, 1)
        e_inner_l = get_offset_pt(re, rw, w_elbow, -1)

        # 手腕
        w_outer = get_offset_pt(rw, re, w_wrist, -1)
        w_inner = get_offset_pt(rw, re, w_wrist, 1)

        # 3. 排列控制点：外侧直接使用 elbow_tip 集中转折，减少过度插值；内侧保留中点插值
        arm_pts = [
            s_outer[0], s_outer[1],
            (s_outer[0] + e_outer_u[0]) * 0.5, (s_outer[1] + e_outer_u[1]) * 0.5,
            # 外侧肘尖
            elbow_tip[0], elbow_tip[1],
            (e_outer_l[0] + w_outer[0]) * 0.5, (e_outer_l[1] + w_outer[1]) * 0.5,
            w_outer[0], w_outer[1],
            # 手腕末端
            rw[0], rw[1] + 2,
            w_inner[0], w_inner[1],
            (e_inner_l[0] + w_inner[0]) * 0.5, (e_inner_l[1] + w_inner[1]) * 0.5,
            (e_inner_u[0] + e_inner_l[0]) * 0.5, (e_inner_u[1] + e_inner_l[1]) * 0.5,
            (s_inner[0] + e_inner_u[0]) * 0.5, (s_inner[1] + e_inner_u[1]) * 0.5,
            s_inner[0], s_inner[1]
        ]

        # 4. 绘制手臂
        self.create_polygon(
            arm_pts,
            fill=colors["skin_shade"],
            outline=VIEW_SKIN_LINE,
            width=1.2,
            smooth=True,
            splinesteps=16
        )

    def _draw_legs_and_shoes(self, cx, hip_y, knee_y, ankle_y, ground, bust_w, thigh_w, calf_w, foot, colors):
        """绘制双腿、膝盖细节与鞋子（配置统一的比肤色略暗腿部外轮廓线条）。"""
        lh, rh = cx - bust_w * .31, cx + bust_w * .31
        lk, rk = cx - bust_w * .39, cx + bust_w * .36
        la, ra = cx - bust_w * .30, cx + bust_w * .29

        for hx, kx, ax, direction in ((lh, lk, la, -1), (rh, rk, ra, 1)):
            calf_top_y = knee_y + (ankle_y - knee_y) * 0.35
            ankle_transition_y = ankle_y - (ankle_y - knee_y) * 0.15

            # 1. 腿部基础轮廓与外描边
            self.create_polygon([
                hx - direction * thigh_w * 0.5, hip_y - 5,
                hx - direction * thigh_w * 0.54, (hip_y + knee_y) * 0.45,
                kx - direction * thigh_w * 0.45, knee_y - 10,
                kx - direction * calf_w * 0.42, knee_y,
                kx - direction * calf_w * 0.52, calf_top_y,
                ax - direction * calf_w * 0.30, ankle_transition_y,
                ax - direction * calf_w * 0.22, ankle_y,
                ax + direction * calf_w * 0.22, ankle_y,
                ax + direction * calf_w * 0.28, ankle_transition_y,
                kx + direction * calf_w * 0.42, calf_top_y + (ankle_y - knee_y) * 0.08,
                kx + direction * calf_w * 0.38, knee_y,
                hx + direction * thigh_w * 0.45, (hip_y + knee_y) * 0.5,
                hx + direction * thigh_w * 0.5, hip_y - 5,
            ], fill=colors["skin"],
               outline=VIEW_SKIN_LINE, width=1.2,  # 统一修改：比 skin (#f8eade) 略暗柔和的肤色描边
               smooth=True, splinesteps=24)

            # 内部阴影区域保持无描边，避免线条叠加重影
            self.create_polygon([
                hx + direction * thigh_w * .12, hip_y + 4,
                hx + direction * thigh_w * .45, hip_y + 3,
                kx + direction * thigh_w * .41, (hip_y + knee_y) / 2,
                kx + direction * thigh_w * .38, knee_y,
                kx + direction * calf_w * .38, calf_top_y,
                kx + direction * calf_w * .25, ankle_transition_y,
                ax + direction * calf_w * .13, ankle_y,
                kx + direction * calf_w * .12, knee_y
            ], fill=colors["skin_shade"], outline="", smooth=True, splinesteps=12)

            self.create_oval(
                kx - thigh_w * .23, knee_y - thigh_w * .16,
                kx + thigh_w * .23, knee_y + thigh_w * .19,
                fill=colors["skin_shade"], outline=VIEW_SKIN_LINE, width=1  # 同步改用略暗肤线
            )
            self.create_line(
                [ax - calf_w * .22, ankle_y + 2, ax + calf_w * .22, ankle_y + 1],
                fill=VIEW_SKIN_LINE, width=1, smooth=True  # 同步改用略暗肤线
            )

            # 2. 鞋子绘制
            shoe_h = ground - ankle_y
            toe_x = ax + direction * (foot * 0.85)
            heel_x = ax - direction * (calf_w * 0.38)
            top_opening_y = ankle_y + shoe_h * 0.15

            sole_y = ground - 1
            sole_pts = [
                heel_x - direction * 1, sole_y - 2,
                toe_x + direction * 2, sole_y - 2,
                toe_x + direction * 1, ground,
                heel_x, ground
            ]
            self.create_polygon(
                sole_pts,
                fill=VIEW_CLOTH_DARK, outline=VIEW_CLOTH_DARK_LINE, width=1,
                smooth=True, splinesteps=8
            )

            shoe_upper_pts = [
                heel_x, top_opening_y,
                ax - direction * (calf_w * 0.1), top_opening_y + shoe_h * 0.25,
                ax + direction * (calf_w * 0.2), top_opening_y + shoe_h * 0.2,
                ax + direction * (foot * 0.5), top_opening_y + shoe_h * 0.35,
                toe_x, sole_y - 1,
                heel_x, sole_y - 1
            ]
            self.create_polygon(
                shoe_upper_pts,
                fill=colors["cloth_white"], outline=VIEW_OUTLINE_DARK, width=1.2,
                smooth=True, splinesteps=16
            )

            strap_x1 = ax - direction * (calf_w * 0.15)
            strap_x2 = ax + direction * (calf_w * 0.25)
            strap_y = top_opening_y + shoe_h * 0.22
            self.create_line(
                [strap_x1, strap_y, strap_x2, strap_y + 1],
                fill=colors["navy_base"], width=2, capstyle="round"
            )

    def _draw_outfit(self, cx, shoulder_y, hip_y, bust_w, lower_bust_w, colors):
        """绘制水手服（采用增宽后的 lower_bust_w 绘制裙子）。"""
        v = self._current_values or {}
        h = max(self.winfo_height(), 100)
        total_height = h * 0.82
        ground = h - 30

        # 白色部位的统一较暗边线与短袖变暗填充色
        dark_white_outline = VIEW_WHITE_LINE
        sleeve_fill = VIEW_SLEEVE

        # 1. 计算膝盖位置与裙子范围
        knee_y = ground - (v.get("knee_height_ratio", 0.25) * total_height)
        torso_h = max(hip_y - shoulder_y, 10.0)
        skirt_top = hip_y - torso_h * 0.33
        thigh_mid_y = (hip_y + knee_y) * 0.5

        skirt_bot_mid = thigh_mid_y + 4
        skirt_bot_side = thigh_mid_y - 6

        # 2. 绘制裙子主体与裙褶
        self.create_polygon([
            cx - lower_bust_w * .56, skirt_top,
            cx, skirt_top + 2,
            cx + lower_bust_w * .56, skirt_top,
            cx + lower_bust_w * .75, (skirt_top + skirt_bot_side) / 2,
            cx + lower_bust_w * .82, skirt_bot_side,
            cx + lower_bust_w * .50, skirt_bot_mid,
            cx, skirt_bot_mid + 3,
            cx - lower_bust_w * .50, skirt_bot_mid,
            cx - lower_bust_w * .82, skirt_bot_side,
            cx - lower_bust_w * .75, (skirt_top + skirt_bot_side) / 2
        ], fill=colors["navy_base"], outline="", smooth=True, splinesteps=16)

        for offset in (-.44, -.15, .15, .44):
            self.create_line(
                [cx + lower_bust_w * offset, skirt_top + 4, cx + lower_bust_w * offset * 1.25, skirt_bot_mid - 6],
                fill=colors["navy_line"], width=2, smooth=True)

        # 3. 短袖绘制（彻底解决自交叉沙漏问题，精准对齐几何轮廓）
        arm_span = v.get("arm_span_ratio", 0.3) * total_height
        forearm_ratio = v.get("forearm_diameter_ratio", 0.05)

        # 1:1 计算大臂粗细
        arm_w_avg = forearm_ratio * total_height * 0.65
        arm_w_elbow = arm_w_avg / 0.85
        w_shoulder = 1.25 * arm_w_elbow

        # 大臂骨骼朝向与单位法线
        len_upper = arm_span * 0.38
        dir_u_len = math.hypot(0.22, 0.35)
        u_dx = 0.22 / dir_u_len
        u_dy = 0.35 / dir_u_len

        sleeve_len = len_upper * 0.48
        sleeve_w = w_shoulder * 1.15  # 袖宽与大臂直径增幅保持 1:1

        for side in (-1, 1):
            # 1. 微调：更靠外侧、高度稍低的肩膀顶点
            s_outer = (cx + side * bust_w * 0.78, shoulder_y + 16)

            # 2. 腋下内侧连接点
            s_inner = (cx + side * bust_w * 0.45, shoulder_y + 50)

            # 3. 大臂中轴线延伸出的袖口中心
            rs = (cx + side * bust_w * 0.60, shoulder_y + 32)
            c_center = (rs[0] + side * u_dx * sleeve_len, rs[1] + u_dy * sleeve_len)

            # 4. 显式计算向上/向外的法线偏移（保证 c_outer 绝对在大臂外上方，c_inner 在内下方）
            perp_x = side * u_dy
            perp_y = -u_dx

            # 袖口外侧点与内侧点
            c_outer = (c_center[0] + perp_x * (sleeve_w * 0.5), c_center[1] + perp_y * (sleeve_w * 0.5))
            c_inner = (c_center[0] - perp_x * (sleeve_w * 0.5), c_center[1] - perp_y * (sleeve_w * 0.5))

            # 5. 根据左右侧(side)显式调整多边形点集的排列顺序，保证无论左右都是凸多边形无交叉
            if side == -1:  # 左臂
                sleeve_pts = [
                    s_outer[0], s_outer[1],
                    c_outer[0], c_outer[1],
                    c_inner[0], c_inner[1],
                    s_inner[0], s_inner[1]
                ]
            else:  # 右臂
                sleeve_pts = [
                    s_outer[0], s_outer[1],
                    s_inner[0], s_inner[1],
                    c_inner[0], c_inner[1],
                    c_outer[0], c_outer[1]
                ]

            # 绘制凸多边形短袖，填充颜色变暗，采用统一较暗的白色边线
            self.create_polygon(sleeve_pts, fill=sleeve_fill, outline=dark_white_outline, width=1.0)

            # 绘制平行于大臂截面的袖口水手线
            self.create_line([c_outer[0], c_outer[1], c_inner[0], c_inner[1]],
                             fill=VIEW_STITCH, width=2, capstyle="round")

        # 4. 上衣躯干主体（边线采用统一较暗的白色边线）
        self.create_polygon([
            cx - bust_w * .60, shoulder_y + 22,
            cx - bust_w * .45, shoulder_y + 16,
            cx, shoulder_y + 20,
            cx + bust_w * .45, shoulder_y + 16,
            cx + bust_w * .60, shoulder_y + 22,
            cx + lower_bust_w * .53, skirt_top + 5,
            cx, skirt_top + 13,
            cx - lower_bust_w * .53, skirt_top + 5
        ], fill=colors["cloth_white"], outline=dark_white_outline, width=1.0, smooth=True)

        # 5. 肩部遮罩
        # 覆盖短袖与水手领之间的肩部连接区域
        mask_fill = VIEW_SLEEVE
        mask_outline = dark_white_outline

        for side in (-1, 1):
            if side == -1:
                pts = [
                    # 外侧肩头
                    (cx - bust_w * 0.78, shoulder_y + 16),
                    # 肩部上缘
                    (cx - bust_w * 0.55, shoulder_y + 17),
                    # 向下覆盖肩部
                    (cx - bust_w * 0.36, shoulder_y + 55),
                    # 回到外侧下方
                    (cx - bust_w * 0.57, shoulder_y + 38),
                ]
            else:
                pts = [
                    # 外侧肩头
                    (cx + bust_w * 0.78, shoulder_y + 16),
                    # 肩部上缘
                    (cx + bust_w * 0.55, shoulder_y + 17),
                    # 向下覆盖肩部
                    (cx + bust_w * 0.36, shoulder_y + 57),
                    # 回到外侧下方
                    (cx + bust_w * 0.57, shoulder_y + 39),
                ]

            self.create_polygon(
                pts,
                fill=mask_fill,
                outline=mask_outline,
                width=1.0,
                joinstyle="round",
            )

        # 6. 水手领与蝴蝶结
        self.poly([cx - bust_w * .63, shoulder_y + 18, cx - bust_w * .19, shoulder_y + 10,
                   cx, shoulder_y + 51, cx - bust_w * .15, shoulder_y + 82, cx - bust_w * .50, shoulder_y + 30],
                  colors["navy_base"])
        self.poly([cx + bust_w * .63, shoulder_y + 18, cx + bust_w * .19, shoulder_y + 10,
                   cx, shoulder_y + 51, cx + bust_w * .15, shoulder_y + 82, cx + bust_w * .50, shoulder_y + 30],
                  colors["navy_base"])
        self.poly([cx - 16, shoulder_y + 45, cx, shoulder_y + 64, cx + 16, shoulder_y + 45,
                   cx + 10, shoulder_y + 90, cx, shoulder_y + 101, cx - 10, shoulder_y + 90], colors["navy_base"])


    def _draw_front_hair(self, cx, head_top, head_h, head_w, colors):
        """绘制前发与刘海（最顶层）。"""
        eye_y = head_top + head_h * .58
        eye_h = max(5.0, head_h * .15)

        self.create_polygon([
            cx - head_w * .58, head_top + head_h * .55,
            cx - head_w * .50, head_top + head_h * .22,
            cx - head_w * .22, head_top + head_h * .02,
            cx, head_top,
            cx + head_w * .22, head_top + head_h * .02,
            cx + head_w * .45, head_top + head_h * .22,
            cx + head_w * .58, head_top + head_h * .55,
            cx + head_w * .35, eye_y + eye_h * .20,
            cx + head_w * .22, head_top + head_h * .48,
            cx + head_w * .03, eye_y + eye_h * .35,
            cx - head_w * .20, head_top + head_h * .50,
            cx - head_w * .38, eye_y + eye_h * .20
        ], fill=colors["hair_base"], outline="", smooth=True)

        for side in (-1, 1):
            self.create_polygon([
                cx + side * head_w * .58, head_top + head_h * .48,
                cx + side * head_w * .65, head_top + head_h * .70,
                cx + side * head_w * .62, head_top + head_h * .82,
                cx + side * head_w * .55, head_top + head_h * .90,
                cx + side * head_w * .44, head_top + head_h * .77,
                cx + side * head_w * .35, head_top + head_h * .65,
                cx + side * head_w * .22, head_top + head_h * .46
            ], fill=colors["hair_shade"], outline="", smooth=True)


# ==================== 身材预览 PNG 渲染（未上传形象时替代角色形象） ====================



def _flatten_points(points: list) -> List[tuple]:
    """把 Tk 多边形/折线的点列表（扁平行或[(x,y),...]）规范为 [(x, y), ...]。"""
    if not points:
        return []
    if isinstance(points[0], (tuple, list)):
        return [(float(x), float(y)) for x, y in points]
    out = []
    for i in range(0, len(points) - 1, 2):
        out.append((float(points[i]), float(points[i + 1])))
    return out


def _tk_spline_points(pts: List[tuple], closed: bool = False,
                      steps: int = 16) -> List[tuple]:
    """Tk 画布 smooth=True 的样条精确复刻（经实测 Tk 8.6 输出验证）。

    Tk 以相邻顶点对中点为端点、顶点为控制点构造三次贝塞尔等价曲线：
    - 闭合（多边形，n 段）：从 p[i]~p[i+1] 中点 到 p[i+1]~p[i+2] 中点，控制点 p[i+1]（下标取模）；
    - 开放（折线，n-1 段）：首段 p0 -> p1~p2 中点（控制点 p1），
      末段 p[n-2]~p[n-1] 中点 -> p[n-1]（控制点 p[n-1]），中间段同闭合规则。
    返回展平后的折线点列（每段 steps 个采样点，端点不重复）。"""
    n = len(pts)
    if n < 2:
        return pts
    if n == 2:
        return [pts[0], pts[1]]

    def mid(a, b) -> tuple:
        return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)

    segs: List[tuple] = []
    if closed:
        for i in range(n):
            p0, p1, p2 = pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
            segs.append((mid(p0, p1), p1, mid(p1, p2)))
    else:
        if n == 3:
            # Tk 特例：三点线为一条经过中间点的二次贝塞尔
            segs.append((pts[0], pts[1], pts[2]))
        else:
            segs.append((pts[0], pts[1], mid(pts[1], pts[2])))
            for i in range(1, n - 2):
                segs.append((mid(pts[i], pts[i + 1]), pts[i + 1], mid(pts[i + 1], pts[i + 2])))
            segs.append((mid(pts[n - 2], pts[n - 1]), pts[n - 1], pts[n - 1]))

    out: List[tuple] = []
    for s, c, e in segs:
        cx1 = s[0] + (c[0] - s[0]) * (2.0 / 3.0)
        cy1 = s[1] + (c[1] - s[1]) * (2.0 / 3.0)
        cx2 = e[0] + (c[0] - e[0]) * (2.0 / 3.0)
        cy2 = e[1] + (c[1] - e[1]) * (2.0 / 3.0)
        for k in range(steps):
            t = k / steps
            mt = 1.0 - t
            out.append((
                mt * mt * mt * s[0] + 3 * mt * mt * t * cx1
                + 3 * mt * t * t * cx2 + t * t * t * e[0],
                mt * mt * mt * s[1] + 3 * mt * mt * t * cy1
                + 3 * mt * t * t * cy2 + t * t * t * e[1],
            ))
    out.append((segs[-1][2][0], segs[-1][2][1]))
    return out



_PREVIEW_RENDER_W = 450     # 渲染宽（预览图导出用，放大 2 倍）
_PREVIEW_RENDER_H = 800     # 渲染高
_PREVIEW_RENDER_BG = (0, 0, 0, 0)   # 透明背景（RGBA）
_PREVIEW_AA = 4             # 超采样倍数：放大渲染后缩回目标尺寸以获得抗锯齿边缘
_PREVIEW_DESIGN_W = _CANVAS_W   # 线宽按设计宽等比放大


class BodyPreviewPilCanvas(BodyPreviewPainter):
    """把 BodyPreviewPainter 的绘制逻辑重定向到 PIL 图片，用于把身材预览生成为 PNG。

    复用父类全部部件绘制方法（refresh 及各 _draw_* 方法），仅把画布图元
    create_oval / create_polygon / create_line 改写为绘制到 PIL ImageDraw。
    """

    def __init__(self, width: int, height: int, background=_PREVIEW_RENDER_BG):
        self.preview_width = width
        self.preview_height = height
        self._current_values: Optional[dict] = None
        self._dark = appearance.is_dark()
        self._canvas_width = width
        self._canvas_height = height
        self._background = background
        # 线宽按设计宽等比放大，使 PNG（放大的画布）与对话框预览的线型粗细一致
        self._stroke_scale = width / float(_PREVIEW_DESIGN_W)
        self._image = None
        self._draw = None

    # ---- 让父类渲染逻辑按固定尺寸工作，而非查询真实 Tk 控件 ----
    def winfo_width(self) -> int:
        return self._canvas_width

    def winfo_height(self) -> int:
        return self._canvas_height

    def delete(self, *_args):
        pass

    def _dim_kwargs(self, kwargs):
        """与身材对话框预览一致：暗色模式下对亮色通道做曝光衰减。"""
        if not self._dark:
            return kwargs
        for key in ("fill", "outline"):
            color = kwargs.get(key)
            if color and _preview_dim_color(color) != color:
                kwargs[key] = _preview_dim_color(color)
        return kwargs

    @staticmethod
    def _pil_color(color):
        if not color or color == "":
            return None
        return color

    def _stroke_width(self, width) -> int:
        return max(1, int(round((width or 1) * self._stroke_scale)))

    # ---- 图元绘制到 PIL（等价复刻 Tk 画布的线宽/圆角/平滑规则） ----
    def create_oval(self, x1, y1, x2, y2, fill=None, outline=None, width=1, **kwargs):
        kwargs = self._dim_kwargs({"fill": fill, "outline": outline, "width": width})
        fill = kwargs["fill"]
        outline = kwargs["outline"]
        width = kwargs["width"]
        w = self._stroke_width(width)
        if fill:
            self._draw.ellipse([x1, y1, x2, y2], fill=self._pil_color(fill), outline=None)
        if outline and w > 0:
            # Tk 描边以边界线居中（内外各一半），PIL 描边在包围盒内侧，扩大包围盒模拟居中
            hw = w / 2.0
            self._draw.ellipse([x1 - hw, y1 - hw, x2 + hw, y2 + hw],
                               fill=None, outline=self._pil_color(outline), width=w)

    def create_polygon(self, points, fill=None, outline=None, width=1,
                       smooth=False, splinesteps=None, joinstyle="round", **kwargs):
        kwargs = self._dim_kwargs({"fill": fill, "outline": outline, "width": width})
        fill = kwargs["fill"]
        outline = kwargs["outline"]
        width = kwargs["width"]
        pts = _flatten_points(points)
        if smooth:
            pts = _tk_spline_points(pts, closed=True)
        w = self._stroke_width(width)
        if len(pts) < 3:
            self._draw.line(
                pts, fill=self._pil_color(fill) or "black",
                width=w, joint="curve")
            return None
        self._draw.polygon(
            pts, fill=self._pil_color(fill),
            outline=self._pil_color(outline), width=w)
        return None

    def create_line(self, points, fill=None, width=1, smooth=False,
                    capstyle="butt", joinstyle="round", **kwargs):
        kwargs = self._dim_kwargs({"fill": fill, "width": width, "capstyle": capstyle})
        fill = kwargs["fill"]
        width = kwargs["width"]
        capstyle = kwargs["capstyle"]
        pts = _flatten_points(points)
        if smooth and len(pts) >= 3:
            pts = _tk_spline_points(pts, closed=False)
        color = self._pil_color(fill) or "black"
        w = self._stroke_width(width)
        if len(pts) < 2:
            if pts:
                self._draw.point(pts, fill=color)
            return None
        self._draw.line(pts, fill=color, width=w, joint="curve")
        if capstyle == "round" and w > 1:
            # PIL 线条端点平头，Tk round 端点用端点圆补上圆头
            r = w / 2.0
            for p in (pts[0], pts[-1]):
                self._draw.ellipse(
                    [p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=color)
        return None

    def refresh(self):
        if not self._current_values:
            return
        self._image = Image.new("RGBA", (self._canvas_width, self._canvas_height),
                                self._background)
        self._draw = ImageDraw.Draw(self._image, "RGBA")
        BodyPreviewPainter.refresh(self)


def body_ratio_values(body_parts: dict, height: float) -> dict:
    """把按身高换算的绝对部位值还原为预览画布所需的比例值（身高等同缩放）。

    与 CreationService.get_body_parts 互为逆运算：part / height 即预设比例，
    进阶后身材比例不变。
    """
    values = {}
    safe = height or 1.6
    for cn, (attr, *_) in _PART_PARAMS.items():
        part = body_parts.get(cn)
        if part:
            values[attr] = part / safe
    return values


def render_body_preview_image(body_parts: dict, height: float = 1.6,
                              width: int = _PREVIEW_RENDER_W,
                              height_px: int = _PREVIEW_RENDER_H) -> Optional[Image.Image]:
    """根据身材部位数据渲染身材比例预览图（RGBA PIL 图片，背景透明）。

    仅需“与身高成正比”的部位值，身高只用于把绝对尺度还原为比例。
    以超采样倍数放大绘制后 Lanczos 缩回目标尺寸，得到与对话框预览一致的抗锯齿边缘。
    """
    if not body_parts:
        return None
    aw = width * _PREVIEW_AA
    ah = height_px * _PREVIEW_AA
    canvas = BodyPreviewPilCanvas(aw, ah, _PREVIEW_RENDER_BG)
    canvas.update_values(body_ratio_values(body_parts, height))
    img = canvas._image
    if img is None:
        return None
    if _PREVIEW_AA > 1 and (aw, ah) != (width, height_px):
        img = img.resize((width, height_px), Image.Resampling.LANCZOS)
    return img


def render_preset_preview_image(preset,
                                width: int = _PREVIEW_RENDER_W,
                                height_px: int = _PREVIEW_RENDER_H) -> Optional[Image.Image]:
    """按身材预设直接渲染预览图（参数面板用：未创建角色时只有比例、没有绝对尺寸）。

    预览画布要的本来就是「与身高成正比」的比例值，而预设里存的正是比例，因此
    直接取用即可，不必先换算成绝对部位值再除回去。
    """
    if preset is None:
        return None
    values = {}
    for attr, *_rest in _PART_PARAMS.values():
        value = getattr(preset, attr, None)
        if value is not None:
            values[attr] = value
    if not values:
        return None

    aw = width * _PREVIEW_AA
    ah = height_px * _PREVIEW_AA
    canvas = BodyPreviewPilCanvas(aw, ah, _PREVIEW_RENDER_BG)
    canvas.update_values(values)
    img = canvas._image
    if img is None:
        return None
    if _PREVIEW_AA > 1 and (aw, ah) != (width, height_px):
        img = img.resize((width, height_px), Image.Resampling.LANCZOS)
    return img


def render_body_preview_to_file(body_parts: dict, height: float, out_path: str) -> str:
    """将身材比例预览渲染为 PNG 写入 out_path，返回路径；失败返回 ''。"""
    img = render_body_preview_image(body_parts, height)
    if img is None:
        return ""
    try:
        img.save(out_path, "PNG")
        return out_path
    except Exception:
        return ""
