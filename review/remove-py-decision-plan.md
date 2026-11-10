# 方案 A 有界代码结构优化计划：删除自动模式重复 Python 决策链

> 状态：DELIVERED_WITH_EVIDENCE（计划已落盘并经完成广播直投组织者；**本 Planner 未改任何源代码**）
> 生成时刻：20261107-095442；组织者催办 09:57:59 / 10:01:07 / 10:02:24
> 角色：Planner（只读规划；下述 TODOs 是**给执行者**的实施步骤，本会话不执行）
> 产出路径：`review/remove-py-decision-plan.md`
> 依据：用户已批准「方案 A + 安全重扫策略」；`review/handoff-remove-py-decision-chain-20261106.md` 仅作背景，其行号（如 715-1119 / 424-429 / 519-528）**一律不沿用**，全部按 2026-11-07 当前工作树重新核对。
> 意图分类：**Mid-sized（边界优先）** + 决策主链变更 → 按高风险走 **DELIBERATE** 共识模式。

---

## 1. Context（当前工作树事实，逐条带 file:line）

### 1.1 自动模式 `play()` 的当前决策链

| 事实 | 当前证据 |
|---|---|
| `play()` 定义与主循环 | `utils/solver.py:243`、`utils/solver.py:278` |
| 每轮整盘 OCR | `utils/solver.py:415`（`complete_scan(cell_value, True)`） |
| Py 单格规则推理 `mine_clear1` | `utils/solver.py:424`（`try/except ValueError` 于 `:423-427`） |
| Py 子集推理 `mine_clear3_1` | `utils/solver.py:428` |
| 进展判据 `sum2/sum3 ==` 后才进 `number5_1` | `utils/solver.py:422`、`:430-431`、`:434` |
| 推理标记 10/11 的恢复逻辑 | `utils/solver.py:433-440`（`_pre_flags`） |
| `except ImportError: pass` 空吞 | `utils/solver.py:441-442` |
| 热力图刷新 | `utils/solver.py:446`；`_emit_heatmap` 定义 `utils/solver.py:459-482` |
| `finally: _flush_stats()` | `utils/solver.py:456-457` |

结论：自动模式当前是「Py 规则推理（A1-A4）→ 无进展才 `number5_1`」的**编排链**。用户批准的方案 A 要删除的是 `number5_1` 内部 `decide` 可用时控制流不可达的**重复 Python 决策链**，并把 `play()` 编排改为每轮直接进 `number5_1`。

### 1.2 `utils/probability.py` 的当前结构（本次重核的核心）

| 区块 | 当前行号 | 处置 |
|---|---|---|
| 预算/失败计数模块级状态 | `:44-56`（`DECISION_TIME_BUDGET`、`_DECISION_BUDGET_MULT:49`、`_DECIDE_FAIL_SEQ:51`、`decision_budget():54-56`） | 保留（兼容 API + 可观测计数） |
| `number5_1()` 入口（进度心跳） | `:469-482` | 保留 |
| `_number5_1_core()` 定义 | `:484` | 保留函数，改主体 |
| 预处理：`num10` 统计 / `bg` / `distanceTransform` / `clicks`+`clicks9` 划分 / 空盘早退重扫 | `:506-553`（早退重扫在 `:546-553`，含 `self.count += 1`） | **保留**：decide 输入准备 + 空盘安全重扫的现成范式 |
| C++ `decide` 分支 | `:569-713`；`native.has_decide()` 于 `:569`；首次 `decide` 于 `:572-578`；成功清零于 `:580` | 保留并改造失败出口 |
| 帮助模式单次 decide（不点击、写 `pos_dict_list`） | `:596-626`（`return cell_value` 于 `:626`） | **原样保留** |
| 自动模式 decide 循环（点击→`rescan_after_click`→再 decide） | `:628-704`（循环内二次 decide `:691-701`；无进展守卫 `:681-686`；`return` 于 `:704`） | **原样保留**，仅补统计/计数接线 |
| 循环内二次 decide 的 `except Exception` | `:699-701`：仅 `logger.warning` + `break`，**然后落到 `:704 return`** | 需与新失败语义对齐（见 Step 2） |
| 外层 `except Exception` | `:705-712`：`logger.error("…跌回 Python 链")` 后**无 return，控制流继续下行** | **静默回退的唯一入口，必须改** |
| **重复 Python 决策链主体** | **`:715-1120`**（起点 `limitation = 12` 于 `:715`；分组 `:743-787`；预算/枚举 `:789-917`；win_rate `:978-1048`；`process_bigger_situation` `:1050`；`_last_heatmap` 组装 `:1066-1071`；点击 `:1073-1110`（含 `data.json` 统计 `:1095-1110`）；`self.count = 0` `:1112`；全盘重扫 `:1114-1118`；`return` `:1120`） | **删除**（方案 A 选定，见第 5 节裁决 1） |
| 必须保留的兼容方法/纯函数 | `try_solve :1122-1155`、`_pbs_group_histograms :1157-1175`、`_pbs_convolve :1177-1187`、`_pbs_compute_dp :1189-1247`、`_pbs_compute_python :1249-1326`、`process_bigger_situation :1328-1494`、`_wr_memory_ok :1496-1534`、`win_rate :1536-1728`、`open_num5x5 :1730-1748`、`part_solve_single :1750-1837`、`part_solve :1839-1989` | **一个都不删**（测试与 bench 引用） |

> 与旧交接报告的行号差异（证明未沿用）：旧报告称 Py 链 `715-1119`，当前实际到 `:1120`；旧报告 `solver.py:424-429` 当前为 `:422-442`（多了 `_pre_flags` 与 `except ImportError`）；旧报告 `help():519-528` 当前为 `:519-528`（此段恰好未变，但仍逐字重读确认）。

### 1.3 帮助模式 `help()`（用户明确要求保留）

| 事实 | 当前证据 |
|---|---|
| `help()` 定义 / 预算乘数 2.0 / finally 恢复 1.0 | `utils/solver.py:484`、`:489-490`、`:558-560` |
| 首轮 `complete_scan(..., False)` | `utils/solver.py:514` |
| **2 轮 `mine_clear1`+`mine_clear3_1` + 末次 `mine_clear1`** | `utils/solver.py:519-524` ← **禁止删除** |
| 推理 `ValueError` → 警告退出 | `utils/solver.py:525-528` |
| `pos_dict_list` 为空才进最多 5 轮 `number5_1` | `utils/solver.py:530-546`（`flag==4` 警告于 `:543-546`） |
| 发射 `update_btn_list_signal` | `utils/solver.py:548` |
| `mine_clear1`/`number0`/`mine_clear3_1`/`number_3_1` 本体 | `utils/deduction.py:26-43`、`:45-106`、`:244-256`、`:108-242` |
| 帮助模式标雷/标安全提示（`pos_dict_list`） | `utils/deduction.py:55-72`、`:85-99`、`:132-152`、`:163-181`、`:186-206`、`:217-234` |

