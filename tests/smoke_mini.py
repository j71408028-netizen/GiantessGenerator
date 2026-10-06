"""挂件版冒烟测试：不依赖人工点击，直接跑通核心链路。

覆盖：启动装配 → 调查（世界包 / 风格 / 副本 / 挑战包随机）→ 创建角色 →
生成报告并自动归档 → 尺寸视图 → 导出 → 副本与挑战的启动装配 → 整窗换屏
（角色档案 / 设置 / AI 配置）→ 主题与置顶开关。

测试会写入 data/archives 与 data/user/settings.json，运行结束后自动还原：
settings 备份后恢复，测试期间新建的文件与角色档案一并移出 data/。

清理一律用「移到系统临时目录」而不是删除（见 :func:`_discard`）：data/ 是用户
数据区，自检不该在这里做删除，搬走后由系统自行回收，也不会误伤同名文件。

任一步骤失败即以非零码退出。

用法：python tests/smoke_mini.py
"""

import os
import shutil
import sys
import tempfile
import tkinter as tk
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logic import get_comparisons
from paths import data_dir, ensure_cwd
from ui.common import appearance
from services.exploration.context import ExplorationContext
from persistence import (
    CharacterRepo, ScenarioRepo, LandmarkRepo, PersonalityRepo, PresetRepo,
    QuipRepo, SettingsRepo,
)
from services.world_service import WorldManager
from ui.mini import pixel as px
from ui.mini.app import MiniApp
from ui.mini.report_view import COMPARE_MARK


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        raise AssertionError(label)


def _discard(path):
    """把测试产物移出 data/（搬到系统临时目录），不做删除。

    data/ 存的是用户数据；自检产生的角色档案、挑战包、副本回放等一律搬走，
    既保证「跑完不留痕」，也不会在用户数据区做任何不可逆删除。
    """
    if not os.path.exists(path):
        return None
    target = os.path.join(tempfile.mkdtemp(prefix="mini_smoke_trash_"),
                          os.path.basename(path))
    try:
        shutil.move(path, target)
        return target
    except OSError as exc:
        print(f"（搬移失败 {path}: {exc}）")
        return None


def widest_style(landmark_repo):
    """地标最多的风格：冒烟测试用它保证报告一定有对比段落。"""
    styles = landmark_repo.get_styles()
    return max(styles, key=lambda s: len(landmark_repo.load(s))) if styles else ""


