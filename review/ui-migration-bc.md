# 桌面端 UI 迁移 B+C 接线条实施报告（executor）

> 生成：2026-11-07 · 角色：executor（MEDIUM 档位）
> 范围（硬边界，**已遵守**）：`main.py`、`tests/test_ui_migration.py`（新增）、本文件。
> **未修改**：`ui/preview_popup.py`（批 A 只读模块，mtime 仍是 2026/11/7 9:32:27）、`ui/window.py` / `ui/window.ui`（pyuic 生成物）、`utils/**`（solver/算法零改动）、`cfg.json`。
> 上游：`review/ui-migration-explore.md`（勘察）、`review/ui-design-spec.md`（设计规范）、`review/ui-migration-implementation.md`（首批 A+B+C 实施记录）。

## 0. 交付摘要

| 批次 | 本次落点（file:line） | 状态 |
|---|---|---|
| B | `main.py:570-629` `_build_compact_ui`（状态条/决策卡/图例/候选详情，运行期 `insertWidget` 注入） | 完成（复核 + 接线修正） |
| A 接线 | hover：`main.py:731-749`；focus：`main.py:192-226`；close：`main.py:751-759` + `main.py:373` | 完成（**focus 通道与 close 通道为本轮新增**） |
| C | `main.py:80-88`（exp 映射补全）、`:102-127`（heatmap 分类修正）、`:152-189`（新增 decision 分类）、`:1262`（接线） | 完成（**修正了首批报告的 P0-边界-1**） |
| 测试 | `tests/test_ui_migration.py`（新增 34 用例，offscreen） | 完成 |
| 报告 | `review/ui-migration-bc.md`（本文件） | 完成 |

**相对首批（`ui-migration-implementation.md`）的真实增量**，逐条列明（不重复描述已存在内容）：

1. **修掉首批遗留的 `uniform_prior` 缺口**：首批 `classify_heatmap_kind` 对「`prob` 为空」一律返回 `unknown`，但 `utils/probability.py:718-741` 的随机分支**恰好在 `prob` 为空时**发出 `confidence = 1 - (a-num10)/len(clicks9)` 的均匀先验。于是 C 批要求的 `uniform_prior` 分类在热力图/决策卡路径上**从未真正产生过**。本轮改为按 `best` 是否存在于空 `prob` 中区分（`main.py:102-127`）。
2. **新增 `classify_decision_value_kind`（`main.py:152-189`）**：决策卡展示的是 `best_conf`，而 `best_conf` 在 pbs 路径（`probability.py:1069`）与 win_rate 路径（`:1013`）语义不同；首批用 `classify_payload_kind` 一刀切，win_rate 自动模式下会把**策略胜率**标成「安全概率（精确解）」。新函数用真实字段比对（`best_conf` vs `prob[best]`，容差 1e-5）判定，不猜算法内部。
3. **补齐 `exp` 映射**：`utils/deduction.py:68,95,145,173,199,226` 的「由(i, j)得出」是纯逻辑推导出的 0/1 确定结论，首批漏映射 ⇒ 全部落到 `unknown`；本轮加 `("由(", VALUE_KIND_EXACT)`（`main.py:84`）。
4. **新增键盘 focus 通道**：首批只挂了 hover（`attach_hover_preview` 的 Enter/Leave）。纯键盘用户 Tab/方向键聚焦候选按钮**不产生 Enter 事件**，浮层永不出现 ⇒ 设计规范 P0-UI-06「所有任务均可键盘完成、弹层 Esc 关闭」不成立。本轮加 `_PreviewFocusFilter`（FocusIn 显示 / FocusOut 未固定时关闭）。
5. **新增 close 通道**：浮层是独立顶层 `Qt.Tool` 窗口，切页后不随页面隐藏、会盖在新页面上；主窗口关闭时应用级 `eventFilter` 会残留在 QApplication 上。本轮接 `stackedWidget.currentChanged`（`main.py:373`）与 `MyMainWindow.closeEvent`（`main.py:761-769`）。
6. **修掉一个 id 复用缺陷（真 bug）**：首批用 `id(button)` 集合去重 `_attach_preview`。`reset_btn_list()`（`main.py` 内）用 `sip.delete` 整批销毁重建按钮，新对象**可能复用刚释放的 id** ⇒ 新按钮被误判「已挂」而静默跳过接线，预览功能在该路径上静默失效。改为 `weakref.WeakSet`（`main.py:12,535,731-736`）。
7. **帮助页首屏棋盘缓存**：`update_image` 扫描出的真实 `cell_value` 现在缓存到 `_last_cell_value`（`main.py:1023`），候选格浮层 7×7 局部棋盘因此有真实数据可渲染（首批该字段只在自动页 `update_heatmap` 里赋值，帮助页候选浮层拿到的常是 None）。

