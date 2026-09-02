# Minesweeper Help 重构交接文档

> 更新时间：本次 util.py 拆分（T5 文件级）完成后

## 1. 当前进度

**T5 的文件级拆分已完成**（本次会话），另有若干此前已合入的性能优化。任务状态总览：

| # | 任务 | 状态 |
|---|------|------|
| T1 | P5: Qt 信号节流 | ✅ 已完成（代码中已有 `_throttled_pv_signal_emit`，100ms 节流） |
| T2 | P1: 模板缓存 | ✅ 已完成（`load_img` 基于 mtime + cell_width 的缓存） |
| T5 | A0: 拆分 util.py | ✅ 文件级拆分完成（零功能变更）；⚠️ 深度拆分（SolverCore/SolverThread 解耦）未做，依赖 T4 |
| T3 | A1: 统一配置管理 | ⏳ pending |
| T4 | A2: 解耦 GUI 和业务逻辑 | ⏳ pending |
| T6 | A5: 拆分 main.py | ⏳ pending |

计划文件: `C:\Users\ycwan\.claude\plans\linear-toasting-sutherland.md`（内容已过时，以本文档为准）

### 1.1 额外已完成的优化（不在原任务列表中）

- **统计缓存**: `data.json` 懒加载到内存，每 20 次点击才落盘一次（`_load_stats`/`_flush_stats`，位于 `utils/solver.py`）
- **点击后增量重扫**: `rescan_after_click` 基于新旧棋盘图像变更检测，只对变化的格子重新做模板匹配（正确处理 flood-fill 大面积更新），位于 `utils/vision.py`
- **整屏截图复用**: `play()` 循环中 ok/win/lose 检测复用同一张整屏截图（`_grab_screen_bgr` + `_locate(f, screen)`）
- **对话框模板缓存**: `_locate_templates` 字典缓存（`utils/vision.py`）
- **测试基础设施**: `tests/` 包，71 个测试全部通过

## 2. 已了解的知识

### 2.1 项目概览

这是一个扫雷辅助工具，用 Python + PyQt5 编写。功能包括：
- **自动扫雷**: 截图识别棋盘状态，推理+枚举求解，自动点击
- **帮助人类**: 截图分析后以可视化方式展示推荐点击位置（带颜色编码概率）
- **截图帮助**: 用于重新截图校准模板

### 2.2 文件结构（当前实际状态）

```
D:\python\projects\minesweeper_help/
├── main.py                  # 1082行，5个GUI类，应用入口
├── setting.py               # 读取cfg.json中的win_name，sleep=0.01
├── color.py                 # OKLCH渐变，独立
├── logger.py                # 日志工具
├── cfg.json                 # 运行时配置（w=30, h=16, a=99等）
├── data.json                # 运行时胜率统计
├── test_accuracy.py         # 根目录独立脚本：胜率统计显著性检验 + matplotlib 绘图
├── utils/                   # ← 本次拆分后的结构
│   ├── __init__.py          # pyautogui 全局配置 + 从 mm0/solver re-export
│   ├── mm0.py               # ClientToScreen, GetMousePosition, MouseWindowTread, minesweeper_run 等（188行，未拆）
│   ├── util.py              # 薄包装层（37行）：re-export 全部旧名字，旧导入路径继续可用
│   ├── combinatorics.py     # 纯组合数学：C, C_num, get_list, get_index_from_list, A, p_of_c, combination_ratio
│   ├── clicking.py          # sort_clicks（MST 点击排序，未被调用）
│   ├── misc.py              # MyEncoder, print_board
│   ├── vision.py            # BoardVisionMixin：模板加载、截图、compare_img、complete_scan、rescan_after_click
│   ├── deduction.py         # DeductionMixin：cell_around, mine_clear1/3_1, number0, number_3_1, get_set(_1)
│   ├── probability.py       # ProbabilityMixin：number5_1, try_solve, process_bigger_situation, win_rate, part_solve(_single), open_num5x5
│   └── solver.py            # AutoPlayThread + Solver（Mixin 组合）：__init__/reload/play/help, 统计缓存, 信号节流, __main__ 基准
├── tests/                   # unittest 测试包
│   ├── helpers.py           # make_solver（跳过__init__的轻量实例）/ make_full_solver / make_board / load_test_data
│   ├── test_combinatorics.py# C/C_num/A/p_of_c/get_list/combination_ratio
│   ├── test_solver_core.py  # cell_around/get_set/number0/mine_clear1/open_num5x5
│   ├── test_probability.py  # part_solve(_single)/number5_1/win_rate（含 tests/data/*.json 棋盘数据）
│   └── test_scan.py         # _slice_cell/_cell_changed/rescan_after_click/模板缓存/统计缓存
├── ui/                      # pyuic5 生成（window/edit_setting/screenshot/bg_rc），未改动
├── build/lib.win-amd64-cpython-37/minesweeper_cpp.cp37-win_amd64.pyd  # 已编译C++扩展（见2.6）
├── dist/, build/, image/, icons/, logs/, state/  # 打包产物与资源
└── HANDOFF.md               # 本文档
```

