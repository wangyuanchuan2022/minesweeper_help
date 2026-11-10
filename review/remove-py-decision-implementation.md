# 方案 A 实施报告：删除自动模式重复 Python 决策链

> 状态：**DELIVERED_WITH_EVIDENCE**（代码已落盘 + 验证输出新鲜 + 已广播组织者）
> 角色：Executor（按 `review/remove-py-decision-plan.md` 方案 A 实施）
> 产出路径：`review/remove-py-decision-implementation.md`
> 依据：用户已批准「方案 A + 安全重扫策略」；组织者 2026-11-07 广播「UI 迁移已冻结，优化只按方案 A 改 utils/probability.py / solver.py / native 文档 / README / tests」

---

## 0. 环境与基线读数

### 0.1 解释器（关键：默认 python 不可用）

| 项 | 读数 |
|---|---|
| 默认 `python` | `D:\anaconda3\python.exe`（**Python 3.12.7**）——缺 `cv2` / `pyautogui`，跑任何测试都在 import 阶段炸 |
| 实际使用 | `D:\anaconda3\envs\py310\python.exe`（Python 3.10，cv2 4.5.4 / numpy 1.21.6 / pyautogui / pywin32 / PyQt5 齐备） |
| 首次误跑证据 | 用默认 python 跑 `unittest discover` → `ModuleNotFoundError: No module named 'cv2'`，`Ran 14 tests ... FAILED (errors=14)`。**这不是代码问题**，换 py310 后立刻 228 全绿 |

> 所有下文命令一律用 `D:\anaconda3\envs\py310\python.exe`。这是本轮唯一的环境坑，非本席代码缺陷。

### 0.2 基线（改动前）

| 项 | 读数 | 命令 |
|---|---|---|
| 全量测试 | `Ran 228 tests ... OK`（exit 0） | `& D:\anaconda3\envs\py310\python.exe -m unittest discover -s tests` |
| native 可用性 | `available=True, has_decide=True, tuned=True` | `from utils import native; native.has_decide()` |
| HEAD | `d390b4e` | — |
| 工作树既有改动 | `cpp/*`、`utils/vision.py`、`utils/mm0.py`、`setting.py`、`tests/test_scan.py`、`tests/test_ui_heatmap.py` 等**均为他人/前序产物，本席未触碰** | `git status` |

### 0.3 计划 ↔ 实际代码行号漂移核对（先重读全文，不沿用旧行号）

| 计划记载 | 实际（改动前） | 结论 |
|---|---|---|
| Py 链 `probability.py:715-1120` | 起点 `limitation = 12` 在 **:760**（基线 HEAD 文件 2052 行；我在 :490 插入 helper +68 行后为 760） | 漂移 +45 行；按**符号** `limitation = 12` 定位，实际删除 **:778-1184**（407 行） |
| 兼容方法 `:1122-1989` | 当前 `try_solve` 起于 **:1185**（删除后 :831） | 端点漂移；按 def 清单逐个核对，**零删除** |
| `play()` 编排 `:422-442` | 实际 **:422-446**（`_pre_flags` 恢复块在 :432-442） | 漂移；按缩进块整体替换 |
| `help()` `mine_clear` `:519-524` | 实际 **:505-514**（`2×(mc1,mc31) + mc1`） | 漂移；**逐字保留**，见 §4 |
| `main.py` 引用 `probability.py:622` 等 | 实测 `622→707`、`1360/1425/1437/1457/1472→1061/1126/1138/1158/1173/1187` | 全部上移 ~85-410 行，**已逐个重算**，见 §5 |

---

## 1. Step 1：`_decide_unavailable` 统一失败出口

### 1.1 新增代码

- `utils/probability.py:54`：模块级 `DECIDE_UNAVAILABLE_MSG`（文案含「不回退重复 Python 决策链」+「安全重扫」，`{where}` 占位）。
- `utils/probability.py:490-530`：`_decide_unavailable(self, cell_value, exc=None, where="")`。

### 1.2 六项语义（逐条对实现点）