## 1. 批次 B：紧凑现代信息区（运行期注入，不改生成物）

`_build_compact_ui`（`main.py:570-629`）在 `__init__` 末尾调用，全部用 `insertWidget` 插进生成物既有布局：

| 组件 | 属性名 | 插入位置 | 内容 |
|---|---|---|---|
| 状态条 | `status_card` / `status_label` | `verticalLayout_2` index 2 | 求解状态 + 已完局/赢局/胜率 |
| 决策卡 | `decision_card` / `_decision_value` / `_decision_kind` | `verticalLayout_2` index 3 | 建议点击坐标 + value_kind 文案 + 数值 + 来源 |
| 图例 | `legend_card` / `legend_body` | `verticalLayout_2` index 4 | 五项语义（已证明安全/已证明是雷/概率估计/推荐未证明/无数据） |
| 候选详情 | `candidate_card` / `candidate_list` | `verticalLayout_2` index 5 | QListWidget，最多 5 项 |
| 帮助图例 | `help_legend_card` / `help_legend_body` | `verticalLayout_3` index 3 | 语义说明 + 悬停提示 |
| 帮助候选 | `help_candidate_card` / `help_candidate_list` | `verticalLayout_3` index 4 | 真实 `pos_dict_list` 渲染 |

三条不变量（有测试守着）：
- `QStackedWidget` 仍 3 页，索引 0/1/2 不变（`test_generated_ui_not_patched`）；
- 两条 `Solver` 线程信号连接一行未改（`test_solver_threads_still_present` 断言 `auto_play_thread.is_play=True` / `help_thread.is_play=False`）；
- `ui/window.py` 零改动（`git status` 确认无该文件，mtime 2026/11/3）。

## 2. 批次 A 接线：hover / focus / close 三通道

| 通道 | 触发 | 实现 | 落点 |
|---|---|---|---|
| hover | `QEvent.Enter` / `HoverEnter` → 显示；`Leave` / `HoverLeave` → 未固定时关闭 | `attach_hover_preview`（`ui/preview_popup.py:487-497`） | `main.py:731-749` 统一挂接 |
| focus | `QEvent.FocusIn` → 显示；`FocusOut` → 未固定时关闭 | 新增 `_PreviewFocusFilter`（`main.py:192-226`） | 同上，`button._preview_focus_filter` 持有防 GC |
| close | 页面切换 / 主窗口关闭 / `Esc` / 关闭按钮 | `_hide_preview_popup`（`:751-759`）+ `currentChanged`（`:373`）+ `MyMainWindow.closeEvent`（`:761-769`）；Esc/关闭按钮由浮层自身处理 | — |

真实数据注入：
- 帮助页候选：`_preview_payload_for_candidate`（`main.py:695-713`）的 `cell_value` 取 `self._last_cell_value`（真实扫描棋盘），`value`/`value_kind`/`source` 全部来自真实 `pos_dict`；
- 自动页热力图格：`_preview_payload_for_heatmap_cell`（`:715-729`）的 `value` 取真实 `prob[(i,j)]`，`cell_value` 取 payload 的实际棋盘（回落 `_last_cell_value`）；
- 两处 `transitions` **恒为 `None`**，浮层因此显示 `NO_TRANSITION_TEXT = "暂无真实状态转移分布"`，不合成任何分支概率。

挂接范围：帮助页只对有真实 `pos_dict` 的候选格挂（`main.py:1104-1110` 附近）；自动页只对 `(i+1, j+1) in prob` 的格挂（`main.py:1280-1290` 附近）——无真实估值不弹浮层，也不显示占位概率。

