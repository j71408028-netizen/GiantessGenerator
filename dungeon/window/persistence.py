"""结局结算与副本/回放/报告的持久化。"""

import datetime
import os

from dungeon import process_log
from dungeon.window.host import (DIALOG_ASK, DIALOG_INFO, DIALOG_WARNING,
                                 HostPort)
from paths import data_dir
from logic import compute_casualty
from models import CharacterSnapshot
from persistence.json_store import load_json_with_backup, write_json_atomic, write_text_atomic
from dungeon.terms import scenario_id_of
from services.state_service import StateService


class DungeonPersistence:
    # ------------------ 收尾提示（经宿主端口，window 层不 import ui） ------------------
    # 兜底宿主：给不带 host 的替身/旧调用用（无宿主时提示打到标准输出）。
    _fallback_host = None

    def _host_port(self):
        host = getattr(self, "host", None)
        if host is not None:
            return host
        if DungeonPersistence._fallback_host is None:
            DungeonPersistence._fallback_host = HostPort()
        return DungeonPersistence._fallback_host

    def _dialog_info(self, title, message):
        """提示类弹框。无宿主时由端口打到标准输出。"""
        return self._host_port().dialog(DIALOG_INFO, title, message)

    def _dialog_warning(self, title, message):
        return self._host_port().dialog(DIALOG_WARNING, title, message)

    def _dialog_ask(self, title, message) -> bool:
        """询问类弹框；无宿主（自检）时按端口缺省回答。"""
        return bool(self._host_port().dialog(DIALOG_ASK, title, message))

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
        scenario_id、trigger_index（结局触发器在 triggers 列表中的下标）、name、
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
            "scenario_id": self.scenario_id or "",
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
        if any(scenario_id_of(ex) == record["scenario_id"]
               and ex.get("trigger_index") == idx
               for ex in (char.achieved_endings or [])):
            return  # 同一结局只记录首次达成
        char.achieved_endings = list(char.achieved_endings or []) + [record]
        self._achievement_record = record
        if self.character_repo is not None:
            try:
                self.character_repo.save(char)
            except Exception as e:
                process_log.log(f"[Ending] 结局索引写入角色档案失败: {e}")

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
                process_log.log(f"[Ending] 结局索引回填角色档案失败: {e}")

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

        # 坐标统一夹取 0.5~4.5（与角色坐标边界一致）
        self.dungeon_state.intrusion = max(0.5, min(4.5, self.dungeon_state.intrusion + intr_d))
        self.dungeon_state.destruction = max(0.5, min(4.5, self.dungeon_state.destruction + dest_d))
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
            # 副本会话内演化的步长同步到角色存储（未演化为 None 时保留角色现值）
            ds = self.dungeon_state
            if getattr(ds, "step_intrusion", None) is not None:
                char.step_intrusion = ds.step_intrusion
            if getattr(ds, "step_destruction", None) is not None:
                char.step_destruction = ds.step_destruction
            if self.character_repo is not None:
                try:
                    self.character_repo.save(char)
                except Exception as e:
                    process_log.log(f"[Ending] 角色数据保存失败: {e}")

        process_log.log(f"[Ending] 结局增量已结算：介入度{intr_d:+.2f}，破坏性{dest_d:+.2f}，"
              f"伤亡{cas_d:+.2f}，行动点数返还{refund:+d}")

    def _build_scenario_report_text(self, incomplete: bool = False,
                                   reason: str = "") -> str:
        lines = [f"{self.name}    副本报告"]
        lines.append("═" * (12 + max(0, len(str(self.name)) * 2)))
        if incomplete:
            # 未触发结局就退出：报告仍然落盘，但明确标注本局未走完
            detail = f"（{reason}）" if reason else ""
            lines.append("")
            lines.append(f"【未完成】本次副本未触发结局即结束{detail}。")
            lines.append("以下为退出时已生成的内容；结局结算与结局索引均未发生。")
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
        # 副本会话内演化的步长同步到角色存储
        char.step_intrusion = getattr(self.dungeon_state, "step_intrusion", None)
        char.step_destruction = getattr(self.dungeon_state, "step_destruction", None)
        return char

    def _write_replay_file(self, char, incomplete: bool = False) -> str:
        """写入角色档案目录下的回放；``incomplete`` 时文件名带「未完成」标记。"""
        replay_dir = os.path.join(data_dir(), "archives", char.giantess_id, "回放")
        os.makedirs(replay_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        marker = "_未完成" if incomplete else ""
        filename = f"{char.name}_回放_{timestamp}{marker}.replay.json"
        path = os.path.join(replay_dir, filename)
        try:
            # 原子写：半截回放文件不会被读到（后缀仍为 .replay.json，回放加载照旧）
            write_json_atomic(path, list(self.replay_data), backup=False)
        except Exception as e:
            process_log.log(f"[Replay] 回放保存失败: {e}")
            return ""
        return path

    def _write_report_file(self, char, incomplete: bool = False,
                           reason: str = "") -> str:
        """写入角色档案目录下的报告；``incomplete`` 时标注本局未走完。"""
        report_dir = os.path.join(data_dir(), "archives", char.giantess_id, "报告")
        os.makedirs(report_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        marker = "_未完成" if incomplete else ""
        filename = f"{char.name}_副本报告_{timestamp}{marker}.txt"
        path = os.path.join(report_dir, filename)
        try:
            write_text_atomic(
                path, self._build_scenario_report_text(incomplete=incomplete, reason=reason),
                backup=False)
        except Exception as e:
            process_log.log(f"[Replay] 报告保存失败: {e}")
            return ""
        return path

    def _write_user_replay_file(self, incomplete: bool = False) -> str:
        """挑战模式/无角色场景：把回放写入 data/user/replays，不创建/更新任何角色。"""
        replay_dir = os.path.join(data_dir(), "user", "replays")
        os.makedirs(replay_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        scenario_key = (self.scenario_id or "副本").replace("/", "_").replace("\\", "_")
        marker = "_未完成" if incomplete else ""
        filename = f"{scenario_key}_回放_{timestamp}{marker}.replay.json"
        path = os.path.join(replay_dir, filename)
        try:
            write_json_atomic(path, list(self.replay_data), backup=False)
        except Exception as e:
            process_log.log(f"[Replay] 用户回放保存失败: {e}")
            return ""
        return path

    def _write_user_report_file(self, incomplete: bool = False,
                                reason: str = "") -> str:
        """挑战模式/无角色场景：报告写入 data/user/reports（与回放同层级）。"""
        report_dir = os.path.join(data_dir(), "user", "reports")
        os.makedirs(report_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        scenario_key = (self.scenario_id or "副本").replace("/", "_").replace("\\", "_")
        marker = "_未完成" if incomplete else ""
        filename = f"{scenario_key}_副本报告_{timestamp}{marker}.txt"
        path = os.path.join(report_dir, filename)
        try:
            write_text_atomic(
                path, self._build_scenario_report_text(incomplete=incomplete, reason=reason),
                backup=False)
        except Exception as e:
            process_log.log(f"[Replay] 用户报告保存失败: {e}")
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
            self.replay_path = replay_path
            if not auto and replay_path:
                self._dialog_info(
                    "保存成功", f"挑战包回放已保存。\n回放：{replay_path}")
            return
        char = self.character
        if char is None:
            if not auto:
                if not self._dialog_ask(
                        "创建角色", "保存副本回放需要角色。\n是否现在创建角色？"):
                    self._dialog_warning("未保存", "未创建角色，本次副本回放未保存。")
                    return
            char = self._create_character_from_session()
            self.character = char
        if self.character_repo is not None:
            try:
                self.character_repo.save(char)
            except Exception as e:
                process_log.log(f"[Replay] 角色保存失败: {e}")
        replay_path = self._write_replay_file(char)
        report_path = self._write_report_file(char)
        self._replay_saved = True
        if replay_path:
            self._backfill_replay_path(replay_path)
        self.replay_path = replay_path
        self.report_path = report_path
        if not auto and replay_path:
            self._dialog_info(
                "保存成功",
                f"副本回放与报告已保存。\n回放：{replay_path}\n报告：{report_path}")

    # ------------------ 统一收尾 ------------------
    def _finalize(self, completed: bool, reason: str = ""):
        """副本收尾的单一入口：正常结局、用户中断、生成异常三条路径共用。

        ``completed=True``（走到结局）：结算结局增量 → 记录重要结局索引 →
        按设置自动保存回放（与改动前的 `_generate_ending` 收尾等价）。

        ``completed=False``（未触发结局就结束）：**不**结算结局增量、**不**写
        ``data/user/endings.json``、**不**记挑战达成——没真正走完的一局不能算达成；
        但已经生成的内容照旧落盘（回放 + 报告，文件名带「未完成」标记），
        避免整局丢失。
        """
        if getattr(self, "_finalized", False):
            return
        self._finalized = True
        self._ending_generating = False
        if completed:
            self.dungeon_ended = True
            # 最后结算一次增量并写入角色
            self._apply_ending_effects()
            # 记录本次达成的重要结局索引（探索模式写角色档案，挑战模式写 data/user）
            self._record_ending_achievement()
            # 自动保存回放开关
            if self._auto_replay_enabled():
                self._save_replay_record(auto=True)
            return

        detail = reason or "未触发结局"
        errors = list(getattr(self, "_session_errors", None) or [])
        if errors:
            detail += f"；会话中出现 {len(errors)} 次生成异常（最近：{errors[-1]}）"
        if not self.replay_data and not self.story_history:
            self._dialog_info("副本退出", "本次副本还没有生成任何内容，未保存。")
            return
        replay_path, report_path = self._save_incomplete_record(detail)
        if replay_path:
            self._dialog_info(
                "副本未完成",
                f"未触发结局就退出，已把生成的内容保存为「未完成」回放：\n{replay_path}\n"
                f"报告：{report_path}")
        else:
            self._dialog_warning(
                "副本退出", "未触发结局就退出，且未完成回放保存失败，本次数据未保存。")

    def _save_incomplete_record(self, reason: str):
        """未完成退出：把已生成的内容落盘（不创建角色、不做任何结算）。

        有角色（探索模式）写角色档案目录；无角色或挑战模式写 ``data/user``。
        返回 ``(回放路径, 报告路径)``，写入失败为空串。
        """
        char = self.character
        if char is not None and getattr(self, "mode", "explore") != "challenge":
            replay_path = self._write_replay_file(char, incomplete=True)
            report_path = self._write_report_file(char, incomplete=True, reason=reason)
        else:
            replay_path = self._write_user_replay_file(incomplete=True)
            report_path = self._write_user_report_file(incomplete=True, reason=reason)
        # 记下来交给 SessionResult（L4）：调用方不必再自己去翻目录
        self.replay_path = replay_path
        self.report_path = report_path
        process_log.log(f"[Dungeon] 未完成收尾落盘：回放={replay_path or '失败'}，"
              f"报告={report_path or '失败'}（{reason}）")
        if replay_path:
            self._replay_saved = True
        return replay_path, report_path

    def _handle_exit(self):
        """用户关闭副本窗口后的退出处理（在主线程、DPG 停止后调用）。

        未触发结局：走 ``_finalize(completed=False)``——不结算、不记结局索引，
        但把已生成的内容保存为「未完成」回放与报告（此前直接丢弃）；
        结局已触发（含文本仍在生成、join 超时）：按「走到结局」收尾，避免
        结算与结局索引被误判为未完成而丢失；
        已触发结局且收尾完毕：``_finalize(completed=True)`` 已在结局生成结束时
        执行，这里只询问是否保存正式回放。
        """
        if self.is_replay:
            return
        thread = getattr(self, "_ending_thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=15)
        if not self.dungeon_ended:
            if self.pending_ending is not None:
                # 结局已触发、文本生成未结束（线程仍在跑）：结局文本兜底为结局名，
                # 让落盘的回放/报告与后续线程 finally 的写法一致
                name = (self.pending_ending or {}).get("name", "")
                if not self.ending_text:
                    self.ending_text = f"结局：{name}"
                    if self._last_ending_record is not None:
                        self._last_ending_record["ending_text"] = self.ending_text
                self._finalize(completed=True, reason="结局已触发，生成未结束")
            else:
                self._finalize(completed=False, reason="未触发结局就退出")
            return
        if self._replay_saved:
            return
        if getattr(self, "mode", "explore") == "challenge":
            # 挑战模式：不涉及角色，仅询问是否保存回放到 data/user
            if self._dialog_ask(
                    "保存回放", "结局已达成。\n是否保存本次挑战的回放？"):
                self._save_replay_record(auto=False)
            return
        if self._dialog_ask(
                "保存回放", "结局已达成。\n是否保存本次副本回放？\n"
                            "（保存回放需要角色，或现在创建角色）"):
            self._save_replay_record(auto=False)


# ------------------ data/user 结局索引文件 ------------------
# 挑战模式达成的重要结局统一记录于此，索引结构便于后续按图标展示、
# 悬停查看结局文本或点击进入对应回放。

def _user_endings_path() -> str:
    return os.path.join(data_dir(), "user", "endings.json")


def _load_user_endings() -> list:
    data = load_json_with_backup(_user_endings_path())
    if not isinstance(data, dict):
        return []
    records = data.get("records", [])
    return records if isinstance(records, list) else []


def _save_user_endings(records: list):
    # 原子写 + .bak：结局索引是玩家的长期收集成果，不能因一次崩溃写坏
    write_json_atomic(_user_endings_path(), {"version": 1, "records": records})


def append_user_ending_record(record: dict) -> dict:
    """把挑战模式达成的重要结局索引追加到 data/user/endings.json（去重）。"""
    records = _load_user_endings()
    for existing in records:
        if (scenario_id_of(existing) == scenario_id_of(record)
                and existing.get("trigger_index") == record.get("trigger_index")):
            return existing
    records.append(record)
    try:
        _save_user_endings(records)
    except Exception as e:
        process_log.log(f"[Ending] 用户结局索引写入失败: {e}")
        return None
    return record


def update_user_ending_record(updated: dict):
    """保存回放后回填其 replay_path。"""
    records = _load_user_endings()
    for existing in records:
        if (scenario_id_of(existing) == scenario_id_of(updated)
                and existing.get("trigger_index") == updated.get("trigger_index")):
            existing["replay_path"] = updated.get("replay_path", "")
            existing["ending_text"] = updated.get("ending_text", existing.get("ending_text", ""))
            break
    try:
        _save_user_endings(records)
    except Exception as e:
        process_log.log(f"[Ending] 用户结局索引回填失败: {e}")
