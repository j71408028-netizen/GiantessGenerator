#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
姓名生成器 (姓氏轮换 + 名字首字母分组)
- 姓氏按指定列表顺序轮换 (含欧阳)
- 名字首字母按两字母一组切换 (排除 I,E,O,U,V，保留 X)
- 切换时不生成，点击“生成姓名”才调用 AI
- 追加到 CSV 时输出：字母组, 姓, 名, 姓名, 频数
"""

import csv
import io
import json
import os
import random
import sys
import tkinter as tk
from tkinter import ttk

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import ui.common.dialogs
from ai import get_ai_client


# ------------- 姓氏列表 (按顺序轮换) -------------
SURNAMES = [
    '王', '李', '张', '刘', '陈',
    '谢', '宋', '许', '董', '欧阳'
]
SURNAME_COUNT = len(SURNAMES)

# ------------- 字母表 (排除 I,E,O,U,V，保留 X) -------------
LETTERS = ['A','B','C','D','F','G','H','J','K','L',
           'M','N','P','Q','R','S','T','W','X','Y','Z']

# 两两分组 (最后一组可能只有单个)
LETTER_GROUPS = []
for i in range(0, len(LETTERS), 2):
    group = tuple(LETTERS[i:i+2])
    LETTER_GROUPS.append(group)
GROUP_COUNT = len(LETTER_GROUPS)  # 11

NAME_CSV = os.path.join(_BASE_DIR, "data", "static", "names", "default.csv")
NAME_COLUMNS = ["字母组", "姓", "名", "姓名", "频数"]


def _decode_csv(raw):
    """尝试按 utf-8-sig 解码，失败再按 GBK，避免已有文件被误存为其他编码。"""
    for enc in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _load_name_rows(path):
    """读取现有姓名 CSV，返回 (表头, 数据行)；文件缺失或为空时用默认表头。"""
    header = list(NAME_COLUMNS)
    rows = []
    if os.path.isfile(path):
        raw = open(path, "rb").read()
        if raw.strip():
            data = list(csv.reader(io.StringIO(_decode_csv(raw), newline='')))
            if data:
                header = data[0]
                rows = data[1:]
    return header, rows

# ------------- 核心生成函数 -------------
def generate_names(surname, letter_group, client):
    """
    调用 AI 生成 5 个姓名，强制使用指定的 surname，
    且名字的第一个字拼音首字母属于 letter_group。
    letter_group: tuple of letters, e.g. ('A','B')
    """
    group_desc = " 或 ".join(letter_group)

    if client is None:
        # 备用姓名 (仅供演示)
        fallback_names = [
            f"{surname}小雅", f"{surname}明", f"{surname}一诺"
        ]
        random.shuffle(fallback_names)
        return fallback_names[:5]

    system_prompt = (
        "你是一个姓名起名助手，请生成5个女性的中文姓名。\n"
        "要求：\n"
        f"1. 姓氏必须为“{surname}”，不可更改。\n"
        "2. 名字为双字，第一个字（即姓氏后面的第一个字）的拼音首字母必须属于 " + group_desc + "\n"
        "3. 前4个名字为双字，分别要求最文雅、最好听、最独特、最中性；第5个名字为单字。"                                                                              
        "4. 只输出一个JSON数组，例如 [\"王希文\", \"王子轩\", \"王伊超\", \"王小明\", \"王芳\"]，不要有其他文字。"
    )
    user_prompt = f"请生成5个姓“{surname}”的姓名，名字首字母属于 {group_desc}。"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    try:
        response = client.generate(messages, temperature=0.9)
        start = response.find('[')
        end = response.rfind(']')
        if start != -1 and end != -1:
            json_str = response[start:end+1]
            data = json.loads(json_str)
            if isinstance(data, list):
                names = [str(item).strip() for item in data if item and isinstance(item, str)]
                return names[:5]
    except Exception as e:
        print(f"AI调用异常: {e}")

    # 降级
    fallback_names = [
        f"{surname}小雅", f"{surname}明", f"{surname}一诺"
    ]
    random.shuffle(fallback_names)
    return fallback_names[:3]

# ------------- GUI 应用程序 -------------
class NameGeneratorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("姓名生成器 (姓氏轮换 + 名字首字母分组)")
        self.root.geometry("640x560")
        self.root.resizable(True, True)

        # AI 客户端
        self.client, self.provider_name = get_ai_client()
        status_text = f"已连接: {self.provider_name}" if self.client else "未连接 AI (将使用静态备用姓名)"
        ttk.Label(root, text=status_text, foreground="gray").pack(pady=5, anchor='w', padx=10)

        # ---- 当前状态显示 ----
        state_frame = ttk.Frame(root)
        state_frame.pack(pady=10, padx=10, fill='x')

        # 姓氏部分
        ttk.Label(state_frame, text="当前姓氏:").pack(side='left')
        self.surname_label = ttk.Label(state_frame, text="?", font=('Arial', 14, 'bold'), foreground='purple')
        self.surname_label.pack(side='left', padx=5)
        self.surname_progress = ttk.Label(state_frame, text="1/10", foreground='gray')
        self.surname_progress.pack(side='left', padx=(0,20))

        # 字母组部分
        ttk.Label(state_frame, text="字母组:").pack(side='left')
        self.group_label = ttk.Label(state_frame, text="?", font=('Arial', 14, 'bold'), foreground='blue')
        self.group_label.pack(side='left', padx=5)
        self.group_progress = ttk.Label(state_frame, text="1/11", foreground='gray')
        self.group_progress.pack(side='left', padx=(0,20))

        # 按钮区 (两个切换按钮)
        btn_frame = ttk.Frame(state_frame)
        btn_frame.pack(side='right')
        self.next_surname_btn = ttk.Button(btn_frame, text="⏭ 下一姓氏", command=self.next_surname)
        self.next_surname_btn.pack(side='left', padx=5)
        self.next_group_btn = ttk.Button(btn_frame, text="⏭ 下一字母组", command=self.next_group)
        self.next_group_btn.pack(side='left', padx=5)
        self.generate_btn = ttk.Button(btn_frame, text="✨ 生成姓名", command=self.generate_current)
        self.generate_btn.pack(side='left', padx=5)

        # 姓名列表（带复选框）
        list_frame = ttk.LabelFrame(root, text="候选姓名 (勾选后追加)")
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

        self.check_vars = []      # 复选框变量
        self.name_labels = []     # 姓名文本

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

        # 初始化索引 (姓氏0, 字母组0)
        self.surname_idx = 0
        self.group_idx = 0
        self.current_surname = SURNAMES[self.surname_idx]
        self.current_group = LETTER_GROUPS[self.group_idx]
        self.update_display()
        self.clear_names()
        self.status_bar.config(
            text=f"起始：姓氏【{self.current_surname}】 字母组【{self.get_group_label()}】，点击“生成姓名”获取推荐"
        )

    def get_group_label(self):
        return ''.join(self.current_group)

    def next_surname(self):
        self.surname_idx = (self.surname_idx + 1) % SURNAME_COUNT
        self.current_surname = SURNAMES[self.surname_idx]
        self.update_display()
        self.clear_names()
        self.status_bar.config(
            text=f"切换姓氏为【{self.current_surname}】，点击“生成姓名”获取推荐"
        )

    def next_group(self):
        self.group_idx = (self.group_idx + 1) % GROUP_COUNT
        self.current_group = LETTER_GROUPS[self.group_idx]
        self.update_display()
        self.clear_names()
        self.status_bar.config(
            text=f"切换字母组为【{self.get_group_label()}】，点击“生成姓名”获取推荐"
        )

    def update_display(self):
        self.surname_label.config(text=self.current_surname)
        self.surname_progress.config(text=f"{self.surname_idx+1}/{SURNAME_COUNT}")
        self.group_label.config(text=self.get_group_label())
        self.group_progress.config(text=f"{self.group_idx+1}/{GROUP_COUNT}")

    def clear_names(self):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.check_vars.clear()
        self.name_labels.clear()

    def generate_current(self):
        self.status_bar.config(text="正在生成 ...")
        self.root.update()

        names = generate_names(self.current_surname, self.current_group, self.client)
        self.display_names(names)

        if names:
            self.status_bar.config(text=f"生成完成，共 {len(names)} 个姓名")
        else:
            self.status_bar.config(text="生成失败，请重试或检查网络")

    def display_names(self, names):
        self.clear_names()
        if not names:
            ttk.Label(self.scrollable_frame, text="没有生成任何姓名").pack()
            return

        for name in names:
            var = tk.BooleanVar(value=True)
            cb = ttk.Checkbutton(self.scrollable_frame, variable=var, text=name)
            cb.pack(anchor='w', padx=5, pady=2)
            self.check_vars.append(var)
            self.name_labels.append(name)

    def select_all(self):
        for var in self.check_vars:
            var.set(True)

    def deselect_all(self):
        for var in self.check_vars:
            var.set(False)

    def on_append(self):
        selected = []
        for var, name in zip(self.check_vars, self.name_labels):
            if var.get():
                selected.append(name)

        if not selected:
            self.status_bar.config(text="没有选中的姓名可追加")
            return

        csv_path = NAME_CSV

        try:
            header, rows = _load_name_rows(csv_path)
            for full_name in selected:
                # 提取姓和名
                if full_name.startswith("欧阳"):
                    surname = "欧阳"
                    given_name = full_name[2:]
                else:
                    surname = full_name[0] if full_name else ""
                    given_name = full_name[1:] if len(full_name) > 1 else ""
                rows.append([self.get_group_label(), surname, given_name, full_name, 0])

            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, lineterminator="\r\n")
                writer.writerow(header)
                writer.writerows(rows)

            self.status_bar.config(text=f"已追加 {len(selected)} 个姓名到 {csv_path}")
        except Exception as e:
            ui.common.dialogs.showerror("写入失败", str(e))

if __name__ == "__main__":
    root = tk.Tk()
    app = NameGeneratorApp(root)
    root.mainloop()
