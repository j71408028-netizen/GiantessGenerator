import ui.ctk.dialogs
import customtkinter as ctk
import re
import copy
import os
from dungeon.audio.chapters import normalize_chapters
from dungeon.admission import (FIELD_ADDRESS, FIELD_HEIGHT_MAX, FIELD_HEIGHT_MIN,
                               FIELD_MAX_DISTANCE, coerce_distance,
                               normalize_fields as normalize_admission_fields)
from dungeon.coupling import (COUPLING_LEVELS, DEFAULT_COUPLING_LEVEL,
                              coupling_initial_prompt,
                              coupling_label, normalize_coupling_level)
from dungeon.rules import EvolutionRules
from dungeon.schema import DEFAULT_TEXT_COMPONENT, normalize_text_component
from dungeon.audio.speech import normalize_voice
from ui.ctk.widgets import (CTkScrollableDropdownFrame, CTkSegmentedControl,
                               OptionButton)
from ui.ctk.address_dlg import AddressTextDialog
from ui.ctk.dialogs import BaseDialog, InputDialog
from ui.scenario.chapter_trigger_mgr import ChapterTriggerManager
from dungeon.terms import DEFAULT_SCENARIO_ID, scenario_config_of, scenario_id_of
from dungeon.validate import format_diagnostics, has_errors
from ui.scenario.character_page import CharacterPage
from ui.scenario.component_mgr import ComponentManager
from ui.ctk.theme import (
    SC_BG, SC_PANEL_BG, SC_BORDER, SC_BORDER_STRONG,
    SC_HOVER, SC_MENU_HOVER, SC_CARD_HOVER, SC_TEXT,
    SC_TEXT_SOFT, SC_TITLE, SC_LINK, SC_OK,
    SC_OK_HOVER, SC_ERR, SC_ERR_HOVER,
)
from ui.ctk import fonts as ui_fonts


def _format_optional_number(value):
    """准入输入框显示：None → 空串，数字去掉多余小数位。"""
    if value is None:
        return ""
    return f"{float(value):g}"