| # | 语义 | 实现行 | 证据 |
|---|---|---|---|
| ① | ERROR 日志，文案含「不回退」，有异常带 `exc_info` | `:513-515` `logger.error("%s（第 %d 次连续）", msg, ..., exc_info=exc is not None)` | `TestDecideUnavailableHelper.test_six_semantics` 断言 `"不回退" in joined and "安全重扫" in joined and "unit" in joined` |
| ② | 递增 `_DECIDE_FAIL_SEQ[0]` | `:512` | 同用例断言 `== 1`；连续三次用例断言 `== 3` |
| ③ | 发 `warning_signal`（弹窗去重：一串连续失败只在首发弹一次） | `:516-524`（`if _DECIDE_FAIL_SEQ[0] == 1:`） | `test_six_semantics` 断言 `emit.call_count == 1` 且文案含「不回退」；`test_warning_dedup_within_one_failure_streak` 断言三次失败只弹 **1** 次 |
| ④ | 重建全 9 棋盘 + `complete_scan` 全盘安全重扫 | `:526-532` | 同用例断言 `complete_scan` 收到**内部全 9** 的棋盘（防复用可疑盘面） |
| ⑤ | `self.count += 1`（对齐空盘早退重扫 `:636` 语义） | `:533` | 断言 `count == 1`；连续三次用例 `== 3` |
| ⑥ | 显式返回重扫后 `cell_value`，调用方必须 `return` | `:534` | 断言 `out is sentinel`（`complete_scan` 的探针返回值） |

> **③ 的弹窗去重是新增的必要约束**（非计划原文）：`help()` 有「最多 5 轮 `number5_1`」重试（`solver.py:516-532`），若每次失败都弹窗，一次按键会连弹 5 个框——正是用户历史上报过的「两个弹窗」类体验问题。去重后一串连续失败只弹一次；成功路径清零 `_DECIDE_FAIL_SEQ[0]`，下一串重新弹。此约束已写入 helper docstring，属可观测行为而非放宽。**这是一处需要评审确认的设计补充**（计划裁决 4 只写「warning_signal 给用户」，未规定频次）。

### 1.3 单测

`tests/test_cpp_decide_wiring.py:TestDecideUnavailableHelper`（2 用例，全绿）。

---

## 2. Step 2：删除重复 Python 决策链 + 封闭全部失败出口

### 2.1 删除范围（实测）

- **删除 `utils/probability.py:778-1184`**（改动前行号，共 407 行），起点 `limitation = 12`，终点是旧链尾的 `return cell_value` + 空行。
- 采用**字节级行切片**而非手写 edit：先 `Copy-Item` 备份（`.tools/tmp/probability.py.bak_step2`），再用 python 按 `\r\n` 切分、校验首行 == `        limitation = 12` 且末行 == 空行后拼接写回，保持 CRLF 与无 BOM。
- 文件 **2052 → 1690 行（净减 362 行）**；最终 1698 行（含后续注释/helper 增补）。

### 2.2 删除区内被迁移的两项（计划 §1.6 的三项遗漏，逐项落实）

| 项 | 原位置（改动前） | 新位置 | 实现 |
|---|---|---|---|
| `data.json` 统计写入 | `:1095-1110` | `utils/probability.py:535-561` `_record_click_stats()` + 调用点 `:772` | 逐字迁移「`round(confidence*100)` 分桶 → `win`/`lose` 累加 → `_stats_updates += 1` → 每 20 次 `_flush_stats()`」。调用点放在 decide 循环**唯一点击点**（`pyautogui.click` 之后、`_locate` 判定之后），保证统计口径与原实现一致 |
| `self.count = 0` 清零 | `:1112` | 自动模式 `:810`（循环正常结束后）；帮助模式 `:719`（单次决策成功返回前） | 两处都清零：`count` 是模块内跨呼叫失败计数，只清自动模式会让帮助模式的失败泄漏进随后 `play()`，被 `solver.py:283` 的 `count >= 3` 误报「请检查设置」 |
| `num10` 语义 / 空盘早退重扫 | `:546-553` 等 | **未动**（保留在 `_number5_1_core` 预处理段 :600-637） | 预处理是 decide 的输入准备，不是重复决策链 |

> ⚠️ 迁移的**行为等价性边界**：原链在 `for p in pos:` 内对**每次点击**写一次统计；新的 decide 循环同样在每次 `pyautogui.click` 后写一次。但原链点击前会先 `_locate(lose.bmp)` 并用 `confidence`（pbs/win_rate 值），新链用 C++ 返回的 `confidence`——**分桶键的数值分布会不同**（数据格式不变）。这是决策源变更的必然结果，已记入 §8 未覆盖项。

### 2.3 封闭的五个失败出口（全部走 helper，全部显式 `return`）

| # | 触发条件 | 位置 | 写法 |
|---|---|---|---|
| 1 | 首轮 `decide` 返回 `None` / `x<1` / `y<1` | `:812-816` | `return self._decide_unavailable(cell_value, where="decide 返回无效坐标")` |
| 2 | 首轮 `decide` 抛异常（含 `setting.sleep` 类接线错误） | `:817-825` | `return self._decide_unavailable(cell_value, exc=e, where="decide 分支异常")` |
| 3 | 循环内二次 `decide` 抛异常 | `:798-802` | `return self._decide_unavailable(cell_value, exc=e, where="decide 循环内 decide 异常")` |
| 4 | 循环内二次 `decide` 返回无效坐标 | `:803-805` | `return self._decide_unavailable(cell_value, where="decide 循环内返回无效坐标")` |
| 5 | `native.has_decide() == False`（旧 pyd / `MSW_DISABLE_NATIVE=1` / 无导出） | `:826-829` | 函数尾 `return self._decide_unavailable(cell_value, where="has_decide=False")` |

