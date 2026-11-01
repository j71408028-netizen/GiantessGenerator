"""资源类型声明：资源层的单一真相源。

「资源」指成组、可替换、可打包的内容：地标风格、描述风格、副本方案、取材表、
挑战包、行为包、话题表、小游戏。本模块只声明**有哪些类型、每种长什么样**——容器形状、
主文件命名、字段清单、id 规则、关联的资源设置键、自由存储根。这里不做任何 IO，
也不 import persistence：路径定位与读写分别落在
``persistence/resources/registry.py`` 与 ``persistence/resources/envelope.py``。

原先散在六处的类型清单（``PACK_RESOURCE_PATHS`` / ``LIST_RESOURCE_TYPES`` /
``_missing_members`` 的成员形状 / ``WorldPackManifest.validate`` /
``RESOURCE_SETTINGS_KEYS`` / 资源中心的可打包清单）都从这里派生，
新增一类资源 = 一份声明 + 一个 repo + 一处界面接入。

三条命名规则对本文件声明的**全部**类型生效：

- 类型 id：``^[a-z][a-z0-9_]*$``，复数形式，同时充当自由目录名与世界包内目录名；
- 资源 id：``^[a-z0-9][a-z0-9._-]{0,63}$``，来自容器名，不从显示名推断；
- 主文件后缀：只有容器本身需要被外部程序认领时才带后缀（``.csv`` 要给表格软件、
  ``.chal`` 是整包、``.py`` 是 Python 包）。单个 JSON 文件的主文件**不带后缀**——
  路径本身就是资源的地址，同目录的 ``.addr.json`` / ``.meta.json`` 一眼可辨，
  按名与按类型两种浏览方式才能得到不同视图；编辑器里关联语言见 docs/dev/architecture.md。

显示名（``name`` / ``label`` 字段）可以是任意语言，只用于界面与提示词。
"""

import re
from dataclasses import dataclass, fields
from typing import Any, Dict, Tuple

TYPE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
RESOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

_ID_REGEX_CACHE: Dict[str, "re.Pattern"] = {}


def _id_regex(pattern: str):
    """按类型声明的正则串取编译结果（同一串只编译一次）。"""
    compiled = _ID_REGEX_CACHE.get(pattern)
    if compiled is None:
        compiled = re.compile(pattern)
        _ID_REGEX_CACHE[pattern] = compiled
    return compiled


#: 容器形状。决定主文件怎么定位、信封放在哪、打包成员怎么核对。
CONTAINERS = (
    "json_array",    # 单个 JSON 文件的根是数组，一个文件即一份资源
    "json_object",   # 单个 JSON 文件的根是对象，一个文件即一份资源
    "json_dir",      # 一个目录即一份资源，主文件在目录内
    "csv",           # 一个 CSV 文件即一份资源（表头即字段）
    "csv_dir",       # 一个目录即一份资源，主文件是目录内的 CSV，可带配置 JSON
    "csv_bundle",    # 一个目录即一份资源，内含若干命名 CSV 成员（取材表）
    "py_package",    # 一个目录即一份 Python 包
    "opaque",        # 不可解释的整包（加密归档等），只有文件名可外读
)

#: 自由存储根（相对数据目录）。世界包内一律按类型 id 平铺，与本字段无关。
ROOTS = ("packs", "static")

#: 字段取值种类。CSV 的列与 JSON 的键共用同一套。
FIELD_KINDS = ("str", "text", "int", "float", "number", "bool", "list", "dict")

#: 界面一次可选数量：``multi`` = 多选列表，``single`` = 单选下拉。
#: 只是界面提示，不参与世界包清单校验（校验看 ``max_items``）。
PICK_MODES = ("multi", "single")

#: 不归属任何资源类型、但同属资源域的设置键。
EXTRA_SETTING_KEYS = ("active_world",)


@dataclass(frozen=True)
class FieldSpec:
    """一个字段（JSON 键或 CSV 列）的声明。

    ``required`` 只用于校验提示：缺失必填字段记为诊断，不阻断读取——存量文件
    可能确实没有该字段，读取侧按 ``default`` 补齐。
    """

    key: str
    kind: str
    label: str = ""
    default: Any = None
    required: bool = False
    choices: tuple = ()
    note: str = ""


