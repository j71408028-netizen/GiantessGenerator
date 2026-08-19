#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
新闻填充生成器 (仿 name_filler)
- 指定 尺寸等级 / 纪念 / 介入度 / 破坏性，调用 AI 生成新闻正文
- 正文自动使用 {name} / {nick} 占位符，并可附带 {wordN} 等替换符
- AI 一次返回 3 条候选新闻（正文 + 替换队列1/2）
- 勾选后追加到 data/static/news/default.csv
- 尺寸等级留空表示垃圾新闻（不参与索引）

用法：在项目根目录执行
    python -m developer_tools.news_filler
（脚本内已自动把项目根目录加入 sys.path，直接运行脚本亦可。）
"""

import csv
import io
import json
import os
import re
import sys
import tkinter as tk
from tkinter import ttk

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import ui.common.dialogs
from ai import get_ai_client


NEWS_CSV = os.path.join(_BASE_DIR, "data", "static", "news", "default.csv")
NEWS_COLUMNS = [
    "尺寸等级", "纪念", "介入度", "破坏性", "正文",
    "替换队列1", "替换队列2",
]

# 尺寸等级（空串表示垃圾新闻）
SIZE_CATEGORIES = ["", "small", "medium", "large", "huge", "colossal"]
SIZE_DISPLAY = {
    "": "垃圾新闻（不参与索引）",
    "small": "7.5~50m", "medium": "50~300m",
    "large": "300~1800m", "huge": "1800~10000m",
    "colossal": "10~150km",
}
MEMORIALS = ["无", "生日", "元旦", "愚人节", "儿童节", "万圣节"]
LEVELS = [str(i) for i in range(5)]


def _decode_csv(raw):
    """尝试按 utf-8-sig 解码，失败再按 GBK，避免已有文件被误存为其他编码。"""
    for enc in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _clean_body(body):
    """正文收尾：截断 AI 输出的 `"` 泄漏内容，末尾统一为句号。"""
    idx = body.find('"')
    if idx != -1:
        body = body[:idx]
    body = body.strip()
    m = re.match(r"^(.*)[。！？!?]\s*(\{[0-9a-z]+\})$", body)
    if m:
        body = m.group(1) + m.group(2)
    if not body.endswith(("。", "！", "？")):
        body += "。"
    return body


def _clean_queue(cell):
    """清理替换队列单元格中的 `"` 泄漏。"""
    return cell.replace('"', " ").strip()


def _load_news_rows(path):
    """读取现有新闻 CSV，返回 (表头, 数据行)；文件缺失或为空时用默认表头。"""
    header = list(NEWS_COLUMNS)
    rows = []
    if os.path.isfile(path):
        raw = open(path, "rb").read()
        if raw.strip():
            data = list(csv.reader(io.StringIO(_decode_csv(raw), newline='')))
            if data:
                header = data[0]
                rows = data[1:]
    return header, rows


def _fallback_news(size):
    """备用正文（未连接 AI 时使用），与垃圾/索引两种模式对应。"""
    if not size:
        return [
            ("某小区楼下的路灯又坏了，居民们表示已经习惯。", "路灯,照明", "居民,住户"),
            ("网友发现某地一处红绿灯连续出错，引发小范围讨论。", "网友,网民", "红绿灯,信号灯"),
            ("一条无意义的网络流行语突然走红，众人纷纷模仿。", "流行语,热词", "走红,刷屏"),
        ]
    return [
        ("{name}的近况引发了广泛讨论，观察员认为局势暂时平稳。", "观察员,分析人士", "平稳,缓和"),
        ("城市上空出现了新的巨大足迹，专家称其来源仍在调查。", "足迹,痕迹", "调查,侦查"),
        ("卫星图像显示，{name}的影响范围正在扩大，世界各地都在等待进一步消息。", "卫星图像,高空影像", "影响范围,活动范围"),
    ]


def generate_news(size, memorial, intrusion, destruction, client):
    """
    调用 AI 生成 3 条新闻候选。
    返回 [(正文, 替换队列1, 替换队列2), ...]
    """
    if client is None:
        return _fallback_news(size)

    size_desc = SIZE_DISPLAY.get(size, "无尺寸要求")

    if not size:
        # ── 垃圾新闻：与巨大娘无关，现实/网络小事 ──
        system_prompt = (
            "你是某近似现实的虚构世界观中的新闻编辑助手，为一家省级网络媒体撰写内容简单的短新闻稿。\n"
            "1. 这些新闻应真实可信，符合现实逻辑，内容无足轻重（如社区琐事、网络趣闻、气象变化等）。除此之外题材不限。\n"
            "2. 正文为一句到两句话的中文新闻，包括时间、地点、人物、事件等要素（可用“美国某地”、“今日”、“身为管理员的张先生”等泛化词甚至部分省略，语意完整即可）。\n"
            "3. （可选）在正文中用 {1}、{2} 占位，分别对应两个替换队列（即在同一语句框架中提供不同的变体内容）。此时提供替换队列1/2（逗号分隔的候选词）。\n"
            "4. 生成 3 条不同内容的候选。只输出一个JSON数组，形如 [{\"正文\":\"...\",\"替换队列1\":\"a,b\",\"替换队列2\":\"c\"}, ...]，不要有其他文字。"
        )
        user_prompt = "请生成 3 条与无足轻重的日常背景小新闻。"
    else:
        # ── 索引新闻：交代假想世界的设定与近况 ──
        system_prompt = f"""
        你是“巨大化少女（巨人娘）”世界观中的新闻编辑助手，为一家省级媒体撰写调查版面的新闻稿。

        ## 世界观设定
        世界正面临“巨大化少女”这一超自然现象的冲击。由于少女的体型极其庞大（几十米至上百公里），她的出现不是单纯的个体事件，而是**一种不可抗拒的、类似常态化自然灾害或地缘政治级别的宏观现象**。新闻稿旨在报道**人类社会（在经济、政治、科技、艺术、文教、生态等各领域）应对、消化、或受制于这种巨型存在时的社会现状与连锁反应**。

        ## 参数说明
        - **尺寸等级**：少女的体型大小，决定了她能影响的物理范围。
          - small (7.5~50m): 相当于中低层建筑，可影响社区、街道。
          - medium (50~300m): 高于大多数建筑，可影响街区、港口。
          - large (300~1800m): 高于摩天大楼，可影响城区、摩天大楼群。
          - huge (1800~10000m): 以公里计，可影响城市、山脉。
          - colossal (10~150km): 以数十上百公里计，可影响城市群、国家、海洋。

        - **纪念**：生日或节日（元旦、愚人节、儿童节、万圣节）；其他日期为“无”。若是纪念日，新闻应着重报道人类社会各界自发或官方组织的相关活动。

        - **介入度（Intrusion）**：描述巨大化少女对人类环境的客观介入程度及报道口径，共 4 级：
          1. 间接影响（宏观表象）：她仅仅作为某种庞大背景存在。新闻只报道其引发的宏观社会、经济、政治、气象或自然环境的动荡，**不直接说明具体原因**，亦不提及她的具体形象。
          2. 被动存在（轮廓气质）：她的存在开始被公开注视或科学解析。报道开始**说明她的整体轮廓、精神气质、神态或日常动作（如行走、坐卧）**，以及引发的社会舆论，但不涉及身体细节。
          3. 主动互动（局部交互）：少女的身体与城市设施发生物理接触（如手脚包裹地标、轻触建筑、阻断交通等）。报道着重于**具体的动作交互、局部身体细节及民众受到的心理冲击**，但保持新闻的客观克制。
          4. 占据与支配（绝对控制）：她已完全展现出支配欲，以身体占据、封锁或玩弄城市，建筑与人群彻底沦为玩物。报道**完全表现这种私密身体对环境的绝对支配与视觉冲击**。

        - **破坏性（Destruction）**：描述少女对物理破坏的主观想法和客观后果程度，共 4 级：
          1. 避免破坏：她小心地对待身边一切，但客观上可能有无人员伤亡的环境改变，或感官上的压迫。
          2. 可能破坏：她采取无所谓态度，活动近似略谨慎的一般少女，可能导致结构受损或人员伤亡。
          3. 尝试破坏：她带有明确目的的动作导致建筑结构性受损或变形、街区或城市被大面积波及。
          4. 享受毁灭：她以身体接触和摧毁为唯一目的，短时间便使建筑结构化为废墟或齑粉，甚至街区或城市被完全抹除。

        ## 视角与语调要求
        1. **人类本位视角**：新闻的焦点是**“人类社会在发生什么”**，而不是“巨女在做什么”。
        2. **克制隐晦**：除非介入度达到 3 级或 4 级，否则严禁以第一人称视角或近距离特写去描写少女的外貌或动作细节。要把她当成“移动的天灾”或“地缘政治实体”来报道。
        3. **占位符规范**：可在正文中侧面提及 {{name}}（巨人少女本名）、{{nick}}（昵称），但应以“关于{{name}}引起的经济危机”、“针对{{nick}}的避难演习”等形式出现，作为事件背景，而非动作主角。
        
        ##格式要求
        1. 正文为一句到两句话的中文新闻摘要，包括时间、地点、人物、事件等要素（可用“美国某地”、“今日”、“身为管理员的张先生”等泛化词甚至部分省略，语意完整即可）。
        2. （可选）在正文中用 {{1}}、{{2}} 占位，分别对应两个替换队列（即在同一语句框架中提供不同的变体内容）。此时提供替换队列1/2（逗号分隔的候选词）。。
        3. 生成 3 条不同内容的候选。只输出一个JSON数组，形如 [{{"正文":"...","替换队列1":"a,b","替换队列2":"c"}}, ...]，不要有其他文字。
        """

        user_prompt = f"""
        当前参数：尺寸等级={size_desc}，纪念={memorial}，介入度={intrusion}，破坏性={destruction}。
        请根据这些参数，生成 3 条符合上述要求的新闻稿，并确保与给定参数一致。
        """

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = client.generate(messages, temperature=0.9)
        start = response.find('[')
        end = response.rfind(']')
        if start != -1 and end != -1:
            data = json.loads(response[start:end + 1])
            results = []
            for item in data:
                if isinstance(item, dict):
                    body = _clean_body(str(item.get("正文", "")).strip())
                    if not body or body == "。":
                        continue
                    results.append((
                        body,
                        _clean_queue(str(item.get("替换队列1", "")).strip()),
                        _clean_queue(str(item.get("替换队列2", "")).strip()),
                    ))
            if results:
                return results[:3]
    except Exception as e:
        print(f"AI调用异常: {e}")

    # 降级
    return _fallback_news(size)


class NewsFillerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("新闻填充生成器 (尺寸等级 + 纪念 + 介入度/破坏性)")
        self.root.geometry("680x620")
        self.root.resizable(True, True)

        # AI 客户端
        self.client, self.provider_name = get_ai_client()
        status_text = f"已连接: {self.provider_name}" if self.client else "未连接 AI (将使用静态备用正文)"
        ttk.Label(root, text=status_text, foreground="gray").pack(pady=5, anchor='w', padx=10)

        # ---- 参数区 ----
        param_frame = ttk.LabelFrame(root, text="新闻参数")
        param_frame.pack(pady=10, padx=10, fill='x')

        grid = ttk.Frame(param_frame)
        grid.pack(padx=10, pady=8, fill='x')
        grid.columnconfigure(7, weight=1)

        # 尺寸等级
        ttk.Label(grid, text="尺寸等级:").grid(row=0, column=0, sticky='w', padx=(0, 5))
        self.size_var = tk.StringVar(value="large")
        self.size_combo = ttk.Combobox(
            grid, textvariable=self.size_var, state="readonly", width=24,
            values=[f"{s} ({SIZE_DISPLAY[s]})" for s in SIZE_CATEGORIES],
        )
        self.size_combo.grid(row=0, column=1, sticky='w', padx=(0, 15))

        # 纪念
        ttk.Label(grid, text="纪念:").grid(row=0, column=2, sticky='w', padx=(0, 5))
        self.memorial_var = tk.StringVar(value="无")
        self.memorial_combo = ttk.Combobox(
            grid, textvariable=self.memorial_var, state="readonly", width=8,
            values=MEMORIALS,
        )
        self.memorial_combo.grid(row=0, column=3, sticky='w', padx=(0, 15))

        # 介入度
        ttk.Label(grid, text="介入度:").grid(row=0, column=4, sticky='w', padx=(0, 5))
        self.intrusion_var = tk.StringVar(value="2")
        self.intrusion_combo = ttk.Combobox(
            grid, textvariable=self.intrusion_var, state="readonly", width=4,
            values=LEVELS,
        )
        self.intrusion_combo.grid(row=0, column=5, sticky='w', padx=(0, 15))

        # 破坏性
        ttk.Label(grid, text="破坏性:").grid(row=0, column=6, sticky='w', padx=(0, 5))
        self.destruction_var = tk.StringVar(value="2")
        self.destruction_combo = ttk.Combobox(
            grid, textvariable=self.destruction_var, state="readonly", width=4,
            values=LEVELS,
        )
        self.destruction_combo.grid(row=0, column=7, sticky='w')

        # 生成按钮
        btn_frame = ttk.Frame(root)
        btn_frame.pack(pady=5, padx=10, fill='x')
        self.generate_btn = ttk.Button(btn_frame, text="✨ 生成新闻", command=self.generate_current)
        self.generate_btn.pack(side='left', padx=5)

        # ---- 候选列表（带复选框） ----
        list_frame = ttk.LabelFrame(root, text="候选新闻 (勾选后追加)")
        list_frame.pack(pady=10, padx=10, fill='both', expand=True)

        self.names_frame = ttk.Frame(list_frame)
        self.names_frame.pack(fill='both', expand=True)

        self.canvas = tk.Canvas(self.names_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.names_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.check_vars = []   # 复选框变量
        self.news_rows = []    # 每条的元组 (正文, 队列1, 队列2)

        # 底部按钮
        bottom_frame = ttk.Frame(root)
        bottom_frame.pack(pady=10, padx=10, fill='x')
        self.select_all_btn = ttk.Button(bottom_frame, text="全选", command=self.select_all)
        self.select_all_btn.pack(side='left', padx=5)
        self.deselect_all_btn = ttk.Button(bottom_frame, text="取消全选", command=self.deselect_all)
        self.deselect_all_btn.pack(side='left', padx=5)
        self.append_btn = ttk.Button(bottom_frame, text="追加到 CSV (无提示)", command=self.on_append)
        self.append_btn.pack(side='right', padx=5)

        # 状态栏
        self.status_bar = ttk.Label(root, text="就绪", relief='sunken', anchor='w')
        self.status_bar.pack(side='bottom', fill='x')

        self.status_bar.config(
            text="设置新闻参数后点击“生成新闻”获取候选"
        )

    def get_size_value(self):
        text = self.size_var.get()
        for s in SIZE_CATEGORIES:
            if text.startswith(s or "垃圾"):
                return s
        return ""

    def generate_current(self):
        self.status_bar.config(text="正在生成 ...")
        self.root.update()

        size = self.get_size_value()
        memorial = self.memorial_var.get()
        intrusion = self.intrusion_var.get()
        destruction = self.destruction_var.get()

        results = generate_news(size, memorial, intrusion, destruction, self.client)
        self.display_news(results)

        if results:
            self.status_bar.config(text=f"生成完成，共 {len(results)} 条候选新闻")
        else:
            self.status_bar.config(text="生成失败，请重试或检查网络")

    def display_news(self, results):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.check_vars.clear()
        self.news_rows.clear()

        if not results:
            ttk.Label(self.scrollable_frame, text="没有生成任何新闻").pack()
            return

        for body, q1, q2 in results:
            var = tk.BooleanVar(value=True)
            frame = ttk.Frame(self.scrollable_frame)
            frame.pack(anchor='w', fill='x', padx=5, pady=3)
            ttk.Checkbutton(frame, variable=var).pack(side='left', anchor='n')
            text_frame = ttk.Frame(frame)
            text_frame.pack(side='left', fill='x', expand=True)
            ttk.Label(text_frame, text=body, wraplength=560, justify='left').pack(anchor='w')
            queues = [q for q in (q1, q2) if q]
            if queues:
                ttk.Label(
                    text_frame, text="替换队列: " + " | ".join(queues),
                    foreground='gray', wraplength=560, justify='left'
                ).pack(anchor='w')
            self.check_vars.append(var)
            self.news_rows.append((body, q1, q2))

    def select_all(self):
        for var in self.check_vars:
            var.set(True)

    def deselect_all(self):
        for var in self.check_vars:
            var.set(False)

    def on_append(self):
        selected = [
            row for var, row in zip(self.check_vars, self.news_rows)
            if var.get()
        ]
        if not selected:
            self.status_bar.config(text="没有选中的新闻可追加")
            return

        size = self.get_size_value()
        memorial = self.memorial_var.get()
        intrusion = self.intrusion_var.get()
        destruction = self.destruction_var.get()

        try:
            header, rows = _load_news_rows(NEWS_CSV)
            for body, q1, q2 in selected:
                rows.append([size, memorial, intrusion, destruction, body, q1, q2])
            with open(NEWS_CSV, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, lineterminator="\r\n")
                writer.writerow(header)
                writer.writerows(rows)
            self.status_bar.config(text=f"已追加 {len(selected)} 条新闻到 {NEWS_CSV}")
        except Exception as e:
            ui.common.dialogs.showerror("写入失败", str(e))


if __name__ == "__main__":
    root = tk.Tk()
    app = NewsFillerApp(root)
    root.mainloop()