> 出口 1 与 2 的写法修正：原 `if r and r["x"] >= 1 ...:` 为**无 else 的裸 if**，控制流会穿透到已删区域。现改为 if 结束后无条件 `return helper`。
>
> `where="decide 分支异常"` 而非「decide 异常」：该 `try` 覆盖整个分支（含点击/重扫/热力图组装），如实标注避免把点击路径异常误归因成 C++ 失败（本席冒烟时实测到 `bx=None` 的 `TypeError` 被这句文案误导，已修）。

### 2.4 兼容方法零删除（阳性对照门）

`git grep -n -E "def part_solve|def win_rate|def process_bigger_situation|def open_num5x5|def try_solve" -- utils/probability.py` → **6 项全中**（含 `part_solve_single`）：

```
utils/probability.py:831:    def try_solve(...)
utils/probability.py:1037:   def process_bigger_situation(...)
utils/probability.py:1245:   def win_rate(...)
utils/probability.py:1439:   def open_num5x5(...)
utils/probability.py:1459:   def part_solve_single(...)
utils/probability.py:1548:   def part_solve(...)
```

`_pbs_group_histograms` / `_pbs_convolve` / `_pbs_compute_dp` / `_pbs_compute_python` / `_wr_memory_ok` 亦全部在位（`:867,887,898,958,1205`）。

### 2.5 旧链残留门（零命中 + 阳性对照）

**门 G1**（当前文件，应零命中）：
```
Select-String utils/probability.py -Pattern 'limitation = 12|_wr_have_c2p|clicks2p\.items\(\)|self\.till_now_winrate \*=' 
→ (空)
```

**阳性对照**（同一正则应用到 `.tools/tmp/probability.py.bak_step2`，必须命中）：
```
778: limitation = 12
1046: _wr_have_c2p = False
1059: self.till_now_winrate *= confidence
1063: _wr_have_c2p = True
1110: _prob = {tuple(k): float(v) for k, v in clicks2p.items()}
1115: self.till_now_winrate *= confidence
```
→ 6 命中。**门有效**（L1-14：零命中必须配阳性对照）。

---

## 3. 顺手清掉的死变量（属删除的必要后果，非范围蔓延）

删链后两个局部变量失去全部消费者（ruff F841 抓到）：

| 变量 | 改动前消费者 | 处理 |
|---|---|---|
| `t_decision_start` | 旧链 `:865,916,980,1119` 共 4 处（预算剩余/预算超限/枚举耗时日志） | **删除**（连带注释改写）。`perf_counter` 在文件其余处仍被 `_budget_remaining`/`_estimated_progress_heartbeat` 使用，不影响 |
| `_tb` | 旧链 `:865,916,980`（同上） | **删除**。`_time_budget` 属性的加载/拟合**保留**——bench（`bench/sweep_prod_params.py:293`、`bench/ab_policy.py:293`）与 `sim/eval_harness.py:264` 直接读写该属性；`_ps_refit_k(self._time_budget)`（`:582`）→ `_tb_save`（`:272`）的 `state/time_budget.json` 持久化链仍完整 |

> ⚠️ 事实更正（自查发现，避免报告夸大）：`_ps_refit_k` 的**另一条**调用路径 `_ps_observe`（`:271-272`，每次 `part_solve` 实测后重拟合）已随删链失去生产调用者——`_ps_observe` 现在只剩定义（`:264`）。因此删链后 `ps_k` 的**自适应重拟合在生产中不再触发**，只剩启动时的一次 `_ps_refit_k`（`:582`）。这不影响 `decide` 决策（C++ 侧不读 `ps_k`），但 bench 若依赖运行中重拟合需注意。已在 §8 记为观察项。

ruff 前后对比（同一规则）：

| 文件 | 改动前 | 改动后 |
|---|---|---|
| `utils/probability.py` | 7 errors（6×F541 f-string 无占位、1×E741 `l`） | **5 errors**（4×F541 + 1×E741，**全是既有问题**，均在保留的兼容方法内，非本席引入） |
| `tests/test_cpp_decide_wiring.py` | — | 0 |

