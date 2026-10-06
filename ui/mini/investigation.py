"""调查：随机抽取一整套配置。

挂件版删去了文本管理器、副本方案编辑器与设置页里的选择项，原本需要手动挑选的
四类资源——世界包、地标风格、描述风格、副本方案与挑战包——统一改由「调查」
按钮随机掷出：每按一次就换一整套环境与剧本，形成挂件式的重复游玩循环。

「副本」在主项目里已改名「副本方案（scenario）」，本模块的字段因此用
``scenario_id``；界面文案仍说「副本」，那是玩家侧的说法。
"""

import random
from dataclasses import dataclass
from typing import Optional, Tuple

from core.logic import get_comparisons

NO_WORLD_LABEL = "（无世界包）"
EMPTY_LABEL = "（无）"


@dataclass
class Investigation:
    """一次调查的结果快照。"""

    world_id: Optional[str] = None
    world_name: str = NO_WORLD_LABEL
    landmark_style: str = ""
    quip_style: str = ""
    scenario_id: str = ""
    challenge_base: str = ""
    challenge_title: str = ""

    @property
    def has_world(self) -> bool:
        return bool(self.world_id)

    @property
    def has_challenge(self) -> bool:
        return bool(self.challenge_base)

    @property
    def has_scenario(self) -> bool:
        return bool(self.scenario_id)

    # ---------- 展示 ----------
    def world_text(self) -> str:
        return self.world_name or NO_WORLD_LABEL

    def landmark_text(self) -> str:
        return self.landmark_style or EMPTY_LABEL

    def quip_text(self) -> str:
        return self.quip_style or EMPTY_LABEL

    def scenario_text(self) -> str:
        return self.scenario_id or EMPTY_LABEL

    def challenge_text(self) -> str:
        return self.challenge_title or EMPTY_LABEL


class Investigator:
    """「调查」按钮背后的抽取与应用逻辑。"""

    def __init__(self, app):
        self.app = app

    # ==================== 对外入口 ====================
    def investigate(self) -> Investigation:
        """掷出一整套配置并立即生效，返回结果。"""
        inv = Investigation()

        # 世界包必须先落地：风格、副本与挑战包的可选集合都随世界包变化。
        inv.world_id, inv.world_name = self._roll_world()
        self._apply_world(inv.world_id)

        inv.landmark_style = self._roll_landmark_style()
        inv.quip_style = self._roll_one(self.app._quip_repo.get_styles())
        self._apply_styles(inv)

        inv.scenario_id = self._roll_one(self.app._scenario_repo.list_all())
        inv.challenge_base, inv.challenge_title = self._roll_challenge()
        return inv

    # ==================== 抽取 ====================
    def _roll_world(self) -> Tuple[Optional[str], str]:
        packs = [p for p in self.app.world_manager.list_packs() if not p.get("error")]
        # 「不带世界包」同样是一种合法结果，保留原版的裸世界玩法。
        options = [None] + [p["world_id"] for p in packs]
        names = {p["world_id"]: p.get("name") or p["world_id"] for p in packs}
        world_id = random.choice(options)
        return world_id, names.get(world_id, NO_WORLD_LABEL) if world_id else NO_WORLD_LABEL

    def _roll_landmark_style(self) -> str:
        """抽取地标风格，并尽量避开当前身高下无对比可用的风格。

        地标风格的尺度差异很大：例如只含百公里级地标的风格，对上百米级少女
        会一条对比都匹配不到，报告几乎是空的。这里先按当前身高区间的中位数
        做一次试算，只从能匹配上的风格里抽；全都匹配不上时退回全量随机。
        """
        styles = self.app._landmark_repo.get_styles()
        if not styles:
            return ""
        low, high = self.app.params_panel.height_range()
        probe = {"身高": (low + high) / 2}
        usable = [
            style for style in styles
            if get_comparisons(self.app._landmark_repo.load(style), probe, limit=1)
        ]
        return random.choice(usable or styles)

    def _roll_challenge(self) -> Tuple[str, str]:
        metas = self.app.challenge_service.get_all_metas()
        if not metas:
            return "", ""
        meta = random.choice(metas)
        base = meta.get("pack_base") or ""
        title = base
        filename = meta.get("filename") or ""
        if filename:
            title = filename[:-5] if filename.lower().endswith(".chal") else filename
        if meta.get("bundled"):
            title = f"{title}（世界包）"
        return base, title

    @staticmethod
    def _roll_one(options):
        return random.choice(options) if options else ""

    # ==================== 应用 ====================
    def _apply_world(self, world_id: Optional[str]) -> None:
        app = self.app
        manager = app.world_manager
        state = getattr(manager, "world_state", None)
        current = state.world_id if state is not None and state.active else None
        if current == world_id:
            return

        # deactivate() 会从磁盘重载设置，先把内存里的改动落盘，避免丢失。
        try:
            app._settings_repo.save(app.settings)
        except Exception as e:
            print(f"[Warning] 世界包切换前保存设置失败: {e}")

        try:
            if state is not None and state.active:
                manager.deactivate(app.settings, app._settings_repo)
            if world_id:
                manager.activate(world_id, app.settings, app._settings_repo)
        except ValueError as e:
            print(f"[Warning] 世界包切换失败: {e}")
            return

        app.context.reload_merged_data()

    def _apply_styles(self, inv: Investigation) -> None:
        """把抽中的风格写回结果与上下文；风格库为空时保持原选择。"""
        app = self.app
        inv.landmark_style = self._valid_style(
            inv.landmark_style, app._landmark_repo.get_styles())
        inv.quip_style = self._valid_style(
            inv.quip_style, app._quip_repo.get_styles())

        app.context.update_styles(
            [inv.landmark_style] if inv.landmark_style else [],
            [inv.quip_style] if inv.quip_style else [],
        )
        app.settings["selected_styles"] = list(app.context.selected_styles)
        app.settings["selected_quip_styles"] = list(app.context.selected_quip_styles)

    @staticmethod
    def _valid_style(style: str, available) -> str:
        if style in available:
            return style
        return available[0] if available else ""