@dataclass(frozen=True)
class MemberSpec:
    """多成员容器（``csv_bundle``）内的一个成员文件。

    ``key`` 同时是成员键与文件名主干：``names`` → ``names.csv``。``required``
    只表示「组内必须有这份文件」；标记为非必填的成员允许缺席，读取侧回落到
    内置缺省组里的同名成员——这样新增一种成员不必迁移存量组，用户只覆盖其中
    一两张表时也不必复制整套。
    """

    key: str
    label: str = ""
    suffix: str = ".csv"
    fields: Tuple[FieldSpec, ...] = ()
    required: bool = False
    note: str = ""

    def file_name(self) -> str:
        return f"{self.key}{self.suffix}"

    def relpath(self, resource_id: str) -> str:
        """成员相对资源目录的路径（正斜杠）。"""
        return f"{resource_id}/{self.file_name()}"

    def field_keys(self) -> Tuple[str, ...]:
        return tuple(f.key for f in self.fields)

    def required_keys(self) -> Tuple[str, ...]:
        return tuple(f.key for f in self.fields if f.required)


@dataclass(frozen=True)
class ResourceTypeSpec:
    """一类资源的完整声明。

    - ``primary``：主文件相对路径模板，必须含 ``{id}``，如 ``{id}``（无后缀的
      JSON 容器）/ ``{id}/config.json`` / ``{id}.csv``；
    - ``config``：目录型容器内的可选配置 JSON 路径模板，如 ``{id}/config.json``；
    - ``root``：自由目录所在的存储根（``packs`` = 用户可写素材，``static`` = 内置随包素材）；
    - ``id_required``：载荷内必须自带 ``id``（目录型容器才有地方放）；
      其余类型的 id 取自容器名，信封里的 ``id`` 只做冗余校验；
    - ``settings_keys``：指向本类型的资源设置键（选择态）；
    - ``packable``：可进世界包；
    - ``addressable``：带 ``<id>.addr.json`` 注册地址伴随文件；
    - ``max_items``：世界包一次可声明的份数上限（0 = 不限）；
    - ``id_pattern``：本类型专属的资源 id 正则（空 = 用全局 ``RESOURCE_ID_RE``）；
      行为包要按 Python 标识符取名，比全局规则宽，不能共用一个正则；
    - ``ui_pick``：界面上的选择粒度，见 ``PICK_MODES``；
    - ``trait_values``：本类型的特色（``traits``）词表，声明在信封 ``traits``
      字段里的合法取值；空 = 该类型暂未启用特色，界面对其隐藏该筛选项。
      词表外的值不清洗、原样保留；
    - ``trait_pick``：特色在界面上的选择粒度，见 ``PICK_MODES``；
    - ``members``：``csv_bundle`` 容器的成员清单（第一项即主文件）。其余容器
      为空，字段写在 ``fields`` 上。
    """

    type_id: str
    container: str
    primary: str
    config: str = ""
    label: str = ""
    root: str = "packs"
    fields: Tuple[FieldSpec, ...] = ()
    id_required: bool = False
    settings_keys: Tuple[str, ...] = ()
    packable: bool = True
    addressable: bool = False
    max_items: int = 0
    id_pattern: str = ""
    ui_pick: str = "multi"
    trait_values: Tuple[str, ...] = ()
    trait_pick: str = "single"
    members: Tuple[MemberSpec, ...] = ()
    note: str = ""

    def id_regex(self):
        """资源 id 的编译后正则：类型自带的优先，否则用全局规则。"""
        return _id_regex(self.id_pattern) if self.id_pattern else RESOURCE_ID_RE

    def valid_id(self, resource_id: str) -> bool:
        return bool(self.id_regex().match(str(resource_id or "")))

    def field_keys(self) -> Tuple[str, ...]:
        return tuple(f.key for f in self.fields)

    def field(self, key: str):
        for f in self.fields:
            if f.key == key:
                return f
        return None

    def required_keys(self) -> Tuple[str, ...]:
        return tuple(f.key for f in self.fields if f.required)

    def member(self, key: str):
        """按成员键取声明；无此成员返回 None。"""
        for m in self.members:
            if m.key == key:
                return m
        return None


# ---------------------------------------------------------------------------
# 字段清单
# ---------------------------------------------------------------------------

_LANDMARK_FIELDS = (
    FieldSpec("name", "str", "名称", required=True),
    FieldSpec("size", "number", "尺寸（米）", required=True),
    FieldSpec("dimension", "str", "维度朝向", default="vertical",
              choices=("vertical", "horizontal")),
    FieldSpec("frequency", "str", "出现频率", default="unique",
              choices=("unique", "common")),
    FieldSpec("horizontal_type", "str", "水平类型", default=None,
              note="仅 dimension=horizontal 有意义，缺省为 length"),
    FieldSpec("address", "str", "注册地址", default=""),
    FieldSpec("address_mode", "str", "地址参与方式", default="",
              choices=("", "inherit", "portable", "exact", "relative")),
)

