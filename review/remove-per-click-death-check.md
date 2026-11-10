# 删除 decide 循环内「逐点击死亡探测」并让扫描不确定错误不再打断自动循环

- 任务来源：用户 2026-11-07 指令（原文逐字引用见 §1）+ 组织者广播 msg-mw5v15qk-c
- 执行者：Executor（本会话 session-c0fca09d-8626-4392-b262-c9aa6944d157）
- 结论状态：`DELIVERED_WITH_EVIDENCE`（代码已落盘、测试已跑、本文件已落盘、完成广播已发）
- 改动文件：`utils/probability.py`、`tests/test_cpp_decide_wiring.py`（新增/更新回归）、本报告
- 未改动（按任务边界）：UI、`utils/vision.py`、C++（`cpp/`）、`cfg.json`、`utils/solver.py` 主循环胜负检测

---

## 1. 用户诉求（逐字引用）

用户本轮原话：

> 「我指的是每一局结束之后，因为出现了雷的图像，以可能会导致爆出棋盘扫描不确定的错误，这个错误不要影响自动循环的执行」

组织者随后补充（msg-mw5v15qk-c）：

> 「当前 probability.py 仍有 _dead_hit/_locate lose/time.sleep。用户明确要删除。请立即完成最小改动、测试、报告并广播，不要继续分析。」

拆成两条硬需求：

| # | 需求 | 落地位置 |
|---|---|---|
| R1 | 删除 decide 循环内**逐点击**的 `_dead_hit` / `_locate('./image/lose.bmp')` / `time.sleep(setting.sleep)` | `utils/probability.py:774-787`（点击循环） |
| R2 | 「局末棋盘出现雷 → 扫描不确定」这类错误**不得**打断自动循环 | `utils/probability.py:535-539`、`644-651`、`789-798` + `_record_click_stats` 去调用 |

---

## 2. 改动前行为（file:line 事实）

改动前的事实基线（Git HEAD 之后、本次修改之前的文件内容）：

| 位置 | 内容 | 后果 |
|---|---|---|
| `utils/probability.py:756` | `_dead_hit = False` | 每轮 decide 前的死亡标志 |
| `utils/probability.py:764` | `time.sleep(setting.sleep)` | **每个点击**后固定睡 `setting.sleep`（0.01s；批量 64 格 = 0.64s 纯等待，且 `pyautogui.PAUSE` 已自带同值延迟 ⇒ 双份） |
| `utils/probability.py:767` | `_lose, _, _ = self._locate("./image/lose.bmp")` | **每个点击**后做一次整屏模板匹配（`_locate` 内部 `ImageGrab.grab()` 抓全屏 + `cv.matchTemplate`，全屏数十万像素级） |
| `utils/probability.py:772` | `self._record_click_stats(confidence, _lose)` | 每点击写一次 `data.json` 分桶 |
| `utils/probability.py:773-775` | `if _lose: _dead_hit = True; break` | 命中失败弹窗即跳出本批点击 |
| `utils/probability.py:778-779` | `if _dead_hit: break` | 跳出整个 decide 循环 |
| `utils/probability.py:682`（注释） | 「捕获 `_locate` 到 lose 则跳出」 | 设计语义：逐点击判死 |
| `utils/vision.py:447-449` | `STRICT_LOW_CONFIDENCE=False` 时低置信度**不抛**，只 log+warning | 但 `tests`/自动化可置 True；且 `_locate`/rescan 的**误判**会直接改变 `cell_value` |
| `utils/solver.py:283-286` | `if self.count >= 3: warning_signal.emit("请检查设置…"); return` | **停机闸门**：任何让 `count` 连续 3 次 +1 的路径都会把自动循环停掉并弹「请检查设置」 |
| `utils/solver.py:387-413` | `_lose, x, y = self._locate(... "image/lose.bmp" ...)` + 「游戏失败」`FindWindow` 分支 | **主循环的胜负检测**（本次明确不动） |

现场证据（`logs/minesweeper.log`，2026-11-07 11:19:36）：

```
[ERROR] utils.vision: 点击后重扫：99/99 格无法可信识别（比例 100% ≥ 阈值 50%），棋盘不可信，请重新截图/校准模板与窗口位置
```

以及 10:58:04 连续三次「全盘扫描：25/25 格无法可信识别」+ 紧随其后的 `C++ decide 决策不可用` ERROR —— 即用户描述的「出现雷的图像 → 爆出棋盘扫描不确定」链路真实发生过。

