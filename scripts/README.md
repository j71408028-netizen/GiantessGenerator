# scripts/ — 可复用工具 + 已落地归档

这个目录放**不进 `tests/` 常驻自检**的开发者工具：既包括还在用的可复用工具，也包括
一次性用掉、保留备查的归档（数据迁移脚本、原生崩溃探针）。常驻自检（守卫 / 行为 /
GUI 冒烟）在 `tests/`，由 `python tests/run_checks.py` 统一驱动；另有一批查看资源 / 行为
改动影响（人工检查）的工具在 `developer_tools/`。三者分工见根目录 README
「开发与架构（速查）」。

> **本目录的源码仍会被离线守卫静态扫描。** `check_entrypoints` / `check_import_graph` /
> `check_scenario_naming` 的跳过表里只有 `developer_tools`，没有 `scripts`。放这里的脚本要
> 守「第一方 import 存在」「未知顶层名」「scenario 旧标识符」这些规则——已有两处为此开了
> 口子：`tests/check_entrypoints.py` 的 `UNDECLARED_ALLOWED`（`escape_sim.py` 的 `game`）
> 与 `tests/check_scenario_naming.py` 的 `ALLOWED_FILES`（`migrate_scenario_naming.py`）。
> **删 / 挪这两个脚本时，记得同步改那两个守卫。**

## 持续维护

| 脚本 | 性质 | 用途 |
|---|---|---|
| `dungeon_autopilot.py` | 真窗口自动驾驶 | 副本窗口全生命周期回归（15 场景，需显示器）：构造 → `run()` → 步进 → 关闭 → 落盘。最强回归，`--in-process` 连跑全部场景，`--scene <名> --isolate` 单跑。**进 CI**（`.github/workflows/gui-smoke.yml`，跳 `text-components` 实跑 14 个） |
| `escape_sim.py` | 无头模拟 | escape_giantess 小游戏模拟（无显示器可跑）：地图生成 / 出生点连通性 / bot 实跑结算。是 `dungeon_autopilot` 对应 DPG 场景的前置逻辑验证 |

## 已落地归档（留档备查，不再演进）

| 脚本 / 目录 | 性质 | 说明 |
|---|---|---|
| `migrate_scenario_naming.py` | 一次性迁移 | 旧标识符（`dungeon_id` / `DungeonRepo` 等）改写为 `scenario_*`（幂等、原子写 + `.bak`）。**被 `tests/check_scenario_naming.py` 按路径白名单引用** |
| `migrate_snapshot_evolution.py` | 一次性迁移 | 存档演化字段的历史迁移（幂等、原子写 + `.bak`） |
| `dpg_probe/` | 一次性调查留档 | DPG 2.3.1 原生崩溃（首帧 `0xC0000005`）追查的现场工具，**只存档不复用**。原在 `developer_tools/`，2026-10-08 迁来。见文末 |

约定：归档项**完成后不删**（迁移脚本幂等 + 有 `.bak`，可对旧存档重放；探针是下次原生
崩溃的现场工具），但**不再演进**。需要长期维护的回归请写成 `tests/smoke_*.py` 或
`tests/check_*.py`。

## 产物

各工具的落盘产物统一写 `scripts/_out/`（整目录不入库，见根 `.gitignore`）：

| 子目录 | 来源 |
|---|---|
| `_out/dpg_probe/` | `dpg_probe/` 各探针的 `_*.txt` / `*.dmp` / `_dpg_bindings.json`（共享常量 `OUT_DIR` / `out()` 在 `dpg_probe/_outdir.py`） |

`dungeon_autopilot.py` / `escape_sim.py` / 两个迁移脚本都不落盘（迁移脚本直写 `data/`）。

## 附录：dpg_probe/ 说明

DPG 2.3.1 的 Windows wheel 是 ANSI(MBCS) 构建，非 ASCII 标题建窗会**静默失败**，随后
首帧渲染必现访问违例。这批探针是当时追这一例留下的现场工具，**只存档不复用**——留着
是因为下次遇到原生崩溃还得靠它们。**脚本留在此目录，产物统一写 `_out/dpg_probe/`**
（共享常量在 `_outdir.py`），因此探针目录里只有代码。三层结构：

- **抓现场（四条路，按需挑一条）**：`_probe_native.py`（纯 ctypes + dbghelp，进程内装
  vectored exception handler，打印「模块!符号+偏移」与栈扫描链，也是 `_probe_crash_run` /
  `_probe_trace` 的后端）、`_probe_veh.py`（VEH 宿主）、`_veh_filter.py`
  （`SetUnhandledExceptionFilter` + minidump，不影响 first-chance 分发）、
  `_probe_dbg.py`（进程外迷你调试器，不注入子进程）。
- **跑 + 出结论**：`_probe_run.py`（带超时跑命令，报告写 `_out/dpg_probe/_probe_last.txt`）→
  `_probe_report.py`（读同一份给紧凑结论）；`_probe_trace.py` 是带面包屑的启动器。
- **自检与单点复现**：`_probe_av.py`（故意触发 AV，验收处理器还活着）、
  `_probe_symtest.py`（验收 dbghelp 能命名地址）、`_probe_veh_selftest.py`、
  `_probe_dpg_min.py`（最小 DPG 渲染）、`_probe_fabric.py`（织物绘制复现）、
  `_probe_bindings.py` / `_probe_pyd.py`（导出表 / RVA，给纯地址命名）。

注意：这些探针依赖 Windows 专有 API（`ctypes.WinDLL`、dbghelp、minidump），**在
macOS / Linux 上跑不了**。但离线守卫只对源码做 AST 静态解析、**不执行**它们，所以放进
`scripts/` 后门禁照样过（2026-10-08 实测 `tests/run_checks.py` 15 项全过）。
