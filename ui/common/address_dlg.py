"""注册地址对话框（级联选择 + 在线注册表浏览）。

上半部分按 世界观 → 一级地域 → 二级地域 → 三级地域 级联选择已申领地址：
选择框在左侧堆叠，各下拉弹层统一弹出在右侧并与级联区域同高（无框线）。
下半部分为序列化地址输入框（可输入搜索，下拉弹层直接置于输入框正下方），
与级联选择双向联动。对话框顶部显示原先注册地址；地标风格在改选新地址时
会提示已注册地标地址将失效。仅保留简要操作说明，地址格式、申领流程、
注册表 schema 等详细说明见 ``docs/address_system.md``。
"""

import threading
import tkinter as tk

import customtkinter as ctk

import services.address_registry as address_registry
import ui.common.dialogs
from ui.common.dialogs import BaseDialog
from ui.common import fonts as ui_fonts
from ui.common.widgets import CTkScrollableDropdownFrame
from ui.common.theme import (
    SOFT, TEXT, TEXT_MUTED, TEXT_DISABLED,
    BORDER_ALT, HOVER_ALT,
    STATUS_OK, OK_HOVER, PNL_BG, CLEAR_BG, CLEAR_BORDER, STATUS_ERR,
)
from address_model import validate_address_text, format_addr_verbose

LEVEL_LABELS = ("世界观", "一级地域", "二级地域", "三级地域")
CLEAR_OPT = "（不选此级，止于上一级）"