def main():
    ensure_cwd()
    user_dir = os.path.join(data_dir(), "user")
    archives_dir = os.path.join(data_dir(), "archives")
    # 备份放系统临时区：既不会在 data/ 里留下测试目录，也免去开头的清空
    backup_dir = tempfile.mkdtemp(prefix="mini_smoke_backup_")
    created_ids = []
    created_packs = []

    os.makedirs(backup_dir, exist_ok=True)
    for name in os.listdir(user_dir):
        if name.endswith(".json"):
            shutil.copy2(os.path.join(user_dir, name), os.path.join(backup_dir, name))

    # 失败必须以非零码退出（见模块 docstring）：check() 失败即抛，靠这个变量把
    # 结果带过 finally——此前 finally 里的 os._exit(0) 会把任何失败吞成成功。
    code = 1
    try:
        settings_repo = SettingsRepo()
        settings = settings_repo.load()
        settings["always_on_top"] = False
        settings["use_preview_image_as_avatar"] = True
        settings["show_all_details"] = False
        settings.pop("window_geometry", None)

        world_manager = WorldManager(data_dir="data")
        world_manager.apply_world_settings(settings)
        settings_repo.world_state = world_manager.world_state
        ws = world_manager.world_state

        context = ExplorationContext(
            settings=settings,
            landmark_repo=LandmarkRepo(world_state=ws),
            quip_repo=QuipRepo(world_state=ws),
            preset_repo=PresetRepo(world_state=ws),
            personality_repo=PersonalityRepo(world_state=ws),
            character_repo=CharacterRepo(),
            settings_repo=settings_repo,
            scenario_repo=ScenarioRepo(world_state=ws),
            world_state=ws,
        )

        appearance.set_mode(settings.get("theme_mode", "Dark"))

        root = tk.Tk()
        app = MiniApp(root, context, world_manager, settings_repo)
        app.auto_answer = True          # 确认类交互直接答「是」，不弹屏
        root.update_idletasks()
        root.update()
        check("窗口装配完成", app.mode == "params")

        # ---------- 像素基元 ----------
        check("点阵字体已解析", bool(px.pixel_family()), px.pixel_family())
        from PIL import Image
        sprite = px.pixelate(Image.new("RGBA", (450, 800), (200, 100, 100, 255)),
                             (52, 92), colors=8)
        check("像素化尺寸正确", sprite.size == (52, 92), str(sprite.size))
        check("方块进度条", px.block_bar(50, 100, 10) == "█████░░░░░",
              px.block_bar(50, 100, 10))

        # ---------- 调查 ----------
        app.investigate()
        root.update_idletasks()
        inv = app.investigation
        check("调查掷出地标风格", bool(inv.landmark_style), inv.landmark_style)
        check("调查掷出描述风格", bool(inv.quip_style), inv.quip_style)
        check("调查掷出副本方案", inv.has_scenario, inv.scenario_id)
        check("风格写入上下文",
              context.selected_styles == [inv.landmark_style]
              and context.selected_quip_styles == [inv.quip_style])
        check("地标数据非空", len(context.merged_landmarks) > 0,
              f"{len(context.merged_landmarks)} 个地标")
        low, high = app.params_panel.height_range()
        check("掷出的风格在当前规模区间可用",
              bool(get_comparisons(context.merged_landmarks,
                                   {"身高": (low + high) / 2}, limit=1)),
              f"{low:.0f}~{high:.0f} m")
        check("重复调查不报错", _repeat_investigate(app))

        # ---------- 创建角色 ----------
        app.params_panel.name_var.set("冒烟少女")
        app.params_panel.scale_row.set_value((2, 3))
        app.create_character()
        root.update_idletasks()
        state = app.current_state
        created_ids.append(state.giantess_id)
        check("角色已创建", state is not None and state.name == "冒烟少女")
        check("身高落在规模区间内", 100 <= state.height <= 1000,
              f"{state.height:.1f} m")
        check("部位尺寸已生成", len(state.body_parts) >= 14, f"{len(state.body_parts)} 项")
        check("性格已落地", state.personality is not None,
              getattr(state.personality, "name", ""))
        check("进入角色模式", app.mode == "state")
        check("行动点数已初始化", state.action_points > 0, str(state.action_points))
        check("预览头像已生成", bool(state.avatar_path), state.avatar_path)

        # ---------- 生成报告（自动归档） ----------
        context.update_styles([widest_style(context.landmark_repo)],
                              context.selected_quip_styles)
        app.generate_report()
        root.update_idletasks()
        report = app.report_view.last_report
        check("报告已生成", report is not None)
        check("报告含尺寸对比", len(report.comparisons) >= 1,
              f"{len(report.comparisons)} 条对比")
        check("报告正文足够长", len(report.report_text.strip()) > 200,
              f"{len(report.report_text)} 字")
        archived = app.report_view.archive_report(state.giantess_id, state.name)
        check("报告自动归档", bool(archived) and os.path.exists(archived),
              os.path.relpath(archived) if archived else "")
        check("报告按步显示", _check_stepping(app), _stepping_detail(app))
        check("尺寸视图可渲染", _render_details(app))
        check("视图可切回报告", _toggle_back(app))

        # ---------- 角色管理 ----------
        app.unload_character(confirm=False)
        check("卸载后回到参数模式",
              app.mode == "params" and app.current_state is None)

        # 无角色时生成报告：尺寸一览也只露报告里提过的部位（与有角色同一规则）。
        app.generate_report()
        root.update_idletasks()
        check("无角色也能生成报告", app.report_view.last_report is not None)
        check("无角色尺寸一览只露已提及部位", _details_hidden_parts(app))

        entries = app.collect_characters()
        check("角色列表可读", any(e["id"] == state.giantess_id for e in entries),
              f"{len(entries)} 位")
        app.load_character(state.giantess_id)
        check("可按 id 重新载入角色",
              app.current_state is not None and app.current_state.name == "冒烟少女")

        from services.character_service.archive_export import export_character_mhtml
        export_dir = os.path.join(archives_dir, state.giantess_id, "导出")
        os.makedirs(export_dir, exist_ok=True)
        card_path = os.path.join(export_dir, "角色卡.chara.json")
        app._export_card(app.current_state, card_path)
        check("角色卡已导出", os.path.getsize(card_path) > 200,
              f"{os.path.getsize(card_path)} 字节")
        html_path = os.path.join(export_dir, "角色档案.html")
        export_character_mhtml(app.current_state, html_path)
        check("HTML 档案已导出", os.path.getsize(html_path) > 2000,
              f"{os.path.getsize(html_path)} 字节")

        # ---------- 副本 / 挑战的启动装配 ----------
        # 真正弹出副本窗口会进入 Dear PyGui 事件循环并阻塞，因此这里替换成记录用的
        # 桩对象，只验证参数装配：配置来源、模式、行动点消耗与风格临时接管。
        _install_dungeon_stub()
        real_load_config = app._scenario_repo.load_config
        real_config = real_load_config(inv.scenario_id)
        app._scenario_repo.load_config = lambda did: dict(
            real_load_config(did) or {}, entry_action_cost=7)

        app.current_state.action_points = 100
        app.enter_dungeon()
        call = _LAST_CALL["kwargs"]
        check("探索副本已装配", call is not None and call.get("mode") == "explore")
        check("副本窗口参数与签名一致", _kwargs_match_signature(call),
              _signature_diff(call))
        check("配置沿用真实方案（仅注入消耗）",
              {k: v for k, v in call["scenario_config"].items()
               if k != "entry_action_cost"}
              == {k: v for k, v in (real_config or {}).items()
                  if k != "entry_action_cost"},
              f"进入消耗 {call['scenario_config'].get('entry_action_cost')}")
        check("携带当前角色", call.get("character") is app.current_state)
        check("行动点按进入消耗扣除",
              app.current_state.action_points == 93,
              f"100 - 7 = {app.current_state.action_points}")
        check("扣点已写回档案",
              context.character_repo.load(
                  app.current_state.giantess_id).action_points == 93)
        app._scenario_repo.load_config = real_load_config

        challenge_name = "冒烟挑战"
        app.challenge_service.create_challenge(
            app.current_state.giantess_id, [inv.landmark_style], [inv.quip_style],
            inv.scenario_id, "冒烟测试用挑战包", challenge_name)
        created_packs.append(os.path.join(
            data_dir(), "packs", "challenges", f"{challenge_name}.chal"))
        metas = app.challenge_service.get_all_metas()
        check("挑战包已被识别", any(m.get("pack_base") == challenge_name for m in metas))

        app.investigation.challenge_base = challenge_name
        app.investigation.challenge_title = challenge_name
        styles_before = list(context.selected_styles)

        # 「挑战」键只开确认屏，不直接进副本；确认屏上点「进入挑战」才装配副本。
        _LAST_CALL["kwargs"] = None
        app.enter_challenge()
        check("挑战键打开的是确认屏",
              type(app._screen).__name__ == "ChallengeScreen")
        check("确认屏读到挑战包信息",
              (app.challenge_meta() or {}).get("character_name") == state.name,
              str((app.challenge_meta() or {}).get("character_name")))
        check("确认屏未装配副本", _LAST_CALL["kwargs"] is None)
        app._screen.enter_btn.invoke()
        check("确认后确认屏已退出", app._screen is None)
        call = _LAST_CALL["kwargs"]
        check("挑战已装配", call is not None and call.get("mode") == "challenge")
        check("挑战包配置已注入", bool(call["scenario_config"]))
        check("挑战不消耗行动点", call.get("character") is None)
        check("挑战窗口参数与签名一致", _kwargs_match_signature(call),
              _signature_diff(call))
        check("挑战包性格已还原为对象",
              call["personality"].name == app.current_state.personality.name,
              call["personality"].name)
        check("挑战包风格已还原", context.selected_styles == styles_before,
              str(context.selected_styles))

        # ---------- 整窗换屏 ----------
        _check_screens(app, settings)
        check("换屏之后主界面仍可用", _still_reports(app))

        # ---------- 副本链路实测（真实窗口） ----------
        check("挂件弹框走原生 messagebox 且父窗口是挂件根", _mini_dialogs_use_native(root))
        while not app.investigation.has_scenario:
            app.investigate()
        if app.current_state is not None:
            # 补足行动点，让实测一定走到「进入副本」而不是卡在行动点不足
            app.current_state.action_points = 99
            app._character_repo.save(app.current_state)
        # 实测会给方案注入进入消耗（见 _INJECTED_ENTRY_COST），扣点链路才验得到
        cost = _INJECTED_ENTRY_COST
        ap_before = getattr(app.current_state, "action_points", None)
        cleanup = [archives_dir,
                   os.path.join(data_dir(), "user", "replays"),
                   os.path.join(data_dir(), "user", "reports")]
        outcome = _real_dungeon_run(app, root, cleanup)
        check("副本窗口真实跑通（未抛异常并取回结果）", outcome["ran"])
        check("副本未被入口校验拦下", not outcome["failed"], outcome["error"])
        check("副本运行期间挂件已隐藏", outcome["hidden"] == "withdrawn",
              str(outcome["hidden"]))
        check("活动窗口已登记到挂件主控", outcome["registered"] is True)
        check("宿主弹框已换成挂件实现",
              outcome["dialogs"] == "MiniDialogs", str(outcome["dialogs"]))
        check("副本结束后挂件恢复显示", outcome["restored"] == "normal",
              str(outcome["restored"]))
        check("副本结束后活动窗口已注销", outcome["unregistered"] is True)
        check("收尾提示经宿主弹框端口发出",
              any(kind == "info" for kind, _t, _m in outcome["dialog_calls"]),
              str(outcome["dialog_calls"])[:80])
        if ap_before is not None:
            ap_after = app.current_state.action_points
            check("进入消耗按配置扣除行动点", ap_before - ap_after == cost,
                  f"{ap_before} → {ap_after}，配置消耗 {cost}")

        print("\n结果：全部通过")
        code = 0
    except Exception:
        traceback.print_exc()
        print("\n结果：存在失败项（见上方 FAIL / traceback）")
    finally:
        try:
            for name in os.listdir(backup_dir):
                shutil.copy2(os.path.join(backup_dir, name),
                             os.path.join(user_dir, name))
            for name in os.listdir(user_dir):
                if name.endswith(".json") and not os.path.exists(
                        os.path.join(backup_dir, name)):
                    _discard(os.path.join(user_dir, name))
            for gid in created_ids:
                _discard(os.path.join(archives_dir, gid))
            for pack in created_packs:
                if os.path.exists(pack):
                    _discard(pack)
                    print(f"（已移出测试挑战包 {os.path.basename(pack)}）")
            print("（测试数据已还原，测试产物已移出 data/）")
        except Exception:
            # 还原失败也必须如实上报，不能让清理异常把退出码改回 0 或丢掉 traceback。
            traceback.print_exc()
            code = 1
        # 收尾：数据已还原，这里直接退出，不再碰任何 Tk 调用。
        #
        # 实测（2026-09-28 复核，DPG 2.3.1 + Py3.13）跑过真实副本会话后：
        # 触发点是会话收尾里的 dpg.destroy_context()（把它换成空操作后同一路径正常），
        # 受影响的是**那个已存在的 Tk 根窗口**——destroy() / withdraw() / Win32
        # ShowWindow(hwnd) 一律 0xC0000005（无 traceback）；而 quit()、winfo_*()、
        # state()、geometry()、protocol() 仍然可用，新建一个 Tk 根也完全正常，
        # 所以「解释器活着、控件树完好」的观察是对的，但结论不能推广成「碰 Tk 就崩」。
        # 产品侧已由「会话收尾补隐藏保活视口」修掉（见 dungeon/window/dpg_state.py），
        # 自检这里仍走 os._exit（挂件主程序自己的退出路径就是它，沿用同一条），
        # 只是退出码如实反映成败；退出前手动 flush，免得 os._exit 丢掉还在缓冲里的输出。
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)


