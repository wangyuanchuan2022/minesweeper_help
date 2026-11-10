# 修复：配置/资源路径与停止生命周期（P0/P1）

状态：**已完成并验证**（代码已落盘 + 定向/全量测试新鲜通过）
日期：2026-11-06
范围（任务书限定）：`setting.py`、`utils/solver.py`、`utils/mm0.py`、必要时 `main.py`；只改路径解析与停止信号。
未改：`cfg.json`、`README.md`、`main.spec`、HTML 原型、概率算法、UI 原型。
新增测试：`tests/test_config_lifecycle.py`（30 个用例）。

## 1. 问题清单（修复前实测）

| 编号 | 级别 | 修复前位置 | 问题 |
|---|---|---|---|
| P0-1 | P0 | `setting.py:3` | 模块导入即 `open("./cfg.json")`：从非项目目录启动直接 `FileNotFoundError`，整个程序无法 import（已实测复现：在临时目录执行 `import main` 崩溃于 `setting.py:3`）。 |
| P0-2 | P0 | `utils/solver.py:101`、`utils/solver.py:175` | `Solver.__init__` 与 `reload()` 各写一遍 `open("cfg.json")`，均 cwd 相对，且字段赋值逻辑重复。 |
| P0-3 | P0 | `main.py:78`、`main.py:431`、`main.py:968`、`main.py:1167` | 主窗口 / `EditWindow` / `ScreenShot` 四处重复 cwd 相对读 cfg。 |
| P0-4 | P0 | `main.py:1130`、`main.py:1143` | 设置保存写 `open(r"cfg.json","w")`：非项目根启动会把配置写到**启动目录**，静默分裂成两份配置。 |
| P1-1 | P1 | `utils/solver.py:174-188` | `reload()` 重读配置后不重算派生字段 `p`（雷密度）与 `screenshot_w/h`；改配置后概率先验仍是构造期旧值。 |
| P1-2 | P1 | `utils/solver.py:135`、`utils/solver.py:145` | `data.json` 统计读写用裸相对名，cwd 变化即读写到别处（历史统计丢失 / 写脏启动目录）。 |
| P1-3 | P1 | `utils/mm0.py:174` | `__main__` 诊断脚本裸读 `data.json`。 |
| P1-4 | P1 | `main.py:183/548/550/1248/1250/1265/1266` | `image/*` 资源（示例图、截图预览、temp.bmp、模板另存）全 cwd 相对。 |
| P1-5 | P1 | `utils/solver.py:287/293/298/328` | `_locate("./image/ok.png" / exit.png / win.bmp / lose.bmp)` 全 cwd 相对。 |
| P1-6 | P1 | `main.py:399-420` | 停止帮助/自动流程直接 `QThread.terminate()`：绕过 `finally`（统计落盘、预算乘数恢复），无协作式取消通道。 |
| P2-1 | P2 | `utils/vision.py:114/145/190/227` | 模板 `image/` 路径仍 cwd 相对（**本任务文件范围外**，见第 4 节）。 |
| P2-2 | P2 | `logger.py:21` | 日志目录 `logs/minesweeper.log` cwd 相对（**本任务文件范围外**，见第 4 节）。 |

## 2. 所做变更（file:line 为修复后位置）

### 2.1 `setting.py`：单一资源根

- `setting.py:22` `PROJECT_ROOT`：按 `__file__` 定位，或由环境变量 `MINESWEEPER_HELP_ROOT` 显式覆盖。
- `setting.py:26-29` `CFG_PATH` / `IMAGE_DIR` / `STATE_DIR` / `DATA_PATH` 全部由 `PROJECT_ROOT` 派生（绝对路径）。
- `setting.py:60` `project_path(rel)`：相对项目根 → 绝对；已是绝对路径原样返回。
- `setting.py:68` `load_cfg(path=None)`：缺省读 `CFG_PATH`；`cfg`/`win_name`/`sleep`/`num_workers` 既有接口零改动（`main.py`、`utils/*` 的 20+ 处 `setting.win_name`/`setting.sleep` 调用点不受影响）。
- `setting.py:37` `normalize_cwd()`（`setting.py:57` 调用）：把进程 cwd 归一到 `PROJECT_ROOT`。**理由**：`utils/vision.py` 的模板路径与 `logger.py` 的日志路径在本任务文件范围外仍用 cwd 相对，归一到同一资源根即可让它们同样收敛；置 `MINESWEEPER_HELP_NO_CHDIR=1` 可关闭（测试用它来真正验证「路径解析与 cwd 解耦」，否则断言退化成同义反复）。

### 2.2 `utils/solver.py`