### 1.4 C++ 接线层与 UI 消费侧

| 事实 | 当前证据 |
|---|---|
| `native.decide()` / `has_decide()` / `tuned()` | `utils/native.py:93-119`、`:122-124`、`:140-142` |
| `native.decide` 旧 pyd `TypeError` 兼容回退（非静默 WARNING） | `utils/native.py:111-119` |
| `native.py` 文档字符串仍宣称「运行期任何异常…回退到纯 Python」 | `utils/native.py:7-11` ← **改后必须同步** |
| `main.py` 自动/帮助 `warning_signal` 已接线到弹窗 | `main.py:392-394`、`:409-411`；`warning_signal_2` 于 `:412` |
| `heatmap_signal → update_heatmap` / `update_btn_list_signal → update_btn_list` | `main.py:399`、`:408` |
| UI 分类纯函数（必须保留） | `main.py:80-99`（`_EXP_TO_KIND`）、`classify_heatmap_kind :102-128`、`classify_payload_kind :131-137`、`classify_decision_value_kind :152-189` |
| `main.py` 注释里引用 `probability.py` 的行号 | `main.py:81-86`、`:108-114`、`:157-164` ← 删 `:715-1120` 后**全部上移约 406 行，必须同步** |
| 热力图渲染 / 候选列表渲染 / 一次性 GUI 扫描 | `main.py:1162-1282`、`:1072-1152`、`:1036-1070` |
| README 自动回退承诺 | `README.md:61-63`、`README.md:55-100`；主循环/概率图 `README.md:129-145`、`:188-200` |

### 1.5 测试现状（重核，与旧报告「7 测试」不符）

- `tests/test_cpp_decide_wiring.py` 当前 **9 个**测试（`:148`、`:167`、`:186`、`:209`、`:244`、`:261`、`:271`、`:297`、`:321`），`_run_core` 于 `:108-143`，「防静默降级」断言于 `:138-142`（`_DECIDE_FAIL_SEQ[0] == 0`）。
- **当前没有任何 `decide` 失败/不可用用例** → 这是本优化最大的测试缺口（Step 5 补）。
- `tests/test_solver_core.py:1-3` 明确测 `number0`/`mine_clear1`/`number_3_1`/`open_num5x5` 等；`:157-192` `TestNumber0`、`:194-` `TestMineClear1` → 兼容 API 删除会直接打红。
- `tests/helpers.py:13-46` `make_solver`、`tests/helpers.py:49-64` `make_full_solver` → 测试构造契约必须保持。

### 1.6 自行复核发现的三个遗漏（analyst 等价替代产出，见第 14 节降级留痕）

1. **`data.json` 统计写入只存在于待删链** `utils/probability.py:1095-1110`（`_load_stats`/`_flush_stats`/`_stats_updates`），删链后统计断流，而 `utils/solver.py:171-189` 的定义与 `play()` `finally` 的 `_flush_stats()` 仍在 → 属「保留 I/O」范围，必须迁移接线，不能静默丢功能。
2. **`self.count` 清零只在 `utils/probability.py:1112`**；`count += 1` 在空盘早退 `:552`，`play()` 在 `utils/solver.py:283-286` 用 `count >= 3` 触发「请检查设置」并退出 → 删链后成功路径不清零会误触发警告，必须迁移。
3. **旧 pyd / `MSW_DISABLE_NATIVE=1` / `MSW_TRIAL_DECIDE` 未导出**：`utils/native.py:122-124` 的 `has_decide()` 为 False 时，`:569` 分支整个跳过，当前 `:715-1120` 是唯一决策路径；删链后该环境不再有生产决策兜底 → 行为变化必须显式写进 README 与 open-questions，不能假装没变。

---

## 2. Work Objectives

1. 自动模式删除「C++ `decide` 可用时不可达」的重复 Python 决策链，使单步决策唯一归属 `native.decide` 三层（OPTIMAL / 自适应前瞻 / pbs+PSEQ）。
2. `decide` 失败（异常 / 无效返回 / 不可用）改为**可观测错误 + 安全重扫 / 警告**，**绝不静默回退**到重复 Python 链。
3. 帮助模式**保留** `mine_clear1` / `mine_clear3_1` 及其标雷/确定安全提示，人类交互逻辑不退化。
4. 保留 I/O（`vision.py`、`complete_scan`、`rescan_after_click`、`data.json`/`game_stats.json` 统计）、UI（`main.py` 全部渲染与纯函数）、测试纯函数与必要兼容 API（`part_solve` / `win_rate` / `process_bigger_situation` / `_pbs*` / `open_num5x5` / `try_solve`）。
5. 产出可评审、可按步验收、可回滚的计划；**本轮不实现、不改源代码**。

## 3. Guardrails

### Must Have
- 3-6 个可执行步骤，每步带「解锁条件 = 前置产出存在且非空」、自身产出、可核验验收标准。
- 所有关键结论带当前 `file:line`（本文件第 1 节已完成）。
- 明确「帮助模式 Py 推理保留」「`decide` 失败不静默 fallback」。
- 含可测测试命令、最小反例 / 回归路径、注入必红负向自测、真实输入无 workaround 全量跑、回滚方案。
- 计划 ≥100 行；RALPLAN-DR 全字段；ADR 全字段；歧义裁决四要素齐备。
- 写范围收敛：`utils/probability.py`、`utils/solver.py`、`utils/native.py`（仅文档字符串）、`README.md`、`main.py`（仅注释行号）、`tests/test_cpp_decide_wiring.py`、`review/open-questions.md`。**不触碰** `utils/vision.py`、`utils/deduction.py`、`cpp/`、`ui/`、任何 `.pyd`。

### Must NOT Have
- 不删 `utils/deduction.py` 任何函数、不删 `utils/probability.py:1122-1989` 任何方法、不删 `main.py` 任何纯函数。
- 不改 `utils/native.py:93-142` 的函数行为；不改 C++ 源码、不重编译 `.pyd`。
- 不给 `decide` 失败路径接回 `part_solve` / `win_rate` / `process_bigger_situation`；不新增「静默 except: pass」。
- 不做架构重写、不把 `number5_1` 改成新类、不引入新依赖。
- 不越过批准门派工执行者；不群发广播；本 Planner 不写任何 `.py` 文件。