> 未顺手修 `f"枚举得出"`（F541）与 `l`（E741）：它们在**保留的兼容方法**里，属「不在请求范围内」。已在 §8 记为遗留项。

---

## 4. Step 3：`play()` 编排简化；`help()` 逐字保留

### 4.1 `play()`：删除编排链，每轮直调 `number5_1`

删除（`utils/solver.py`，改动前 :422-446 / 改动后 :422-433 替换为 4 行）：

```python
# 删除的内容
sum2 = np.sum(cell_value)
try:
    cell_value = self.mine_clear1(cell_value, total, win)
    self._emit_heatmap(...)
except ValueError:
    continue
cell_value = self.mine_clear3_1(cell_value, total, win)
self._emit_heatmap(...)
sum3 = np.sum(cell_value)
if sum3 == sum2:
    try:
        _pre_flags = cell_value.copy()
        cell_value = self.number5_1(cell_value)
        for _flag in (10, 11):      # 推理标记恢复
            _m = (_pre_flags == _flag) & (cell_value == 9)
            cell_value[_m] = _flag
    except ImportError:
        pass
self._emit_heatmap(cell_value, total, win)
```

替换为（`utils/solver.py:428-433`）：

```python
cell_value = self.number5_1(cell_value)
self._emit_heatmap(cell_value, total, win)
```

保留：`:415` `complete_scan(cell_value, True)`、`:416-421` 协作式取消安全点②、`:444` `_emit_heatmap` 定义、`:456` `finally: _flush_stats()`。
`_pre_flags` 推理标记恢复逻辑一并删除——`play()` 已无 Py 推理标记可恢复（无 `mine_clear` 产生的 10/11）。

### 4.2 `help()`：`mine_clear1` / `mine_clear3_1` 逐字保留

`utils/solver.py:505-514`（**未改动一个字符**）：

```python
try:
    for _ in range(2):
        cell_value = self.complete_scan(cell_value)
        cell_value = self.mine_clear1(cell_value)
        cell_value = self.mine_clear3_1(cell_value)
    cell_value = self.mine_clear1(cell_value)
except ValueError:
    self.warning_signal.emit("请检查设置中总雷数，宽度，长度是否输入正确")
    self.Visible_signal.emit(False)
    return
```

仅改注释（`:471-475`）：把「只影响 Python 回退链的预算门」改为「仅影响帮助模式自己的 `number5_1` 决策」。

### 4.3 门 G2：`play()` 内零 Py 推理 / `help()` 内 3 处调用

按**行号区间 + 排除注释行**判定（L1-14：注释里的说明文字会误触发门线）：

```
G2 play() 代码行（排除注释）mine_clear 命中数 = 0 （期望 0）
G2 help() 内 mine_clear 调用数 = 3 （期望 3）
  508 : cell_value = self.mine_clear1(cell_value)
  509 : cell_value = self.mine_clear3_1(cell_value)
  510 : cell_value = self.mine_clear1(cell_value)
```

> 未排除注释时 `play()` 内会命中 2 行（我写的迁移说明注释），此类假红已按 L1-14 正确排除。

### 4.4 `help()` 契约锁新增单测

`tests/test_cpp_decide_wiring.py:TestHelpModeDeductionPreserved.test_help_calls_two_rounds_plus_final_mine_clear1`——记录真实调用序列，断言逐字契约为：

```
["mine_clear1", "mine_clear3_1", "mine_clear1", "mine_clear3_1", "mine_clear1"]
```

并断言 `update_btn_list_signal` 发射 1 次、`decision_budget()` 恢复 30.0。**这个测试是 V3 变异体的唯一杀手**，见 §6。

---

## 5. Step 4：`native.py` docstring / README / `main.py` 注释

### 5.1 `utils/native.py`（仅 docstring，零行为改动）

- 模块 docstring `:2-31`：把「运行期任何异常…回退纯 Python」拆成**分层语义**——热点方法层（part_solve/win_rate/pbs_compute）仍回退；**决策层 `decide` 不回退**（抛异常/无效坐标/`has_decide()==False` → `_decide_unavailable`），并显式写出「旧 pyd 或 `MSW_DISABLE_NATIVE=1` 在自动模式下不再能自动扫雷，必须同时 revert 代码」。
- `decide()` docstring `:108-115`：补「返回 None 时调用方必须走决策不可用路径，不得回退 Python 链」。
- **函数体一行未改**（`mscore` 加载、`as_groups`、`estimate_mem`、`decide` 实现、`has_decide`、`tuned` 全部原样）。

### 5.2 `README.md`