# ==================== 辅助 ====================
def _repeat_investigate(app):
    app.investigate()
    return app.investigation.has_scenario and bool(app.investigation.landmark_style)


def _render_details(app):
    app.report_view.show_details(app.current_state)
    text = app.report_view.textbox.read()
    return "身高" in text and "米" in text


def _toggle_back(app):
    """播完之后切回「报告」：看到的是收尾提示，而不是报告全文。"""
    app.report_view.toggle_view()
    view = app.report_view
    text = view.textbox.read()
    return (view._view == "report" and COMPARE_MARK not in text
            and "播放完毕" in text)


def _details_hidden_parts(app):
    """无角色的尺寸一览：未在报告里提过的部位应当留空（显示 —）。"""
    app.report_view.show_details(app.current_state)
    text = app.report_view.textbox.read()
    return "身高" in text and "—" in text


def _stepping_detail(app):
    view = app.report_view
    return (f"{len(view._steps)} 步，当前第 {view._step + 1} 步，"
            f"分步中 {view._stepping}")


def _check_stepping(app):
    """报告的分步流程：首步为身高对比 → 边界询问 → 播完清空面板、不显示全文。"""
    view = app.report_view
    if not (view._steps and view._view == "report" and view._stepping):
        return False

    # 第一步固定是身高对比：报告开头的标题（含「身高：」）与意愿不进播放。
    first = view.textbox.read()
    if not first.strip() or "身高：" in first or COMPARE_MARK not in first:
        return False
    if "身高" not in first:
        return False

    # 逐次推进：第 3 步之后会问「是否继续」，auto_answer 已设为「是」自动续。
    while view._stepping:
        view.advance()
    if view._step != len(view._steps) - 1:
        return False
    # 单向前进：此时再看一次，正文应当是最后一步而不是第一步。
    last = view.textbox.read()
    if last.strip() == first.strip():
        return False
    # 走完最后一步后，再推一次即播完：面板清空，且绝不出现报告全文。
    view.advance()
    if not view._finished or view._view != "report":
        return False
    tail = view.textbox.read()
    return COMPARE_MARK not in tail and "播放完毕" in tail