注意：旧文档提到的 `cpp/minesweeper_solver.cpp` 源文件与 `test_solver_comprehensive.py`、`test_quick.py` **在工作区中不存在**（可能已被删除或从未提交），勿再引用。

### 2.3 Solver 类结构（拆分后）

```python
class Solver(BoardVisionMixin, DeductionMixin, ProbabilityMixin, AutoPlayThread):
```

MRO（已验证）: `Solver → BoardVisionMixin → DeductionMixin → ProbabilityMixin → AutoPlayThread → QThread → QObject`

| 模块 | 职责 | 关键方法 |
|------|------|----------|
| `utils/vision.py` | 视觉识别 | `load_img`（模板缓存）, `_grab_screen_bgr`, `_grab_board`, `_slice_cell`, `compare_img`, `complete_scan`, `rescan_after_click`, `_locate`, `locate_exit` |
| `utils/deduction.py` | 基础规则推理 | `cell_around`, `mine_clear1`, `number0`, `number_3_1`, `mine_clear3_1`, `get_set`, `get_set_1` |
| `utils/probability.py` | 概率枚举与胜率 | `number5_1`（主入口）, `part_solve`, `part_solve_single`, `try_solve`, `process_bigger_situation`, `win_rate`（MD5 memoization）, `open_num5x5` |
| `utils/solver.py` | 线程与主循环 | `AutoPlayThread`（信号定义）, `Solver.__init__/reload/run`, `play`, `help`, `_load_stats/_flush_stats`, `_throttled_pv_signal_emit` |

Mixin 不单独实例化使用，依赖宿主实例属性；每个 Mixin 文件头部有依赖属性说明。

### 2.4 配置加载分散点（T3 待处理）

| 文件 | 行号 | 方式 |
|------|------|------|
| `main.py` | 78 | MyMainWindow.__init__ 中 `open("cfg.json")` |
| `main.py` | 366, 762, 961 | 各 GUI 类中读取 |
| `main.py` | 924, 937 | 写回 cfg.json |
| `utils/solver.py` | Solver.__init__ / reload() | `open("cfg.json")` |
| `setting.py` | 模块 import 时 | 直接 `open("./cfg.json")`（导入时崩溃风险） |

### 2.5 Qt 信号流（未受拆分影响，已验证）

**Solver 定义的信号（`utils/solver.py` AutoPlayThread）：**
- `pv_signal` (int) - 进度条更新
- `text_signal` (str) - 文本输出
- `Visible_signal` (bool) - 进度条显隐
- `warning_signal` (str) - 警告弹窗
- `warning_signal_2` (str) - 额外警告
- `start_signal` (tuple) - 开始进度条动画
- `end_signal` (str) - 结束信号
- `update_btn_list_signal` (list) - 更新帮助页面按钮

**main.py 中的连接：**
- auto_play_thread 连接了所有 8 个信号
- help_thread 连接了 pv_signal, Visible_signal, update_btn_list_signal, warning_signal, warning_signal_2, start_signal, end_signal

### 2.6 C++ 求解器状态

- 已编译: `build/lib.win-amd64-cpython-37/minesweeper_cpp.cp37-win_amd64.pyd`
- **pybind11 源文件 `cpp/minesweeper_solver.cpp` 在工作区不存在**
- 没有任何 Python 代码导入该模块；后续集成需先找回源码或反推接口

### 2.7 已知代码重复（更新位置）

1. `MyEncoder`: `utils/misc.py` 与 `main.py:1066` 重复定义
2. `cell_value` 重置双循环共 11 处：`utils/solver.py` 6 处（play 初始化 1 + 胜利/失败窗口处理 4 + help 初始化 1）、`utils/probability.py` 5 处。建议提取 `make_unopened_board(w, h)` 工具函数
3. 窗口检测-重置循环: `play()` 中 4 处近似重复（win 窗口/lose 窗口/win.bmp/lose.bmp 四个分支结构几乎相同）

### 2.8 已知性能问题

1. ~~信号过载~~ ✅ 已解决（`_throttled_pv_signal_emit`，特殊值 0/100/-1 直发）
2. ~~模板重复加载~~ ✅ 已解决（mtime + cell_width 缓存）
3. ~~_locate() 全屏截图~~ ✅ 已解决（play 循环复用同一张 screen）
4. `sort_clicks()` 未被调用: MST 排序已实现但从未使用（位于 `utils/clicking.py`）
5. `pyautogui.moveTo()` 冗余: `complete_scan` 每次都移鼠标（位于 `utils/vision.py`）