_SCENARIO_FIELDS = (
    FieldSpec("initial_prompt", "text", "开场提示词", default=""),
    FieldSpec("coupling_level", "str", "耦合等级"),
    FieldSpec("protagonist_title", "str", "主角称谓", default=""),
    FieldSpec("text_component", "str", "文本组件"),
    FieldSpec("entry_action_cost", "int", "进入消耗", default=0),
    FieldSpec("entry_address", "str", "进入准入地址", default=""),
    FieldSpec("entry_address_max_distance_m", "number", "进入准入最远距离", default=None),
    FieldSpec("entry_height_min_m", "number", "进入准本身高下限", default=None),
    FieldSpec("entry_height_max_m", "number", "进入准本身高上限", default=None),
    FieldSpec("ending_policy", "str", "结局策略"),
    FieldSpec("section_prompts", "dict", "分段提示词", default=()),
    FieldSpec("section_steps", "dict", "分段步进", default=()),
    FieldSpec("transition_matrix", "dict", "转移矩阵", default=None),
    FieldSpec("evolution_attrs", "list", "演化属性", default=()),
    FieldSpec("chapters", "list", "章节", default=()),
    FieldSpec("triggers", "list", "触发器", default=()),
    FieldSpec("voice", "dict", "对话语音", default=None),
    FieldSpec("components", "list", "显示组件", default=()),
)

_MANIFEST_FIELDS = (
    FieldSpec("id", "str", "资源 id", required=True),
    FieldSpec("name", "str", "显示名", required=True),
    FieldSpec("backend", "str", "运行后端", default="py", choices=("py",)),
    FieldSpec("entry", "str", "入口文件", default="game.py"),
    FieldSpec("params", "list", "参数声明", default=()),
)

_TOPIC_FIELDS = (
    FieldSpec("id", "str", "类型 id", required=True),
    FieldSpec("label", "str", "显示名", default="",
              note="兜底视角（virtual）可以不写"),
    FieldSpec("coord", "list", "坐标", required=True,
              note="三分量：好感 / 紧张 / 状态"),
    FieldSpec("radius", "number", "命中半径", default=None),
    FieldSpec("prompt", "text", "体裁", default=""),
    FieldSpec("focus", "str", "信息预告", default=""),
    FieldSpec("frame", "text", "注意力框架", default=""),
    FieldSpec("opens", "list", "开放字段", default=()),
    FieldSpec("denies", "list", "关闭字段", default=()),
    FieldSpec("budget", "int", "字段预算", default=None),
    FieldSpec("disclosure", "bool", "允许私密字段", default=False),
    FieldSpec("tags", "list", "软分类标签", default=()),
    FieldSpec("priority", "int", "优先级", default=0),
    FieldSpec("cooldown", "float", "冷却", default=0.0),
    FieldSpec("virtual", "bool", "兜底视角", default=False),
)


def _csv_fields(*pairs, required: tuple = ()) -> Tuple[FieldSpec, ...]:
    """``(key, kind)`` 或 ``(key, kind, label)`` 列表 → 字段元组。

    ``required`` 里列出的列在表头缺失时给诊断：读表时会整列取空，表坏了不报错
    最难排查。
    """
    return tuple(
        FieldSpec(p[0], p[1], p[2] if len(p) > 2 else "", required=p[0] in required)
        for p in pairs
    )


_QUIP_FIELDS = _csv_fields(
    ("size_category", "str", "尺寸等级"),
    ("intrusion", "int", "介入度"),
    ("destruction", "int", "破坏性"),
    ("text", "text", "正文"),
    ("style", "str", "风格"),
    ("step", "number", "步进"),
    ("summary", "str", "摘要"),
    required=("size_category", "intrusion", "destruction", "text"),
)


# 取材表的四个成员。姓名 / 身材 / 性格三张表产出角色状态，新闻表的索引列正是
# 这些状态变量（尺寸等级来自身高，介入度与破坏性来自性格演化），所以它们共用
# 一套坐标系，只能整组替换。

_NAME_FIELDS = _csv_fields(
    ("surname", "str", "姓"), ("ppm", "number", "权重"),
    ("name", "str", "名"), ("nick", "str", "昵称"),
)