- 导入 `threading`；`utils/solver.py:36-37` `_GS_STATE_PATH`/`_DATA_PATH` 改由 `setting.STATE_DIR`/`setting.DATA_PATH` 派生（`state/game_stats.json` 原本已按 `__file__` 定位，现统一到同一资源根，行为不变）。
- `utils/solver.py:217` 新增 `_apply_cfg(cfg)`：集中写配置字段并重算**派生字段**——`p = a/(w*h)`（面积 0 时 0.0，不除零）、`screenshot_h/w`（由 `cell_width` 换算）。
- `utils/solver.py:239` `reload()` → `_apply_cfg(setting.load_cfg())` + `load_img()`；`__init__` 复用同一方法，消除两处重复赋值。
- `utils/solver.py:171/181` `_load_stats`/`_flush_stats` 改用 `_DATA_PATH`（绝对路径）；`_flush_stats` 额外 `makedirs` 保证父目录存在。
- 资源路径：`utils/solver.py:324/330/335/365` 附近四处 `_locate` 改为 `setting.project_path("image/...")`。
- **协作式取消**：`utils/solver.py:83` 实例持有 `threading.Event`；`request_cancel()`（:101）/`clear_cancel()`（:111）/`cancel_requested()`（:117）、语义别名 `_stop_requested`（:123）；`start()`（:125）在派发前清残留标志。
  - 取属性走 `self.__dict__.get(...)` 而非 `getattr(self, ..., None)`：测试用 `Solver.__new__` 的轻量实例上做**缺失属性**的 getattr/hasattr 会抛 `RuntimeError: super-class __init__() of type Solver was never called`（已实测）。
  - 安全点：`play()` 循环起点（:280）、整盘扫描之后推理之前（:419）；`help()` 入口（:498）、首轮扫描后（:516）、每轮概率决策前（:535）。均 `logger.info` 留痕后 `return`，正常走 `finally`。
  - `run()`（:208）**不清**标志（清除只在 `start()`）。理由：若在 `run()` 清，「刚点启动、线程还没跑到 run()，用户已点停止」窗口内的取消会被静默吞掉——已有专项用例。

### 2.3 `utils/mm0.py`

- `utils/mm0.py:175` `__main__` 诊断脚本改 `setting.DATA_PATH`（顺带补 `encoding="utf-8"`）。

### 2.4 `main.py`

- `main.py:60` `_SOLVER_STOP_TIMEOUT_MS = 3000`（协作退出等待上限）。
- `main.py:402` 新增 `_stop_solver_thread(thread, label)`：未运行直接返回 → `request_cancel()` → `wait(3000)` 成功即返回（协作退出）→ 超时才 `terminate()` + `wait(1000)`，并以 `logger.warning` 记录「本次退出不保证 finally 清理」。
- `main.py:425` `end_h_func` / `main.py:435` `end_a_func` 改走该 helper（`terminate()` 仅兜底，功能面不变：仍隐藏进度条/隐藏结束按钮/恢复按钮可用/弹提示）。
- cfg 读 4 处 → `setting.load_cfg()`（`main.py:82/457/994/1192`）；cfg 写 2 处 → `setting.CFG_PATH` 且补 `encoding="utf-8"`（`main.py:1155/1168`）。
- image 路径 7 处 → `setting.project_path(...)`（`main.py:186/573/1272/1290` 等）；`_save_img` 提示文案同步改为绝对路径回显。

## 3. 验证（全部为本次新鲜输出）

环境：`D:\anaconda3\envs\py310\python.exe`（3.10.16）。原因：系统默认 `python` 是 3.12.7，`win32gui` DLL 导入失败（`ImportError: DLL load failed while importing win32gui`），本仓既有环境约定即 py310（`HANDOFF.md:23`）。

| 项 | 命令 | 结果 |
|---|---|---|
| 生命周期模块 | `python -m unittest tests.test_config_lifecycle -q` | **Ran 30 tests … OK** |
| 全量回归 | `python -m unittest discover -s tests -q`（`QT_QPA_PLATFORM=offscreen`） | **Ran 154 tests … OK** |
| 字节编译 | `python -m compileall -q setting.py utils/solver.py utils/mm0.py main.py tests/test_config_lifecycle.py` | **exit 0** |
| 语法编译 | `python -m py_compile` 同上 6 文件 | **exit 0** |

修复前基线（同一环境）：`Ran 122 tests，FAILED (failures=1)` —— `test_ui_heatmap.test_stats_line_content` 文案断言已过时（UI 文案先被改过）。修复后该失败消失（`tests/test_ui_heatmap.py` 本身已在他人改动中更新，我未改其断言）。

关键实测证据：

1. **非项目 cwd 导入**（修复前必崩）：
   - 修复前：临时目录执行 `import main` → `FileNotFoundError: './cfg.json'`（`setting.py:3`）。
   - 修复后：同法输出 `WIN ok 30 16 99`，且 `os.getcwd()` 已归一到项目根。