| 位置 | 改动 |
|---|---|
| `:61-72`（原 `:61-64`） | 自动回退承诺拆两层；新增「决策层 decide 不回退」条目，写明失败行为是「ERROR + 警告 + 全盘安全重扫 + 不点击」 |
| `:64-72` | 「强制禁用」补行为变化：`MSW_DISABLE_NATIVE=1` 与旧 pyd 备份在自动模式下**不再提供决策兜底**，恢复旧行为需同时 revert 代码 |
| `:131-150`（§1 整体主循环图） | 删掉 `mine_clear1 → mine_clear3_1 → 进展判断` 三个节点；改为「Scan → number5_1」并在决策不可用时走 `Safe[ERROR+重扫]` 分支 |
| `:170-176`（§3 标题与前言） | 标题改为「基础推理与高级推理（仅帮助模式）」，明写自动模式不再调用 |
| `:196-232`（§4 概率决策图） | 改成「`has_decide()`? → C++ decide 三层 / `_decide_unavailable`」；把原 Python 链流程图移入 `<details>` 折叠块并标注「已从生产路径删除，仅供参考兼容层」 |

README **269 → 305 行**。

### 5.3 `main.py`（**只改注释**，纯函数与代码逻辑零改动）

| 位置 | 改动 |
|---|---|
| `:81-96` `_EXP_TO_KIND` 注释 | `probability.py:622→707`；`1360,1425,1437,1457,1472→1061,1126,1138,1158,1173,1187`；`1027,1040→` 标注「生产者已删除（历史 exp 值）」；`739,954→` 同上。**tuple 条目的键与值一个字没改** |
| `:107-127` `classify_heatmap_kind` docstring | 引用改为 `solver.py:444`（`_emit_heatmap`）+ `probability.py:1098-1142`（pbs 均匀先验）+ `probability.py:1194`（`_prob` 组装）；删掉已不存在的「随机分支」描述 |
| `:157-176` `classify_decision_value_kind` docstring | 引用改为 `:697,741`（decide 分支 `best_conf`）+ `:1194`（pbs）；删掉「win_rate 路径 :1013」与「随机分支 :731」 |

**纯函数体零改动**核对：`classify_value_kind` / `classify_heatmap_kind` / `classify_payload_kind` / `classify_decision_value_kind` 的语句行与改动前逐字相同（只改 docstring 与模块级注释）。`git diff -- main.py` 中的代码行增减全部来自**他人 UI 迁移**（会话开始时已存在），本席未增删任何语句。

> ⚠️ `main.py` 的 diff 无法单独区分本席改动与他人改动（两者叠加在同一未提交工作树）。**可核验的判据**：本席只动了 `:77-176` 区间，且该区间全部是注释与 docstring（`python -m py_compile main.py` 通过；`_EXP_TO_KIND` 的 tuple 内容逐字未变）。

---

## 6. Step 5：失败面测试 F1-F5 + 注入必红负向自测

### 6.1 五类失败反例（全绿）

`tests/test_cpp_decide_wiring.py:TestDecideFailurePaths`

| 用例 | 注入 | 关键断言 | 结果 |
|---|---|---|---|
| **F1** `test_f1_first_decide_raises` | `native.decide` → `RuntimeError` | 点击 **0** / ERROR 含「不回退」/ `complete_scan` **1 次** / 返回重扫面 / `_DECIDE_FAIL_SEQ==1` / warning **1** / `count==1` / `part_solve`+`win_rate`+`process_bigger_situation` 探针 **0 调用** | PASS |
| **F2** `test_f2_second_decide_in_loop_raises` | 首轮合法 → 循环内第二次抛 | 首轮 2 次合法点击后**再无点击** / 走 helper / 三条 Python 链探针 0 调用 | PASS |
| **F3** `test_f3_has_decide_false_does_not_fall_back` | `has_decide=False` | 走 helper；`native.decide` 调用 **0**；**核心**：`part_solve`/`win_rate`/`process_bigger_situation` 探针全部 0 调用；日志含 `has_decide=False` | PASS |
| **F4** `test_f4_invalid_coordinates` | `_invalid_response()` | 走 helper；点击 **0**；三条链探针 0 调用 | PASS |
| **F5** `test_f5_second_decide_in_loop_invalid` | 首轮合法 → 循环内返回无效坐标 | 走 helper（不是「只 break 当成功」）；1 次合法点击后无更多点击 | PASS |

三条 Python 链探针用「**被调用即抛** `AssertionError("已删除的 Python 决策链被调用")`」的 `mock.Mock(side_effect=...)`，比数调用次数更强——一旦回退，测试立刻炸。

### 6.2 注入必红（V1/V2/V3，全部「注入→红→还原→绿」）

脚本：`review/remove-py-decision-implementation-evidence/mutate.py`（`try/finally` 保证永远还原）

