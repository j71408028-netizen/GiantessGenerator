# 发布与打包策略

面向维护者：**一个 tag = 一次发布**。本文是发布流程的单一口径，后续版本照做即可。
打包脚本在 [`build/release/package.py`](../../build/release/package.py)，三份平台专用脚本
（`build/windows` / `build/macos` / `build/linux`）是它的下位替代，开发期单平台快出包时用。

> 本文替代此前「直接手敲 pyinstaller」的做法——那套只产出一个裸目录，不带版本号、
> 不带源码归档、不带校验和，事后无法确认「这个包到底是哪个提交打的」。

## 1. 分支与标签模型

| 角色 | 名字 | 语义 |
|---|---|---|
| 开发线 | `main` | 日常开发，随时可动 |
| 发布线 | `release/v<X.Y.Z>` | 冻结后的维护分支，只进修复，不再并入功能 |
| 发布点 | `v<X.Y.Z>`（**附注标签**） | 不可变。一旦推送就不再移动 |

规矩：

- **标签只加不改**。发现包有问题时不要 force-move 旧标签，发一个新版本
  （`v1.0.1`）或加后缀（`v1.0.0-post1`）。已推送的标签被人拉走过，移动它会让人与你的
  仓库不一致。
- **附注标签里写清基线**。用 `-m` 说明「这版包含什么、从哪个提交分出」，例如：

  ```bash
  git tag -a v1.0.0 -m "v1.0.0 正式发布

  - 描述风格 Events.json 换为新版（140 条）
  - paths.py 版本号定为 1.0.0

  发布线自 befca6f（20260901功能新增）分出。"
  ```

  这段注记会被打包器自动抄进 `RELEASE-NOTES-<version>.md`，是事后追溯的第一手材料。

- **发布线与基线**：当某条线要长期维护（如 v1.0.x）而 `main` 正在做下一世代时，从
  基线提交拉一条 `release/v1.0`，在这条线上只做发布与修 bug。v1.0.0 就是从
  `befca6f` 拉出 `release/v1.0.0` 后定稿的。

## 2. 版本号必须与标签一致

版本号只有一个来源：`paths.py` 的 `APP_VERSION`（导航栏与挂件页脚都显示它）。

- 打 `v1.0.0` 标签**之前**，先在发布线上把 `APP_VERSION` 改成 `1.0.0` 并提交，
  标签打到那个提交上。程序里显示的版本与包名里的版本永远对得上。
- `APP_VERSION` 允许带后缀（`2.0.0 alpha`），但**正式发布点用纯 `<X.Y.Z>`**。

## 3. 发布清单（每次照做）

```bash
# 0) 在发布线上、工作副本干净
git switch release/v1.0      # 或 main（视发布线而定）
git status --short           # 应为空

# 1) 门禁全绿（离线守卫 + 默认方案校验）
python tests/run_checks.py
python tests/check_scenarios.py

# 2) 定版本号 → 提交
#    改 paths.py: APP_VERSION = "1.0.0"
git commit -am "release: v1.0.0 定稿"

# 3) 打附注标签
git tag -a v1.0.0 -m "v1.0.0 正式发布 ..."

# 4) 出本平台产物（在 Windows 上跑就得 Windows 包）
python build/release/package.py --tag v1.0.0

# 5) 在另外两个系统上对**同一个 tag** 各跑一次第 4 步，汇总 dist/release/

# 6) 推送标签与发布线
git push origin release/v1.0
git push origin v1.0.0
```

## 4. 打包器：`python build/release/package.py`

关键设计：**它只依赖 tag**。脚本在系统临时目录里为 tag 建一份干净 `git worktree`，
按**该 tag 自己的** `requirements.txt` 装依赖再构建——所以工作副本是否脏、当前在哪个
分支、那个版本的目录结构长什么样（v1.0.0 还是重构前的扁平结构）都不影响结果。
临时目录用完即删，不会在你的工作副本里留 `build/`、`dist/`。

| 参数 | 说明 |
|---|---|
| `--tag v1.0.0` | 要发布的 tag；不写则取 HEAD 上正好指着的 tag |
| `--out DIR` | 产物目录，默认 `dist/release/` |
| `--work DIR` | 暂存目录，默认系统临时目录；配合 `--keep` 排错 |
| `--python PATH` | 创建构建虚拟环境的解释器，默认当前解释器 |
| `--backend {pyinstaller,nuitka}` | 打包后端，默认 `pyinstaller`（onedir）；`nuitka` 走 `--standalone` |
| `--wheelhouse DIR` | 离线 wheel 目录：pip 改用 `--no-index --find-links`，Nuitka 取 Zig 也走它（无网/受限网络用，见 §5） |
| `--skip-build` | 只出源码归档（例如只想补一份源码包时） |
| `--skip-source` | 只出运行时包 |
| `--keep` | 保留暂存目录，构建失败时看现场 |
| `--console` | 排错用：构建带控制台的包，能看到启动期 traceback（正式发布不要用） |

### 构建解释器必须带 Tk