def _check_screens(app, settings):
    """角色档案 / 设置 / AI 三种换屏都能打开并退回。"""
    app.push_screen("characters")
    check("角色档案屏已打开", isinstance(app._screen, object)
          and type(app._screen).__name__ == "CharactersScreen")
    app.pop_screen()
    check("角色档案屏已退回", app._screen is None)

    app.push_screen("settings")
    check("设置屏已打开", type(app._screen).__name__ == "SettingsScreen")
    dark_before = app.is_dark()
    app.toggle_theme()
    check("主题可切换", app.is_dark() != dark_before,
          f"dark {dark_before} -> {app.is_dark()}")
    app.set_topmost(True)
    check("置顶开关生效", bool(app.root.attributes("-topmost")))
    app.set_topmost(False)
    app.pop_screen()

    before = len(app.ai_configs())
    app.new_ai_profile()
    check("AI 屏已打开", type(app._screen).__name__ == "AIScreen")
    check("AI 档案已新建", len(app.ai_configs()) == before + 1,
          f"{before} -> {len(app.ai_configs())}")
    profile = app.settings.get("ai_provider")
    app.save_ai_profile(profile, {"name": "冒烟配置", "url": "http://x",
                                  "model": "m", "api_key": "k"})
    check("AI 档案已保存", app.ai_provider_name(profile) == "冒烟配置")
    app.delete_ai_profile(profile)
    check("AI 档案已删除", len(app.ai_configs()) == before)
    app.pop_screen()
    check("AI 屏已退回", app._screen is None)

    # 确认屏在真实（非测试）模式下也要能构造出来，并且按钮要真能退出
    app.auto_answer = None
    app.notify("冒烟提示")
    check("消息屏已打开", type(app._screen).__name__ == "MessageScreen")
    app._screen.buttons[0].invoke()          # 点「确定」
    check("消息屏点确定后退出", app._screen is None)

    answered = {"yes": 0}
    app.ask("冒烟确认", lambda: answered.update(yes=answered["yes"] + 1))
    check("确认屏已打开", type(app._screen).__name__ == "MessageScreen")
    app._screen.buttons[0].invoke()          # 点「是」
    check("确认屏点是后退出并执行回调",
          app._screen is None and answered["yes"] == 1, str(answered["yes"]))
    app.auto_answer = False
    answered = {"value": None}
    app.ask("自动答否", lambda: answered.update(value="yes"),
            lambda: answered.update(value="no"))
    check("测试态确认直接应答", answered["value"] == "no", str(answered["value"]))
    app.auto_answer = True