# ==================== 副本编辑器 ====================
class ScenarioEditor(ctk.CTkFrame):
    """副本编辑器：通用区域 + 两个 TreeviewManager 子面板（属性/触发器）"""

    def __init__(self, parent, scenario_repo, gui_ref, challenge_mgr=None):
        super().__init__(parent, fg_color="transparent")
        self._scenario_repo = scenario_repo
        self._challenge_mgr = challenge_mgr
        self.gui = gui_ref
        self.current_scenario_id = None
        self.initial_prompt = ""
        self.section_prompts = {}
        self.coupling_level = DEFAULT_COUPLING_LEVEL
        self.protagonist_title = ""
        # 对话语音（Solea/Bulla 台词朗读）：未配置时按默认值走
        self.voice = normalize_voice(None)
        self.text_component = DEFAULT_TEXT_COMPONENT  # 文本主组件三选一
        self.evolution_attrs = []  # 统一演化量列表
        self.chapters = []  # 章节列表（不含条件，只描述背景与持续敏感效果）
        self.triggers = []
        self.components = []  # 显示组件 id 列表（不含文本主组件）
        self.components_params = {}  # {组件id: {参数: 值}}
        self._modified = False
        self._saved_prompt_snapshot = {}  # 用于检测提示词是否被修改
        self._prompt_modified = False  # 辅助标志（实际用快照对比更准确）

        # --- 新增：自定义步进值与转移矩阵 ---
        self.section_steps = {}
        self.transition_matrix = None

        # 进入副本所需行动点数（0 表示免费）
        self.entry_action_cost = 0
        # 进入准入：入口地址（主世界锚点）/ 距离上限（None=不限）/ 身高区间（None=不限）
        self.entry_address = ""
        self.entry_address_max_distance = None
        self.entry_height_min = None
        self.entry_height_max = None

        self._build_ui()
        self._refresh_scenario_list()
        self._load_scenario(DEFAULT_SCENARIO_ID)

    def _build_ui(self):
        toolbar = ctk.CTkFrame(self, fg_color=SC_BG)
        toolbar.pack(fill='x', padx=10, pady=6)
        self.dungeon_switch_btn = CTkSegmentedControl(
            toolbar,
            values=[" 通用 ", " 角色 ", " 动态 ", " 组件 "],
            command=self._on_scenario_tab_switch,
            width=360, font=ui_fonts.ui_font(13)
        )
        self.dungeon_switch_btn.pack(side='left', padx=5, pady=(3, 1))
        # 初始化即选中“通用”
        self.dungeon_switch_btn.set(" 通用 ")
        right_frame = ctk.CTkFrame(toolbar, fg_color="transparent")
        right_frame.pack(side='right', padx=5)
        ctk.CTkLabel(right_frame, text="副本方案:",
                     text_color=SC_TEXT_SOFT).pack(side='left', padx=5)
        # ---- CTkComboBox + CTkScrollableDropdownFrame ----
        self.scenario_combo = ctk.CTkComboBox(
            right_frame,
            values=[],
            state="readonly",
            width=150,
            fg_color=SC_PANEL_BG,
            border_color=SC_BORDER_STRONG,
            button_color=SC_BORDER_STRONG,
            button_hover_color=SC_MENU_HOVER,
            dropdown_fg_color=SC_PANEL_BG,
            dropdown_hover_color=SC_HOVER
        )
        self.scenario_combo.pack(side='left', padx=7)
        self._rebuild_dropdown()

        _btn_spec = {"fg_color": "transparent", "border_width": 1, "corner_radius": 8}
        _btn_muted = {"text_color": SC_TEXT_SOFT,
                      "hover_color": SC_HOVER,
                      "border_color": SC_BORDER_STRONG}
        ctk.CTkButton(right_frame, text="新建", width=80, command=self._new_scenario,
                       text_color=SC_OK,
                       hover_color=SC_OK_HOVER,
                       border_color=SC_OK,
                       **_btn_spec).pack(side='left', padx=2)
        ctk.CTkButton(right_frame, text="重命名", width=80, command=self._rename_scenario,
                       **_btn_spec, **_btn_muted).pack(side='left', padx=2)
        _del_spec = {"text_color": SC_ERR,
                     "hover_color": SC_ERR_HOVER,
                     "border_color": SC_ERR}
        ctk.CTkButton(right_frame, text="删除", width=80, command=self._delete_scenario,
                       **_btn_spec, **_del_spec).pack(side='left', padx=2)

        # 内容容器
        self.content_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.content_frame.pack(fill='both', expand=True, padx=10, pady=10)

        # 创建各面板
        self.prompt_panel = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        self._build_prompt_ui(self.prompt_panel)  # 重写 UI 构建

        self.character_panel = CharacterPage(
            self.content_frame, self._scenario_repo, self)
        # 主题切换由 window_manager 调 evolution_panel.update_theme，保持旧入口可用
        self.evolution_panel = self.character_panel.evolution_panel
        self.chapter_trigger_panel = ChapterTriggerManager(
            self.content_frame, self._scenario_repo, self)
        self.component_panel = ComponentManager(self.content_frame, self._scenario_repo, self)

        # 默认显示通用面板
        self.character_panel.pack_forget()
        self.chapter_trigger_panel.pack_forget()
        self.component_panel.pack_forget()
        self.prompt_panel.pack(fill='both', padx=9, expand=True)

    def _build_combined_items(self):
        schemes = self._scenario_repo.list_all()
        items = list(schemes)
        text_colors = {}
        self._pack_display_map = {}
        if self._challenge_mgr and self._challenge_mgr.has_any_valid_key():
            packs = self._challenge_mgr.get_available_packs()
            for pack in packs:
                display = f"⚔ {os.path.splitext(pack)[0]}"
                items.append(display)
                text_colors[display] = SC_LINK
                self._pack_display_map[display] = pack
        return items, text_colors

    def _rebuild_dropdown(self):
        items, text_colors = self._build_combined_items()
        self.scenario_combo.configure(values=items if items else [])
        if hasattr(self, '_scenario_dropdown') and self._scenario_dropdown:
            self._scenario_dropdown.configure(values=items, text_colors=text_colors)
        else:
            _FG = SC_PANEL_BG
            _BD = SC_BORDER_STRONG
            _HOVER = SC_HOVER
            self._scenario_dropdown = CTkScrollableDropdownFrame(
                attach=self.scenario_combo,
                values=items,
                text_colors=text_colors,
                command=self._on_dropdown_select,
                height=200, button_height=28,
                fg_color=_FG,
                hover_color=SC_CARD_HOVER,
                scrollbar_button_color=_HOVER,
                scrollbar_button_hover_color=_HOVER,
                frame_border_color=_BD,
                text_color=SC_TEXT,
                button_color=_FG,
                frame_border_width=1,
                justify="left"
            )

    def _on_dropdown_select(self, choice):
        if choice in getattr(self, '_pack_display_map', {}):
            pack_name = self._pack_display_map[choice]
            self._on_scenario_challenge_pack_select(pack_name)
        else:
            self._on_scenario_select(choice)

    def _on_scenario_challenge_pack_select(self, choice):
        if not choice:
            return
        file_path = self._challenge_mgr.resolve_pack_path(choice)
        if file_path is None:
            ui.ctk.dialogs.showerror("错误", f"无法读取挑战包 '{choice}'，未找到对应秘钥")
            return
        result = self._challenge_mgr.try_open_with_keys(file_path)
        if result is None:
            ui.ctk.dialogs.showerror("错误", f"无法读取挑战包 '{choice}'，未找到对应秘钥")
            return
        data, _, _ = result
        if not data:
            return
        scenario_config = scenario_config_of(data)
        scenario_id = scenario_id_of(data)
        if not scenario_config:
            ui.ctk.dialogs.showinfo("提示", "该挑战包中没有副本方案")
            return

        if self._scenario_repo.exists(scenario_id):
            self._scenario_repo.save_config(scenario_id, scenario_config)
        else:
            self._scenario_repo.create(scenario_id, scenario_config)

        self._refresh_scenario_list()
        self._load_scenario(scenario_id)
        self._notify_validation()
        ui.ctk.dialogs.showinfo("成功", f"已从挑战包加载副本方案 '{scenario_id}'")

    def _on_scenario_tab_switch(self, value):
        # 分段值可能带装饰空格（如 " 动态 "），统一去掉后再比较
        tab = value.strip()
        # 隐藏所有面板
        self.prompt_panel.pack_forget()
        self.character_panel.pack_forget()
        self.chapter_trigger_panel.pack_forget()
        self.component_panel.pack_forget()

        if tab == "通用":
            self.prompt_panel.pack(fill='both', padx=9, expand=True)
        elif tab == "角色":
            self.character_panel.refresh_from_config()
            self.character_panel.pack(fill='both', expand=True)
        elif tab == "动态":
            self.chapter_trigger_panel.refresh_list()
            self.chapter_trigger_panel.pack(fill='both', expand=True)
        elif tab == "组件":
            self.component_panel.refresh_list()
            self.component_panel.pack(fill='both', expand=True)

    # ------------------ 新版通用面板 ------------------
    def _build_prompt_ui(self, parent):
        """通用面板：基础设置（通栏）+ 初始提示 / 段落分类提示。"""
        for child in parent.winfo_children():
            child.destroy()

        # 与其他管理页面保持一致的配色
        _CARD_BG = SC_PANEL_BG
        _TITLE = SC_TITLE
        _MUTED = SC_TEXT_SOFT
        _DARK = SC_TEXT
        _F_TITLE = 14
        _F_BIG = 14     # 表单标签与正文输入
        _F_SMALL = 13   # 次要说明

        # ---------- 底部按钮 ----------
        bottom_frame = ctk.CTkFrame(parent, fg_color="transparent")
        bottom_frame.pack(side='bottom', fill='x', pady=(6, 3))
        ctk.CTkButton(bottom_frame, text="保存此页面",
                      fg_color="transparent", border_width=2, corner_radius=10,
                      height=30, text_color=SC_OK,
                      hover_color=SC_OK_HOVER,
                      border_color=SC_OK,
                      font=ui_fonts.ui_font(_F_BIG, "bold"),
                      command=self._save_prompts).pack(fill='x')

        # ---------- 上排通栏 + 下排两栏 ----------
        columns = ctk.CTkFrame(parent, fg_color="transparent")
        columns.pack(fill='both', expand=True, pady=(0, 5))
        columns.grid_columnconfigure(0, weight=1, uniform='col')
        columns.grid_columnconfigure(1, weight=1, uniform='col')
        columns.grid_rowconfigure(0, weight=0)   # 上排按内容高度
        columns.grid_rowconfigure(1, weight=1)   # 下排吃掉剩余高度

        def _card(row, column, padx, columnspan=1):
            card = ctk.CTkFrame(columns, fg_color=_CARD_BG,
                                border_width=0, corner_radius=10)
            card.grid(row=row, column=column, columnspan=columnspan,
                      sticky='nsew', padx=padx,
                      pady=(0, 8) if row == 0 else 0)
            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill='both', expand=True, padx=12, pady=10)
            return card, inner

        # ---- 上排：基础设置（耦合等级 / AP 消耗 / 进入准入 / 系统初始提示） ----
        basic_card, left_inner = _card(0, 0, (0, 0), columnspan=2)

        head_row = ctk.CTkFrame(left_inner, fg_color="transparent")
        head_row.pack(fill='x', padx=(0, 20))
        ctk.CTkLabel(head_row, text="耦合等级", font=ui_fonts.ui_font(_F_BIG),
                     text_color=_TITLE).pack(side='left')
        # 选项按钮：复用 widgets.OptionButton（点击弹出档位下拉），档位值用
        # 等级显示名，回调里再映射回等级键
        self.coupling_option = OptionButton(
            head_row, values=[coupling_label(lv) for lv in COUPLING_LEVELS],
            command=self._on_coupling_option,
            width=110, height=26, corner_radius=8, border_width=1,
            font=ui_fonts.ui_font(_F_BIG, "bold"),
            fg_color="transparent", border_color=SC_BORDER_STRONG,
            hover_color=SC_HOVER, text_color=SC_LINK)
        self.coupling_option.set(coupling_label(self.coupling_level))
        self.coupling_option.pack(side='left', padx=8)
        self.entry_cost_var = ctk.StringVar(value="0")
        self.entry_cost_entry = ctk.CTkEntry(head_row, textvariable=self.entry_cost_var,
                                             width=64, font=ui_fonts.ui_font(_F_BIG),
                                             text_color=_DARK, justify='right',
                                             fg_color=SC_PANEL_BG,
                                             border_color=SC_BORDER_STRONG)
        self.entry_cost_entry.pack(side='right')
        ctk.CTkLabel(head_row, text="AP 消耗量", font=ui_fonts.ui_font(_F_BIG),
                     text_color=_TITLE).pack(side='right', padx=(12, 6))
        ctk.CTkLabel(left_inner,
                     text="主角称呼与对话语音已移到「角色」页；进入准入见下。",
                     font=ui_fonts.ui_font(_F_SMALL), text_color=_MUTED,
                     anchor='w').pack(fill='x', pady=(6, 0))

        # 卡片宽度随窗口均分，文本按固定宽度折行即可（CTkLabel 的自然宽度
        # 不受 wraplength 约束，这里只用于排版）
        _TEXT_W = 860

        # ---- 进入准入：入口地址（主世界锚点，距离上限）与身高区间 ----
        admission_card = ctk.CTkFrame(left_inner, fg_color=SC_PANEL_BG,
                                      border_width=1, border_color=SC_BORDER,
                                      corner_radius=8)
        admission_card.pack(fill='x', padx=(0, 20), pady=(4, 0))

        addr_row = ctk.CTkFrame(admission_card, fg_color="transparent")
        addr_row.pack(fill='x', padx=8, pady=(6, 2))
        ctk.CTkLabel(addr_row, text="入口地址", font=ui_fonts.ui_font(_F_SMALL),
                     text_color=_TITLE, width=64, anchor='w').pack(side='left')
        self.entry_address_var = ctk.StringVar()
        self.entry_address_entry = ctk.CTkEntry(
            addr_row, textvariable=self.entry_address_var, height=26,
            font=ui_fonts.ui_font(_F_SMALL), text_color=_DARK,
            placeholder_text="（不限）", state="readonly",
            fg_color=SC_BG, border_color=SC_BORDER_STRONG)
        self.entry_address_entry.pack(side='left', fill='x', expand=True,
                                      padx=(6, 4))
        ctk.CTkButton(addr_row, text="选", width=28, height=26,
                      font=ui_fonts.ui_font(_F_SMALL),
                      fg_color="transparent", border_width=1,
                      border_color=SC_BORDER_STRONG, text_color=SC_LINK,
                      hover_color=SC_HOVER, corner_radius=6,
                      command=self._pick_entry_address).pack(side='left')

        dist_row = ctk.CTkFrame(admission_card, fg_color="transparent")
        dist_row.pack(fill='x', padx=8, pady=2)
        ctk.CTkLabel(dist_row, text="距离上限", font=ui_fonts.ui_font(_F_SMALL),
                     text_color=_TITLE, width=64, anchor='w').pack(side='left')
        self.entry_distance_var = ctk.StringVar()
        ctk.CTkEntry(dist_row, textvariable=self.entry_distance_var, width=64,
                     height=26, font=ui_fonts.ui_font(_F_SMALL),
                     text_color=_DARK, placeholder_text="不限",
                     fg_color=SC_BG, border_color=SC_BORDER_STRONG).pack(
            side='left', padx=(6, 4))
        ctk.CTkLabel(dist_row, text="米（0 = 必须同址）",
                     font=ui_fonts.ui_font(_F_SMALL), text_color=_MUTED).pack(
            side='left')

        height_row = ctk.CTkFrame(admission_card, fg_color="transparent")
        height_row.pack(fill='x', padx=8, pady=(2, 4))
        ctk.CTkLabel(height_row, text="身高区间", font=ui_fonts.ui_font(_F_SMALL),
                     text_color=_TITLE, width=64, anchor='w').pack(side='left')
        self.entry_height_min_var = ctk.StringVar()
        ctk.CTkEntry(height_row, textvariable=self.entry_height_min_var, width=56,
                     height=26, font=ui_fonts.ui_font(_F_SMALL),
                     text_color=_DARK, placeholder_text="不限",
                     fg_color=SC_BG, border_color=SC_BORDER_STRONG).pack(
            side='left', padx=(6, 3))
        ctk.CTkLabel(height_row, text="~", font=ui_fonts.ui_font(_F_SMALL),
                     text_color=_MUTED).pack(side='left')
        self.entry_height_max_var = ctk.StringVar()
        ctk.CTkEntry(height_row, textvariable=self.entry_height_max_var, width=56,
                     height=26, font=ui_fonts.ui_font(_F_SMALL),
                     text_color=_DARK, placeholder_text="不限",
                     fg_color=SC_BG, border_color=SC_BORDER_STRONG).pack(
            side='left', padx=(3, 4))
        ctk.CTkLabel(height_row, text="米", font=ui_fonts.ui_font(_F_SMALL),
                     text_color=_MUTED).pack(side='left')

        ctk.CTkLabel(left_inner, text="系统初始提示",
                     font=ui_fonts.ui_font(_F_SMALL, "bold"),
                     text_color=_MUTED, anchor='w').pack(fill='x', pady=(10, 0))
        # 标签展示：直接读取 coupling.py 中该等级的 initial_prompt（只读）
        self.coupling_prompt_label = ctk.CTkLabel(
            left_inner, text=coupling_initial_prompt(self.coupling_level),
            width=_TEXT_W, wraplength=_TEXT_W,
            font=ui_fonts.ui_font(_F_SMALL), text_color=_MUTED,
            anchor='w', justify='left')
        self.coupling_prompt_label.pack(fill='x', pady=(4, 0))

        # ---- 左下：初始提示 ----
        prompt_card, prompt_inner = _card(1, 0, (0, 8))
        ctk.CTkLabel(prompt_inner, text="初始提示（角色设定/世界背景）",
                     font=ui_fonts.ui_font(_F_TITLE, "bold"),
                     text_color=_TITLE).pack(anchor='w')
        self.initial_prompt_text = ctk.CTkTextbox(prompt_inner, height=140, wrap='word',
                                                  font=ui_fonts.ui_font(_F_BIG),
                                                  text_color=_DARK,
                                                  fg_color=SC_PANEL_BG,
                                                  border_width=1,
                                                  border_color=SC_BORDER_STRONG)
        self.initial_prompt_text.pack(fill='both', expand=True, pady=(6, 0))

        # ---------- 右下：段落分类提示（横向可滚动卡片） ----------
        sections_card, sections_inner = _card(1, 1, (8, 0))
        title_row = ctk.CTkFrame(sections_inner, fg_color="transparent")
        title_row.pack(fill='x')
        ctk.CTkLabel(title_row, text="段落分类提示（针对不同文本类型）", font=ui_fonts.ui_font(_F_TITLE, "bold"),
                     text_color=_TITLE).pack(side='left')
        ctk.CTkButton(title_row, text="转移设置", width=100,
                      fg_color="transparent", border_width=1, corner_radius=8,
                      text_color=_MUTED,
                      hover_color=SC_HOVER,
                      border_color=SC_BORDER_STRONG,
                      font=ui_fonts.ui_font(_F_BIG),
                      command=lambda: self._edit_transition_matrix(None)).pack(side='right')

        # ---------- 各分类卡片 ----------
        self.section_frames = {}  # key -> CTkTextbox
        self.section_step_vars = {}  # key -> DoubleVar

        section_types = [
            ("background", "背景", "环境与世界状态的描写提示"),
            ("branch", "分支", "其他角色之间的对话或互动"),
            ("dialog", "对话", "与她有关的对话描写提示"),
            ("interaction", "互动", "她与其他人的身体互动"),
            ("action", "行动", "她独自做出的行动描写"),
        ]
        _CARD_WIDTH = 240

        strip = ctk.CTkScrollableFrame(sections_inner, orientation="horizontal",
                                       height=300, corner_radius=0,
                                       fg_color="transparent")
        strip.pack(fill='both', expand=True, pady=(8, 0))

        for key, label, hint in section_types:
            card = ctk.CTkFrame(strip, fg_color="transparent",
                                border_width=1, corner_radius=10,
                                border_color=SC_BORDER_STRONG,
                                width=_CARD_WIDTH)
            card.pack(side='left', fill='y', padx=4, pady=2)
            card.pack_propagate(False)

            # ---- 顶部：分类名 + 步进输入 ----
            head = ctk.CTkFrame(card, fg_color="transparent")
            head.pack(fill='x', padx=10, pady=(10, 0))
            ctk.CTkLabel(head, text=label, font=ui_fonts.ui_font(_F_TITLE, "bold"),
                         text_color=_TITLE).pack(side='left')

            step_box = ctk.CTkFrame(head, fg_color="transparent")
            step_box.pack(side='right')
            ctk.CTkLabel(step_box, text="步进:", font=ui_fonts.ui_font(_F_SMALL),
                         text_color=_MUTED).pack(side='left', padx=(0, 3))
            step_var = ctk.DoubleVar(value=self._default_step_for(key))
            step_entry = ctk.CTkEntry(step_box, textvariable=step_var, width=56,
                                      font=ui_fonts.ui_font(_F_SMALL + 1))
            step_entry.pack(side='left')
            self.section_step_vars[key] = step_var

            ctk.CTkLabel(card, text=hint, font=ui_fonts.ui_font(_F_SMALL),
                         text_color=_MUTED, anchor='w',
                         justify='left').pack(fill='x', padx=10, pady=(1, 0))

            # ---- 提示文本框（撑满卡片剩余高度） ----
            textbox = ctk.CTkTextbox(card, wrap='word',
                                     font=ui_fonts.ui_font(_F_BIG),
                                     text_color=_DARK,
                                     fg_color=SC_PANEL_BG,
                                     border_width=1,
                                     border_color=SC_BORDER_STRONG)
            textbox.pack(fill='both', expand=True, padx=10, pady=(4, 10))
            self.section_frames[key] = textbox

    # ------------------ 转移矩阵编辑对话框 ------------------
    def _edit_transition_matrix(self, section_key):
        """打开转移矩阵编辑窗口（编辑整个矩阵）"""
        dialog = BaseDialog(self)
        dialog.title("编辑转移概率矩阵")
        dialog.geometry("450x350")
        dialog.transient(self)
        # 抓取延到窗口可见之后（X11 要求 viewable，见 BaseDialog._grab_deferred）
        dialog._grab_deferred()

        types = ["background", "branch", "dialog", "interaction", "action"]
        matrix = self.transition_matrix if self.transition_matrix else self._default_transition_matrix()

        # 创建变量网格
        vars_dict = {}
        for src in types:
            vars_dict[src] = {}
            for dst in types:
                val = matrix.get(src, {}).get(dst, 0.0)
                vars_dict[src][dst] = ctk.DoubleVar(value=val)

        # 主框架
        main_frame = ctk.CTkFrame(dialog)
        main_frame.pack(fill='both', expand=True, padx=10, pady=10)

        # 列标题
        ctk.CTkLabel(main_frame, text="源\\目标", font=ui_fonts.ui_font(10, "bold")).grid(row=0, column=0, padx=5, pady=5)
        for col, dst in enumerate(types, start=1):
            ctk.CTkLabel(main_frame, text=dst, font=ui_fonts.ui_font(10, "bold")).grid(row=0, column=col, padx=5, pady=5)

        # 行
        for row, src in enumerate(types, start=1):
            ctk.CTkLabel(main_frame, text=src, font=ui_fonts.ui_font(10, "bold")).grid(row=row, column=0, padx=5, pady=5)
            for col, dst in enumerate(types, start=1):
                entry = ctk.CTkEntry(main_frame, textvariable=vars_dict[src][dst], width=60)
                entry.grid(row=row, column=col, padx=2, pady=2)

        # 按钮区域
        btn_frame = ctk.CTkFrame(dialog)
        btn_frame.pack(pady=10)
        ctk.CTkButton(btn_frame, text="保存",
                      command=lambda: self._save_transition_matrix(dialog, vars_dict, types)).pack(side='left', padx=10)
        ctk.CTkButton(btn_frame, text="取消", command=dialog.destroy).pack(side='left', padx=10)
        ctk.CTkButton(btn_frame, text="重置默认", command=lambda: self._reset_transition_matrix(vars_dict, types)).pack(
            side='left', padx=10)

        dialog._center_dialog(self)

    def _save_transition_matrix(self, dialog, vars_dict, types):
        """保存矩阵并自动归一化每行"""
        new_matrix = {}
        for src in types:
            row = {}
            total = 0.0
            for dst in types:
                val = vars_dict[src][dst].get()
                if val < 0:
                    val = 0.0
                row[dst] = val
                total += val
            if total == 0:
                for dst in types:
                    row[dst] = 1.0 / len(types)
            else:
                for dst in types:
                    row[dst] /= total
            new_matrix[src] = row
        self.transition_matrix = new_matrix
        dialog.destroy()

    def _reset_transition_matrix(self, vars_dict, types):
        default = EvolutionRules().transition_matrix  # 使用 EvolutionRules 默认矩阵
        for src in types:
            for dst in types:
                vars_dict[src][dst].set(default.get(src, {}).get(dst, 0.0))

    def _default_transition_matrix(self):
        """返回默认转移矩阵（字符串键）"""
        return copy.deepcopy(EvolutionRules.DEFAULT_TRANSITION_MATRIX)

    def _default_step_for(self, key):
        """返回默认步进值"""
        steps = {
            "background": 0.02,
            "branch": 0.05,
            "dialog": 0.1,
            "interaction": 0.2,
            "action": 0.3
        }
        return steps.get(key, 0.1)

    def _refresh_ui_from_config(self):
        """更新所有UI控件（包括步进值）"""
        self.initial_prompt_text.delete("1.0", "end")
        self.initial_prompt_text.insert("1.0", self.initial_prompt)
        self._refresh_coupling_widgets()
        self.entry_cost_var.set(str(int(self.entry_action_cost)))
        self.entry_address_var.set(self.entry_address)
        self.entry_distance_var.set(_format_optional_number(
            self.entry_address_max_distance))
        self.entry_height_min_var.set(_format_optional_number(self.entry_height_min))
        self.entry_height_max_var.set(_format_optional_number(self.entry_height_max))
        # 主角称呼与对话语音在「角色」页，由它自己回填
        self.character_panel.on_coupling_changed(self.coupling_level)
        self.character_panel.refresh_from_config()

        for key, box in self.section_frames.items():
            box.delete("1.0", "end")
            box.insert("1.0", self.section_prompts.get(key, ""))

        # 更新步进值
        for key, var in self.section_step_vars.items():
            step = self.section_steps.get(key, self._default_step_for(key))
            var.set(step)

        # 转移矩阵无需显示，但已保存在 self.transition_matrix 中

        self.chapter_trigger_panel.refresh_list()
        self.component_panel.refresh_list()
        self._update_prompt_snapshot()

    def _load_scenario(self, scenario_id):
        config = self._scenario_repo.load_config(scenario_id)
        if config is None:
            return
        self.initial_prompt = config.get("initial_prompt", "")
        self.section_prompts = config.get("section_prompts", {})
        self.coupling_level = normalize_coupling_level(config.get("coupling_level"))
        self.protagonist_title = str(config.get("protagonist_title", "") or "").strip()
        self.evolution_attrs = config.get("evolution_attrs", [])
        self.chapters = normalize_chapters(config.get("chapters", []))
        self.triggers = config.get("triggers", [])
        self.entry_action_cost = max(0, int(config.get("entry_action_cost", 0) or 0))
        admission = normalize_admission_fields(config)
        self.entry_address = admission[FIELD_ADDRESS]
        self.entry_address_max_distance = admission[FIELD_MAX_DISTANCE]
        self.entry_height_min = admission[FIELD_HEIGHT_MIN]
        self.entry_height_max = admission[FIELD_HEIGHT_MAX]
        self.text_component = normalize_text_component(config.get("text_component"))
        self.voice = normalize_voice(config.get("voice"))
        self.components = [c for c in config.get("components", [])
                           if c not in ("text", "text_card", "text_nvl")]
        if not isinstance(self.components, list):
            self.components = []
        self.components_params = config.get("components_params", {})
        if not isinstance(self.components_params, dict):
            self.components_params = {}

        # 加载步进值和转移矩阵，缺失则用默认
        self.section_steps = config.get("section_steps", {})
        for key in ["background","branch","dialog","interaction","action"]:
            if key not in self.section_steps:
                self.section_steps[key] = self._default_step_for(key)

        self.transition_matrix = config.get("transition_matrix")
        if not self.transition_matrix:
            self.transition_matrix = self._default_transition_matrix()

        # 先定位副本方案 id 再刷新 UI：刷新过程中各子面板的自动保存/同步
        # 逻辑依赖 current_scenario_id 指向正在加载的配置
        self.current_scenario_id = scenario_id
        self._refresh_ui_from_config()
        self.scenario_combo.set(scenario_id)

    def _get_prompt_data_from_ui(self) -> dict:
        """从UI控件收集提示词相关数据"""
        try:
            entry_cost = max(0, int(self.entry_cost_var.get()))
        except Exception:
            entry_cost = self.entry_action_cost
        return {
            "initial_prompt": self.initial_prompt_text.get("1.0", "end-1c").strip(),
            "coupling_level": self.coupling_level,
            "protagonist_title": self.character_panel.protagonist_title(),
            "section_prompts": {key: box.get("1.0", "end-1c").strip()
                                for key, box in self.section_frames.items()},
            "section_steps": {key: var.get() for key, var in self.section_step_vars.items()},
            "transition_matrix": self.transition_matrix,
            "entry_action_cost": entry_cost,
            "entry_address": self.entry_address,
            "entry_address_max_distance_m": self._optional_number(
                self.entry_distance_var, self.entry_address_max_distance),
            "entry_height_min_m": self._optional_number(
                self.entry_height_min_var, self.entry_height_min),
            "entry_height_max_m": self._optional_number(
                self.entry_height_max_var, self.entry_height_max),
            "voice": self.character_panel.collect_voice()
        }

    def _pick_entry_address(self):
        """选择入口地址（主世界锚点；复用地址注册表对话框；消除 = 不做地址准入）。"""
        dialog = AddressTextDialog(
            self, "副本入口地址（主世界）",
            description=("入口是主世界里的一扇门：角色有地址时，进入副本需要与这里选择的"
                         "地址满足距离上限，只能选择已申领的地址（详见 "
                         "docs/designs/address-system/authoring.md）；点「消除」表示不做地址准入。"
                         "它与副本内章节的取景地址无关。"),
            initial=self.entry_address, allow_empty=True)
        if dialog.result is None:
            return
        self.entry_address = dialog.result
        self.entry_address_var.set(self.entry_address)

    def _optional_number(self, var, fallback):
        """准入输入的归一化：空白 = None，非法输入保留上一次的值。"""
        text = var.get().strip()
        if not text:
            return None
        value = coerce_distance(text)
        return fallback if value is None else value

    def destroy(self):
        """销毁时收预听（停播、收线程、清临时 WAV）——预听在「角色」页里。"""
        panel = getattr(self, "character_panel", None)
        if panel is not None:
            panel.shutdown_preview()
        super().destroy()

    def _save_prompts(self):
        """仅保存提示词部分，不修改演化量和触发器"""
        if not self.current_scenario_id:
            return
        # 加载现有配置
        config = self._scenario_repo.load_config(self.current_scenario_id)
        if config is None:
            config = {}
        # 更新提示词字段
        prompt_data = self._get_prompt_data_from_ui()
        config["initial_prompt"] = prompt_data["initial_prompt"]
        config["coupling_level"] = prompt_data["coupling_level"]
        # 主角称呼与语音在「角色」页，由角色页自己防抖保存；这里一并写回，
        # 保证「保存此页面」之后磁盘上的方案与界面完全一致
        config["protagonist_title"] = prompt_data["protagonist_title"]
        config["voice"] = prompt_data["voice"]
        config["section_prompts"] = prompt_data["section_prompts"]
        config["section_steps"] = prompt_data["section_steps"]
        config["transition_matrix"] = prompt_data["transition_matrix"]
        config["entry_action_cost"] = prompt_data["entry_action_cost"]
        config["entry_address"] = prompt_data["entry_address"]
        config["entry_address_max_distance_m"] = prompt_data[
            "entry_address_max_distance_m"]
        config["entry_height_min_m"] = prompt_data["entry_height_min_m"]
        config["entry_height_max_m"] = prompt_data["entry_height_max_m"]
        self.entry_address_max_distance = prompt_data[
            "entry_address_max_distance_m"]
        self.entry_height_min = prompt_data["entry_height_min_m"]
        self.entry_height_max = prompt_data["entry_height_max_m"]
        # 保留组件选择与参数（组件面板独立保存，这里不覆盖已保存值）
        if "text_component" in config:
            config["text_component"] = self.text_component
        if "components" in config:
            config["components"] = self.components
        if "components_params" in config:
            config["components_params"] = self.components_params
        # 保存（保留 evolution_attrs 和 triggers）
        self._scenario_repo.save_config(self.current_scenario_id, config)
        # 更新快照
        self._update_prompt_snapshot()
        self._notify_validation()
        ui.ctk.dialogs.showinfo("成功", "通用设置已保存")

    def _save_evolution_triggers(self):
        """仅保存演化量、章节与触发器，不修改提示词"""
        if not self.current_scenario_id:
            return
        config = self._scenario_repo.load_config(self.current_scenario_id)
        if config is None:
            config = {}
        config["evolution_attrs"] = self.evolution_attrs
        config["chapters"] = self.chapters
        config["triggers"] = self.triggers
        self._scenario_repo.save_config(self.current_scenario_id, config)
        self._notify_validation()
        # 不更新提示词快照

    def _notify_validation(self) -> None:
        """保存后把方案校验结果反馈给作者：有错误级诊断时弹窗汇总，其余仅控制台。"""
        diagnostics = getattr(self._scenario_repo, "last_diagnostics", None) or []
        if has_errors(diagnostics):
            ui.ctk.dialogs.showwarning(
                "方案校验发现问题",
                format_diagnostics(diagnostics, include_info=False))

    def _rename_scenario(self):
        old_name = self.current_scenario_id
        if old_name == DEFAULT_SCENARIO_ID:
            ui.ctk.dialogs.showwarning("警告", "默认副本方案不可重命名")
            return

        dlg = InputDialog(self, title="重命名副本方案", prompt=f"将 '{old_name}' 重命名为:")
        new_name = dlg.get_input()
        if not new_name or not re.match(r'^\w+$', new_name):
            return

        if self._scenario_repo.exists(new_name):
            ui.ctk.dialogs.showerror("错误", "副本方案名称已存在")
            return

        # 加载旧配置并保存为新名称，再删除旧配置
        config = self._scenario_repo.load_config(old_name)
        if config is None:
            ui.ctk.dialogs.showerror("错误", f"无法加载副本方案 '{old_name}'")
            return

        try:
            self._scenario_repo.save_config(new_name, config)
            self._scenario_repo.delete(old_name)
            self._refresh_scenario_list()
            self._load_scenario(new_name)
        except Exception as e:
            ui.ctk.dialogs.showerror("错误", f"重命名失败: {e}")

    def _delete_scenario(self):
        if self.current_scenario_id == DEFAULT_SCENARIO_ID:
            ui.ctk.dialogs.showwarning("警告", "默认副本方案不可删除")
            return
        if not ui.ctk.dialogs.askyesno("确认", f"确定删除副本方案 '{self.current_scenario_id}' 吗？\n此操作不可恢复！"):
            return
        try:
            self._scenario_repo.delete(self.current_scenario_id)
            self._refresh_scenario_list()
            self._load_scenario(DEFAULT_SCENARIO_ID)
        except Exception as e:
            ui.ctk.dialogs.showerror("错误", str(e))

    def _new_scenario(self):
        dlg = InputDialog(self, title="新建副本方案", prompt="请输入新副本方案名称:")
        new_id = dlg.get_input()
        if not new_id or not re.match(r'^\w+$', new_id):
            return
        if self._scenario_repo.exists(new_id):
            ui.ctk.dialogs.showerror("错误", "副本方案已存在")
            return
        empty_config = {
            "initial_prompt": "",
            "coupling_level": DEFAULT_COUPLING_LEVEL,
            "protagonist_title": "",
            "text_component": DEFAULT_TEXT_COMPONENT,
            "section_prompts": {k: "" for k in ["background","branch","dialog","interaction","action"]},
            "custom_attrs": [],
            "chapters": [],
            "triggers": [],
            "entry_action_cost": 0,
            "entry_address": "",
            "entry_address_max_distance_m": None,
            "entry_height_min_m": None,
            "entry_height_max_m": None,
            "components": ["attr_bar"],
            "components_params": {},
        }
        if self._scenario_repo.create(new_id, empty_config):
            self._refresh_scenario_list()
            self._load_scenario(new_id)
        else:
            ui.ctk.dialogs.showerror("错误", "创建失败")

    def _update_prompt_snapshot(self):
        """用当前UI内容更新快照（用于对比）"""
        self._saved_prompt_snapshot = self._get_prompt_data_from_ui()

    def _is_prompt_modified(self) -> bool:
        """对比当前UI与快照，判断提示词是否被修改"""
        current = self._get_prompt_data_from_ui()
        return current != self._saved_prompt_snapshot

    def _on_scenario_select(self, choice):
        if self.current_scenario_id == choice:
            return
        # 如果提示词有修改，询问是否保存
        if self._is_prompt_modified():
            if ui.ctk.dialogs.askyesno("通用设置未保存", "当前通用设置已修改，是否保存？", parent=self):
                self._save_prompts()
            # 如果用户选择“否”，则放弃修改（快照不变）
        # 加载新副本方案
        self._load_scenario(choice)

    def _refresh_scenario_list(self):
        dungeon_list = self._scenario_repo.list_all()
        self._rebuild_dropdown()
        if self.current_scenario_id not in dungeon_list:
            self.current_scenario_id = dungeon_list[0] if dungeon_list else None
            self.scenario_combo.set(self.current_scenario_id or "")
            if self.current_scenario_id:
                self._load_scenario(self.current_scenario_id)

    def _mark_modified(self):
        # 仅用于标识，暂不需要额外操作，因为保存时重新收集
        pass

    def refresh_list_for_components(self):
        """组件面板保存后同步编辑器的 text_component/components/components_params。"""
        self.text_component = self.component_panel.text_component
        self.components = list(self.component_panel.components)
        self.components_params = dict(self.component_panel.components_params)

    def _on_coupling_option(self, value):
        """OptionButton 回调：把选项控件的档位标签映射回等级并刷新左栏。"""
        self.coupling_level = normalize_coupling_level(value)
        self._refresh_coupling_widgets()

    def _refresh_coupling_widgets(self):
        """把当前耦合等级回显到左栏（轮换档位、说明、等级初始提示标签）。"""
        self.coupling_option.set(coupling_label(self.coupling_level))
        self.coupling_prompt_label.configure(
            text=coupling_initial_prompt(self.coupling_level))
        # 主角称呼与对话语音依赖说话人标记，只在 Solea/Bulla 有意义（角色页置灰）
        self.character_panel.on_coupling_changed(self.coupling_level)


