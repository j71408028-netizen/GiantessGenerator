"""统一界面配色常量（亮/暗 = (light, dark) 元组）。

各 UI 页面把原先各自定义的颜色常量统一集中在此，使用时按原名导入。
需要单独取某一模式颜色时按索引取值：[0] = 亮色，[1] = 暗色。

主框架的棕色 / 灰色已收敛为两套统一色阶（档位越大越深）：
    BROWN : 暖棕色阶，50 ~ 1000
    GRAY  : 中性色阶，0 ~ 1000（0 = 纯白）
"""

# -----------------------------------------------------------------
# 统一色阶
# -----------------------------------------------------------------

# 运行时补丁：模式切换时同步刷新 CTk 控件的 Frame 底色，
# 避免主题切换后几何重排露出的缝隙闪现旧模式颜色（所有 UI 入口通用）。
import ui.common.ctk_patch  # noqa: E402,F401

# 暖棕色阶：浅米 → 最深棕
BROWN = {
    50:   "#F9F3F2",
    100:  "#EFEBE9",
    200:  "#E4DCD6",
    300:  "#D7CCC8",
    400:  "#BCAAA4",
    500:  "#A08D81",
    600:  "#8B7A60",
    700:  "#6B5F47",
    800:  "#5A5240",
    900:  "#4A4334",
    1000: "#3A392B"
}

# 中性色阶：白 → 近黑（暗色模式由低档取浅、高档取深）
GRAY = {
    0:    "#FFFFFF",    # 纯白
    100:  "#FAFAFA",    # 基底亮
    200:  "#F5F5F5",    # 浅灰：顶部悬停
    300:  "#E0E0E0",    # 浅灰：边框 / 正文明
    400:  "#BDBDBD",    # 中浅灰：禁用 / 占位
    500:  "#9E9E9E",    # 中灰：次要文字
    600:  "#757575",    # 中深灰：提示文字
    700:  "#616161",    # 深灰：禁用明
    800:  "#424242",    # 深灰：描边
    900:  "#2D2D2D",    # 近黑：暗色面板底
    1000: "#1E1E24",    # 近黑：暗色最深底
}

# 导航栏
NAV_BG = (BROWN[100], GRAY[900])
NAV_WORLD_BG = ("#EAF0E6", "#202723")
NAV_WORLD_GREEN = ("#55705A", "#9DB39E")
NAV_TITLE = (BROWN[700], BROWN[400])

# 世界包加载时导航栏的绿色主题（低饱和度灰绿，视觉更柔和）
NAV_WORLD_TITLE = ("#4C5F4F", "#A9BCA7")
NAV_WORLD_SELECTED_BG = ("#5F7A63", "#45564A")
NAV_WORLD_SELECTED_TEXT = ("#F4F7F2", "#E9EFE6")
NAV_WORLD_TEXT = ("#55705A", "#9DB39E")
NAV_WORLD_HOVER = ("#CBD8C9", "#2E3B33")

# 选中 / 未选中 / 悬停
NAV_SELECTED_BG = (BROWN[500], BROWN[400])
NAV_SELECTED_TEXT = (GRAY[200], BROWN[50])
NAV_TEXT = (GRAY[600], GRAY[500])
NAV_HOVER = (BROWN[300], GRAY[700])

# 分段控件轨道与选中态（配色直接取自导航栏）
SEG_TRACK_BG = NAV_BG
SEG_TRACK_BORDER = (BROWN[200], GRAY[700])
SEG_SELECTED_BG = NAV_SELECTED_BG
SEG_SELECTED_HOVER = (BROWN[600], GRAY[700])
SEG_SELECTED_TEXT = NAV_SELECTED_TEXT
SEG_UNSELECTED_TEXT = NAV_TEXT
SEG_HOVER = NAV_HOVER

# ---------------------------------------------------------------
# 通用面板 / 列表 / 卡片配色（各管理页与探索页共用）
# ---------------------------------------------------------------

# 面板底色 / 边框 / 悬停
BASE = (GRAY[100], GRAY[1000])
BORDER = (GRAY[300], GRAY[800])
HOVER = (BROWN[100], GRAY[900])
BORDER_ALT = (BROWN[400], GRAY[700])

# ---------------------------------------------------------------
# 设置页调色板
# ---------------------------------------------------------------