def _still_reports(app):
    app.generate_report()
    return app.report_view.last_report is not None


_LAST_CALL = {"kwargs": None}
_SIGNATURE_PARAMS = {"names": None}
_REAL_WINDOW = {"cls": None, "stub": None}


def _install_dungeon_stub():
    """把副本窗口替换为记录参数的桩，避免测试进入图形事件循环。

    真实类存进 ``_REAL_WINDOW``：后面的「真实拉起一次」会用它临时换回，
    验证桩覆盖不到的交接（显隐、活动窗口登记、收尾与结果对象）。
    """
    import inspect

    import dungeon.window as dungeon_window

    real_params = set(inspect.signature(
        dungeon_window.DungeonSessionWindow.__init__).parameters) - {"self"}
    _SIGNATURE_PARAMS["names"] = real_params
    _REAL_WINDOW["cls"] = dungeon_window.DungeonSessionWindow

    class _StubResult:
        """``DungeonSessionWindow.run()`` 的返回壳：挂件侧只看 failed / launch_error。"""
        failed = False
        launch_error = ""

    class _StubWindow:
        def __init__(self, parent, **kwargs):
            _LAST_CALL["kwargs"] = kwargs

        def run(self):
            return _StubResult()

    dungeon_window.DungeonSessionWindow = _StubWindow
    _REAL_WINDOW["stub"] = _StubWindow