---

## 4. RALPLAN-DR 共识摘要

**模式：DELIBERATE**（理由：改决策主链 + 跨 4 文件 + GUI 可观测行为变化 + 存在旧 pyd 兼容面；需 pre-mortem 与扩展测试计划）。非 `--deliberate` 的 SHORT 模式在本任务不适用。

### Principles（5 条）
1. **单一决策权威**：自动模式每一步只由 `native.decide` 产出动作，第二套决策实现不并存。
2. **失败必须可见**：任何决策不可用都要留日志 + 用户可见信号 + 计数，禁止空输出 / 空 except 表达「无意见」。
3. **安全优先于连续**：决策不可信时宁可整盘重扫一轮再试，也不基于可疑盘面点击。
4. **帮助模式是独立产品面**：人类提示链路不因自动模式优化而删减。
5. **兼容面与生产面分离**：兼容 API / 纯函数保留给测试与 bench，但生产控制流不得再引用它们。

### Decision Drivers（前 3 条）
1. 用户已批准方案 A 与「安全重扫 + 显式提示、禁止静默回退重复 Python 链」（最高优先级，不可翻转）。
2. `utils/probability.py:705-712` 是当前唯一静默回退入口，且历史根因（漏 import `setting` 的 `NameError` 被宽 except 吞掉 → 点一格两下 / 两个弹窗）见 `:706-709` 逐字注释 → 必须消除该控制流。
3. 删链会连带影响统计写入（`:1095-1110`）、`count` 清零（`:1112`）、UI 注释行号（`main.py:81-86` 等）与旧 pyd 行为 → 边界必须逐项显式处理，不能「顺带」。

### 可行选项（≥2，含有界利弊）

| 选项 | 做法 | 有界利弊 |
|---|---|---|
| **A（选定）有界删除** | 删 `utils/probability.py:715-1120`；`decide` 分支改造为「成功走现有两分支；失败/不可用/无效 → 新 helper：ERROR 日志 + `warning_signal` + `_DECIDE_FAIL_SEQ` 计数 + `complete_scan` 全盘安全重扫 + 显式 return」；兼容方法全留 | 利：单一决策权威、静默回退根因消除、diff 最小、可逐文件 revert。弊：`has_decide()==False` 的环境（旧 pyd / `MSW_DISABLE_NATIVE=1`）失去生产决策兜底，需文档与 open-question 显式声明 |
| **B（否决）条件保留** | 保留 `:715-1120`，仅加 `if not native.has_decide():` 守卫，使其只在 decide 不可用时可达 | 利：旧 pyd 仍可玩、行为面变化最小。弊：重复链代码仍在，与「删除重复 Python 决策链」的批准范围不符；「不可用」与「运行期失败」两条语义分叉，未来仍可能被误接成静默回退；`main.py` 行号漂移、统计接线问题一个都没解决 |
| **C（否决）重写入口** | 把 `number5_1` 整体重写为纯 `decide` 薄封装，连带删预处理 `:506-553` 与预算设施 | 利：代码最短。弊：架构重设计，违反「缺省最小范围」；空盘早退重扫范式、预算/阈值兼容 API、帮助模式放宽参数全部要重做；回归面不可控 |

**否决理由记录**：B 的唯一收益（旧 pyd 可玩）可由 A + 「显式不可用警告」+ README 行为说明 + 整体 revert 检查点覆盖，代价是保留一整套与批准范围冲突的重复实现，故否；C 属过度规划且触碰冻结的预算/阈值设施，故否。**当前可行选项为 A 与 B 两个（非单一）**，选定 A。

---

## 5. 歧义裁决（每条四要素：候选解释 / 选择方案 / 依据 / 翻转条件）

### 裁决 1：删除边界
1. **候选解释**（≥2，含边界）：① 只删 `_number5_1_core` 尾部 `:715-1120`，`:1122-1989` 全部方法保留；② 连带删 `part_solve`/`win_rate`/`process_bigger_situation`/`_pbs*` 等「只被旧链调用」的方法体。
2. **选择方案**：①。删除后 `grep` 必须确认 `:1122-1989` 每个 `def` 仍在；生产控制流不再引用它们，但 `tests/test_solver_core.py`、`tests/helpers.py` 与 bench 的调用不受影响。
3. **依据**：任务书「保留 … 测试纯函数和必要兼容 API」；`tests/test_solver_core.py:1-3` 逐字列测 `number0`/`mine_clear1`/`open_num5x5`；交接文档 `review/handoff-remove-py-decision-chain-20261106.md:69-70` 亦记「win_rate/process_bigger_situation/_pbs* 方法体倾向保留（bench 引用）」。
4. **翻转条件**：若执行前确认 bench 已全量迁移到 `decide` 且 `tests/test_solver_core.py` 已重写并经用户批准，才允许二次清理；**本轮不删**。

### 裁决 2：`has_decide() == False` 算不算「`decide` 失败」
1. **候选解释**：① 算，统一走「不可用」处理；② 不算，仅「运行期抛异常」算失败，不可用时保留旧 Python 链（等价选项 B）。
2. **选择方案**：①。`utils/native.py:122-124` 的 `has_decide()` 为 False（`.pyd` 缺失、`MSW_DISABLE_NATIVE=1`、旧 pyd 无 `decide` 导出）时，也走同一 helper：可观测错误 + 安全重扫 + 警告，**不执行任何 Python 枚举**。
3. **依据**：用户「C++ decide 失败改为可观测错误+安全重扫/警告，不静默回退重复 Python 链」；若把「不可用」排除在外，等于保留一条静默回退通道，与 `utils/probability.py:706-709` 记录的历史根因同构。
4. **翻转条件**：若组织者转呈用户明确确认「旧 pyd / `MSW_DISABLE_NATIVE=1` 环境仍须可自动扫雷」，则改选选项 B（加显式 `if not has_decide()` 守卫 + 独立 WARNING，仍禁止异常路径静默回退）；此项列入 open-questions 由组织者转呈，**Planner 不擅自定夺**。