`_preview_buttons` 是 `weakref.WeakSet`（不是 `id()` 集合）：`reset_btn_list` 会 `sip.delete` 并重建整批按钮，按 `id` 去重会因 id 复用而静默跳过新按钮的接线；弱引用集按对象身份判断，对象销毁即自动移出。

## 3. 批次 C：value_kind 真实区分

### 3.1 三个分类的真实发射点（逐行锚定，只做映射不改上游）

| value_kind | 文案 | 真实发射点 |
|---|---|---|
| `exact_safety` | 安全概率（精确解） | `probability.py:622`（C++ 三层决策）、`:1360,1425,1437,1457,1472,1486`（枚举得出）、`deduction.py:68,95,145,173,199,226`（由(i,j)推出 0/1） |
| `uniform_prior` | 均匀先验（非精确解） | `probability.py:739,954`（随机选择）、`:731` 与 `:1399` 的 `1 - 剩余雷密度` 兜底 |
| `heuristic_win_rate` | 策略胜率（启发式，非安全概率） | `probability.py:1027,1040`（胜率计算得出）、`:1013` 的 `win_rate[arg]` |

### 3.2 三个分类函数

| 函数 | 落点 | 判定依据（全部来自真实字段，不自造数） |
|---|---|---|
| `classify_value_kind(exp)` | `main.py:90-100` | `exp` 文案映射；未列出 ⇒ `unknown`（宁标未知不错标） |
| `classify_heatmap_kind(payload)` | `main.py:102-127` | `prob` 非空 ⇒ `exact_safety`；`prob` 空且 `best` 存在 ⇒ `uniform_prior`（随机分支）；都空 ⇒ `unknown` |
| `classify_decision_value_kind(payload)` | `main.py:152-189` | `best_conf` 与 `prob[best]` 容差内相等 ⇒ `exact_safety`；异值 ⇒ `heuristic_win_rate`；`prob` 空或 `best∉prob` ⇒ `uniform_prior`；缺字段 ⇒ `unknown` |

决策卡接线（`main.py:1262-1269`）：`best` 存在时用 `classify_decision_value_kind` 判 `best_conf`（决策卡展示的正是 `best_conf`），并把类型写进 `source`；`best` 缺失时 `unknown` + 「本轮无最优格」，数值显示 `—` 而不是 0%。

### 3.3 无 transitions 时的占位（硬约束）

- `main.py` 两处 payload 构造的 `transitions` 恒 `None`；
- `ui/preview_popup.py:101-119` `transitions_text` 只渲染 payload 里真实存在的 `probability`，缺失项直接丢弃（不补 0、不补均值）；
- 浮层显示 `NO_TRANSITION_TEXT` + `NO_TRANSITION_HINT`（「当前 solver 不产出点击后的分支概率，此处不显示合成概率」）；
- 测试 `test_popup_placeholder_never_contains_percentages` 断言占位文案里**连一个 `%` 都没有**——防止后续有人往占位里偷偷塞合成概率。

## 4. 未做与范围边界（如实申报）

1. **`classify_heatmap_kind` 对非空 `prob` 仍一律判 `exact_safety`**：pbs 路径（`probability.py:1493` 的 `res`）与 win_rate 路径（`:1047` 的 `clicks2p`）都会发满格 `prob`，两者**都是「该格不是雷」的世界枚举占比**，语义上确实同属安全概率，故此判定成立；但 payload 不含「来源分支」字段，无法在 UI 侧再细分。若要细分需在 `_emit_heatmap` 加字段 → 改 `utils/probability.py`/`utils/solver.py`，**超出本批授权（不改 solver 算法）**。
2. **`best_conf` 在 win_rate 路径下是策略胜率、在 pbs 路径下是安全概率**：这是上游既有语义（`probability.py:1013` vs `:1069`），本轮**只在展示层如实标注**，没有改上游。根治需在发射侧给 `best_conf` 打标——同属改算法，未做。
3. **固定（pin）状态不随新选格复位**：`show_for_cell` 换格重建 payload 但保持 `_pinned`。行为可接受（固定即钉住跟随），与原型「固定后关闭才复位」有差异，未改。
4. **状态机 13 态未实现**：当前只有「就绪 / 自动运行中 / 帮助模式」三态文案；完整状态机需后端事件源（`end_signal`/`pv_signal` 信息量不足），移交后续批次。
5. **图例仍是纯文本 `■` 符号**，非真实色块控件（P2）。