打包用的解释器（`--python`，默认当前解释器）**必须自带 `tkinter`**。有些便携 /
standalone 发行版不含 Tk：`python -m venv` 照样成功、依赖也照装，直到用户双击打出来的
包，才在启动瞬间炸 `ModuleNotFoundError: No module named 'tkinter'`——本应用的界面正是
Tk，整个包等于废的。打包器会在**装依赖之前**做这项体检并直接退出，提示换解释器：

```bash
python build/release/package.py --tag v1.0.0 --python /path/to/python-with-tk
```

### 出包后必做的冒烟

别只看"zip 生成了"。把包解开，用**一次性的用户数据目录**启动一次，确认能起来并正确
引导数据目录（不会碰真实用户数据）：

```bash
# Windows（PowerShell）：LOCALAPPDATA 指到临时目录再启动 exe
$env:LOCALAPPDATA = "$env:TEMP\gg-smoke"; .\GiantessGenerator\GiantessGenerator.exe
# Linux：XDG_DATA_HOME 指到临时目录
XDG_DATA_HOME=/tmp/gg-smoke ./GiantessGenerator/GiantessGenerator
```

起来的标志是临时目录下出现 `<name>/data/{packs,static,user}`。若进程一闪即逝，用
`--console` 重打一版看 traceback。注意 PyInstaller 6.x 的 onedir 把资源放在
`_internal/` 下（`sys._MEIPASS` 指向那里），所以 `assets`、`data` 不在 exe 同级目录——
这是正常的，别按旧布局误判成"资源没打进去"。

产物（`<name>` 默认 `GiantessGenerator`）：

| 文件 | 内容 |
|---|---|
| `<name>-<version>-<os>-<arch>.zip` | 运行时包：PyInstaller `onedir` 整目录的压缩 |
| `<name>-<version>-src.zip` | 源码归档：`git archive` 出的 tag 源码树，**剔除作者本机数据** |
| `RELEASE-NOTES-<version>.md` | tag 注记 + 构建平台/Python/时间 + 产物清单 |
| `SHA256SUMS.txt` | 上述产物的 SHA256 |

- `<os>` ∈ `windows` / `macos` / `linux`；`<arch>` ∈ `x64` / `arm64` / `x86`。
- 源码归档剔除 `data/user/`、`data/archives/`（作者本机设置与存档，打包版首启会自己
  生成）、`developer_tools/_out/`、`scripts/_out/` 与 `__pycache__` / `*.pyc` 等缓存。
- 运行时包里带的只读资源是 `assets/`、`data/packs/`、`data/static/`；用户数据**不进包**。

校验：

```bash
# Windows
certutil -hashfile dist/release/GiantessGenerator-1.0.0-windows-x64.zip SHA256
# macOS / Linux
shasum -a 256 -c dist/release/SHA256SUMS.txt
```

## 5. 打包后端：PyInstaller（默认）与 Nuitka

两者产出结构相似（一个目录：可执行文件 + 依赖），`--backend` 一键切换。产物名相应带
`-nuitka` 后缀，同一 tag 可并存两版对照：

```bash
python build/release/package.py --tag v1.0.0                        # PyInstaller（默认）
python build/release/package.py --tag v1.0.0 --backend nuitka       # Nuitka 对照
```

### v1.0.0 实测对比（Windows x64，同一提交 `9785356`）

| 指标 | PyInstaller `onedir` | Nuitka `--standalone` |
|---|---|---|
| 发行包（zip） | 37.3 MB | 45.2 MB |
| 包内条目数 | 3374 | 987 |
| 可执行文件 | `GiantessGenerator.exe` 小，依赖散在 `_internal/` | 单个 `.exe` 91 MB（Python/Tk/应用代码全部编译进去） |
| 构建耗时 | 数分钟 | ~14 分钟（含首次准备 Zig 编译器） |
| 构建前提 | 装 `pyinstaller` 即可 | **需要 C 编译器**（见下） |
| 首启数据引导 | 1.2 s | 2.4 s |
| 稳态工作集内存 | 141 MB | 149 MB |
| 运行 | 正常，数据目录正确引导 | 正常，数据目录正确引导 |

结论：**体积上 PyInstaller 更小**——Nuitka 把全部字节码编译成 C 再合成一个巨型 exe，压缩率
低，所以 zip 反而更大；但 **Nuitka 的包内条目数少一个量级**（987 vs 3374），分发时没有"几千个
散碎文件"的观感，也不暴露 `.pyc` 字节码。首启与内存两者接近（本机实测 Nuitka 略高，且首次
运行要多解一层）。**日常发布保持默认 PyInstaller**；需要"少文件 / 不暴露字节码 / 编译期优化"
时再切 Nuitka。

### Nuitka 后端的三个关键点（都在打包器里处理好了）

1. **注入冻结语义**。Nuitka **不会**设置 `sys.frozen` / `sys._MEIPASS`（只对少数已知
   三方包做字符串改写）。本应用的 `paths.py` 正是靠这两个属性判断"是否打包运行"并定位
   只读资源——不打补丁会退化成"源码运行"分支，用户数据被写进安装目录。打包器会把一份
   引导入口 `_gg_nuitka_entry.py` 写进临时 worktree（**不进入仓库**）作为 Nuitka 的
   main module，它在导入应用前补齐 `sys.frozen=True` 与 `sys._MEIPASS=<exe 目录>`。
   该文件仅随 Nuitka 包发布；PyInstaller 后端不需要它。