_PRESET_FIELDS = _csv_fields(
    ("weight", "number", "权重"),
    ("name", "str", "名称"),
    ("height_ratio", "number"), ("leg_ratio", "number"),
    ("foot_length_ratio", "number"), ("arm_span_ratio", "number"),
    ("index_finger_ratio", "number"), ("palm_length_ratio", "number"),
    ("chest_width_ratio", "number"), ("thigh_diameter_ratio", "number"),
    ("forearm_diameter_ratio", "number"),
    ("index_finger_diameter_ratio", "number"),
    ("fingerprint_width_ratio", "number"), ("finger_gap_ratio", "number"),
    ("knee_height_ratio", "number"), ("ankle_height_ratio", "number"),
    ("stride_ratio", "number"),
    required=("name",),
)

_PERSONALITY_FIELDS = _csv_fields(
    ("weight", "number", "权重"),
    ("name", "str", "名称"),
    ("init_intrusion", "number"), ("step_intrusion", "number"),
    ("init_destruction", "number"), ("step_destruction", "number"),
    ("sensitivity", "number"), ("gravity", "number"),
    ("skip_base_prob", "number"), ("description", "text"),
    required=("name",),
)

_NEWS_FIELDS = _csv_fields(
    ("size_category", "str", "尺寸等级"),
    ("memorial", "str", "纪念"),
    ("intrusion", "int", "介入度"),
    ("destruction", "int", "破坏性"),
    ("text", "text", "正文"),
    ("substitute_1", "str", "替换队列1"),
    ("substitute_2", "str", "替换队列2"),
    required=("text",),
)

#: 取材表的成员清单（顺序即界面展示顺序，第一项是主文件模板指向的成员）。
#: 四项都不强制存在：组内至少有一份 CSV 即成立，缺的成员读取时回落到内置组。
MATERIAL_MEMBERS: Tuple[MemberSpec, ...] = (
    MemberSpec("names", "姓名表", fields=_NAME_FIELDS,
               note="surname 与 name 为空的行会被跳过"),
    MemberSpec("presets", "身材表", fields=_PRESET_FIELDS,
               note="列即字段，weight 不进字段顺序"),
    MemberSpec("personalities", "性格表", fields=_PERSONALITY_FIELDS,
               note="init/step 决定角色进入时的介入度与破坏性"),
    MemberSpec("news", "新闻表", fields=_NEWS_FIELDS,
               note="表头即索引优先级顺序"),
)


# ---------------------------------------------------------------------------
# 特色词表
# ---------------------------------------------------------------------------
# ASCII slug → 中文显示名，全局一张表，slug 跨类型唯一。
# 各类型启用哪些取值见 ResourceTypeSpec.trait_values。

TRAIT_LABELS: Dict[str, str] = {
    "realistic": "物理写实",
    "fantasy": "奇幻",
    "anime": "二次元",
    "scifi": "科幻",
    "longform": "长线剧情",
    "gameplay": "游戏为主",
    "multi_ending": "多结局",
}


def trait_label(value: str) -> str:
    """特色 slug 的中文显示名；未知值原样返回。"""
    return TRAIT_LABELS.get(str(value or ""), str(value or ""))


# ---------------------------------------------------------------------------
# 类型清单
# ---------------------------------------------------------------------------
# 顺序即界面展示顺序（世界包创建对话框的打包列表按此排列），也是诊断里的遍历顺序。