### 裁决 3：「安全重扫」的具体语义
1. **候选解释**：① 全盘 `complete_scan`（与 `utils/probability.py:546-553` 空盘早退、`:1114-1118` 旧链收尾同范式）；② `rescan_after_click` 局部重扫（`:678` 的现成调用）。
2. **选择方案**：①。helper 内重建全 9 棋盘后调 `self.complete_scan(...)`，返回重扫后的 `cell_value` 并**显式 return**，交回 `play()` 主循环下一轮重新决策；不点击、不修改盘面、不写 `_last_heatmap` 新值。
3. **依据**：失败路径没有发生点击，局部重扫缺少「变更集」输入，语义不成立；全盘重扫是当前代码已验证的安全范式；「安全」= 不基于可疑盘面产生动作。
4. **翻转条件**：若压测给出「全盘重扫导致每轮 >X 秒卡顿」的实测读数且有可验证的局部等价方案，另开议题；本轮不做。

### 裁决 4：「可观测错误」的落点
1. **候选解释**：① 仅 `logger.error`（沿用 `:711`）；② `logger.error` + `warning_signal` 弹窗 + `_DECIDE_FAIL_SEQ` 计数 + 状态条文案；③ 仅状态条。
2. **选择方案**：②。ERROR 日志给开发排障；`warning_signal`（`utils/solver.py:65` 定义、`main.py:392-394` / `:409-411` 已接线）给用户；`_DECIDE_FAIL_SEQ`（`:51`）连续失败计数成功后清零（`:580`）；自动页状态条走 `main.py:637-643` `set_solver_status`。帮助模式沿用既有 `flag==4` 的「请检查设置」警告（`utils/solver.py:543-546`），不新增弹窗风暴。
3. **依据**：任务书「可观测错误+安全重扫/警告」；`main.py` 的 `warning_signal` 已是现成用户通道，无需改 UI 代码。
4. **翻转条件**：若 GUI 冒烟证明弹窗在自动模式打断连续对局（多弹窗复发），降级为 ③（仅状态条 + ERROR 日志 + 计数），并在报告留痕。

### 裁决 5：「`decide` 失败」是否包含「返回无效坐标」
1. **候选解释**：① 仅异常；② 异常 + 返回 `None` / `x<1 or y<1`（`:579`、`:702-703`）+ 循环内二次 decide 抛异常（`:699-701`）。
2. **选择方案**：②。统一归一为「决策不可用」：首轮异常、首轮无效返回、循环内二次异常、循环内无效返回、`has_decide()==False` 五类全部走 helper。自动模式 decide 循环**成功完成**后仍按 `:704` 正常返回（不视为失败）。
3. **依据**：交接 `review/handoff-remove-py-decision-chain-20261106.md:114` R7 已把「decide 返回无效坐标」列为需保留的警告路径；归一语义避免「break 后落到已删区域」的悬空控制流。
4. **翻转条件**：若用户要求「无效坐标 = 正常放弃本轮决策、不报错」，则把无效返回从失败集合移出，仅保留「正常 break + 下一轮重扫」；此项列入 open-questions。

### 显式零项
- 「帮助模式保留 `mine_clear1`/`mine_clear3_1`」：用户措辞明确，**未识别到多解读歧义**，不列入裁决，直接作为 Guardrails Must Have 与 Step 3 验收项。
- 除上述 5 条外，本轮**未识别到其他多解读歧义**（显式为零，避免 silently 按字面序落地）。

---

## 6. Task Flow（目标态）

```
自动 play()  [utils/solver.py:243]
  弹窗/新局管理 (:288-413) → complete_scan (:415) → number5_1 (:434 改为每轮直调)
     └─ _number5_1_core (:484)
          预处理 (:506-553，空盘早退重扫保留)
          ├─ has_decide()==True → decide (:572)
          │     ├─ is_play=False → 单次 decide + pos_dict_list (:596-626)  ★原样
          │     ├─ is_play=True  → decide 循环 (:628-704)  ★原样 + 统计/count 接线
          │     └─ 任一失败/无效 → helper：ERROR + warning_signal + 计数
          │                          + complete_scan 全盘重扫 + 显式 return
          └─ has_decide()==False → 同一 helper（裁决 2）
  → _emit_heatmap (:446 / :459-482)

帮助 help()  [utils/solver.py:484]
  complete_scan (:514) → mine_clear1/mine_clear3_1 ×2 + mine_clear1 (:519-524) ★保留
  → pos_dict_list 为空才 5 轮 number5_1 (:530-546) → update_btn_list_signal (:548)
```

---

## 7. Detailed TODOs（6 步；按构建顺序，依赖与解锁条件逐条显式）

> 通用纪律（用户 `[28f30943]`）：**每步验收通过立即 `git commit` 建检查点再放行下一步**；每步只改声明的写范围；改动前完整重读目标文件当前全文（`[f46f675f]`）；用 `edit` 逐处修改而非命令行写盘。

### Step 1 — 接口/契约层：定义「决策不可用」统一结果（类型与接口先行）
- **依赖**：无（起始步）。
- **解锁条件**：Step 0 检查点 sha 已记录；`utils/probability.py` 当前全文已重读。
- **改动范围**：`utils/probability.py` 新增模块级/类内 helper（建议名 `_decide_unavailable(self, cell_value, exc=None, where="")`），不删任何现有代码。
- **自身产出**：helper 定义 + 常量/文案（如 `DECIDE_UNAVAILABLE_MSG`）+ 一个独立单测；helper 语义固定为：① `logger.error(..., exc_info=bool(exc))` 且文案含「不回退」；② 递增 `_DECIDE_FAIL_SEQ[0]`（`utils/probability.py:51`）；③ `self.warning_signal.emit(...)`（自动与帮助共用，定义 `utils/solver.py:65`）；④ 重建全 9 棋盘并 `self.complete_scan(...)`；⑤ `self.count += 1` 语义与 `:552` 对齐；⑥ **返回重扫后的 `cell_value`**，调用方必须 `return`。
- **验收标准**：`python -m unittest tests.test_cpp_decide_wiring -v` 现有 9 用例全绿；新增 helper 单测断言上述 6 项各一次；`git status` 显示只动 1 个 `.py`。