2. **显式资源根正/负向对照**（一次性探针，跑完已删）：`MINESWEEPER_HELP_ROOT` 指项目根 → `positive rc=0 | OK 30`；指向不存在目录 → `negative rc=1 | FileNotFoundError: True`。证明解析确实走显式根，而不是「恰好命中 cwd」。
3. **`reload()` 重算**：配置 `a=40,w=20,h=10,cell_width=36` → `p==0.2`、`screenshot_h/w==28/20`；对照用例断言与构造期 `p=3/25` 不等（证明非恒等）。
4. **取消**：已置位时 `play()`/`help()` 不进入扫描（扫描调用计数 0）；扫描中置位 → 扫描后安全点立即返回（事件序列恒为 `["scan"]`，不含推理）。
5. **`terminate` 兜底**：协作成功用例断言 `terminate` 调用数为 0；顽固线程用例断言 `terminate` 调用数 1 且捕获到含 `terminate()` 的 WARNING 日志。

## 4. 未覆盖 / 未能安全修改（如实申报）

1. **`utils/vision.py` 与 `logger.py` 的 cwd 相对路径未改**（P2-1/P2-2）。二者在任务书文件范围之外。当前靠 `setting.normalize_cwd()` 在导入期把 cwd 归一到资源根来保证行为一致；**这是收敛而非根治**：若宿主在同一进程内先改 cwd 再调用这些模块，仍会错。根治建议见第 5 节。
2. **`normalize_cwd()` 会改变宿主进程 cwd**（副作用）。这是让范围外模块同样收敛的显式取舍；可用 `MINESWEEPER_HELP_NO_CHDIR=1` 关闭。属于「行为面变化」，请在验收时确认可接受。
3. **真实 GUI 真机停止未验证**：`terminate()` 兜底路径与协作路径均用 fake/stub 线程验证；没有在真机上点按钮观察实际退出耗时。沙箱无法操作真实扫雷窗口与鼠标。
4. **`play()` 的概率枚举内部不设取消点**：只在「循环起点 / 扫描后」两处检查。单次 `number5_1` 决策最长可能跑到自适应预算上限（该内部已有预算门），因此最坏响应延迟 = 一次决策耗时，未做枚举批次级中断（那属于概率算法内部，任务书明确不改）。
5. **`EditWindow`/`get_pos_*`/`mouse_window` 的 `terminate()` 未改**（`main.py:1039/1041/1078/1093/1123-1153` 等）：这些是鼠标取点辅助线程，不在任务书「play/help 主循环」范围；且改成协作取消需要改 `GetMousePosition.run` 的忙等循环，属扩大范围。
6. **`tests/test_scan.py` 有一处测试契约更新**：`test_flush_stats` 原本用 `builtins.open` 的 fake 匹配裸字符串 `"data.json"`，改动后 `_flush_stats` 走绝对路径 `_DATA_PATH`，故改为 patch `utils.solver._DATA_PATH`。**断言未放宽**（仍验证真实落盘且内容含 `"50"`），只是把注入点从「字符串匹配」换成「路径常量」——旧写法在路径修复后必然失效。
7. **测试环境的一个沙箱坑（已在用例里规避，非代码缺陷）**：在 `.tools/tmp` 下用 `tempfile.mkdtemp` 创建的目录，其下再 `mkdir` 会 `PermissionError: [WinError 5]`（沙箱 ACE 只覆盖建目录前已存在的路径）。用例已改用 `os.makedirs` 显式建目录。**这解释了此前广播里「state 目录 mkdir PermissionError」的失败——是我的测试写法问题，不是业务断言失败，也不是产品代码缺陷。**

## 5. 移交建议（后续）

1. `utils/vision.py`：`_template_mtime`/`_validate_templates`/`load_img`/`locate_exit` 的 `image/` 路径改 `setting.project_path("image", ...)`；改完后可移除 `setting.normalize_cwd()`（副作用消除）。
2. `logger.py`：`_DEFAULT_LOG_FILE` 改 `os.path.join(setting.PROJECT_ROOT, "logs", "minesweeper.log")`（需注意 `logger.py` 被 `setting` 之外极早导入，避免循环导入可用 `__file__` 同源计算）。
3. `play()` 的 `number5_1` 内部如需更短停止延迟，在概率枚举的批循环加 `_stop_requested()` 检查（属概率模块改动，需单独授权）。
4. 真机验收：启动自动模式后立即点「结束」，确认日志出现「收到停止请求，自动模式在循环起点安全退出」且无 `terminate() 强杀兜底` warning。

## 6. 交付判定

- 代码：4 个允许文件已改（`setting.py`/`utils/solver.py`/`utils/mm0.py`/`main.py`），另按约定更新 `tests/test_scan.py` 一处测试契约。
- 测试：`tests/test_config_lifecycle.py` 新增 30 用例；全量 154 用例通过；compileall/py_compile 通过。
- 未覆盖项已在第 4 节逐条申报，无静默省略。
