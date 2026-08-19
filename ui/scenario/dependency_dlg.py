"""触发器依赖关系检查对话框。

- 用 networkx 建立副本触发器的前置条件依赖有向图，并检测循环依赖；
- 用 graphviz 将依赖图渲染成图片；
- 在对话框中展示图片，并以红色高亮循环依赖中的触发器和边。
依赖方向：边 A → B 表示“B 依赖 A”，即 A 是 B 的前置条件（A 必须先触发）。
"""
import io
import os
import shutil

import customtkinter as ctk
import networkx as nx
import graphviz as gv
from PIL import Image

from ui.common.dialogs import BaseDialog
from ui.common.fonts import graphviz_font
from ui.common.theme import (
    GRAPH_EDGE_ERR, GRAPH_EDGE_NORMAL, GRAPH_NODE_ERR_BORDER, GRAPH_NODE_ERR_FILL,
    GRAPH_NODE_OUTLINE, ACTION_FILL_BACKGROUND, ACTION_FILL_ENDING,
    ACTION_FILL_INSERT, ACTION_FILL_NONE, ACTION_FILL_OPTION, ACTION_FILL_SENSITIVITY,
    TEXT, SOFT, STATUS_ERR, STATUS_OK,
)

# 动作类型 → 展示名、节点填充色
_ACTION_LABEL = {
    "insert": "插入段落",
    "option": "选项分支",
    "sensitivity": "敏感效果",
    "ending": "结局",
    "background": "背景图",
    "none": "空触发器",
}
_ACTION_FILL = {
    "insert": ACTION_FILL_INSERT,
    "option": ACTION_FILL_OPTION,
    "sensitivity": ACTION_FILL_SENSITIVITY,
    "ending": ACTION_FILL_ENDING,
    "background": ACTION_FILL_BACKGROUND,
    "none": ACTION_FILL_NONE,
}

# 图例文本
_LEGEND = ("红色填充节点 / 红色箭头边：循环依赖；方框内括号为动作类型。"
           "箭头 A→B 表示 “A 是 B 的前置条件”。")


def _find_dot():
    """定位 Graphviz 的 dot 可执行文件（可能在 PATH，也可能未加入 PATH）。"""
    p = shutil.which("dot")
    if p and os.path.isfile(p):
        return p
    candidates = [
        r"C:\Program Files\Graphviz\bin\dot.exe",
        r"C:\Program Files (x86)\Graphviz\bin\dot.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Graphviz\bin\dot.exe"),
        "/opt/homebrew/bin/dot",
        "/usr/local/bin/dot",
        "/usr/local/opt/graphviz/bin/dot",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _build_graph(triggers):
    """返回 (DiGraph, 参与循环的节点集合, 各循环的节点列表)。"""
    G = nx.DiGraph()
    names = set()
    for t in triggers:
        n = t.get("name")
        if n:
            names.add(n)
            G.add_node(n)
    for t in triggers:
        n = t.get("name")
        if not n:
            continue
        for pre in t.get("precondition_names", []):
            if pre in names:
                G.add_edge(pre, n)  # pre 是 n 的前置条件
    cycles = []
    try:
        cycles = list(nx.simple_cycles(G))
    except Exception:
        cycles = []
    cycle_nodes = set()
    for cyc in cycles:
        cycle_nodes.update(cyc)
    return G, cycle_nodes, cycles


def _render_png(triggers, G, cycle_nodes):
    """渲染依赖图为 PNG 字节；dot 缺失时抛异常。"""
    dot = _find_dot()
    if not dot:
        raise RuntimeError("未找到 Graphviz 的 dot 可执行文件")
    bin_dir = os.path.dirname(dot)
    os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")

    type_by_name = {t.get("name"): t.get("action_type") for t in triggers}

    font_name = graphviz_font()
    d = gv.Digraph()
    d.attr(rankdir="TB", bgcolor="white",
           nodesep="0.35", ranksep="0.45", fontname=font_name)
    d.attr("node", shape="box", style="filled", fontname=font_name)

    for n in G.nodes():
        at = type_by_name.get(n)
        label = n
        if at and at != "unknown":
            label = f"{n}\n({_ACTION_LABEL.get(at, at)})"
        if n in cycle_nodes:
            d.node(n, label=label, fillcolor=GRAPH_NODE_ERR_FILL, color=GRAPH_NODE_ERR_BORDER,
                   fontcolor="white", penwidth="1.6")
        else:
            d.node(n, label=label, fillcolor=_ACTION_FILL.get(at, ACTION_FILL_NONE),
                   color=GRAPH_NODE_OUTLINE)

    for a, b in G.edges():
        if a in cycle_nodes and b in cycle_nodes:
            d.edge(a, b, color=GRAPH_EDGE_ERR, penwidth="1.8")
        else:
            d.edge(a, b, color=GRAPH_EDGE_NORMAL)

    return d.pipe("png")