---

## 3. 改动内容（最小可行 diff）

### 3.1 R1：删除逐点击死亡探测与 sleep（`utils/probability.py:773-787`）

改动前：

```python
_num9_before = int(np.sum(cell_value == 9))
_dead_hit = False
for (cx, cy) in _to_click:
    if cell_value[cy, cx] != 9:
        continue
    pyautogui.click(self.bx + cx * self.cell_width, self.by + cy * self.cell_width)
    time.sleep(setting.sleep)                                   # ← 删除
    _lose, _, _ = self._locate("./image/lose.bmp")               # ← 删除
    self._record_click_stats(confidence, _lose)                  # ← 删除调用
    if _lose:                                                    # ← 删除
        _dead_hit = True                                         # ← 删除
        break                                                    # ← 删除
cell_value = self.rescan_after_click(cell_value)
if _dead_hit:                                                    # ← 删除
    break                                                        # ← 删除
```

改动后（`utils/probability.py:773-787`）：

```python
_num9_before = int(np.sum(cell_value == 9))
for (cx, cy) in _to_click:
    if cell_value[cy, cx] != 9:
        continue  # 已开/已标记（certain_safe 批量可能含旧格）
    pyautogui.click(self.bx + cx * self.cell_width, self.by + cy * self.cell_width)
    # ⚠️ 点完**只点击、不探测**（用户裁决 2026-11-07）：
    # 这里曾逐点击 `_locate("./image/lose.bmp")` 判死并
    # `time.sleep(setting.sleep)`，现两者都删除——
    # ① 局末棋盘出现雷/失败弹窗时扫描本来就不准，属正常节奏；
    # ② 胜负判定统一由主循环 `utils/solver.py` 的
    #    「游戏胜利/游戏失败」弹窗检测接管（本切点未改）。
    # 点击节奏由 pyautogui.PAUSE(=setting.sleep) 自带。
```

理由链（每条都可核验）：

1. **`time.sleep(setting.sleep)` 是重复等待**：`utils/__init__.py:7` 已设 `pyautogui.PAUSE = setting.sleep`，`pyautogui.click` 自身会等这么长。删除后点击节奏不变，只是不再双份。
2. **`_locate('./image/lose.bmp')` 每点击一次全屏抓取 + 模板匹配**，是 O(批量格数) 的整屏开销，且路径 `"./image/lose.bmp"` 是 **cwd 相对**（生产用 `setting.project_path(...)` 绝对路径，见 `utils/solver.py:387`）；`settings.normalize_cwd()` 虽能兜住，但一旦 cwd 被改就会 `cv.imread` 返回 None → `_load_locate_template` 抛 `FileNotFoundError` → 被 decide 分支宽 `except` 捕成「C++ decide 决策不可用（decide 分支异常）」→ 直接把**成功的决策**误报成失败。
3. **死亡判定本来就有更权威的来源**：`utils/solver.py:288/317` 的 `FindWindow(None, "游戏胜利"/"游戏失败")` 与 `:357/387` 的 win/lose 模板检测在主循环每轮执行，且会重置棋盘、记账、开新局。decide 循环内的二次判定既重复又会与传统路径打架。
4. **用户的语义裁决**：局末出现雷图导致扫描不准是**正常节奏**，不该被当成决策失败条件。

### 3.2 R2：扫描不确定错误不再中断自动循环（三处出口）

**出口 A — decide 循环内重扫**（`utils/probability.py:788-798`）：

```python
try:
    cell_value = self.rescan_after_click(cell_value)
except LowConfidenceScanError as e:
    # 扫描不确定（典型场景：局末失败弹窗盖住棋盘）**不是决策失败**：
    # 本轮作废、原样交回盘面，不计 count、不弹警告。
    logger.warning("decide 循环重扫不确定，本轮作废（不影响自动循环）：%s", e)
    return cell_value
```

**出口 B — 空盘早退的全盘重扫**（`utils/probability.py:644-651`）：

```python
try:
    cell_value = self.complete_scan(cell_value)
except LowConfidenceScanError as e:
    logger.warning("全盘扫描不确定，本轮作废（不影响自动循环）：%s", e)
    return cell_value
self.count += 1
return cell_value
```

**出口 C — `_decide_unavailable` 的安全重扫**（`utils/probability.py:534-539`）：