# 设置的标签
HARD_LABEL = (BROWN[800], "#DDE1E6")
CHECKBOX_HOVER = (BROWN[700], BROWN[300])

# 输入控件：填充色与卡片对比度刻意压低，仅做弱区分
INPUT_BG = (BROWN[50], "#23262C")
INPUT_BORDER = (BROWN[200], "#3B414B")
INPUT_HOVER = (BROWN[100], "#2A2E37")

# OptionMenu 右侧按钮：低对比度柔和色
MENU_BTN = (BROWN[200], "#2A2E37")
MENU_BTN_HOVER = (BROWN[300], "#3B414B")

# 开关 / 进度条 / 状态
SWC_FG = (BROWN[300], GRAY[800])
SWC_PROGRESS = (BROWN[600], BROWN[400])
SWC_BTN = (BROWN[100], GRAY[300])
STATUS_OK = ("#2E7D32", "#81C784")
STATUS_ERR = ("#C62828", "#EF9A9A")

# ---------------------------------------------------------------
# 对话框配色
# ---------------------------------------------------------------
DLG_BORDER = (GRAY[300], "#3B414B")
DLG_HOVER = (GRAY[200], GRAY[800])
DLG_BTN_PRIMARY = ("#3B8ED0", "#1F6AA5")
DLG_BTN_PRIMARY_HOVER = ("#2F7FB5", "#1A5B8F")
DLG_FG = (GRAY[200], GRAY[1000])

# ---------------------------------------------------------------
# 参数自定义对话框配色
# ---------------------------------------------------------------
PARAMS_STATUS = {
    "random": (GRAY[500], GRAY[600]),
    "item": (BROWN[1000], GRAY[300]),
    "custom": ("#FFB864", "#FBBF24"),
}
VAL_DISABLED = (GRAY[400], GRAY[700])
VAL_GOLDEN = ("#F57F17", "#FBBF24")
VIEW_PNL_FG = ("#FFFDF5", "#1A1E26")
VIEW_PNL_BORDER = (BROWN[300], GRAY[800])

# ---------------------------------------------------------------
# 通用控件态（各管理页 / 探索页 / 剧本页共用）
# ---------------------------------------------------------------

# 白色面板 / 输入框底色（亮色全白、暗色深灰）
PNL_BG = (GRAY[0], GRAY[900])
PNL_BORDER = (GRAY[300], GRAY[800])          # 浅灰边框

# 中性悬停 / 按钮底色（比 HOVER 暗色更深一档，使用最广泛）
HOVER_ALT = (BROWN[100], GRAY[800])
# OptionMenu 按钮 / 滚动条悬停：柔和的中性过渡色
MENU_HOVER = (BROWN[400], GRAY[700])

# 文字
TEXT = (BROWN[1000], GRAY[300])
SOFT = (BROWN[600], BROWN[500])
TEXT_MUTED = (BROWN[500], GRAY[500])            # 次要文字（链接 / 说明）

HARD_TITLE = (BROWN[800], BROWN[300])           # 小标题/栏目标题
TITLE = (BROWN[700], BROWN[400])                # 区块标题

PLACEHOLDER = (GRAY[500], GRAY[600])            # 输入占位 / 空状态提示
TEXT_DISABLED = (GRAY[400], GRAY[700])          # 禁用文字
BROWN_HINT = (BROWN[800], "#B0BEC5")            # 触发器提示（暗侧蓝灰，保留原值）
TEXT_WHITE = (GRAY[0], GRAY[0])                 # 进度条 / 亮色底上白字

# 单色链接 / 标记
LINK_BLUE = "#2196F3"                         # Treeview可点击高亮蓝
TEXT_CYAN = "#00BCD4"
TEXT_ORANGE = "#FF6D00"

# ---------------------------------------------------------------
# 状态按钮配色
# ---------------------------------------------------------------
OK_HOVER = ("#E8F5E9", "#1B5E20")             # 成功操作悬停
COLD_DEL_BG = "#C0392B"
COLD_DEL_HOVER = "#E74C3C"
OK_BTN_HOVER = ("#1B5E20", "#66BB6A")         # 对话框成功按钮悬停
ERR_STRONG = ("#C62828", "#EF5350")           # 删除 / 严重错误
ERR_HOVER = ("#FFEBEE", "#4A1414")            # 删除悬停
CLEAR_BG = ("#FBE9E7", "#3A2B2B")             # 消除悬停（参数弹窗）
CLEAR_BORDER = ("#E0B4B4", "#7A4A4A")         # 消除描边
BLUE_HOVER = ("#BBDEFB", "#3D5A80")           # 蓝色卡片悬停（角色 / 地标列表）