def _fit_size(img, max_w, max_h):
    """按最大显示框等比缩小，但不放大。"""
    w, h = img.size
    scale = min(1.0, max_w / w, max_h / h)
    return (max(1, round(w * scale)), max(1, round(h * scale)))


class DependencyGraphDialog(BaseDialog):
    """展示触发器依赖关系图并高亮循环依赖的对话框。"""

    def __init__(self, parent, triggers):
        super().__init__(parent)
        self.title("触发器依赖关系")
        self.geometry("780x640")
        self.minsize(600, 480)
        self.resizable(True, True)
        self.triggers = triggers or []
        self.G = None
        self.cycle_nodes = set()
        self.cycles = []
        self.result = None
        self.transient(parent)
        self.grab_set()
        self._build_ui()
        self._center_dialog(parent)
        self.wait_window()

    def _build_ui(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill='both', expand=True, padx=14, pady=12)

        if not self.triggers:
            ctk.CTkLabel(main, text="当前方案没有触发器。",
                         font=self.UI_FONT,
                         text_color=TEXT).pack(pady=40)
            ctk.CTkButton(main, text="关闭", width=88, height=28,
                          font=self.UI_FONT,
                          command=self.destroy).pack(pady=10)
            return

        # 分析依赖、检测循环
        self.G, self.cycle_nodes, self.cycles = _build_graph(self.triggers)

        # 顶部汇总
        summary = self._build_summary()
        self.summary_label = ctk.CTkLabel(
            main, text=summary, justify="left", wraplength=720,
            font=self.UI_FONT,
            text_color=STATUS_ERR if self.cycle_nodes else STATUS_OK)
        self.summary_label.pack(fill='x', padx=4, pady=(0, 6))

        # 图例
        ctk.CTkLabel(main, text=_LEGEND, justify="left", wraplength=720,
                     font=self.UI_FONT_SMALL,
                     text_color=SOFT).pack(
                         fill='x', padx=4, pady=(0, 6))

        # 图片滚动区
        self.view = ctk.CTkScrollableFrame(main, fg_color="transparent")
        self.view.pack(fill='both', expand=True)

        # 底部按钮
        footer = ctk.CTkFrame(main, fg_color="transparent")
        footer.pack(fill='x', pady=(8, 0))
        self.render_status = ctk.CTkLabel(
            footer, text="", font=self.UI_FONT,
            text_color=STATUS_ERR)
        self.render_status.pack(side='left', padx=4)
        ctk.CTkButton(footer, text="关闭", width=88, height=28,
                      font=self.UI_FONT,
                      command=self.destroy).pack(side='right', padx=4)

        self._render_graph()

    def _build_summary(self):
        if not self.cycle_nodes:
            return "未发现循环依赖，触发依赖关系正常。\n共 %d 个触发器。" % len(self.triggers)
        lines = []
        lines.append("%d 处循环依赖、%d 个触发器互为前置，将永远无法触发：" % (
            len(self.cycles), len(self.cycle_nodes)))
        for cyc in self.cycles:
            lines.append("  " + " → ".join(cyc) + " → " + list(cyc)[0])
        return "\n".join(lines)

    def _render_graph(self):
        try:
            png = _render_png(self.triggers, self.G, self.cycle_nodes)
            img = Image.open(io.BytesIO(png)).convert("RGBA")
        except Exception as exc:
            self.render_status.configure(
                text=f"无法渲染依赖图：{exc}\n请确认已安装 Graphviz 并可通过 PATH 调用 dot。")
            return
        size = _fit_size(img, 730, 500)
        self._image = ctk.CTkImage(light_image=img, dark_image=img, size=size)
        img_label = ctk.CTkLabel(self.view, image=self._image, text="")
        img_label.pack(padx=6, pady=6)