RESOURCE_TYPES: Tuple[ResourceTypeSpec, ...] = (
    ResourceTypeSpec(
        type_id="landmarks",
        container="csv",
        primary="{id}.csv",
        label="地标风格",
        fields=_LANDMARK_FIELDS,
        settings_keys=("selected_styles",),
        addressable=True,
        note="地标风格：一份 CSV 是一个风格组，每行一个地标条目",
    ),
    ResourceTypeSpec(
        type_id="quips",
        container="csv_dir",
        primary="{id}/quips.csv",
        config="{id}/config.json",
        label="描述风格",
        fields=_QUIP_FIELDS,
        settings_keys=("selected_quip_styles",),
        addressable=True,
        trait_values=("realistic", "fantasy", "anime", "scifi"),
        note="描述风格：目录即一份资源，quips.csv 每行一个条目，"
             "config.json 存自定义类型与后续匹配配置",
    ),
    ResourceTypeSpec(
        type_id="materials",
        container="csv_bundle",
        primary="{id}/names.csv",
        label="取材表",
        root="static",
        members=MATERIAL_MEMBERS,
        settings_keys=("material_table",),
        max_items=1,
        ui_pick="single",
        trait_values=("realistic", "fantasy", "anime", "scifi"),
        note="取材表：目录即一份资源，内含姓名 / 身材 / 性格 / 新闻四个 CSV 成员，"
             "整组替换；缺席的成员回落到内置组",
    ),
    ResourceTypeSpec(
        type_id="scenarios",
        container="json_dir",
        primary="{id}/config.json",
        label="副本方案",
        fields=_SCENARIO_FIELDS,
        trait_values=("longform", "gameplay", "multi_ending"),
        trait_pick="multi",
        note="副本方案：目录内含 config.json 与 images/ components/ audio/ 等素材",
    ),
    ResourceTypeSpec(
        type_id="challenges",
        container="opaque",
        primary="{id}.chal",
        label="挑战包",
        note="挑战包：整包加密、文件名是唯一外读信息，纳入索引时必须外加兄弟信封",
    ),
    ResourceTypeSpec(
        type_id="behaviors",
        container="py_package",
        primary="{id}/*.py",
        label="行为包",
        root="static",
        max_items=1,
        id_pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
        ui_pick="single",
        note="行为包：目录即包，带 __init__.py 时按包导入；同时只能启用一个，"
             "包名要能当 Python 标识符",
    ),
    ResourceTypeSpec(
        type_id="topics",
        container="json_array",
        primary="{id}",
        label="话题表",
        fields=_TOPIC_FIELDS,
        max_items=1,
        ui_pick="single",
        note="话题表：整表替换，一个世界包只带一张；条目形状与 "
             "services/chat/topic_kinds 的导出格式一致，导出的表能原样导回",
    ),
    ResourceTypeSpec(
        type_id="minigames",
        container="json_dir",
        primary="{id}/manifest.json",
        label="小游戏",
        fields=_MANIFEST_FIELDS,
        id_required=True,
        note="小游戏：数据包目录，manifest.json 声明后端与参数",
    ),
)

SPECS: Dict[str, ResourceTypeSpec] = {spec.type_id: spec for spec in RESOURCE_TYPES}


def spec_of(type_id: str):
    """按类型 id 取声明；未知类型返回 None。"""
    return SPECS.get(str(type_id or ""))


def type_ids() -> Tuple[str, ...]:
    return tuple(spec.type_id for spec in RESOURCE_TYPES)


def packable_type_ids() -> Tuple[str, ...]:
    """可进世界包的类型 id（保持声明顺序）。"""
    return tuple(spec.type_id for spec in RESOURCE_TYPES if spec.packable)


def valid_type_id(type_id: str) -> bool:
    return bool(TYPE_ID_RE.match(str(type_id or "")))


def valid_resource_id(resource_id: str) -> bool:
    """资源 id 是否符合全局规则（类型专属规则见 :func:`valid_id_for`）。"""
    return bool(RESOURCE_ID_RE.match(str(resource_id or "")))


def valid_id_for(type_id: str, resource_id: str) -> bool:
    """某类型的资源 id 是否合规（类型自带规则优先）。"""
    spec = spec_of(type_id)
    return spec.valid_id(resource_id) if spec is not None else False


def type_labels() -> Dict[str, str]:
    """``{类型 id: 界面显示名}``（保持声明顺序）。"""
    return {spec.type_id: spec.label for spec in RESOURCE_TYPES}


def pick_mode(type_id: str) -> str:
    """界面选择粒度（``multi`` / ``single``）；未知类型按 ``multi``。"""
    spec = spec_of(type_id)
    return spec.ui_pick if spec is not None else "multi"


def trait_values_of(type_id: str) -> Tuple[str, ...]:
    """某类型的特色词表；未知类型或未启用返回空元组。"""
    spec = spec_of(type_id)
    return spec.trait_values if spec is not None else ()