## 3. 本次 util.py 拆分记录（T5 文件级）

**改动内容**（零功能变更，方法体逐字搬移）：

1. `utils/util.py` 从 2113 行 → 37 行薄包装层，re-export 全部旧名字（`C, C_num, get_list, get_index_from_list, A, p_of_c, combination_ratio, sort_clicks, MyEncoder, print_board, AutoPlayThread, Solver`）
2. 新建 6 个模块：`combinatorics.py` / `clicking.py` / `misc.py` / `vision.py` / `deduction.py` / `probability.py` / `solver.py`（详见 2.2、2.3）
3. 原 util.py 模块级 `pyautogui.PAUSE = setting.sleep` 副作用移到 `utils/__init__.py`（导入任意子模块都会先执行 `__init__`，行为等价，已验证）
4. 唯一外部改动：`tests/test_scan.py` 的 `mock.patch("utils.util.cv.imread")` → `"utils.vision.cv.imread"`（`cv.imread` 调用点已移至 vision）
5. `main.py`、`tests/helpers.py`、`tests/test_combinatorics.py` 的 `from utils.util import ...` / `from utils import ...` 均无需改动，接口完全兼容

**验证结果**：

- `D:\anaconda3\envs\py37\python.exe -m unittest discover -s tests` → **71 个测试全部通过（exit 0）**
- 冒烟测试：真实 Solver 上 `part_solve` 与 `part_solve_single` 各得 310 种解、逐格概率完全一致（allclose=True）
- 导入链 / MRO / pyautogui.PAUSE=0.01 均验证正常

## 4. 剩余任务

| # | 任务 | 依赖 | 难度 |
|---|------|------|------|
| T3 | A1: 统一配置管理（新建 config.py，替换分散加载；setting.py 延迟加载） | 无 | 低-中 |
| T4 | A2: 解耦 GUI 和业务逻辑（SolverCore 纯逻辑 + SolverThread Qt 包装，回调替代信号） | T3 | 中 |
| T5-深度 | 拆 SolverCore/SolverThread；automation/（点击操作、game_loop）再细分 | T4 | 高 |
| T6 | A5: 拆分 main.py（gui/main_window.py, edit_setting.py, screenshot.py, message_box.py） | T3 | 中 |

**实施顺序**: T3 → T4 →（T5-深度 与 T6 可并行）

文件级拆分已为 T4 留好接缝：`utils/probability.py` 与 `utils/deduction.py` 对 Qt 信号的依赖集中在少数 emit 调用点，解耦时可将它们改为回调注入。

## 5. 关键约束

1. **零功能变更**: 重构不改变任何用户可见行为
2. **向后兼容**: `utils/__init__.py` 与 `utils/util.py` 的导出接口不变，`main.py` 入口不变
3. **每步可测试**: 每个任务完成后，测试套件必须全绿
4. **C++ 不在此范围**: 作为后续 work item
5. **mock 路径约定**: 打 patch 时注意 `cv.imread` 现在在 `utils.vision`，不再是 `utils.util`

## 6. 验证策略

```powershell
# 运行测试（py37 环境，71 个测试）
D:\anaconda3\envs\py37\python.exe -m unittest discover -s tests -v

# 快速验证导入链与接口
D:\anaconda3\envs\py37\python.exe -c "import utils.util as u; print(u.Solver)"
```

- py37 环境**没有安装 pytest**，用 unittest
- T3/T6 完成后手动启动 GUI: `D:\anaconda3\envs\py37\python.exe main.py`
- 特别注意信号连接不被破坏：main.py 中 auto_play_thread 和 help_thread 的所有 .connect() 调用必须继续有效

## 7. 运行环境

- **可用解释器**: `D:\anaconda3\envs\py37\python.exe`（Python 3.7.16，PyQt5/opencv/networkx/pyautogui/pywin32/numpy 全部正常）
- ⚠️ **不要用系统默认 python**（3.12.7，`C:\Users\ycwan\AppData\Local\Programs\Python\Python311\python.exe` 等路径）：pywin32 报 `DLL load failed`（ImportError: win32gui）
- 其它 conda 环境: ai_agent, couplet, djangoProject, py310, py311, pyins（在 `D:\anaconda3\envs\`）
- Windows 11

## 8. git 状态

- 当前分支: main，最近提交: `716cd92 优化代码`
- **未提交**: 本次 utils 拆分改动（`utils/util.py`、`utils/__init__.py` 修改 + 6 个新模块未跟踪；HANDOFF.md 本身也未跟踪）
- tests/ 目录此前已被 `git add`（含 `tests/__pycache__/*.pyc`，建议提交前 unstage 并加入 .gitignore）
- 工作区还有大量未跟踪文件 (.claude/, .idea/, __pycache__/, build/, dist/, MineSweeper.zip 等)