## 5. 验证（新鲜输出，本会话实跑）

环境：`D:\anaconda3\envs\py310\python.exe`（系统默认 python 3.11 无 PyQt5）；`QT_QPA_PLATFORM=offscreen`。

### 5.1 编译

```
D:\anaconda3\envs\py310\python.exe -m compileall -q main.py tests\test_ui_migration.py
→ COMPILEALL_EXIT=0
D:\anaconda3\envs\py310\python.exe -m py_compile main.py tests\test_ui_migration.py
→ PYCOMPILE_EXIT=0
```

### 5.2 定向测试（新增）

```
set QT_QPA_PLATFORM=offscreen
D:\anaconda3\envs\py310\python.exe -m unittest tests.test_ui_migration
→ Ran 34 tests
→ OK   (exit 0)
```

34 个用例分组：`TestMainWindowInstantiation`（3，主窗口 offscreen 实例化 + 注入控件存在 + 生成物未改）、`TestStatusDecisionLegend`（6，状态条/决策卡/图例/五项语义/候选详情含 3 元素批次）、`TestPopupWiring`（7，hover/focus/close 三通道 + 幂等 + 真实棋盘 + 切页关闭 + 热力图接真实 prob）、`TestValueKindDistinction`（14，三分类判定 + 占位 + 端到端决策卡）、`TestAssertionDiscrimination`（3，注入必红）、`TestCancelAndThreadsUntouched`（1，线程未改）。

### 5.3 定向测试（既有 UI 契约不回归）

```
D:\anaconda3\envs\py310\python.exe -m unittest tests.test_ui_heatmap tests.test_ui_preview_popup
→ Ran 43 tests
→ OK   (exit 0)
```
三模块合并跑（含新增）：`Ran 77 tests → OK (exit 0)`。

### 5.4 全量回归

```
D:\anaconda3\envs\py310\python.exe -m unittest discover -s tests -t .
→ Ran 228 tests
→ OK   (FULL_EXIT=0)
```

### 5.5 注入必红负向自测（断言鉴别力条款②③）

用临时脚本对 **main.py 生产代码真值**注入 3 个变异体，逐个跑 `tests.test_ui_migration`，跑完立即还原并复跑：

| 变异体 | 结果 | 被谁抓住 |
|---|---|---|
| `("胜率计算得出", VALUE_KIND_WIN_RATE)` → `VALUE_KIND_EXACT` | **RED（5 failures）** | `test_mislabel_mutant_is_caught`、`test_hover_channel_shows_popup_with_real_board` 等 |
| `("随机选择", VALUE_KIND_PRIOR)` → `VALUE_KIND_WIN_RATE` | **RED（2 failures）** | `test_candidate_list_three_kinds_in_one_batch`、`test_classify_value_kind_covers_all_real_emitters` |
| 删掉 `classify_heatmap_kind` 里 `prob` 空 + `best` 存在的 `uniform_prior` 分支 | **RED（1 failure）** | `test_heatmap_random_branch_is_uniform_prior` |
| 还原后基线 | **GREEN（exit 0）** | — |

临时脚本与日志已在验证后删除（`review/_tmp_*` 不留痕）；确认 `main.py` 还原后 `git diff` 分类真值仍在 `main.py:85`。

### 5.6 未跑项（skipped，如实申报）

| 检查 | 状态 | 原因 | 复跑命令 |
|---|---|---|---|
| 真实扫雷窗口 hover/focus 联调 | **skipped** | 需真实扫雷进程 + 屏幕，offscreen 沙箱无法验证真实鼠标/键盘事件路径 | `python main.py` 后手动 hover / Tab 到帮助页候选格 |
| 真机 DPI / 字体渲染 | **skipped** | offscreen 无字体目录（`QFontDatabase: Cannot find font directory` 警告，不影响逻辑） | 真机启动后目视检查 |
| integration / acceptance 层 | **未跑（移交）** | 按 Verification_Boundary，executor 只承担 unit 层：编译 + 受影响文件定向测试 + 全量单测 | 归 test-engineer（回归矩阵）/ verifier（验收探针） |