REPORT = ("#EF6C00", "#FFB74D")               # 报告代表色
REPORT_HOVER = ("#FFF3E0", "#E65100")
DUNGEON = ("#6A1B9A", "#BA68C8")               # 副本代表色
DUNGEON_HOVER = ("#F3E5F5", "#4A148C")
TYPEVIEW = ("#7C4DFF", "#B388FF")               # 紫色类型视图
TYPEVIEW_HOVER = ("#EDE7F6", "#311B92")

GOLD = ("#FFB864", "#FBBF24")                 # 金色特殊状态
GOLD_BTN = ("#FFB864", "#FFB864")             # 金色滑块按钮
GOLD_BTN_HOVER = ("#FFA040", "#FFA040")
GOLD_BORDER = ("#FBBF24", "#725B10")          # 金色描边（信息卡 hover）
GOLD_STRONG_BORDER = ("#FBBF24", "#FBBF24")   # 金色粗边框（样式选中框）
GOLD_OUTLINE = "#FBBF24"                      # 对话框聚焦描边

# 滑块 / 进度条
PROGRESS_BTN = (BROWN[500], BROWN[400])
PROGRESS_BTN_HOVER = (BROWN[600], BROWN[300])
SLIDER_TRACK = (GRAY[300], GRAY[800])

# 状态面板指标色（介入度 / 破坏性 / 行动点数）
STAT_BLUE = ("#1976D2", "#42A5F5")
STAT_BLUE_LIGHT = ("#1976D2", "#64B5F6")
STAT_BLUE_DEEP = ("#0D47A1", "#1565C0")

# ---------------------------------------------------------------
# 取色画布 / 弹层
# ---------------------------------------------------------------
CANVAS_BG = GRAY[900]                         # 取色画布底色
CANVAS_BORDER = GRAY[800]                     # 旧 #424242
OVERLAY = "#000000"                           # 模态遮罩

# ---------------------------------------------------------------
# 表格 / 列表（ttk.Treeview 与 tk.Listbox 的单模式色表）
# ---------------------------------------------------------------
TREE_ALT = ("#F8F5F3", "#252326")             # 隔行变色
TREE_SELECT_BG = ("#BBDEFB", "#3D5A80")
TREE_SELECT_FG = (BROWN[1000], GRAY[200])     # 旧 #EFEFEF
TREE_HEAD_BG = (BROWN[200], GRAY[900])        # 旧 #E5DDD6, #2A2A33
TREE_HEAD_FG = (BROWN[800], BROWN[400])
TREE_DISABLED_FG = GRAY[600]                  # Treeview禁用行前景

# ---------------------------------------------------------------
# Quip 标签分类色
# ---------------------------------------------------------------
QUIP_TYPE_COLORS = {
    "a": ("#C2410C", "#FB923C"),
    "b": ("#15803D", "#4ADE80"),
    "c": ("#1D4ED8", "#60A5FA"),
    "d": ("#A16207", "#FACC15"),
    "e": ("#7E22CE", "#C084FC"),
}

# ---------------------------------------------------------------
# 介绍页（Facebook 风格信息流）
# ---------------------------------------------------------------
FB_CARD_BG = ("#FFFFFF", "#242526")           # 卡片底
FB_ACCENT = ("#1877F2", "#64B5F6")            # 顶栏强调条
FB_TEXT = ("#1C1E21", "#E4E6EB")              # 昵称 / 正文
FB_BLUE = ("#1877F2", "#90CAF9")              # 蓝色文字
FB_CHIP_BG = ("#E8F0FE", "#3A3B3C")           # 胶囊按钮底
FB_CHIP_HOVER = ("#D2E3FC", "#4E4F50")
FB_MUTED = ("#65676B", "#B0B3B8")             # 次要灰字
FB_BTN = ("#1877F2", "#1877F2")               # 实心蓝按钮
FB_BTN_HOVER = ("#166FE5", "#166FE5")
FB_TAG_BG = ("#E3F2FD", "#2C3E50")            # 话题标签底
FB_TAG_FG = ("#1565C0", "#90CAF9")            # 话题标签字
FB_TAG_HOVER = ("#BBDEFB", "#1E88E5")         # 话题标签按钮悬停
FB_TAG_BORDER = ("#90CAF9", "#1E88E5")        # 话题标签按钮描边
FB_MENU_BORDER = ("#64B5F6", "#1E88E5")       # 介绍页下拉框描边
FB_MENU_SCROLL = ("#E3F2FD", "#37474F")       # 介绍页滚动条
FB_MENU_SCROLL_HOVER = ("#90CAF9", "#5C6BC0")
FB_MENU_TEXT = ("#1A237E", "#BBDEFB")         # 介绍页下拉文字

