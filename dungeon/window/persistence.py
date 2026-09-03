"""结局结算与副本/回放/报告的持久化。"""

import datetime
import json
import os

import ui.common.dialogs
from paths import data_dir
from logic import compute_casualty
from models import CharacterSnapshot
from services.state_service import StateService


class DungeonPersistence:
    # ------------------ 结局结算与保存 ------------------
    def _auto_replay_enabled(self) -> bool:
        return bool((self.settings or {}).get("auto_save_replay", False))

    # ------------------ 重要结局索引记录 ------------------
    def _record_ending_achievement(self):
        """把本次重要结局（配置了图标）的索引写入档案。

        - 探索模式 + 已加载角色：写进角色档案（achieved_endings），每个结局只记录首次达成；
        - 挑战模式：写入 data/user/endings.json 的用户结局索引；
        - 回放模式 / 无图标结局：不记录。
        索引字段（与结局图标一一对应，便于后续以图标展示/悬停/点击查看）：
        dungeon_id、trigger_index（结局触发器在 triggers 列表中的下标）、name、
        icon_path（相对副本目录）、ending_text、replay_path、achieved_at。
        """
        if self.is_replay:
            return
        if not getattr(self, "ending_icon_path", ""):
            return  # 未配置图标 -> 该结局不重要，不记录
        idx = getattr(self, "_ending_trigger_index", -1)
        if idx < 0:
            return
        now_str = datetime.datetime.now().isoformat()
        record = {
            "dungeon_id": self.dungeon_id or "",
            "trigger_index": idx,
            "name": getattr(self, "_ending_name", "") or "",
            "icon_path": self.ending_icon_path,
            "ending_text": self.ending_text or "",
            "replay_path": "",
            "achieved_at": now_str,
        }
        mode = getattr(self, "mode", "explore")
        if mode == "challenge":
            record["mode"] = "challenge"
            appended = append_user_ending_record(record)
            if appended is not None:
                self._achievement_record = appended
            return

        char = self.character
        if char is None:
            return  # 探索模式但未加载角色，不写入
        if any(ex.get("dungeon_id") == record["dungeon_id"]
               and ex.get("trigger_index") == idx
               for ex in (char.achieved_endings or [])):
            return  # 同一结局只记录首次达成
        char.achieved_endings = list(char.achieved_endings or []) + [record]
        self._achievement_record = record
        if self.character_repo is not None:
            try:
                self.character_repo.save(char)
            except Exception as e:
                print(f"[Ending] 结局索引写入角色档案失败: {e}")

    def _backfill_replay_path(self, replay_path: str):
        """保存回放后，把回放路径回填到本次达成的结局索引记录。"""
        record = self._achievement_record
        if not record:
            return
        record["replay_path"] = replay_path
        mode = getattr(self, "mode", "explore")
        if mode == "challenge":
            update_user_ending_record(record)
        elif self.character is not None and self.character_repo is not None:
            try:
                self.character_repo.save(self.character)
            except Exception as e:
                print(f"[Ending] 结局索引回填角色档案失败: {e}")

    # ------------------ 结局结算 ------------------

    def _apply_ending_effects(self):
        """触发结局后最后一次结算增量：介入度、破坏性、伤亡、自定义属性、行动点数返还。"""
        effects = getattr(self, "ending_effects", None) or {}

        def _num(v, default=0.0):
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        intr_d = _num(effects.get("intrusion_delta"))
        dest_d = _num(effects.get("destruction_delta"))
        refund = int(_num(effects.get("action_points_refund")))
        custom_deltas = effects.get("custom_deltas", {}) or {}
        # 伤亡由"步进"按探索模式的伤亡公式自动计算
        casualty_step = _num(effects.get("casualty_step"))
        height = max(1.0, self.height or 1.0)

        self.dungeon_state.intrusion = max(0.0, min(5.0, self.dungeon_state.intrusion + intr_d))
        self.dungeon_state.destruction = max(0.0, min(5.0, self.dungeon_state.destruction + dest_d))
        cas_d = compute_casualty(height, casualty_step, self.dungeon_state.destruction,
                                 self.ending_text or "")
        self.dungeon_state.total_casualties += cas_d
        for name, delta in custom_deltas.items():
            try:
                self.dungeon_state.custom_attrs[name] = (
                    self.dungeon_state.custom_attrs.get(name, 0.0) + float(delta)
                )
            except (TypeError, ValueError):
                pass

        char = self.character
        if char is not None:
            StateService.refund_action_points(char, refund)
            # 结局结算记为一行完整演化：步进取结局配置的伤亡步进；
            # 角色坐标增量经统一方法平移并夹取到 0.5~4.5 边界
            intr_after, dest_after = StateService.shift_coordinates(
                char.intrusion, char.destruction, intr_d, dest_d)
            char.record_change(
                step=casualty_step,
                intrusion=intr_after,
                destruction=dest_after,
                casualties=char.total_casualties + cas_d,
                source="_apply_ending_effects",
            )
            if self.character_repo is not None:
                try:
                    self.character_repo.save(char)
                except Exception as e:
                    print(f"[Ending] 角色数据保存失败: {e}")

        print(f"[Ending] 结局增量已结算：介入度{intr_d:+.2f}，破坏性{dest_d:+.2f}，"
              f"伤亡{cas_d:+.2f}，行动点数返还{refund:+d}")

    def _build_dungeon_report_text(self) -> str:
        lines = [f"{self.name}    副本报告"]
        lines.append("═" * (12 + max(0, len(str(self.name)) * 2)))
        lines.append("")
        lines.append(f"介入度：{self.dungeon_state.intrusion:.2f}")
        lines.append(f"破坏性：{self.dungeon_state.destruction:.2f}")
        if self.dungeon_state.custom_attrs:
            for k, v in self.dungeon_state.custom_attrs.items():
                lines.append(f"{k}：{v:.2f}")
        lines.append(f"总伤亡：{int(self.dungeon_state.total_casualties):,}")
        lines.append(f"总步数：{self.dungeon_state.total_steps}")
        lines.append("")
        lines.append("─── 正文 ───")
        for item in self.story_history:
            lines.append((item.get("type_str") or "") + (item.get("text") or ""))
        return "\n".join(lines)

    def _create_character_from_session(self) -> CharacterSnapshot:
        char = CharacterSnapshot(
            giantess_id=f"{self.name}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}",
            name=self.name,
            nick=self.nick or "",
            original_height=self.original_height or 1.6,
            height=self.height or self.original_height or 1.6,
            body_parts=self.body_parts or {},
            personality=self.personality,
            greed=self.greed,
            action_points=50,
            selected_tags=list(self.tags or []),
            intro_hidden=self.intro_hidden or "",
            intro_visible=self.intro_visible or "",
        )
        # 初始演化行：记录副本结束时的介入度/破坏性/累计伤亡
        # （副本会话内坐标为 0~5，写入角色时统一夹取到角色的 0.5~4.5 边界）
        session_intr, session_dest = StateService.clamp_coordinates(
            getattr(self.dungeon_state, "intrusion", 0.0),
            getattr(self.dungeon_state, "destruction", 0.0))
        char.record_change(
            step=0.0,
            intrusion=session_intr,
            destruction=session_dest,
            casualties=getattr(self.dungeon_state, "total_casualties", 0.0),
            source="_create_character_from_session",
        )
        return char

    def _write_replay_file(self, char) -> str:
        replay_dir = os.path.join(data_dir(), "archives", char.giantess_id, "回放")
        os.makedirs(replay_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"{char.name}_回放_{timestamp}.replay.json"
        path = os.path.join(replay_dir, filename)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(list(self.replay_data), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Replay] 回放保存失败: {e}")
            return ""
        return path

    def _write_report_file(self, char) -> str:
        report_dir = os.path.join(data_dir(), "archives", char.giantess_id, "报告")
        os.makedirs(report_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"{char.name}_副本报告_{timestamp}.txt"
        path = os.path.join(report_dir, filename)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._build_dungeon_report_text())
        except Exception as e:
            print(f"[Replay] 报告保存失败: {e}")
            return ""
        return path

    def _write_user_replay_file(self) -> str:
        """挑战模式：把回放写入 data/user/replays，不创建/更新任何角色。"""
        replay_dir = os.path.join(data_dir(), "user", "replays")
        os.makedirs(replay_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        dungeon_key = (self.dungeon_id or "副本").replace("/", "_").replace("\\", "_")
        filename = f"{dungeon_key}_回放_{timestamp}.replay.json"
        path = os.path.join(replay_dir, filename)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(list(self.replay_data), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Replay] 用户回放保存失败: {e}")
            return ""
        return path

    def _save_replay_record(self, auto: bool = False):
        """保存副本回放及其附带报告。需要角色，否则自动/询问创建。"""
        if self._replay_saved:
            return
        if getattr(self, "mode", "explore") == "challenge":
            # 挑战模式：不更新角色状态，只把回放写入 data/user
            replay_path = self._write_user_replay_file()
            self._replay_saved = True
            if replay_path:
                self._backfill_replay_path(replay_path)
            if not auto and replay_path:
                ui.common.dialogs.showinfo(
                    "保存成功", f"挑战包回放已保存。\n回放：{replay_path}")
            return
        char = self.character
        if char is None:
            if not auto:
                if not ui.common.dialogs.askyesno(
                        "创建角色", "保存副本回放需要角色。\n是否现在创建角色？"):
                    ui.common.dialogs.showwarning("未保存", "未创建角色，本次副本回放未保存。")
                    return
            char = self._create_character_from_session()
            self.character = char
        if self.character_repo is not None:
            try:
                self.character_repo.save(char)
            except Exception as e:
                print(f"[Replay] 角色保存失败: {e}")
        replay_path = self._write_replay_file(char)
        report_path = self._write_report_file(char)
        self._replay_saved = True
        if replay_path:
            self._backfill_replay_path(replay_path)
        if not auto and replay_path:
            ui.common.dialogs.showinfo(
                "保存成功",
                f"副本回放与报告已保存。\n回放：{replay_path}\n报告：{report_path}")

    def _handle_exit(self):
        """用户关闭副本窗口后的退出处理（在主线程、DPG 停止后调用）。"""
        if self.is_replay:
            return
        thread = getattr(self, "_ending_thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=15)
        if not self.dungeon_ended:
            ui.common.dialogs.showwarning(
                "副本退出", "副本进程数据将丢失。\n未触发结局就退出，本次副本数据不会保存。")
            return
        if self._replay_saved:
            return
        if getattr(self, "mode", "explore") == "challenge":
            # 挑战模式：不涉及角色，仅询问是否保存回放到 data/user
            if ui.common.dialogs.askyesno(
                    "保存回放", "结局已达成。\n是否保存本次挑战的回放？"):
                self._save_replay_record(auto=False)
            return
        if ui.common.dialogs.askyesno(
                "保存回放", "结局已达成。\n是否保存本次副本回放？\n"
                            "（保存回放需要角色，或现在创建角色）"):
            self._save_replay_record(auto=False)


# ------------------ data/user 结局索引文件 ------------------
# 挑战模式达成的重要结局统一记录于此，索引结构便于后续按图标展示、
# 悬停查看结局文本或点击进入对应回放。

def _user_endings_path() -> str:
    return os.path.join(data_dir(), "user", "endings.json")


def _load_user_endings() -> list:
    path = _user_endings_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        records = data.get("records", [])
        return records if isinstance(records, list) else []
    except Exception:
        return []


def _save_user_endings(records: list):
    path = _user_endings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "records": records}, f, ensure_ascii=False, indent=2)


def append_user_ending_record(record: dict) -> dict:
    """把挑战模式达成的重要结局索引追加到 data/user/endings.json（去重）。"""
    records = _load_user_endings()
    for existing in records:
        if (existing.get("dungeon_id") == record.get("dungeon_id")
                and existing.get("trigger_index") == record.get("trigger_index")):
            return existing
    records.append(record)
    try:
        _save_user_endings(records)
    except Exception as e:
        print(f"[Ending] 用户结局索引写入失败: {e}")
        return None
    return record


def update_user_ending_record(updated: dict):
    """保存回放后回填其 replay_path。"""
    records = _load_user_endings()
    for existing in records:
        if (existing.get("dungeon_id") == updated.get("dungeon_id")
                and existing.get("trigger_index") == updated.get("trigger_index")):
            existing["replay_path"] = updated.get("replay_path", "")
            existing["ending_text"] = updated.get("ending_text", existing.get("ending_text", ""))
            break
    try:
        _save_user_endings(records)
    except Exception as e:
        print(f"[Ending] 用户结局索引回填失败: {e}")
