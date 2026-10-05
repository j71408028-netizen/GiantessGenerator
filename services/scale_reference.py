"""介入度 / 破坏性 / 体型尺度的分级解释（提示词参考数据）。

内容恢复自历史上的 developer_tools/quip_filler.py（描述填充开发者工具）：
该工具曾把这三组解释硬编码进提示词，供 AI 把握写作尺度。现拆分为独立的
参考数据模块，供聊天协议、描述填充等需要向 AI 解释坐标含义的功能共用。

体型分类与身高区间以 logic.py 的 SIZE_CATEGORIES / get_size_category 为准，
本模块不重复定义。
"""

from logic import SIZE_CATEGORIES, get_size_category


# 体型与互动尺度（依据 Events.json 各体型条目与体型区间归纳）
# 每个体型给出准确描述 + 适合的互动案例 + 不适合（尺寸过大或过小）的互动案例
SIZE_DEFINITIONS = {
    "small": {
        "desc": "她的高度近似于中低层居民楼，但仍低于部分高楼。步幅足以跨过道路，坐下可覆盖公园草坪等区域；可以随意触动路标、广告牌等设施",
        "suitable": "躯干与单栋建筑、小型社区、口袋公园、公路、路口互动；手指拨弄天线与招牌。",
        "unsuitable": "不适合用手托起整栋摩天大楼、把体育场当坐垫（尺寸不够大）。",
    },
    "medium": {
        "desc": "她高于市内几乎所有建筑；身体范围可以涵盖社区，并在街区产生尺度压迫感。",
        "suitable": "躯干与社区、大楼、体育场、港口、中型公园互动。",
        "unsuitable": "不适合拨弄屋顶天线、触碰招牌等小设施（尺寸过大）；也不适合把整座大城市当舞台、伸手够到山峦（尺寸仍不够大）。",
    },
    "large": {
        "desc": "全市最高的摩天大楼也不及她的腰部；手掌能覆盖学校、商场等大面积建筑，整个港口或街区可以被一些身体姿势包裹。",
        "suitable": "适合：躯干与城区、港口、摩天大楼互动；手掌托起学校/商场等整栋建筑、身体部位之间夹住街区。",
        "unsuitable": "不适合在单条街道上精耕细作、手指拨弄小招牌（尺寸过大）；也不适合把数座城市同时踩在脚下、伸手触及山脉（尺寸不够大）。",
    },
    "huge": {
        "desc": "她以公里计的高度俯视一切，大部分摩天大楼也仅到小腿高度，一步可以跨过小镇、河流等。",
        "suitable": "适合：在中大城市活动、躯干与少数地标互动；脚掌踩瘪街区、手掌碾碎摩天大楼。",
        "unsuitable": "不适合在单栋建筑或街区内精细互动（尺寸过大）；也不适合把整个国家、山脉群或海洋当床（尺寸仍不够大）。",
    },
    "colossal": {
        "desc": "她的身躯以数十上百公里计，一座城市不过是她身边的玩物，甚至可以颠覆局部大气层。",
        "suitable": "适合：躯干与城市及城市群、山脉、盆地、海域互动；手指毫不费力地抹平数个街道、脚掌覆盖县城、冲击波及方圆百里。",
        "unsuitable": "不适合在单个中小型城市、街区或单栋建筑尺度上互动（尺寸过大）；也不适合涉及国家、大陆，以及更大尺度（尺寸过小）。",
    },
}

# 介入度分级定义：巨型少女对人类环境的客观介入程度及报道口径，共 4 级
INTRUSION_LEVELS = {
    1: "被动存在：她仅仅作为庞大存在，被在意的是影响而非她本身（如被新闻报告、被科学解析、被远远注视）。描述着重于轮廓、精神气质、社会影响，不涉及具体的少女形象",
    2: "无意的日常动作：她的行走、跳跃、转身、撩发、坐下、躺下等普通日常动作被观察。描述着重于少女的神态、动作，不涉及身体细节。",
    3: "主动的城市互动：人们被她身体与城市设施的互动所冲击（用手脚包裹地标、挑逗人类等）。描述着重于身体细节、精神改变，但保持基本克制。",
    4: "占据与玩弄：她已经暴露贪婪或情色的一面，以身体占据、封锁或玩弄城市，建筑与人群彻底沦为玩物。描述完全表现这种少女私密身体的支配。",
}

# 破坏性分级定义：少女对物理破坏的主观想法和客观后果程度，共 4 级
DESTRUCTION_LEVELS = {
    1: "避免破坏：她小心地对待身边一切，但客观上可能有无人员伤亡的环境改变，或感官上的压迫。",
    2: "可能破坏：她采取无所谓态度，活动近似略谨慎的一般少女，可能导致结构受损或人员伤亡。",
    3: "尝试破坏：她带有明确目的的动作导致建筑结构性受损或变形、街区或城市被大面积波及。",
    4: "享受毁灭：她以身体接触和摧毁为唯一目的，短时间便使建筑结构化为废墟或齑粉，甚至街区或城市被完全抹除。",
}


def intrusion_level_text(value: float) -> str:
    """把介入度坐标（0.5~4.5）解释为 1~4 级的分级说明文本。"""
    return INTRUSION_LEVELS[max(1, min(4, int(round(value))))]


def destruction_level_text(value: float) -> str:
    """把破坏性坐标（0.5~4.5）解释为 1~4 级的分级说明文本。"""
    return DESTRUCTION_LEVELS[max(1, min(4, int(round(value))))]


def size_definition_text(height: float) -> str:
    """把身高解释为体型分类及对应的互动尺度说明；无分类（过小/超出）返回空串。"""
    category = get_size_category(height)
    if category not in SIZE_DEFINITIONS:
        return ""
    definition = SIZE_DEFINITIONS[category]
    return (f"体型类别：{category}（{definition['desc']}）\n"
            f"适合的互动：{definition['suitable']}\n"
            f"不适合的互动：{definition['unsuitable']}")


def intrusion_block() -> str:
    """全部介入度分级说明（提示词用多行文本块）。"""
    return "\n".join(f"级别{i}：{text}" for i, text in INTRUSION_LEVELS.items())


def destruction_block() -> str:
    """全部破坏性分级说明（提示词用多行文本块）。"""
    return "\n".join(f"级别{i}：{text}" for i, text in DESTRUCTION_LEVELS.items())


def size_block() -> str:
    """全部体型与互动尺度说明（提示词用多行文本块）。"""
    lines = []
    for category in SIZE_CATEGORIES:
        definition = SIZE_DEFINITIONS.get(category)
        if not definition:
            continue
        lines.append(f"{category}：{definition['desc']}\n"
                     f"适合：{definition['suitable']}\n"
                     f"不适合：{definition['unsuitable']}")
    return "\n".join(lines)