```python
try:
    fresh = self.complete_scan(fresh)
except LowConfidenceScanError as e:
    logger.warning("决策不可用后的安全重扫不确定，返回未开棋盘交回主循环：%s", e)
self.count += 1
return fresh
```

**为什么必须这三处都接**（这正是「错误不要影响自动循环」的关键）：

- `LowConfidenceScanError`（`utils/vision.py:67`）在 `STRICT_LOW_CONFIDENCE=True` 时由 `_apply_recognition`（`utils/vision.py:447-448`）抛出；`complete_scan`/`rescan_after_click` 里 `except LowConfidenceScanError: raise`（`utils/vision.py:478-479`、`511-512`）**原样上抛**，不属于「批量失败回退逐格」的兜底范围。
- 在此之前，这三处都没有捕获点：异常会穿出 `_number5_1_core` → 落到 `utils/solver.py` 的 `play()`/`help()` —— 而 `play()` 只有 `pyautogui.FailSafeException` / `win32ui.error` 两个窄捕获（`utils/solver.py:433/437`），**没有通用捕获**；`QThread.run` 也无兜底。结果就是整个自动循环线程被掀掉（`utils/vision.py:56-60` 的注释已记录过同类实测 abort）。
- 三处统一语义：**本轮作废，可观测 WARNING，不累计 `count`、不弹 warning 窗、原样返回盘面**，把控制权交回主循环。这样 `utils/solver.py:283` 的 `count >= 3` 停机闸门不会被「死了一局」误触发。

### 3.3 配套：`_record_click_stats` 去调用（`utils/probability.py:543-571`）

逐点击 lose 探测删除后，「这次点击是否踩雷」这一信号已不存在。若仍按「每点击记 win」写入 `data.json`，会系统性地把统计口径变成「全是 win」，污染置信度校准数据（`data.json` 当前 14 个桶、`"100": {"lose": 0, "win": 197}` 等就是历史累积）。故：

- 删除唯一调用点（原 `:772`）；
- 保留方法本体并更新 docstring 标注「当前无调用点，保留仅为兼容外部 bench/历史脚本」（`utils/probability.py:543-548`），不做超出任务范围的删除；
- `_load_stats` / `_flush_stats`（`utils/solver.py:171-189`）与 `play()` 的 `finally: self._flush_stats()`（`:441-442`）**未改**——本任务边界内不动统计底座。

### 3.4 未改动项（验证边界声明）

- `utils/solver.py`：**0 行改动**。胜负检测 `"游戏胜利"`/`"游戏失败"`（`:288`/`:317`）、win/lose 模板（`:357`/`:387`）、局末重置与 `_gs_save` 记账全部原样。
- `utils/vision.py`：**0 行改动**（`LowConfidenceScanError` 的定义与抛出策略不动，只是消费侧改成「记账不中断」）。
- UI / C++ / `cfg.json`：**0 行改动**。

---

## 4. 测试契约更新（`tests/test_cpp_decide_wiring.py`）

### 4.1 改造：默认探针从「恒 False stub」改为「被调用即抛」

原 `_run_core` / `_run_fail` 把 `_locate` stub 成 `lambda f, screen=None: (False, 0, 0)`、`time.sleep` stub 成 `lambda s: None` —— 这会让**回归重新加回探测也照样变绿**（假绿风险）。现改为 `_Forbidden` 探针（`tests/test_cpp_decide_wiring.py:107-123`）：一旦实现再调用 `_locate`/`time.sleep` 就抛 `AssertionError`；并额外断言 `calls == 0` 做双保险。

### 4.2 新增 `TestNoPerClickDeathProbe`（5 个用例，`tests/test_cpp_decide_wiring.py:605-768`）

| 用例 | 锁住的契约 |
|---|---|
| `test_no_per_click_locate_or_sleep_single_click` | 单点连续两轮：点击照常、`_locate`/`sleep` 各 **0 次**、盘面推进（`num9` 25→24） |
| `test_no_per_click_locate_or_sleep_batch` | certain_safe 批量 3 格：3 次点击全发生（**只删探测不删点击**），零探测零 sleep，`num9` 25→22 |
| `test_rescan_low_confidence_does_not_stop_auto_loop` | rescan 抛 `LowConfidenceScanError` → **不抛、`count==0`、warning 窗 0 次、`_DECIDE_FAIL_SEQ==0`、返回盘面、有 WARNING 日志** |
| `test_auto_loop_can_iterate_after_low_confidence` | 上一轮扫描不确定后，同一实例第二轮仍能正常点击/推进（循环未被失败态卡死） |
| `test_main_loop_winlose_detection_untouched` | 用 `inspect.getsource(Solver.play)` 断言 `"游戏胜利"`/`"游戏失败"`/`image/win.bmp`/`image/lose.bmp`/`_gs_save` 都在（**主循环胜负检测未动**） |