def _kwargs_match_signature(kwargs) -> bool:
    return set(kwargs) <= _SIGNATURE_PARAMS["names"]


def _signature_diff(kwargs) -> str:
    unknown = set(kwargs) - _SIGNATURE_PARAMS["names"]
    return f"未知参数 {sorted(unknown)}" if unknown else ""


# ==================== 副本链路实测（真实窗口，不用桩） ====================
#: 帧循环上限：够走完建 UI、开场调度与一次收尾，又不至于让自检等太久
_TEST_FRAMES = 40
#: 不可达的 AI 地址：连接立即失败，避免真实调用与网络等待
_NO_NETWORK_AI = {"provider": "openai", "api_key": "smoke-test",
                  "url": "http://127.0.0.1:9/v1", "model": "smoke-test"}
#: 注入的进入消耗：默认方案多为 0，扣点链路就验不到，这里强制一个非零值
_INJECTED_ENTRY_COST = 7


def _snapshot_files(root_dir):
    """目录树下全部文件的相对路径集合（用于比对测试新增了哪些产物）。"""
    if not os.path.isdir(root_dir):
        return set()
    found = set()
    for base, _dirs, files in os.walk(root_dir):
        for name in files:
            found.add(os.path.relpath(os.path.join(base, name), root_dir))
    return found