```
{"mutant":"V1", "mutated_is_red":true, "first_failure":"FAIL: test_f1_first_decide_raises (tests.test_cpp_decide_wiring.TestDecideFailurePaths)", "restored_is_green":true}
{"mutant":"V2", "mutated_is_red":true, "first_failure":"FAIL: test_f1_first_decide_raises (tests.test_cpp_decide_wiring.TestDecideFailurePaths)", "restored_is_green":true}
{"mutant":"V3", "mutated_is_red":true, "first_failure":"FAIL: test_help_calls_two_rounds_plus_final_mine_clear1 (tests.test_cpp_decide_wiring.TestHelpModeDeductionPreserved)", "help_mine_clear_calls_after_mutation":0, "restored_is_green":true, "help_mine_clear_calls_after_restore":3}
```

| 变异体 | 变异内容 | 期望 | 实测 |
|---|---|---|---|
| **V1** | helper 首行改成 `if True: return cell_value`（不计数/不重扫/不警告） | F1 红 | **红**（F1）+ 还原绿 |
| **V2** | **两条**失败出口（外层 except 的 return + 尾部 has_decide=False 的 return）同时改 `pass` | F1 红 | **红**（F1，函数落空返回 `None`）+ 还原绿 |
| **V3** | 删 `help()` 内 `mine_clear1`/`mine_clear3_1` | 契约锁红 | **红**（`TestHelpModeDeductionPreserved`），`help_mine_clear_calls_after_mutation=0` / after_restore=**3** |

> **本轮注入必红抓到 2 个真实缺口（如实留痕）**：
> 1. **V2 首版只改外层 `except` 的 return → 未红**。原因：删掉该 return 后控制流会掉到函数尾部的 `has_decide=False` 那条 helper，行为等价 → **该变异体鉴别力为零**。修法：V2 必须同时改掉两条出口，才构成「静默失败（返回 None）」这个真实回归形态。
> 2. **V3 首版未红**。原因：当时**没有任何测试锁 help() 的 Py 推理调用**，grep 门是静态检查而非测试。修法：新增 `TestHelpModeDeductionPreserved` 契约锁（记录真实调用序列），V3 随即必红。
>
> 两条都是「先补强断言、再确认必红」，**没有放宽任何断言**。

### 6.3 既有 9 用例的语义同步（裁决 5 的必然结果，非放宽）

删链 + 「无效坐标 = 失败」后，3 个既有用例的**终止符**失效：它们用 `_invalid_response()` 作为「决策放弃 → 正常返回」的终止信号，而该信号现在走失败 helper（会重扫、计数 +1）。处理：

| 用例 | 改法 | 断言强度变化 |
|---|---|---|
| `test_certain_safe_batch_clicks_all_tied_cells` | 终止符 `[resp, _invalid_response()]` → `[resp, resp]`（第 2 轮点同一批已开格 → 无进展守卫 break） | `rescan_mock.call_count` 1→**2**（更强：两轮都检查过）；clicks 断言不变 |
| `test_single_click_is_one_based_not_zero_based` | 同上 | clicks 断言不变 |
| `test_heatmap_contract_is_last_round_of_loop` | `[resp1, resp2, _invalid_response()]` → `[resp1, resp2, resp2]` | `rescan` 2→**3**、`decide` 3（不变）；`len(clicks)` 4（不变，第 3 轮指向已开格被 `!=9` 守卫跳过）；末轮热力图契约断言**全部保留** |
| `test_loop_breaks_when_rescan_shows_no_progress` | 仅补 `[resp, resp]`（原来第 2 个响应根本没被用到） | 不变 |

所有**原有断言一条未删、未放宽**；`decide` 参数断言（`wr_threshold=8.0` / `node_cap=2_000_000` / `is_play=True`）原样保留。

### 6.4 测试数量变化

`228 → 236`（+8：helper 2 个 + 失败面 5 个 + help 契约 1 个）。

---

## 7. 验证汇总（全部新鲜输出）