### Step 2 — 核心逻辑：删除重复 Python 决策链并封闭失败出口（`utils/probability.py`）
- **依赖**：Step 1（helper 已存在且被单测覆盖）。
- **解锁条件**：Step 1 检查点 sha + helper 单测绿 + 重读 `utils/probability.py` 当前全文。
- **改动范围**：`utils/probability.py` 的 `_number5_1_core` 主体（`:569-1120`）。
- **自身产出**：① 删除 `:715-1120` 整段；② `:705-712` 外层 `except Exception` 改为 `return self._decide_unavailable(cell_value, exc=e, where="decide")`；③ `:579` 成功判定失败分支（无效返回）改走 helper；④ `:699-701` 循环内二次 decide 的 `except` 改走 helper（**不能只 `break` 后落 `:704`**）；⑤ `:702-703` 无效返回改走 helper；⑥ `:569` 的 `if native.has_decide():` 补 `else: return self._decide_unavailable(cell_value, where="has_decide=False")`；⑦ 把 `:1095-1110` 的 `data.json` 统计写入与 `:1112` 的 `self.count = 0` 迁移进 decide 成功循环（`:663-676` 每次点击后 / `:704` 返回前），**保留 I/O 与误警告防护**；⑧ `:1122-1989` 所有方法逐字保留。
- **验收标准**：
  - `python -m py_compile utils/probability.py` 通过；
  - `git grep -n -E "limitation = 12|跌回 Python 链|_load_stats|self\.count = 0" -- utils/probability.py` 无「旧链残留」命中（**配阳性对照**：`git grep -n -E "def part_solve|def win_rate|def process_bigger_situation|def open_num5x5|def try_solve" -- utils/probability.py` 必须 5 项全中，见 L1-14）；
  - 全文 `def` 清单与 Step 1 前逐字比对，`:1122-1989` 零删除。

### Step 3 — 集成层：`play()` 编排简化，`help()` 逐字保留（`utils/solver.py`）
- **依赖**：Step 2（生产链已单一化）。
- **解锁条件**：Step 2 检查点 sha + Step 2 grep 门绿 + 重读 `utils/solver.py` 当前全文。
- **改动范围**：`utils/solver.py` 的 `play()` `:422-446`；`help()` `:487-488` 仅改过期注释。
- **自身产出**：① 删除 `:422-442`（`sum2` / `mine_clear1` / `mine_clear3_1` / `sum3` 比较 / `_pre_flags` 恢复 / `except ImportError: pass`），改为 `cell_value = self.number5_1(cell_value)`；② 保留 `:415` `complete_scan` 与 `:446` `_emit_heatmap`；③ **`help()` `:519-524` 的 `mine_clear1`/`mine_clear3_1` 调用逐字保留**；④ `:487-488` 注释里的「只影响 Python 回退链的预算门」改写为「预算乘数保留兼容，生产决策由 decide 承担」；⑤ `:558-560` finally 恢复原样。
- **验收标准**：`python -m py_compile utils/solver.py` 通过；`git grep -n -E "mine_clear1|mine_clear3_1" -- utils/solver.py` **只剩 `help()` 内 3 处调用**（`play()` 内零命中）；`tests/test_cpp_decide_wiring.py` 9 用例全绿；`tests/test_solver_core.py` 全绿。

### Step 4 — UI / 文档一致性层（注释与承诺同步，不改算法）
- **依赖**：Step 3（行号已稳定，才能重算注释）。
- **解锁条件**：Step 3 检查点 sha + `utils/probability.py` 新行号已确定。
- **改动范围**：`main.py` 注释行号、`utils/native.py:7-11` 文档字符串、`README.md`。
- **自身产出**：① `main.py:81-86`、`:108-114`、`:157-164` 中引用的 `probability.py:NNN` 按新行号重算（**纯函数体 `classify_value_kind` / `classify_heatmap_kind` / `classify_decision_value_kind` 一行不改**）；② `utils/native.py:7-11` 改为「热点方法运行期异常仍回退纯 Python；`decide` 决策层失败/不可用不回退，由 `probability.py` 统一可观测错误 + 安全重扫」；③ `README.md:61-63` 自动回退承诺加同样限定；④ `README.md:129-145` 主循环图删去 `mine_clear1`/`mine_clear3_1` 自动分支或标注「仅帮助模式」；⑤ `README.md:188-200` 概率决策图标注「C++ decide 唯一决策路径；旧 Python 链已移除」；⑥ `README.md:87-100` 补一句：旧 pyd 备份 / `MSW_DISABLE_NATIVE=1` 在改动后**不再提供决策兜底**，恢复旧行为需同时 revert 代码。
- **验收标准**：`git diff -- main.py` 只含注释行（无代码行增减）；`python -m py_compile main.py utils/native.py` 通过；`git diff --stat` 中 `utils/vision.py`、`utils/deduction.py`、`cpp/`、`ui/`、`.pyd` 零改动。

### Step 5 — 测试层：补齐失败面 + 注入必红负向自测 + 真实输入全量跑
- **依赖**：Step 2、Step 3、Step 4（行为与注释均已定稿）。
- **解锁条件**：前三步检查点 sha 齐备；`tests/test_cpp_decide_wiring.py` 当前全文已重读。
- **改动范围**：`tests/test_cpp_decide_wiring.py`（可新增 `tests/test_decide_unavailable.py`）。
- **自身产出**：见第 9 节「扩展测试计划」全部用例；同步 `:14`、`:138-142` 里「跌回 Python 链」的过期文案。
- **验收标准**：新增 ≥4 类失败用例全绿；注入必红负向自测**必须红**；`python -m unittest discover -s tests -v` **真实输入无 workaround 全量跑**全绿；现有 9 个 `test_cpp_decide_wiring` 与 `test_solver_core` 零改写断言（不得为通过测试而放宽断言，见全局 AGENTS）。

### Step 6 — 验收 / 回滚 / 移交层
- **依赖**：Step 5（全量门绿）。
- **解锁条件**：Step 5 检查点 sha + 全量测试输出留存。
- **自身产出**：e2e 对拍读数（标注样本量/CI/口径）、GUI 冒烟记录、回滚说明落执行者报告、open-questions 回写、最终 `git commit`。
- **验收标准**：第 12 节 P0 全绿、P1 无未解释回归、P2 已排期；组织者收到「可派工/需补评审」的明确结论。

**依赖成环检查**：Step1 → 2 → 3 → 4 → 5 → 6 为单向链，无环、无「依赖待澄清」项；Step 4 与 Step 5 都依赖 Step 3 但互不依赖，可并行（写范围不重叠：`main.py`/`native.py`/`README.md` vs `tests/`）。

---

## 8. Pre-mortem（DELIBERATE：3 个失败场景）