def _real_dungeon_run(app, root, cleanup_dirs):
    """真实拉起一次副本窗口，验证挂件与副本窗口的交接。

    桩只能验参数，验不到交接。这里走完整链路：建 DPG 视口 → 藏起挂件 →
    跑帧 → 收尾 → 恢复挂件 → 取回 ``SessionResult``。三处刻意改造：

    - AI 指向不可达地址：连不上立即失败，既不产生真实调用也不挂在网络等待；
    - 帧循环跑到上限就主动收尾，等价于用户点了副本视口的关闭键；
    - 弹框换成记录型：收尾会弹「未完成回放已保存」等提示，真弹出来要等人点，
      自检里改成记录调用，顺便验证提示确实经宿主弹框端口发出。
    """
    import dungeon.window as dungeon_window
    import ui.mini.app as mini_app
    import ui.mini.dialogs as mini_dialogs

    real_cls = _REAL_WINDOW["cls"]
    sample = {}
    before = {}
    for path in cleanup_dirs:
        before[path] = _snapshot_files(path)

    class MiniDialogs:
        """与 ``ui.mini.dialogs.MiniDialogs`` 同名同接口，但不真的弹。"""
        calls = []

        def __init__(self, root=None):
            self.root = root

        def showinfo(self, title, message):
            self.calls.append(("info", title, message))

        def showwarning(self, title, message):
            self.calls.append(("warning", title, message))

        def showerror(self, title, message):
            self.calls.append(("error", title, message))

        def askyesno(self, title, message):
            self.calls.append(("ask", title, message))
            return True

    # 挂件在 _launch_dungeon 里才 import 这两个名字，替换源模块即可生效
    # 给掷出的方案注入进入消耗：挂件不走入口阶段，扣点是它自己做的，
    # 默认方案消耗多为 0，不注入就验不到这条链路。
    saved_load = app._scenario_repo.load_config

    def _load_with_cost(scenario_id):
        config = dict(saved_load(scenario_id) or {})
        config["entry_action_cost"] = _INJECTED_ENTRY_COST
        return config

    saved_dialogs = mini_dialogs.MiniDialogs
    saved_resolve = mini_app.resolve_ai_config
    mini_dialogs.MiniDialogs = MiniDialogs
    mini_app.resolve_ai_config = lambda _settings: dict(_NO_NETWORK_AI)
    app._scenario_repo.load_config = _load_with_cost

    def _limited_frame_loop(self):
        import dearpygui.dearpygui as dpg
        for i in range(_TEST_FRAMES):
            self._pump_host_events()
            self._frame.tick()
            if not dpg.is_dearpygui_running():
                self._on_close()
                return
            dpg.render_dearpygui_frame()
            if i == 5:
                # 会话进行中采样：宿主应已隐藏、活动窗口应已登记到挂件上
                sample["root_state"] = root.state()
                sample["registered"] = app._active_dungeon_window is not None
                sample["dialogs"] = type(
                    getattr(getattr(self, "host", None), "dialogs", None)).__name__
        # 帧数用尽：按用户点关闭键处理（置标记后走正常收尾）
        self._closing = True
        self._on_close()

    saved_loop = real_cls._run_frame_loop
    saved_run = real_cls.run
    real_cls._run_frame_loop = _limited_frame_loop

    def _capture_run(self):
        result = saved_run(self)
        sample["result"] = result
        return result

    real_cls.run = _capture_run
    try:
        dungeon_window.DungeonSessionWindow = real_cls
        app.enter_dungeon()
    finally:
        dungeon_window.DungeonSessionWindow = _REAL_WINDOW["stub"]
        real_cls._run_frame_loop = saved_loop
        real_cls.run = saved_run
        mini_app.resolve_ai_config = saved_resolve
        mini_dialogs.MiniDialogs = saved_dialogs
        app._scenario_repo.load_config = saved_load
        root.update_idletasks()

    result = sample.get("result")
    outcome = {
        "ran": result is not None,
        "registered": sample.get("registered"),
        "dialogs": sample.get("dialogs"),
        "hidden": sample.get("root_state"),
        "restored": root.state(),
        "unregistered": app._active_dungeon_window is None,
        "failed": getattr(result, "failed", None),
        "error": getattr(result, "launch_error", ""),
        "reason": getattr(result, "reason", ""),
        "dialog_calls": list(MiniDialogs.calls),
    }
    # 副本落盘的产物（未完成回放 / 报告）不属于挂件自检，测完即移出 data/
    for path, snapshot in before.items():
        for rel in _snapshot_files(path) - snapshot:
            _discard(os.path.join(path, rel))
    return outcome


def _mini_dialogs_use_native(root):
    """挂件弹框实现走原生 messagebox，且父窗口是挂件根窗口。

    真的弹出来要等人点，自检里把 ``messagebox`` 换成记录函数：验证调用被
    正确转发、返回值是 bool、父窗口不是 CTk 的根。
    """
    import ui.mini.dialogs as mini_dialogs

    calls = []
    saved = mini_dialogs.messagebox.askyesno
    mini_dialogs.messagebox.askyesno = lambda t, m, parent=None: (
        calls.append((t, m, parent)), True)[1]
    try:
        answered = mini_dialogs.MiniDialogs(root).askyesno("标题", "内容")
    finally:
        mini_dialogs.messagebox.askyesno = saved
    return answered is True and bool(calls) and calls[0][2] is root


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