环境噪声说明：测试输出里的 `QFontDatabase` / `This plugin does not support raise()` 是 offscreen 平台插件的固有提示，**不是失败**；判定一律以 `unittest` 的退出码与 `OK/FAILED` 行为准（EXIT=0 已单独取过）。

## 6. 未覆盖与移交

1. **`prob`/`best_conf` 的发射侧打标**（本报告 4.1 / 4.2）：需改 `utils/probability.py` 的 `_last_heatmap` 组装段与 `utils/solver.py:_emit_heatmap`，属「改算法链」，本批授权明确排除。移交后续批次。
2. **真实状态转移分布**（`transitions`）：需算法层按剩余世界枚举计算点击后分支概率，属批次 D，需 architect 评审计算口径。UI 侧已按「有则显示、无则占位」实现，批次 D 只需填 `transitions` 字段，UI 零改动。
3. **状态机 13 态**：需与后端事件源对齐。
4. **固定状态复位语义**：与原型有差异，需产品裁决。
5. **`ui/preview_popup.py` 的 `Qt.Tool` 不抢焦点**：已在 offscreen 下验证窗口标志与 `WA_ShowWithoutActivating`，但真机焦点行为（尤其 hover→focus 切换时是否闪烁）只能真机验证。

## 7. 变更清单（file:line）