| # | 失败场景（假设已经上线并出问题） | 最可能根因 | 预防 / 检出 |
|---|---|---|---|
| PM-1 | 全量测试大面积红：`AttributeError: 'Solver' object has no attribute 'part_solve'` / bench 全崩 | 执行者按交接文档 D2「倾向保留」理解成「可删」，把 `utils/probability.py:1122-1989` 一起清掉 | Step 2 验收的 `def` 清单逐字比对 + 阳性对照 grep；写范围显式排除这些方法；Step 5 保留 `tests/test_solver_core.py` 全绿门 |
| PM-2 | `decide` 抛异常时程序崩溃或又出现「点一格两下 / 两个弹窗」 | `:705-712` 的 `except` 只把日志文案从「跌回 Python 链」改成「不回退」却**没加 `return`**，控制流继续下行到已删除区域（尾部变量 `pos`/`confidence`/`_prob` 未定义 → `UnboundLocalError`）；或 `:699-701` 只 `break` 后落到 `:704` 但盘面已脏 | Step 2 强制「helper 显式 return」+ Step 5 注入必红负向自测（把 `return` 改成 `pass` 的变异体必须红）；断言失败路径 `pyautogui.click` 调用数为 0 |
| PM-3 | 帮助模式红雷标记 / 绿格安全提示消失，用户报「帮助坏了」 | 执行者沿用旧交接 D3（`:72-79`）把 `utils/solver.py:519-524` 的 `mine_clear1`/`mine_clear3_1` 一并删了 | 任务书与 Guardrails 双处写明「帮助模式必须保留」；Step 3 验收 `git grep` 断言 `help()` 内 3 处调用仍在；GUI 冒烟帮助模式 1 次核对 `update_btn_list`（`main.py:1072-1152`）仍渲染 `is_mine`/`is_best` 色块 |

附加观察（非三场景，但列入 P1 监控）：删链后 `num10` 恒 0 会改变 `decide` 输入语义，交接 `review/handoff-remove-py-decision-chain-20261106.md:109` R2 已标注「枚举量增大、速度需实测」→ e2e 对拍必须同时记录单步耗时，不能只看胜率。

---

## 9. 扩展测试计划（单元 / 集成 / e2e / 可观测性）

### 9.1 单元（Step 1 / Step 5）
- helper 六项语义各一条断言（ERROR 日志 / 计数 / 信号 / 重扫 / `count` / 返回值）。
- **R5① 解析/分类类 ≥3 元素输入断言**（保留 `main.py:90-99` 纯函数的回归锁）：
  `[classify_value_kind(x) for x in ["由(1, 2)得出", "胜率计算得出", "随机选择。", "未知来源"]]`
  断言依次 = `[VALUE_KIND_EXACT, VALUE_KIND_WIN_RATE, VALUE_KIND_PRIOR, VALUE_KIND_UNKNOWN]`（4 元素输入，满足 ≥3）。
- `decide` 成功后 `_DECIDE_FAIL_SEQ[0]` 清零（对齐 `utils/probability.py:580`）。

### 9.2 集成（Step 5，最小反例 4 类，全部走真实 `_number5_1_core`）
| 反例 | 注入方式 | 必断言 |
|---|---|---|
| F1 首轮 `decide` 抛 `RuntimeError` | `mock.patch.object(native, "decide", side_effect=RuntimeError("boom"))` | 无 click / ERROR 日志含「不回退」 / `complete_scan` 调用 1 次 / 返回重扫盘面 / `_DECIDE_FAIL_SEQ==1` |
| F2 循环内二次 `decide` 抛 | 首轮返回合法 + `_rescan_opening` 推进 `num9`，第二次 `side_effect` 抛 | 不再点击 / 不落到已删链 / 走 helper |
| F3 `has_decide() == False` | `mock.patch.object(native, "has_decide", return_value=False)` | 走 helper；`part_solve`/`win_rate` 打桩为「被调用即抛」的探针且**调用数 0** |
| F4 `decide` 返回无效坐标 | `side_effect=[{"x":-1,"y":-1,...}]`（复用 `tests/test_cpp_decide_wiring.py:61-65` 的 `_invalid_response`） | 走 helper；`pyautogui.click` 0 次 |

**回归路径（必须保持绿）**：`tests/test_cpp_decide_wiring.py` 现有 9 用例（`:148`、`:167`、`:186`、`:209`、`:244`、`:261`、`:271`、`:297`、`:321`）——尤其 `:209-242` 的「热力图末轮契约」与 `:238-242` 的 decide 参数断言（`wr_threshold=8.0` / `node_cap=2_000_000` / `is_play=True`），帮助模式 `:186-204` 的「单次、不点击、去重」不得被改动。

### 9.3 注入必红负向自测（R5②，闸门必配）
- 变异体 V1：把 helper 改成「失败时原样 `return cell_value`、不 emit warning、不重扫」→ F1/F3 必须红。
- 变异体 V2：把 `:705-712` 的 `return self._decide_unavailable(...)` 改回 `pass` → F1 必须红（或 Step 2 的静态 grep 门红）。
- 变异体 V3：把 `utils/solver.py:519-524` 的 `mine_clear1`/`mine_clear3_1` 删掉 → Step 3 的 `git grep` 门必须红。
- 每个变异体与断言类别一一对应（L1-15），记录「注入→红→还原→绿」三步读数。

### 9.4 可观测性
- `assertLogs(probability.logger, level="ERROR")` 捕获文案含「不回退 / 安全重扫」；连续失败计数递增、成功后清零。
- `warning_signal` 触发次数（自动/帮助各 1 次上限，防弹窗风暴）。
- `main.py:637-643` 状态条文案、`main.py:1162-1282` 热力图在失败帧不产生新 `best`（沿用最近一次或清空，按 helper 约定）。
- GUI 冒烟确认无「一个格子弹两个弹窗」复发（历史根因见 `utils/probability.py:706-709`、`main.py:1087-1094`）。

### 9.5 e2e / 真实输入无 workaround 全量跑（R5③）
- `sim/eval_harness.py play_one`（`is_play=True`）同 seed 对拍：记录胜率、单步耗时、样本量、CI 口径。交接 `:123-125` 预估「删 Py 推理后 ≈ 压测口径（40%），预期高于旧编排链（~20%）」——**这是外推，必须标注「估算/量级，来源=交接文档历史读数，本轮未实测」**；若反而下降，先查 R2（`num10` 缺失导致枚举膨胀），不得直接归因代码。
- GUI 冒烟：自动模式 10 局 + 帮助模式 1 次，核对热力图、红雷/绿格提示、统计栏、无双弹窗。