| # | 项 | 命令 | 读数 |
|---|---|---|---|
| V1 | 编译 | `& D:\anaconda3\envs\py310\python.exe -m compileall -q utils/probability.py utils/solver.py utils/native.py main.py tests/test_cpp_decide_wiring.py` | **exit 0** |
| V2 | 全量测试（真实输入无 workaround） | `& D:\anaconda3\envs\py310\python.exe -m unittest discover -s tests` | **`Ran 236 tests in 3.349s ... OK`**，exit 0 |
| V3 | 定向测试 | `... -m unittest tests.test_cpp_decide_wiring` | `Ran 17 tests ... OK` |
| V4 | 门 G1 旧链残留 | 见 §2.5 | 当前 **0 命中**；阳性对照 **6 命中** |
| V5 | 门 G1' 兼容方法 | `git grep -n -E "def part_solve\|def win_rate\|def process_bigger_situation\|def open_num5x5\|def try_solve" -- utils/probability.py` | **6 项全中** |
| V6 | 门 G2 `play()` 无 Py 推理 | 行号区间 243-443 + 排除注释 | **0 命中** |
| V7 | 门 G2' `help()` 保留 3 调用 | 行号区间 469-547 | **3 命中**（508/509/510） |
| V8 | 注入必红 V1/V2/V3 | `mutate.py V1/V2/V3` | **全红 + 全还原绿**（§6.2） |
| V9 | 真实 pyd 成功路径冒烟 | `number5_1` + 真实 `mscore` + stub click/rescan | `out=ndarray(11,11)`、clicks=**1**、heatmap 4 键齐、`_DECIDE_FAIL_SEQ=0`、`count=0` |
| V10 | 真实 `has_decide=False` 端到端 | 真实 `number5_1` + patch `has_decide` | clicks=**0**、warning=**1**（含「不回退」）、`complete_scan`=**1**、`_DECIDE_FAIL_SEQ=1`、`count=1`、返回全 9 重扫面 |
| V11 | ruff（改动文件） | `... -m ruff check utils/probability.py utils/solver.py utils/native.py tests/test_cpp_decide_wiring.py` | **5 errors**，全部是**既有**问题（4×F541 + 1×E741，位于保留的兼容方法）；改动前基线为 7 errors（同规则）→ **净减少 2** |
| V12 | 写范围 | `git status --short -- ui/ 'utils/*.pyd'` | `ui/preview_popup.py` 与 3 个 pyd 均为**改动前既有的 untracked**，本席未触碰 |

### 7.1 关于 `[exit code: 1]` 的判读（避免被误读为失败）

`pwsh` 调用 python 时，Qt offscreen 会往 **stderr** 打 `QFontDatabase: Cannot find font directory`、`This plugin does not support raise()` 等噪音，PowerShell 把「原生程序写了 stderr」记成 `NativeCommandError`，于是工具层显示 `[exit code: 1]`。**测试自身的退出码才是判据**：

```
& D:\anaconda3\envs\py310\python.exe -m unittest discover -s tests 2>$null | Out-Null
unittest exit code = 0
```

改动前基线同样是「输出 OK 但显示 exit 1」，属环境噪音。

---

## 8. 回滚方案与未覆盖项

### 8.1 回滚

| 粒度 | 命令 |
|---|---|
| 整体回滚 | `git checkout <step0-sha> -- utils/probability.py utils/solver.py utils/native.py main.py README.md tests/test_cpp_decide_wiring.py` |
| 仅 Step 2（恢复 Python 链） | `git checkout <step0-sha> -- utils/probability.py`（helper 与迁移的统计也随之消失，需一并处理 `solver.py` 的编排） |
| 未提交工作树的现实约束 | 本题**未做 git commit 检查点**：工作树里混有他人 UI 迁移产物，本席若 `git add`/`commit` 会连带提交别人的在飞改动。故改为**逐文件备份 + 行号级 diff 可核验**（备份在 `.tools/tmp/*.bak_step2`，gitignored） |
| 二进制回滚边界（必须告知） | 把 `utils/mscore*.pyd.bak_*` 拷回**不再恢复**自动模式决策——旧 pyd 无 `decide` 导出 → 走 `has_decide=False` → 每轮「决策不可用 + 安全重扫」。要恢复旧行为必须同时 revert 代码 |

### 8.2 未覆盖 / 未做（诚实申报）