# ---------------------------------------------------------------
# 报告页（主题色板，轻度游戏质感）
# ---------------------------------------------------------------
HEADER_BG = ("#EDE7DD", "#26282E")            # 报告标题栏底
GOLD_TITLE = ("#5D4037", "#FFD54F")           # 报告标题金
TAG_SEPARATOR = ("#B8860B", "#8D6E63")        # 分隔线
TAG_INTRO = ("#4E342E", "#BCAAA4")            # 引入段
TAG_WILL = ("#2E7D32", "#A5D6A7")             # 意愿
TAG_MEASURE = ("#00695C", "#4DB6AC")          # 身体尺寸
TAG_COMPARE = ("#795548", "#8D6E63")          # 对比
TAG_QUIP = ("#6A1B9A", "#CE93D8")             # 描述
TAG_CASUALTY = ("#B71C1C", "#FF5252")         # 伤亡
TAG_BODY = ("#424242", "#E0E0E0")             # 正文

# ---------------------------------------------------------------
# 预览图绘制调色板（角色预览画布，单色值）
# ---------------------------------------------------------------
VIEW_OUTLINE = "#2A2033"                        # poly 默认描边
VIEW_OUTLINE_DARK = "#332F2E"                   # 深色部件描边
VIEW_HAIR = "#2A2625"
VIEW_HAIR_SHADE = "#3B3635"
VIEW_SKIN = "#F8E6DE"
VIEW_SKIN_SHADE = "#F5E4DD"
VIEW_SKIN_LINE = "#D6BEAE"                      # 皮肤部件描边
VIEW_CLOTH = "#F0EDEB"                          # 白衣
VIEW_NAVY = "#4A5366"                           # 藏蓝衣
VIEW_NAVY_LINE = "#6B778D"
VIEW_EYE = "#7B7771"                            # 虹膜
VIEW_EYE_WHITE = "#E9E8E8"                      # 眼白
VIEW_EYE_WHITE_LINE = "#DED6D6"
VIEW_PUPIL = "#615F5F"                          # 瞳孔
VIEW_HIGHLIGHT = "#EAE8E8"                      # 高光
VIEW_MOUTH = "#B08587"
VIEW_BLUSH = "#E8CFCD"
VIEW_SHADOW = "#707476"                         # 地面阴影
VIEW_CLOTH_DARK = "#232630"                     # 深色衣料
VIEW_CLOTH_DARK_LINE = "#171921"
VIEW_WHITE_LINE = "#D1CDCA"                     # 白衣暗色描边
VIEW_SLEEVE = "#EAE7E5"                         # 袖口 / 面罩
VIEW_STITCH = "#ACA8A6"                         # 缝线

# ---------------------------------------------------------------
# 依赖图（Graphviz）配色
# ---------------------------------------------------------------
ACTION_FILL_INSERT = "#BBDEFB"
ACTION_FILL_OPTION = "#C8E6C9"
ACTION_FILL_SENSITIVITY = "#E1BEE7"
ACTION_FILL_ENDING = "#FFE0B2"
ACTION_FILL_BACKGROUND = "#FFF9C4"
ACTION_FILL_NONE = "#E0E0E0"
GRAPH_NODE_ERR_FILL = "#E53935"               # 环节点填充
GRAPH_NODE_ERR_BORDER = "#B71C1C"
GRAPH_EDGE_ERR = "#C62828"                    # 环边
GRAPH_EDGE_NORMAL = "#78909C"                 # 普通边
GRAPH_NODE_OUTLINE = "#90A4AE"                # 普通节点描边