### 9.6 可测命令清单（执行者逐步跑）
```bat
python -m py_compile utils\probability.py utils\solver.py utils\native.py main.py
python -m unittest tests.test_cpp_decide_wiring -v
python -m unittest tests.test_solver_core -v
python -m unittest discover -s tests -v
git grep -n -E "limitation = 12|跌回 Python 链" -- utils\probability.py
git grep -n -E "def part_solve|def win_rate|def process_bigger_situation|def open_num5x5|def try_solve" -- utils\probability.py
git grep -n -E "mine_clear1|mine_clear3_1" -- utils\solver.py
git diff --stat
```
> 零命中结论一律配阳性对照（L1-14）；`git grep` 用 `-E` 让 `|` 生效（L1-13）；Windows 下 `>` 重定向写 UTF-16LE（L1-09），测试输出直接看终端或让执行者用 `python -m unittest ... 2>&1` 在终端读，不落 UTF-16 文件。

---

## 10. 回滚方案

1. **检查点**：Step 0 记录当前 `git rev-parse HEAD`；Step 1-6 每步验收通过即 `git commit`（用户 `[28f30943]` 分块检查点纪律），sha 写入执行者报告。
2. **单步回滚**：`git revert <step-sha>`；或按文件 `git checkout <step0-sha> -- utils/probability.py utils/solver.py`。写范围仅 6 个文件 + tests，逐文件可还原。
3. **整体回滚**：`git checkout <step0-sha> -- utils/probability.py utils/solver.py utils/native.py main.py README.md tests/test_cpp_decide_wiring.py`；`utils/vision.py` / `utils/deduction.py` / `cpp/` / `ui/` / `.pyd` 本就未改，无需处理。
4. **二进制回滚的边界（必须显式告知用户）**：`README.md:87-100` 的 `.pyd` 备份（`bak_prehelp` / `old_locked` / `bak_baseline`）与 `MSW_DISABLE_NATIVE=1` 在改动后**只影响热点方法与 `tuned()`，不再恢复决策兜底**；旧 pyd 无 `decide` 导出会走 helper 的「不可用」分支。要恢复「旧 pyd 也能自动扫雷」的完整旧行为，必须**同时 revert 本计划的代码改动**，不能只换 `.pyd`。
5. **运行期应急**：若 GUI 大面积异常，先协作式停止（`main.py:824-845` `_stop_solver_thread`），再整体回滚；不杀用户进程（L1-20 / `[33010f3a]`）。
6. **数据面**：`data.json` / `state/game_stats.json` / `state/time_budget.json` / `data_time.json` 不在写范围内；Step 2 已把统计写入迁移进 decide 循环，回滚后旧链写回，无数据格式变更。

---

## 11. ADR（全字段）

- **Decision**：采用方案 A 有界删除。自动模式 `utils/probability.py:715-1120` 的重复 Python 决策链整体移除；`_number5_1_core` 的 `decide` 分支成为唯一决策出口；所有失败/不可用归一为「可观测错误 + 全盘安全重扫 + 警告 + 显式 return」；`utils/solver.py:422-442` 的 `play()` 编排简化为每轮直调 `number5_1`；`help()` 的 `mine_clear1`/`mine_clear3_1` 与 `utils/deduction.py` 全部保留；`utils/probability.py:1122-1989` 兼容 API 与 `main.py` UI 纯函数全部保留。
- **Drivers**：① 用户已批准方案 A + 安全重扫策略；② `utils/probability.py:705-712` 是静默回退唯一入口且为历史双弹窗根因（`:706-709`）；③ 旧报告行号与当前工作树已漂移，必须重核；④ 删链连带统计/count/UI 注释/旧 pyd 四面影响需显式处理。
- **Alternatives considered**：B 条件保留（加 `if not has_decide()` 守卫，旧 pyd 仍可玩）；C 重写 `number5_1` 为薄封装（连带删预处理与预算设施）；以及「保留现状只改日志文案」。三者否决理由见第 4 节与第 5 节裁决 2。
- **Why chosen**：A 在「删除重复链 + 禁止静默回退」两条用户硬约束上闭合得最彻底，diff 收敛到 1 个函数体 + 1 段编排 + 注释/文档，可逐文件 revert；B 保留了与批准范围冲突的重复实现；C 属架构重设计且触碰冻结的预算/阈值设施。
- **Consequences**：
  - 正面：单一决策权威；失败可见；自动模式决策节奏与压测口径一致；代码面减少约 406 行（**量级估算：按 `:715-1120` 行数差外推，未扣除空行/注释**）。
  - 负面/需接受：`has_decide()==False` 环境失去生产决策兜底（改为显式不可用警告）；`data.json` 统计与 `count` 需迁移接线；`main.py` / `README.md` / `utils/native.py` 注释承诺需同步；`num10` 恒 0 可能增大枚举量（速度待实测）。
  - 中性：`utils/probability.py:1122-1989` 变为「仅测试/bench 使用」的兼容层，不再被生产控制流引用。
- **Follow-ups**：① 组织者转呈用户确认裁决 2 / 裁决 5 的翻转条件（旧 pyd 是否仍需可玩、无效坐标是否算失败）；② 补独立 analyst / critic 评审（本轮 analyst 等价替代由 Planner 自行完成，见第 14 节）；③ e2e 实测 `num10=0` 下的单步耗时与胜率，标注样本/CI；④ 若确认 bench 全量迁移，再评估二次清理兼容层；⑤ 更新 `README.md` 主循环/概率图后跑一次文档链接自检。

---

## 12. P0 / P1 / P2 验收标准（组织者判定用）

### P0 — 阻断项，缺一不可（全绿才允许派工合并）
- [ ] 本 Planner 未改任何源代码；计划报告 `review/remove-py-decision-plan.md` ≥100 行且所有关键结论带当前 `file:line`。
- [ ] 自动模式 `decide` 失败/不可用**不点击、不静默**：4 类最小反例全绿，`pyautogui.click` 调用数 0。
- [ ] 帮助模式 `utils/solver.py:519-524` 的 `mine_clear1`/`mine_clear3_1` 逐字保留；`utils/deduction.py` 零改动。
- [ ] `utils/probability.py:1122-1989` 兼容方法零删除；`main.py` 的 `classify_*` 纯函数零改动；I/O（`vision.py` / `complete_scan` / 统计）保留且 `data.json` 写入已迁移。
- [ ] `python -m py_compile` 四个文件通过；`python -m unittest discover -s tests -v` **真实输入无 workaround 全量跑**全绿。
- [ ] 注入必红负向自测 V1/V2/V3 全部「注入→红→还原→绿」有读数。
- [ ] `git grep` 零残留门 + 阳性对照门 + `git diff --stat` 写范围核对通过。
- [ ] 计划已落盘且完成广播 `de_broadcast(wake:true)` 直投组织者 `session-d14dd270-3b10-432a-b410-4c46d544144f`（subject「planner 完成」）。