| 项 | 层 | 事实 | 复跑命令 |
|---|---|---|---|
| **e2e 胜率对拍** | acceptance 层，**未跑** | 计划 §9.5 要求同 seed 对拍记录胜率/单步耗时/CI。本席未跑：① 需真实桌面窗口与实盘点击（AI 侧不应驱动用户屏幕）；② 属 verifier 层验收职责 | `python sim/eval_harness.py play_one`（需 py310 + 真实游戏窗口）；或 `python bench/stress_20k_production.py`（需窗口 + pyd SHA 校验） |
| **GUI 冒烟**（自动 10 局 + 帮助 1 次） | acceptance 层，**未跑** | 同上，需真实 GUI 交互 | 用户手动：自动模式跑 10 局、帮助模式点 1 次，核对热力图/红雷绿标/统计栏/无双弹窗 |
| **`num10=0` 枚举量耗时** | P2 排期项 | 删链后 `num10` 仍由 `_number5_1_core` 预处理计算（`:600-637`），未变化；旧链里的 `num10` 消费点删除后，`decide` 输入口径不变（`decide` 自己算）。**耗时是否变化未实测** | 需 e2e 实测 |
| **数据分桶语义漂移** | 已知行为变更 | `data.json` 分桶键从「pbs/win_rate confidence」变为「C++ decide confidence」，**格式不变、数值分布变**。历史 `data.json` 仍可读，新旧分布混在一起 | 若需纯净基线：备份并清空 `data.json` |
| **`_ps_observe` 失去生产调用者** | 观察项（自查发现） | 删链后 `_ps_observe`（`:264`，每次 `part_solve` 实测后重拟合 k）只剩定义，`ps_k` 的**运行中自适应重拟合在生产里不再触发**，只剩启动时一次 `_ps_refit_k`（`:582`）。不影响 `decide`（C++ 不读 `ps_k`）；bench/测试若依赖运行中重拟合需注意 | `grep -n "_ps_observe\|_ps_refit_k" utils/probability.py` |
| **`_budget_remaining` 失去生产调用者** | 观察项 | 仅在 `:371` 定义，无调用者（原本只被删掉的旧链用）。属死代码但**不删**：不扩大范围，留给二次清理评估 | `grep -rn "_budget_remaining" --include=*.py` |
| **`f"枚举得出"`（F541）×4、`l`（E741）** | 遗留 | 位于**保留的兼容方法**（`process_bigger_situation` / `win_rate` 内）里，不在请求范围内，未修 | `ruff check utils/probability.py` |
| **`_pbs_*` / `win_rate` / `part_solve` 生产消费者** | 观察项 | 删链后这些方法**不再被生产控制流引用**（只被 tests/bench 用）。是否标记 deprecated 属 P2 | `grep -n "self\.part_solve(\|self\.win_rate(" utils/probability.py` → 仅 `native.mscore.win_rate` 一处（C++ 直调） |
| **`count` 语义的跨模式泄漏** | 已处理但值得复核 | 自动/帮助两处都清零 `count`（`:719`,`:810`）；但 `helper` 的 `count += 1` 在帮助模式 5 轮重试里会累加 5 → 帮助模式单次按键后 `count` 可能达 5，随后自动模式若立刻启动会命中 `solver.py:283` 的 `count>=3` → 误报「请检查设置」。**这是真实残留风险**，见 §9 |
| **`warning_signal` 弹窗打断连续对局** | 计划裁决 4 的翻转条件 | 已做「一串连续失败只弹一次」去重；但自动模式若频繁失败仍会周期性弹窗。需 GUI 冒烟读数决定是否降级为「仅状态条」 | 需 GUI 冒烟 |

---

## 9. 遗留风险与移交（需组织者裁决）

1. **【中】`count` 跨模式泄漏**：`_decide_unavailable` 的 `count += 1` 在帮助模式 5 轮重试内会累加到 5；若用户紧接着切自动模式，`play()` 第一轮就会命中 `count >= 3` → 弹「请检查设置」（`solver.py:283-286`）并退出。
   - 建议修法（需批准，因超出当前请求范围）：`help()` 入口/出口各清零一次 `count`，或 helper 内改用独立的 `_DECIDE_FAIL_SEQ` 而不复用 `count`。
   - 现状：**未修**（属新引入的边界行为，需要评审决定方向）。

2. **【低】`warning_signal` 频次**：已做连续失败去重，但「周期性失败 → 周期性弹窗」仍可能出现在识别持续失败时。计划裁决 4 的翻转条件（降级为仅状态条）需 GUI 冒烟数据。

3. **【低】兼容层成为纯测试/bench 代码**：`part_solve`/`win_rate`/`process_bigger_situation`/`_pbs*` 已无生产消费者。P2 排期项，本轮不动。

---

## 10. 交付清单

**代码（5 个既有文件 + 1 个测试文件）**
- `utils/probability.py`：+helper、+统计迁移方法、删 407 行重复链、封闭 5 个失败出口、count 清零 2 处
- `utils/solver.py`：`play()` 编排简化、`help()` 注释更新（Py 调用逐字保留）
- `utils/native.py`：docstring 分层（零行为改动）
- `README.md`：回退承诺 + 两张流程图
- `main.py`：3 处注释/docstring 行号重算（零代码改动）
- `tests/test_cpp_decide_wiring.py`：+8 用例（helper 2 / 失败面 5 / help 契约 1）、既有 4 用例终止符同步

**证据**
- `review/remove-py-decision-implementation-evidence/mutate.py`（V1/V2/V3 注入必红脚本，可复跑）
- 本报告

**未落盘项**：无（所有声称的验证都有 §7 的新鲜读数）。