### 4.3 新增 `TestDecideUnavailableHelper.test_safe_rescan_low_confidence_does_not_raise`

`_decide_unavailable` 的安全重扫本身不可信时不得上抛：断言返回全 9 棋盘、`count==1`、ERROR「不回退」仍可观测（`tests/test_cpp_decide_wiring.py:431-455`）。

### 4.4 负向自测（断言鉴别力，必红验证）

把被删的探测 + sleep 注回生产代码（`.tools/_mut_perclick.py` 注入，锚点定位后 `restore` 还原）：

```
$ py .tools/_mut_perclick.py inject      # 临时探针，用完已删除；源码见下
$ py -m unittest tests.test_cpp_decide_wiring
...
AssertionError: 1 != 0 : time.sleep 不应被调用，实际 1 次
...
Ran 23 tests in 0.447s
FAILED (failures=9)          ← 注入后必红（9 条失败，含 5 条新增契约用例）
$ py .tools/_mut_perclick.py restore
$ py -m unittest tests.test_cpp_decide_wiring
Ran 23 tests in 0.035s
OK                            ← 还原后必绿
```

变异探针是一次性临时脚本（跑完即删，避免在仓库留调试代码）。复现方式：先 `cp utils/probability.py` 备份，然后在下列锚点**之前**插入两行，跑测试应红，再还原备份：

锚点（`utils/probability.py:781` 的注释行）：
```
                            # ⚠️ 点完**只点击、不探测**（用户裁决 2026-11-07）：
```
插入内容（注意缩进与锚点一致，位于 `pyautogui.click(...)` 之后、注释之前）：
```python
                            time.sleep(0.01)
                            _lose, _, _ = self._locate("./image/lose.bmp")
```

变异体与断言的对应关系：注入的 `time.sleep` → `sleep_probe.calls == 0` 断言红；注入的 `_locate` → `locate_probe.calls == 0` 断言红；两者都覆盖批量与单点两条点击路径。

---

## 5. 验证（新鲜输出）

环境说明：本仓测试必须用 **py310 conda env**（`D:\anaconda3\envs\py310\python.exe`）。系统默认 `python` 是 anaconda 3.12 基础环境，**缺 pyautogui/cv2**，直接跑 `python -m unittest` 会在 import 阶段就 `ModuleNotFoundError`（这属于环境面失败，不是代码失败）。

| 层次 | 命令 | 结果 |
|---|---|---|
| 编译 | `py -m compileall -q utils tests main.py` | **exit 0**（无 SyntaxError） |
| 定向测试 | `py -m unittest tests.test_cpp_decide_wiring` | **Ran 23 tests … OK**（原 17 + 新增 6） |
| 全量回归 | `py -m unittest discover -s tests` | **Ran 261 tests in 6.590s … OK**（0 failure / 0 error） |
| 负向自测 | 见 §4.4 | 注入 → **FAILED (failures=9)**；还原 → **OK** |

> 命令前缀 `py` 实指 `D:\anaconda3\envs\py310\python.exe`，`QT_QPA_PLATFORM=offscreen` 已设。

代码面机检（带阳性对照，避免假 0）：

```
utils/probability.py:
  _dead_hit        = 0 处   （阳性对照：同文件 _num9_before = 3 处 ✓ 工具正常）
  self._locate(    = 0 处   （阳性对照：同文件 _locate 字样 = 1 处，仅注释）
  time.sleep(setting 代码 = 0 处（命中 1 处，经核为注释文本 L783）
```

---

## 6. 行为变化对比（前后）