class AddressTextDialog(BaseDialog):
    """级联选择一个已申领的注册地址（风格级或地标级）。"""

    def __init__(self, parent, title: str, description: str, initial: str = "",
                 allow_empty: bool = True, landmark_style: bool = False):
        super().__init__(parent.winfo_toplevel())
        self.title(title)
        self.description = description
        self.result = None
        self._parent = parent.winfo_toplevel()
        self.transient(self._parent)
        self.grab_set()
        self.allow_empty = allow_empty
        # 地标风格：改注册地址会使基于旧地址组合的已注册地标地址失效
        self._landmark_style = landmark_style
        self._entries = []
        self._pending_download = None
        # 级联状态：每级选中的地域 id（"" = 未选）
        self._level_selections = ["", "", "", ""]
        self._level_boxes = {}
        self._level_dropdowns = {}
        self._display_maps = [{}, {}, {}, {}]
        self._cid_to_disp = [{}, {}, {}, {}]
        self._level_index = [dict(), dict(), dict(), dict()]
        # 序列化地址下拉：显示文本 → 地址
        self._addr_display_map = {}
        self._initial = (initial or "").strip()
        self._initial_applied = False

        self._create_widgets(initial)
        self.geometry("600x500")
        self._center_dialog(parent)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_registry()
        self.wait_window()

    # ---------- UI ----------

    def _create_widgets(self, initial):
        ctk.CTkLabel(self, text=self.description, font=self.UI_FONT,
                     text_color=SOFT, justify='left', wraplength=640).pack(
            anchor='w', padx=20, pady=(12, 4))

        # ---- 序列化地址：输入框 + 下拉（与级联双向联动） ----
        ctk.CTkLabel(self, text="序列化地址：",
                     font=self.UI_FONT_BOLD,
                     text_color=TEXT).pack(anchor='w', padx=20, pady=(8, 2))
        addr_frame = ctk.CTkFrame(self, fg_color="transparent")
        addr_frame.pack(fill='x', padx=20)
        self.addr_var = tk.StringVar()
        self.addr_entry = ctk.CTkEntry(
            addr_frame, textvariable=self.addr_var,
            width=460, height=28, font=self.UI_FONT,
            placeholder_text="搜索地址 / 描述 / 申领人…",
            fg_color=PNL_BG, border_color=BORDER_ALT)
        self.addr_entry.pack(side='left')
        ctk.CTkButton(addr_frame, text="刷新", width=80, height=28,
                      font=self.UI_FONT,
                      fg_color="transparent", border_width=1,
                      border_color=BORDER_ALT, text_color=TEXT_MUTED,
                      hover_color=HOVER_ALT,
                      command=self._refresh_registry).pack(side='left', padx=(8, 0))

        self.registry_status_var = tk.StringVar(value="正在下载最新注册表……")
        ctk.CTkLabel(self, textvariable=self.registry_status_var,
                     font=ui_fonts.ui_font(10),
                     text_color=TEXT_MUTED, justify='left', wraplength=640,
                     anchor='w').pack(fill='x', padx=20, pady=(2, 0))

        self.addr_dropdown = CTkScrollableDropdownFrame(
            attach=self.addr_entry, values=[], height=220, width=460,
            button_height=26, justify="left",
            font=self.UI_FONT, fg_color=PNL_BG, hover_color=HOVER_ALT,
            frame_border_color=BORDER_ALT, text_color=TEXT,
            command=self._on_addr_pick)
        # 单击即弹开（组件默认对输入框只绑了双击）
        self.addr_entry.bind("<Button-1>", lambda _e: self._open_addr_dropdown(), add="+")
        self.addr_entry.bind("<Double-Button-1>", lambda _e: self._open_addr_dropdown(), add="+")
        self.addr_entry.bind("<KeyRelease>", self._on_addr_typed)
        self.addr_entry.bind("<Return>", self._on_addr_return)

        # ---- 级联选择区（左侧堆叠，弹层在右侧） ----
        cascade = ctk.CTkFrame(self, fg_color="transparent")
        cascade.pack(fill='x', padx=20, pady=20)
        for level in range(4):
            row = ctk.CTkFrame(cascade, fg_color="transparent")
            row.pack(fill='x', pady=2)
            ctk.CTkLabel(row, text=LEVEL_LABELS[level], width=64,
                         font=self.UI_FONT, text_color=TEXT,
                         anchor='w').pack(side='left')
            box = ctk.CTkButton(row, text="（下载注册表中…）", width=200, height=28,
                                font=self.UI_FONT, anchor='w',
                                fg_color=PNL_BG, border_width=1,
                                border_color=BORDER_ALT, text_color=TEXT,
                                hover_color=HOVER_ALT, state="disabled")
            box.pack(side='left', padx=(4, 0))
            self._level_boxes[level] = box
            dropdown = CTkScrollableDropdownFrame(
                attach=box, values=[], width=220, height=150,
                button_height=24, justify="left", x=340,
                frame_border_width=0,
                font=self.UI_FONT, fg_color=PNL_BG, hover_color=HOVER_ALT,
                text_color=TEXT,
                command=lambda disp, lv=level: self._on_level_pick(lv, disp))
            self._level_dropdowns[level] = dropdown
            # 弹层背景透明：内部滚动区与外层容器都不再自绘底色
            dropdown.configure(fg_color="transparent")
            ctk.CTkFrame.configure(dropdown, fg_color="transparent")
        self._patch_cascade_dropdown_pos()

        prev = self._initial
        prev_text = f"原先注册地址：{prev}" if prev else "原先注册地址：（未注册）"
        if prev:
            prev_text += f"\n可读位置：{format_addr_verbose(prev)}"
        self.prev_label = ctk.CTkLabel(
            self, text=prev_text, font=self.UI_FONT, text_color=TEXT,
            justify='left', wraplength=640, anchor='w')
        self.prev_label.pack(fill='x', padx=20, pady=(4, 0))

        self.status_var = tk.StringVar(value="")
        self.status_label = ctk.CTkLabel(self, textvariable=self.status_var,
                                         font=ui_fonts.ui_font(10),
                                         text_color=TEXT_MUTED, justify='left',
                                         wraplength=640, anchor='w')
        self.status_label.pack(fill='x', padx=20, pady=(4, 0))

        self.warn_var = tk.StringVar(value="")
        self.warn_label = ctk.CTkLabel(self, textvariable=self.warn_var,
                                       font=ui_fonts.ui_font(10),
                                       text_color=STATUS_ERR, justify='left',
                                       wraplength=640, anchor='w')
        self.warn_label.pack(fill='x', padx=20, pady=(2, 0))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(side='bottom', fill='x', padx=20, pady=10)
        ctk.CTkButton(btn_frame, text="确定", width=90, height=28,
                      font=self.UI_FONT, command=self._on_ok,
                      fg_color="transparent", border_width=1,
                      border_color=STATUS_OK, text_color=STATUS_OK,
                      hover_color=OK_HOVER).pack(side='left', padx=(0, 8))
        ctk.CTkButton(btn_frame, text="消除", width=90, height=28,
                      font=self.UI_FONT, command=self._clear,
                      fg_color="transparent", border_width=1,
                      border_color=CLEAR_BORDER, text_color=STATUS_ERR,
                      hover_color=CLEAR_BG).pack(side='left')
        ctk.CTkButton(btn_frame, text="取消", width=90, height=28,
                      font=self.UI_FONT, command=self._on_close,
                      fg_color="transparent", border_width=1,
                      border_color=BORDER_ALT, text_color=TEXT_MUTED,
                      hover_color=HOVER_ALT).pack(side='right')

        self._refresh_hint()

    # ---------- 级联选择 ----------

    def _index_entries(self):
        """把派生条目按层级索引：_level_index[level][(父路径 ids)][地域 id] = 条目。"""
        idx = [dict(), dict(), dict(), dict()]
        for e in self._entries:
            ids = address_registry.ids_of(e.get("address", ""))
            if not ids:
                continue
            level = len(ids) - 1
            idx[level].setdefault(tuple(ids[:-1]), {})[ids[-1]] = e
        self._level_index = idx

    def _refresh_level_options(self, from_level=0):
        """按当前上级选择重建 from_level 及以下的下拉选项。"""
        for level in range(from_level, 4):
            parent = tuple(self._level_selections[:level])
            children = self._level_index[level].get(parent, {})
            box = self._level_boxes[level]
            self._display_maps[level] = {}
            if not children:
                box.configure(text="（无已申领选项）", state="disabled")
                self._level_selections[level] = ""
                self._cid_to_disp[level] = {}
                continue
            values, display_map, cid_to_disp = [], {}, {}
            if level > 0:
                values.append(CLEAR_OPT)
            for cid in sorted(children):
                entry = children[cid]
                disp = cid
                if entry.get("description"):
                    disp = f"{cid} ｜ {entry['description']}"
                values.append(disp)
                display_map[disp] = cid
                cid_to_disp[cid] = disp
            self._display_maps[level] = display_map
            self._cid_to_disp[level] = cid_to_disp
            self._level_dropdowns[level].configure(values=values)
            selected = self._level_selections[level]
            if selected and selected not in children:
                self._level_selections[level] = ""   # 上级变化后残留的失效选择
            box.configure(state="normal")

    def _cascade_popup_y(self):
        """级联弹层的纵坐标：与级联区域第一行顶对齐（逻辑坐标，相对对话框）。

        注意：CTkToplevel 是 window 级缩放，没有 _get_widget_scaling()，
        必须从子部件（widget 级）取缩放；纵向偏移沿父级累加 winfo_y，
        不依赖 winfo_rooty（不受标题栏/窗口位置影响）。
        """
        box = self._level_boxes[0]
        try:
            scaling = box._get_widget_scaling()
        except AttributeError:
            scaling = 1.0
        offset = 0
        target = box
        while target is not None and target is not self:
            offset += target.winfo_y()
            parent = target.winfo_parent()
            if not parent:
                break
            target = target._nametowidget(parent)
        return offset / scaling

    def _patch_cascade_dropdown_pos(self):
        """各级弹层统一弹到级联区域同高处，而不是所点输入框的正下方。"""
        for dropdown in self._level_dropdowns.values():
            orig_place = dropdown.place_dropdown

            def _place(_orig=orig_place, _dd=dropdown):
                _dd.y = self._cascade_popup_y()
                _orig()

            dropdown.place_dropdown = _place

    def _update_boxes_text(self):
        for level in range(4):
            box = self._level_boxes[level]
            if str(box.cget("state")) == "disabled":
                continue
            selected = self._level_selections[level]
            # 框上文字与下拉选项显示一致（含描述），更直观
            disp = self._cid_to_disp[level].get(selected, selected) if selected else ""
            box.configure(text=disp if disp else "（未选择）")

    def _on_level_pick(self, level: int, display: str):
        if display == CLEAR_OPT:
            self._level_selections[level] = ""
        else:
            cid = self._display_maps[level].get(display)
            if cid is None:
                return
            self._level_selections[level] = cid
        for deeper in range(level + 1, 4):
            self._level_selections[deeper] = ""
        self._refresh_level_options(from_level=level + 1)
        self._update_boxes_text()
        self._refresh_hint()
        self._sync_addr_entry()

    def _composed_address(self) -> str:
        """最深选中节点对应的派生地址；全未选返回 ''。"""
        deepest = -1
        for level in range(4):
            if self._level_selections[level]:
                deepest = level
        if deepest < 0:
            return ""
        parent = tuple(self._level_selections[:deepest])
        child = self._level_index[deepest].get(parent, {}).get(
            self._level_selections[deepest])
        return child.get("address", "") if child else ""

    def _refresh_hint(self, _event=None):
        text = self._composed_address()
        if not text:
            self.status_var.set("当前：未注册（到处可用，不参与地址规则）。")
            self.status_label.configure(text_color=TEXT_MUTED)
        else:
            self.status_var.set(f"当前：{text}\n可读位置：{format_addr_verbose(text)}")
            self.status_label.configure(text_color=TEXT)
        # 地标风格：之前有注册地址而选定了不同新地址 → 提示地标地址将失效
        if self._landmark_style and self._initial and text != self._initial:
            self.warn_var.set(
                "⚠ 风格注册地址已变更：原先基于旧地址组合的已注册地标地址将失效，"
                "请在保存后检查各地标的地址。")
        else:
            self.warn_var.set("")

    def _clear(self):
        self._level_selections = ["", "", "", ""]
        self._refresh_level_options()
        self._update_boxes_text()
        self._refresh_hint()
        self._sync_addr_entry()

    # ---------- 序列化地址输入框 ----------

    def _sync_addr_entry(self):
        """级联选择变化 → 输入框显示当前派生地址。"""
        self.addr_var.set(self._composed_address())

    def _build_addr_values(self, keyword: str):
        """按关键词过滤条目，返回 (下拉显示值列表, 显示文本→地址 映射)。"""
        values, disp_map = [], {}
        for entry in address_registry.search_entries(keyword, self._entries):
            addr = entry.get("address", "")
            if not addr:
                continue
            disp = addr
            if entry.get("owner"):
                disp += f"（{entry['owner']}）"
            if entry.get("description"):
                disp += f" ｜ {entry['description']}"
            if disp in disp_map:      # 极端重名时退回纯地址，保证映射唯一
                disp = addr
            disp_map[disp] = addr
            values.append(disp)
        return values, disp_map

    def _on_addr_typed(self, _event=None):
        """输入框输入 → 过滤下拉选项（弹层已在正下方）。"""
        values, disp_map = self._build_addr_values(self.addr_var.get())
        self._addr_display_map = disp_map
        self.addr_dropdown.configure(values=values)
        if self.addr_dropdown.hide:
            self._open_addr_dropdown()
        else:
            self.addr_dropdown.place_dropdown()

    def _open_addr_dropdown(self):
        self.addr_dropdown.hide = True
        self.addr_dropdown._iconify()

    def _on_addr_pick(self, disp: str):
        """下拉选中 → 回填输入框并联动到级联选择。"""
        addr = self._addr_display_map.get(disp)
        if addr is None:
            return
        self.addr_var.set(addr)
        self._apply_selection(addr)

    def _on_addr_return(self, _event=None):
        """回车：输入与某条完整地址一致时直接联动。"""
        self.addr_dropdown.place_forget()
        self.addr_dropdown.hide = True
        text = self.addr_var.get().strip()
        for entry in self._entries:
            if entry.get("address") == text:
                self._apply_selection(text)
                return

    def _apply_selection(self, addr: str):
        """把地址联动到级联选择。"""
        ids = address_registry.ids_of(addr)
        if not ids:
            return
        self._level_selections = (list(ids) + ["", "", "", ""])[:4]
        self._refresh_level_options()
        self._update_boxes_text()
        self._refresh_hint()
        self._sync_addr_entry()

    # ---------- 注册表下载 ----------

    def _refresh_registry(self):
        self._pending_download = None
        self.registry_status_var.set("正在下载最新注册表……")
        threading.Thread(target=self._download_worker, daemon=True).start()
        self.after(80, self._poll_download)

    def _download_worker(self):
        # 工作线程只写普通属性，不触碰 tkinter；由主线程轮询取结果。
        try:
            data, updated = address_registry.download_registry()
            address_registry.save_cache(data, updated)
            entries, issues = address_registry.parse_entries_ex(data)
            self._pending_download = (entries, issues, False, updated)
        except address_registry.RegistryOfflineError:
            data = address_registry.load_cache().get("data") or {}
            entries, issues = address_registry.parse_entries_ex(data)
            self._pending_download = (entries, issues, True, "")

    def _poll_download(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if self._pending_download is None:
            self.after(80, self._poll_download)
            return
        entries, issues, offline, updated = self._pending_download
        self._pending_download = None
        self._download_done(entries, issues, offline, updated)

    def _download_done(self, entries, issues, offline, updated):
        self._entries = entries
        self._index_entries()
        if self._initial and not self._initial_applied:
            self._initial_applied = True
            self._apply_selection(self._initial)
        self._refresh_level_options()
        self._update_boxes_text()
        self._refresh_hint()
        self._sync_addr_entry()
        values, disp_map = self._build_addr_values("")
        self._addr_display_map = disp_map
        self.addr_dropdown.configure(values=values)
        issue_note = f"（另有 {len(issues)} 条申领记录不合法已跳过）" if issues else ""
        if offline:
            self.registry_status_var.set(
                f"⚠ 离线：显示本地缓存的已申领地址（{len(entries)} 条{issue_note}）。"
                "离线只能查询，不能注册。")
        else:
            text = f"✅ 已下载最新注册表：共 {len(entries)} 条已申领地址{issue_note}"
            if updated:
                text += f"（仓库更新于 {updated}）"
            self.registry_status_var.set(text)

    # ---------- 保存 ----------

    def _check_registry_claim(self, text: str) -> bool:
        """注册必须在线完成，且地址已在最新注册表中申领。"""
        try:
            data, updated = address_registry.download_registry()
        except address_registry.RegistryOfflineError as exc:
            ui.common.dialogs.showerror(
                "离线，无法注册",
                f"{exc}\n\n注册地址必须联网完成（需先下载最新地址注册表），"
                "不允许离线注册。")
            return False
        address_registry.save_cache(data, updated)
        entries = address_registry.parse_entries(data)
        if address_registry.find_entry(text, entries) is None:
            ui.common.dialogs.showerror(
                "地址未申领",
                f"地址\n{text}\n未在最新地址注册表中申领，不允许注册。\n"
                "请从上方级联或列表中选择已申领的地址；如需新增地址请先在仓库中申领。")
            return False
        return True

    def _on_ok(self):
        text = self._composed_address()
        if not text and not self.allow_empty:
            ui.common.dialogs.showwarning("警告", "请选择注册地址")
            return
        if text:
            err = validate_address_text(text)
            if err:
                ui.common.dialogs.showwarning("警告", f"地址格式错误：{err}")
                return
            if not self._check_registry_claim(text):
                return
        self.result = text
        self._on_close()

    def _on_close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.withdraw()
        self.destroy()