| 文件 | 位置 | 变更 |
|---|---|---|
| `main.py` | 12 | `import weakref`（去重集合改弱引用，防 id 复用） |
| `main.py` | 84 | `_EXP_TO_KIND` 增「由(」→ `exact_safety`（deduction.py 6 处发射点） |
| `main.py` | 102-127 | `classify_heatmap_kind` 修正：`prob` 空 + `best` 存在 ⇒ `uniform_prior` |
| `main.py` | 149 | `_BEST_CONF_EPS = 1e-5`（两处 `round(...,5)` 的比对容差） |
| `main.py` | 152-189 | 新增 `classify_decision_value_kind`（`best_conf` 三分类判定） |
| `main.py` | 192-226 | 新增 `_PreviewFocusFilter`（键盘 focus 通道） |
| `main.py` | 373 | `stackedWidget.currentChanged` → `_hide_preview_popup`（close 通道） |
| `main.py` | 535 | `_preview_buttons` 由 `set()` 改 `weakref.WeakSet()` |
| `main.py` | 695-729 | 预览 payload 构造：真实 `cell_value` 回落 + `transitions=None` |
| `main.py` | 731-749 | `_attach_preview`：hover + focus 双通道，WeakSet 去重 |
| `main.py` | 751-769 | 新增 `_hide_preview_popup` + `MyMainWindow.closeEvent` |
| `main.py` | 1023 | `update_image` 缓存真实棋盘到 `_last_cell_value` |
| `main.py` | 1256-1269 | `update_heatmap` 决策卡改用 `classify_decision_value_kind` |
| `tests/test_ui_migration.py` | 全文（新增 34 用例） | offscreen 回归（实例化/控件/popup 接线/占位/分类区分/注入必红） |
| `review/ui-migration-bc.md` | 本文件 | 实施报告 |

`ui/preview_popup.py`、`ui/window.py`、`ui/window.ui`、`utils/**`、`cfg.json` 均无改动。

## 8. 首触四问留痕

### 8.1 `main.py`（本会话首次 edit）

① **引用方（file:line）**：`tests/test_ui_heatmap.py:17`（`import main` → `MyMainWindow()`）、`tests/test_ui_preview_popup.py:28`（`import main`，40 用例）、`tests/test_config_lifecycle.py:411`（`import main` + `MyMainWindow.__new__` 轻量实例，`:415`）、`ui/window.py:35`（反向 `from main import oklch_grad`）、`bench/bench_ui_render.py:23`、`bench/verify_ui_legend.py:46`、入口 `main.py:__main__`。
② **受影响公共 API**：新增模块级 `classify_decision_value_kind` / `VALUE_KIND_TEXT` 增项 / `_PreviewFocusFilter`；`MyMainWindow` 新增 `_hide_preview_popup` / `closeEvent`（新覆盖，原类无）；`classify_heatmap_kind` 语义变更（空 `prob` + 有 `best` ⇒ `uniform_prior`，原为 `unknown`）；`_EXP_TO_KIND` 增项。**既有方法签名零改动**（`update_heatmap` / `update_btn_list` / `update_image` / `_attach_preview` / `reset_btn_list` / `update_screen` / `reload` 保持原名原参）。
③ **数据文件字段**：不新增读写。沿用 `cfg.json`：`{"speed":5.2e5,"w":30,"h":16,"a":99,"bx":12,"by":141,"cell_width":24,"limit":80,"path":"<ms_arbiter.exe 绝对路径>","win_name":"Minesweeper Arbiter "}`，**无日期字段**。heatmap payload：`prob{(1-based i,j):float}`、`best`、`best_conf`、`total`、`cell_value` int32 `(h+2)×(w+2)`（0-8 已开 / 9 未开 / 10 推理雷 / 11 推理安全）、`played`、`win`。`pos_dict_list` 项：`pos`、`confidence`、`num`、`is_mine`、`is_best`、`exp`、`is_recommend`。popup payload：`cell`、`value`、`value_kind`、`source`、`cell_value`、`transitions`（本项目恒 None）。
④ **用户当前指令原话**：「B：在 main.py 中运行期注入最小紧凑现代 UI：状态条/决策卡/图例或等价信息，保持现有页面和线程，不能手改 ui/window.py。A popup 接线：在帮助候选按钮或帮助页网格通过 PreviewPopup/attach_hover_preview 绑定 hover/focus/close，真实 cell_value 传入；无 transitions 显示暂无真实状态转移分布。C：从现有 heatmap payload/pos_dict_list 接入真实字段，并区分 value_kind：exact_safety/uniform_prior/heuristic_win_rate；禁止伪造 transition 概率。新增 offscreen 测试，至少验证主窗口能实例化、状态/决策/图例控件存在、popup 接线不报错、无 transitions 显示占位。」

### 8.2 `tests/test_ui_migration.py`（本会话首次 write，新建）

① **将由谁调用**：无生产代码调用；`unittest discover -s tests -t .` 自动收集（已实测进 228 个用例）。已确认**无既有文件承担同一职责**：`tests/` 现有 `test_ui_heatmap.py`（热力图渲染契约，7 用例）与 `test_ui_preview_popup.py`（批 A 浮层单元 + 主窗口控件存在性，40 用例）都**不覆盖**「focus 通道 / close 通道 / 三分类 heatmap+decision 判定 / 注入必红」，故新建独立文件而非扩大既有文件。
② **公共 API 面**：仅测试类，不被生产代码引用；新增断言覆盖 `main.classify_decision_value_kind`、`main._PreviewFocusFilter`、`main.MyMainWindow._hide_preview_popup`、`main.MyMainWindow.closeEvent`、`_preview_buttons` 弱引用集。
③ **数据文件**：不读生产数据文件；构造合成 `cell_value = np.full((h+2,w+2), 9)` + 局部标记（`cv[1,1]=4`、`cv[2,1]=10`、`cv[3,3]=11`）、合成 payload、合成 `pos_dict_list`。
④ **用户当前指令原话**：「新增 offscreen 测试，至少验证主窗口能实例化、状态/决策/图例控件存在、popup 接线不报错、无 transitions 显示占位。」

### 8.3 `review/ui-migration-bc.md`（本会话首次 write，新建）

① **将由谁引用**：组织者验收（任务书指定产出路径）+ 后续批次（批次 D 填 `transitions`、发射侧打标）；无代码引用。
② **公共 API 面**：无（纯文档）。
③ **数据文件**：无读写。
④ **用户当前指令原话**：「先写报告骨架再改」+「产出落盘路径：`review/ui-migration-bc.md`」+「review/ui-migration-bc.md 至少 60 行，含 file:line、验证和未覆盖」。