| 维度 | 改动前 | 改动后 |
|---|---|---|
| 每个点击的 `_locate` 全屏匹配 | 1 次/格 | **0 次** |
| 每个点击的 `time.sleep` | 1 次/格（双份延迟，PAUSE 已含同值） | **0 次** |
| 每格点击后的 `data.json` 分桶写 | 1 次（按 lose 信号记 win/lose） | **0 次**（不再自动写，避免污染） |
| 踩雷后的循环行为 | `_dead_hit` → 立刻 break 出 decide 循环 | 不探测；本批剩余格继续点，重扫后按 num9 守卫自然结束 |
| 局末 rescan/全盘扫描不可信 | 异常穿出 → 掀掉自动循环线程 | WARNING + 本轮作废 + 返回盘面，**循环继续** |
| `count`（`play()` 停机闸门） | 扫描不确定会经失败路径 +=1，3 次即弹「请检查设置」并停机 | 扫描不确定**不累加 count**，不会误停机 |
| 整局胜负判定 | 主循环（`FindWindow` + win/lose 模板）+ decide 循环内二次判死 | **仅主循环**（decide 内判死已删） |
| 主循环胜负检测代码 | — | **逐字未改**（§4.2 用例机检） |

---

## 7. 未覆盖 / 未验证（如实申报）

1. **真实窗口未覆盖**：本任务全部验证用 stub（`native.decide` / `rescan_after_click` / `pyautogui.click` / `_locate`），**没有任何一次真实 GUI 运行**。用户描述的「每局结束后出现雷图」场景在真机上是否被完全消除，需要真机冒烟：自动模式连续跑 ≥10 局（其中至少含 3 局踩雷失败），观察 (a) 是否还出现 `点击后重扫：N/N 格无法可信识别` 后停机的现象；(b) 是否仍能正常进下一局。
2. **`STRICT_LOW_CONFIDENCE` 的真实取值**：仓库默认 `False`（`utils/vision.py:60`），此时 `LowConfidenceScanError` 根本不抛，我的三处捕获是「防御性 + 自动化/测试配置下生效」。若生产某处把它置 `True`，本次改动正是必需的；若始终 `False`，本次改动不改变现有行为（仅 R1 的删除生效）。
3. **性能收益未量化**：删除每点击一次全屏抓取 + 模板匹配的理论收益明显，但本报告**没有做真机计时**（不做无测量支撑的数字）。如需，可用 `bench/profile_pipeline.py` 口径另测。
4. **`data.json` 统计断流**：per-click 统计不再写。`data.json` 现有 14 个桶保持原值不变（不会再增长）。若有下游依赖该文件持续更新（`test_accuracy.py`、`utils/mm0.py:172-189` 的 `__main__` 绘图），需知悉此行为变化——这是删除逐点击探测的**必然推论**，已在 §3.3 说明。
5. **`_record_click_stats` 保留但无调用**：属有意保留（避免波及外部 bench 脚本），若后续确认无人调用可另行删除，不在本任务范围。
6. **踩雷后本批剩余格仍被点击**：删除 `_dead_hit` 后，certain_safe 批量里若第 2 格踩雷，第 3..N 格仍会点。这是用户「扫描不准是正常节奏、不要打断」的直接推论；真机上这些点击落在失败弹窗/已结束棋盘上，由主循环下一轮检测接管。若实测发现有点到「重新开始」按钮的副作用，需要单独提；本次未观察到（无真机）。

---

## 8. 可核验锚点汇总

| 事实 | 位置 |
|---|---|
| 逐点击探测已删（源码已无 `_dead_hit`） | `utils/probability.py:774-787` |
| 模块级契约声明 | `utils/probability.py:14-16` |
| `LowConfidenceScanError` 导入 | `utils/probability.py:32` |
| 出口 A：decide 循环重扫 | `utils/probability.py:788-798` |
| 出口 B：空盘早退全盘扫描 | `utils/probability.py:644-651` |
| 出口 C：失败出口安全重扫 | `utils/probability.py:534-539` |
| `_record_click_stats` 无调用声明 | `utils/probability.py:543-548` |
| `_Forbidden` 探针 | `tests/test_cpp_decide_wiring.py:107-123` |
| 新增契约用例组 | `tests/test_cpp_decide_wiring.py:605-768` |
| 主循环胜负检测（未改） | `utils/solver.py:288`、`:317`、`:357`、`:387` |
| 停机闸门（未改，靠 count 不累加来避免误触发） | `utils/solver.py:283-286` |
| 现场错误证据 | `logs/minesweeper.log`（11:19:36 / 10:58:04 段） |
| 负向自测复现步骤 | 本文件 §4.4（锚点 + 插入内容 + 预期必红） |