def _check_members(spec) -> list:
    """``csv_bundle`` 的成员声明自洽：键唯一且合法、主文件即第一个成员。

    不要求成员必须出现在组里：缺席的成员由读取侧回落到内置组，组本身只要
    含至少一份 CSV 即成立（由校验层的 ``empty_bundle`` 兜底）。
    """
    problems = []
    if not spec.members:
        return [f"{spec.type_id}: csv_bundle 至少要声明一个成员"]
    seen = set()
    for member in spec.members:
        if not re.match(r"^[a-z][a-z0-9_]*$", member.key):
            problems.append(f"{spec.type_id}: 成员键不合规 {member.key!r}")
        if member.key in seen:
            problems.append(f"{spec.type_id}: 成员键重复 {member.key}")
        seen.add(member.key)
        if not member.suffix.startswith("."):
            problems.append(f"{spec.type_id}.{member.key}: 成员后缀应以点开头")
        keys = member.field_keys()
        if len(set(keys)) != len(keys):
            problems.append(f"{spec.type_id}.{member.key}: 字段 key 重复")
        for f in member.fields:
            if f.kind not in FIELD_KINDS:
                problems.append(
                    f"{spec.type_id}.{member.key}.{f.key}: 未知字段种类 {f.kind!r}")
    expected = spec.members[0].relpath("{id}")
    if spec.primary != expected:
        problems.append(
            f"{spec.type_id}: primary 应指向第一个成员 {expected!r}"
            f"（实际 {spec.primary!r}）")
    return problems


def check_spec_consistency() -> list:
    """声明自身的结构自洽检查（守卫与开发者工具共用），返回问题列表。"""
    problems = []
    seen = set()
    for spec in RESOURCE_TYPES:
        if not valid_type_id(spec.type_id):
            problems.append(f"类型 id 不合规: {spec.type_id!r}")
        if spec.type_id in seen:
            problems.append(f"类型 id 重复: {spec.type_id}")
        seen.add(spec.type_id)
        if spec.container not in CONTAINERS:
            problems.append(f"{spec.type_id}: 未知容器 {spec.container!r}")
        if spec.root not in ROOTS:
            problems.append(f"{spec.type_id}: 未知存储根 {spec.root!r}")
        if "{id}" not in spec.primary:
            problems.append(f"{spec.type_id}: primary 缺少 {{id}} 占位")
        if spec.container not in ("json_dir", "py_package", "csv_dir",
                                  "csv_bundle") and "/" in spec.primary:
            problems.append(f"{spec.type_id}: 单文件容器的 primary 不应含子目录")
        if spec.config:
            if "{id}" not in spec.config:
                problems.append(f"{spec.type_id}: config 缺少 {{id}} 占位")
            if spec.container not in ("json_dir", "csv_dir"):
                problems.append(f"{spec.type_id}: 只有目录型容器能声明 config")
        if spec.container == "csv_bundle":
            problems.extend(_check_members(spec))
        elif spec.members:
            problems.append(f"{spec.type_id}: 只有 csv_bundle 容器能声明成员")
        keys = spec.field_keys()
        if len(set(keys)) != len(keys):
            problems.append(f"{spec.type_id}: 字段 key 重复")
        for f in spec.fields:
            if f.kind not in FIELD_KINDS:
                problems.append(f"{spec.type_id}.{f.key}: 未知字段种类 {f.kind!r}")
        if spec.id_required and spec.container != "json_dir":
            problems.append(f"{spec.type_id}: 只有 json_dir 容器能在载荷内声明 id")
        if spec.packable and not spec.label:
            problems.append(f"{spec.type_id}: 可打包的类型必须有界面显示名")
        if spec.ui_pick not in PICK_MODES:
            problems.append(f"{spec.type_id}: 未知选择粒度 {spec.ui_pick!r}")
        if spec.trait_pick not in PICK_MODES:
            problems.append(f"{spec.type_id}: 未知特色选择粒度 {spec.trait_pick!r}")
        if not spec.trait_values and spec.trait_pick != "single":
            problems.append(f"{spec.type_id}: 未启用特色却声明了多选")
        for value in spec.trait_values:
            if value not in TRAIT_LABELS:
                problems.append(f"{spec.type_id}: 特色取值缺少显示名 {value!r}")
        if spec.max_items < 0:
            problems.append(f"{spec.type_id}: max_items 不能为负")
        if spec.max_items == 1 and spec.ui_pick != "single":
            problems.append(f"{spec.type_id}: 只能选一份的类型应在界面上单选")
        if spec.id_pattern:
            try:
                re.compile(spec.id_pattern)
            except re.error as exc:
                problems.append(f"{spec.type_id}: id_pattern 无法编译（{exc}）")
    for name in ("settings_keys", "packable", "addressable", "id_required",
                 "label", "max_items", "id_pattern", "ui_pick",
                 "trait_values", "trait_pick", "members"):
        if name not in {f.name for f in fields(ResourceTypeSpec)}:
            problems.append(f"ResourceTypeSpec 缺少字段 {name}")
    return problems