2. **Tk 必须显式带插件**。界面是 Tk，Nuitka 要 `--enable-plugin=tk-inter` 才会收 Tcl/Tk
   运行时（否则包里缺 `_tkinter.pyd`/`tcl/`，启动即崩）。启动屏用 `multiprocessing` 子进程，
   相应插件 Nuitka 默认启用。
3. **C 编译器**。Nuitka 需要 C 编译器。Windows 上若没有 MSVC / MinGW，且 Python ≥ 3.12
   （`--mingw64` 不支持），打包器用 `--assume-yes-for-downloads` 让 Nuitka 自动下载 **Zig**
   充当 C 编译器（缓存在 Nuitka 私有目录，后续构建复用）。

> 另外注意：Nuitka 的取值选项必须写成 `--opt=value`，空格分隔会被判为"缺参"——脚本里已
> 统一用 `=`。

### 离线 / 受限网络构建：`--wheelhouse`

打包器默认从 PyPI 装依赖。若构建机无外网（或像本次这样代理不稳），可以先把 wheel 备好到一个
目录，再用 `--wheelhouse` 指过去——pip 会切成 `--no-index --find-links`，**同一份设置还会
通过环境变量透传给 Nuitka 派生的私有 pip**，所以 Nuitka 下载 Zig 也走它，全程离线：

```bash
python build/release/package.py --tag v1.0.0 --backend nuitka \
    --wheelhouse D:\wheelhouse
```

`--wheelhouse` 目录里需要放：该 tag `requirements.txt` 的全部 wheel（含传递依赖）+
构建工具（`pyinstaller` 或 `nuitka`；Nuitka 还需 `ziglang`）。可用
`pip download -r requirements.txt -d D:\wheelhouse` 在有网机器上一次性备齐。

## 6. 平台注意事项

- **不能交叉编译**：PyInstaller 的产物与本机平台绑定。三平台的运行时包必须各自在
  对应系统上打；只打得出本平台时，其余平台可先用源码归档交付。
- **Windows**：图标用 `assets/icons/icon.ico`（旧版结构退到 `assets/icon.ico`）。内置
  小游戏窗口依赖系统自带的 **WebView2**（Win10/11 默认有）。
- **macOS**：`.app` 目录由 `--windowed` 产出。本仓暂无 `assets/icon.icns`，暂用
  PyInstaller 默认图标；补上后打包器会自动带上（候选表见脚本内 `ICON_CANDIDATES`）。
- **Linux**：`--icon` 被 PyInstaller 忽略（脚本只在 Windows 传图标）。依赖图功能需要
  系统另装 `graphviz` 的 `dot` 二进制，**不进包**。
- **数据目录**：打包版把可写数据放在系统用户目录（Windows `%LOCALAPPDATA%\GiantessGenerator\data`，
  macOS `~/Library/Application Support/...`，Linux `$XDG_DATA_HOME/GiantessGenerator/data`），
  首启从包内 `data/packs`、`data/static` 拷贝。**不要**把用户数据打进包。

## 7. 与 `build/windows|macos|linux` 的分工

| | 平台专用脚本 | `build/release/package.py` |
|---|---|---|
| 用途 | 开发期在本机快速出包自测、Linux 的打包态自检 | 正式发布 |
| 版本化命名 | 否 | 是 |
| 源码归档 / 校验和 / 发布说明 | 否 | 是 |
| 干净 worktree | 否（直接用当前工作副本） | 是（只用 tag） |
| 平台 | 各写一份 | 一份跨平台 |

Linux 的 `build/linux/build_linux.sh --self-check` 会在**打包态**里真开副本窗口跑自检，
是发布前值得在 Linux 上补跑的一步（`.github/workflows/gui-smoke.yml` 每晚也在跑）。

## 8. v1.0.0 的实际记录（示例）

- 基线：`release/v1.0.0`，自 `befca6f`（2026-09-01）分出，仅含定稿提交 `9785356`。
- 定稿内容：`paths.py` 的 `APP_VERSION` → `1.0.0`；描述风格 `Events.json` 换为新版（140 条）。
- 产物：`GiantessGenerator-1.0.0-windows-x64.zip`、`GiantessGenerator-1.0.0-src.zip`、
  `RELEASE-NOTES-1.0.0.md`、`SHA256SUMS.txt`（在 `dist/release/`，不入库）。
- 另出对照版：`GiantessGenerator-1.0.0-windows-x64-nuitka.zip`（`--backend nuitka`，
  在 `dist/release-nuitka/`，见 §5 对比）。
- 注：该版本的代码结构是**重构前的扁平布局**（根目录直接是 `logic.py` / `ai.py` /
  `world_pack_behaviors.md` 等），与当前 `main` 的目录分层不同——打包器按 tag 取材，
  因此两者都能打。