### P1 — 功能与可观测项
- [ ] 现有 9 个 `test_cpp_decide_wiring` 用例 + `test_solver_core` 全绿，断言零放宽。
- [ ] ERROR 日志 / `warning_signal` / `_DECIDE_FAIL_SEQ` / 状态条四项可观测，无弹窗风暴、无「两个弹窗」复发。
- [ ] sim 同 seed 对拍：胜率无未解释回归；读数自带样本量、CI、口径标注（外推标「估算/量级」）。
- [ ] GUI 冒烟：自动 10 局 + 帮助 1 次，热力图、红雷/绿格提示、统计栏正常。
- [ ] `README.md:61-63` / `utils/native.py:7-11` / `main.py` 注释行号与改动后代码一致。

### P2 — 排期项（不阻断本轮）
- [ ] 独立 analyst / critic 补评审（`### Open Questions` 条目回写）。
- [ ] `num10=0` 枚举膨胀的耗时实测与是否需要 C++ 侧标雷输出评估。
- [ ] 兼容层二次清理的可行性评估（需 bench 迁移完成 + 用户批准）。
- [ ] `till_now_winrate` / `checked` / `_wr_nodecap_hits` 等仅兼容层状态是否标记 deprecated。

---

## 13. 开放问题（集中跟踪；同步写入 `review/open-questions.md`）

- [ ] 旧 pyd / `MSW_DISABLE_NATIVE=1` / 无 `decide` 导出环境是否仍需「可自动扫雷」？ — 决定裁决 2 是否翻转到选项 B；影响 README 承诺与回滚说明。
- [ ] `decide` 返回无效坐标是否计入「失败」（当前裁决 5 选「计入」）？ — 影响帮助模式 `flag==4` 警告路径与自动模式重扫频率。
- [ ] 自动模式失败帧的 `warning_signal` 弹窗是否会打断连续对局？ — 决定裁决 4 是否降级为「仅状态条」；需 GUI 冒烟读数。
- [ ] `num10` 恒 0 后 `decide` 枚举量增大的实测幅度？ — 影响单步耗时与是否需要 C++ 侧补标雷输出（超本轮范围）。
- [ ] 兼容层（`utils/probability.py:1122-1989`）在 bench 迁移后是否二次清理？ — 需用户另行批准，本轮不动。

## 14. 降级留痕 / 未完成项 / 复跑命令

| 项 | 层级 | 事实与处置 | 复跑命令 / 说明 |
|---|---|---|---|
| codegraph 索引 | L3 工具不可用 | `mcp__codegraph__codegraph_explore` 返回「isn't indexed (no .codegraph/)」。改 `read` 逐文件重读（已覆盖 `solver.py` / `probability.py` 全文 1989 行 / `main.py` / `deduction.py` / `native.py` / `README.md` / 两个测试 / helpers）。**未编造任何调用关系** | 若需图查询：在仓库根执行 `codegraph init -y` 后重跑 `codegraph_explore(query="number5_1 decide play help", projectPath="D:\python\projects\minesweeper_help")` |
| 探查子会话 | item 级 skipped | 两个 `subagent`（代码核对 / analyst 缺口分析）均 `failed before it finished`、无 closing message；按 C4 连败不重试、按用户「重试上限 2 次」纪律不再第 3 次派活。改由本 Planner 用 `read` 自行完成核对与缺口分析（第 1.6 节三项遗漏即自复核产出） | 组织者如需独立复核：重新 `spawn` 一个 analyst 子会话并显式传 `evidencePath`；或转 critic 评审本报告 |
| analyst 咨询 | item 级 skipped（等价替代） | 未获得独立 analyst 输出；缺口分析由 Planner 自行执行，**未经第二人复核**，已在 ADR Follow-ups 与 open-questions 显式申报 | 补评审时把本报告第 1.6 / 5 / 8 节作为输入 |
| `de_session spawn` | L3 | 本次工具面未提供，按「未列工具视作不可用」用 `subagent` 替代（亦失败）→ 最终用内置 `read` | — |
| 用户访谈 | 未发起（合规） | 本轮**未**调用 `ask_user_question`：任务书与组织者广播已给出全部偏好/优先级（方案 A、帮助模式保留、失败策略、产出路径、完成广播对象），无剩余偏好问题；**未拿任何代码库事实问用户**。歧义按澄清协议以 open-questions 形式上呈组织者转呈，不擅自拍板 | 组织者转呈用户裁决第 13 节 5 项 |
| skip 的检查 | R2 | 独立 analyst 咨询、独立 critic 评审、e2e 实跑、GUI 冒烟均为**实施阶段**动作，本 Planner 只读规划，未执行；不计入通过项，已列 P1/P2 与 ADR Follow-ups | 由执行者按第 9.6 节命令复跑 |

---

## 15. Final Checklist（Planner 自核）

- [x] 只就偏好问过用户？——本轮无需提问（偏好已由任务书/组织者给全），代码库事实零提问。
- [x] 3-6 步带验收标准？——6 步，每步含依赖/解锁条件/产出/验收标准；依赖成环检查通过。
- [x] 任务书明确要求产出计划？——是（「制定方案 A 的有界代码结构优化计划」）。
- [x] 移交前经组织者转呈用户批准？——计划已落盘 + 完成广播直投组织者；**批准回传前不派工执行者**（本会话保留，不 spawn）。
- [x] 计划已存指定路径？——`review/remove-py-decision-plan.md`。
- [x] 开放问题入 open-questions？——第 13 节 + `review/open-questions.md`。
- [x] 共识模式有 principles/drivers/options 摘要与 ADR？——第 4 节（DELIBERATE / 5 原则 / 3 驱动 / 3 选项含否决理由）、第 11 节 ADR 六字段齐。
- [x] DELIBERATE 有 pre-mortem + 扩展测试计划？——第 8 节 3 场景、第 9 节单元/集成/e2e/可观测性 + 注入必红 + 真实输入全量跑。
- [x] 多解读需求【歧义裁决】四要素齐？——5 条逐条「候选解释 / 选择方案 / 依据 / 翻转条件」，并显式记「帮助模式无歧义」为零项。
- [x] ≥100 行、关键结论带当前 `file:line`、明确不删 help 模式 Py 推理、明确 C++ 失败不静默 fallback、含可测命令/最小反例/回滚/不改源代码？——全文满足。
- [x] R1 状态自报：`DELIVERED_WITH_EVIDENCE`（已落盘 + 已广播），不自报 `DONE`。
