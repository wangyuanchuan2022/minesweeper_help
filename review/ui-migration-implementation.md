# 桌面端 UI 迁移首批（A+B+C）实施报告

> 生成：2026-11-07 · 角色：executor · 状态：**A+B+C 全部落盘，定向+全量测试通过**
> 范围：`ui/preview_popup.py`（新增）、`main.py`（运行期注入 + value_kind 分类）、`tests/test_ui_preview_popup.py`（新增 40 用例）
> **未修改**：`ui/window.py` / `ui/window.ui`（pyuic 生成物）、`utils/*`（算法实现）、`cfg.json`
> 上游：`review/ui-migration-explore.md`（勘察）、`review/ui-prototype.html`（原型）、`review/ui-design-spec.md`（设计规范）

## 0. 交付摘要

| 批次 | 内容 | 落点 | 状态 |
|---|---|---|---|
| A | 悬浮 7×7 状态转移预览浮层 | `ui/preview_popup.py`（497 行，新增） | 完成 |
| B | 紧凑现代布局运行期注入（状态条/决策卡/图例/候选详情） | `main.py:349-560` 附近 | 完成 |
| C | value_kind 三分类真实接入 + 无真实转移数据占位 | `main.py:62-140`（纯函数）+ 接线点 | 完成 |
| 测试 | offscreen 回归 | `tests/test_ui_preview_popup.py`（40 用例） | 完成 |

三条硬约束的落实方式：
1. **不改生成物**：所有新控件在 `MyMainWindow.__init__` 里用 `insertWidget` 插进既有 `verticalLayout_2`（自动页）、`verticalLayout_3`（帮助页），`ui/window.py` 零改动（`git diff --stat ui/window.py` 为空）。
2. **不改算法**：`utils/*` 零改动；value_kind 通过**发射侧已有 exp 文案 / payload 推断**在 GUI 层分类，不触碰 `utils/probability.py`。
3. **不伪造概率**：`transitions` 恒为 `None`（现有 solver 不产出点击后分支分布），浮层显式渲染「暂无真实状态转移分布」+「不显示合成概率」。

## 1. 批次 A：悬浮 7×7 预览浮层（`ui/preview_popup.py`）

### 1.1 控件形态

`PreviewPopup(QtWidgets.QWidget)`，窗口标志 `Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint`，并设 `Qt.WA_ShowWithoutActivating`。

`Qt.Tool` 而非 `Qt.Popup` 的理由（勘察报告 4.2 / 风险 R2）：`Qt.Popup` 会抢焦点，hover 场景下形成「enter→show→抢焦点→leave→close→再 enter」死循环；`Qt.Tool` + `WA_ShowWithoutActivating` 让浮层只显示不激活，配合 `eventFilter` 天然幂等，规避该风险。

### 1.2 公开 API（供 main.py / 测试使用）

| 成员 | 语义 |
|---|---|
| `set_payload(payload)` | 渲染一份 payload，返回归一化摘要 dict |
| `show_for_cell(cell, payload, anchor_widget, anchor_point, screen)` | 在锚点旁显示（自动边缘定位） |
| `close_popup()` | 无条件关闭并摘除应用级过滤器 |
| `hide_if_unpinned()` | 未固定时关闭（hover leave 用） |
| `toggle_pin()` / `is_pinned` | 固定/取消固定；固定后移开鼠标不关闭 |
| `PreviewHoverFilter` / `attach_hover_preview()` | 候选按钮 hover 接浮层 |

`attach_hover_preview` 把过滤器挂到 `button._preview_filter` 上——不持引用会被 Python GC 回收导致 hover 静默失效（已在测试 `test_attach_sets_attribute_and_filter` 断言）。

### 1.3 7×7 局部棋盘

`sample_board(cell_value, cell, span=7)`：以目标格（1-based）为中心取 ±3，返回 `rows[j][i]` 编码矩阵；越界位置为 `None`（渲染为透明空格）。编码沿用全链路契约（`utils/vision.py:37`）：0-8 已开数字、9 未开、10 推理雷（红底「雷」）、11 推理安全（绿底「✓」）。中心格额外加 2px 青色实线框标示目标格。

局部棋盘**不复用主热力图色带**——它是棋盘事实，不是概率，混用色带会让用户把形态当概率读。

### 1.4 边缘定位

`compute_popup_position(anchor_rect, popup_size, screen_rect, margin=12)` → `(left, top)`，纯函数无 Qt 依赖。语义对齐原型 `review/ui-prototype.html:1024-1055`：
- 右侧放得下 → 右侧；
- 右侧放不下 → 翻到左侧；
- 两侧都放不下 → 退回右侧再夹取；
- 上下同理，最终四边夹进 `availableGeometry`。

### 1.5 交互

- **Esc**：浮层不激活，拿不到自己的 `keyPressEvent`，故 `show()` 时安装**应用级** `eventFilter`，`KeyPress + Key_Escape` → `close_popup()` 并 `return True` 吞掉事件（避免穿透主窗口）；`keyPressEvent` 作为兜底；`close_popup()` 摘除过滤器。
- **关闭按钮**：`popupClose` → `close_popup()`。
- **固定**：`popupPin`（checkable）→ 固定后 `hide_if_unpinned()` 不复位。

### 1.6 挂接点（eventFilter）

- 帮助页候选格：`main.py` 的 `update_btn_list` 里对每个 `pos_dict` 对应按钮调 `self._attach_preview(btn, partial(self._preview_payload_for_candidate, pos_dict))`。
- 自动页热力图格：`update_heatmap` 里只对**有真实概率**的格挂接（`if (i+1, j+1) not in prob: continue`），无概率格不显示浮层——宁可不显示，也不显示占位概率。
- 幂等：`self._preview_buttons` 是 `set`，按 `id(button)` 去重，重复渲染不重复挂载。

## 2. 批次 B：紧凑现代布局运行期注入（`main.py`）

### 2.1 注入的四个组件

`_build_compact_ui()`（`main.py:401-505` 附近）在 `__init__` 末尾调用，全部插进生成物的既有布局：

| 组件 | 插入位置 | 内容 |
|---|---|---|
| 状态条 `status_card` | `verticalLayout_2` index 2（自动页） | 求解状态 / 已完局 / 赢局 / 胜率 |
| 决策卡 `decision_card` | `verticalLayout_2` index 3 | 建议点击坐标 + value_kind 标签 + 数值 + 来源 |
| 图例 `legend_card` | `verticalLayout_2` index 4 | 五项语义（证明安全/证明雷/概率估计/推荐未证明/无数据） |
| 候选详情 `candidate_card` | `verticalLayout_2` index 5 | `QListWidget` 最多 5 项：#序号 坐标 · 类型 · 数值 · 标志 |
| 帮助图例 `help_legend_card` | `verticalLayout_3` index 3 | 语义说明 + 「悬停候选格查看 7×7 预览」 |
| 帮助候选 `help_candidate_card` | `verticalLayout_3` index 4 | 真实 `pos_dict_list` 渲染 |

样式统一走 `_CARD_QSS`（深色半透明卡片 + 圆角 + 细描边），与既有 `heatmap_frame` 磨砂质感同源，不复用楷体承载关键数值（数字用 `Cascadia Mono`）。

### 2.2 保持既有结构不变

- `QStackedWidget` 仍是 3 页，索引 0/1/2 不变（测试 `test_generated_window_py_not_required` 断言 `count()==3`）。
- 两条 `Solver` 线程（`auto_play_thread` / `help_thread`）的信号连接一行未改。
- `update_screen` 的 50ms 轮询保持原样，状态条是**叠加**信息而非替代（避免风险 R4 的状态抖动）。

### 2.3 offscreen 可实例化

`MyMainWindow()` 在 `QT_QPA_PLATFORM=offscreen` 下可正常构造（`tests/test_ui_preview_popup.py::TestMainWindowCompactUI::test_offscreen_instantiation_and_widgets`，同时断言 9 个新控件属性存在）。

## 3. 批次 C：value_kind 语义区分与真实数据

### 3.1 问题（勘察报告风险 R1）

现有 `pos_dict_list` 只带 `confidence` 数值，但不同发射分支语义完全不同：
- `probability.py:622`（C++ 三层决策）/ `:1360,1425,1437,1457,1472`（枚举得出）→ **精确解安全概率**
- `:739`（随机选择）→ `1 - 剩余雷密度`，**均匀先验**，非精确解
- `:1027,1040`（胜率计算得出）→ win_rate 前瞻估计，**策略胜率，不是安全概率**

直接把三者放同一色带并统称「不是雷的概率」会让用户把 62% 的策略胜率读成 62% 的安全概率。

### 3.2 实现：只在 GUI 层分类，不动算法

`main.py` 新增四个模块级纯函数（无 Qt、无副作用）：

| 函数 | 判定依据 |
|---|---|
| `classify_value_kind(exp)` | exp 文案映射：`C++ 三层决策`/`枚举得出` → `exact_safety`；`胜率计算得出` → `heuristic_win_rate`；`随机选择` → `uniform_prior`；其余 → `unknown` |
| `classify_heatmap_kind(payload)` | `prob` 为空 → `unknown`；非空 → `exact_safety` |
| `classify_payload_kind(payload)` | payload 自带 `value_kind` 且合法则尊重，否则按热力图推断 |
| `VALUE_KIND_TEXT` | 三分类的**用户可见文案**，`heuristic_win_rate` 显式写「策略胜率（启发式，非安全概率）」 |

文案层同一套定义在 `ui/preview_popup.py:VALUE_KIND_LABELS`（浮层与主窗口各自独立持有一份，避免 main→ui 循环导入；两处文案由测试共同约束）。

### 3.3 决策卡与候选详情的真实数据源

- 决策卡：`update_heatmap` 里从真实 payload 取 `best` / `best_conf`，经 `classify_payload_kind` 定类型；无 `best` 时显示 `—` + 「无估值数据」，**不显示 0%**。
- 候选详情：`update_btn_list` 里把真实 `pos_dict_list` 逐项渲染，`confidence` 直接来自 solver，`classify_value_kind(exp)` 定类型，`is_recommend`/`is_best` 决定标志列。
- 帮助状态条：按类型统计本轮候选数（如「安全概率（精确解）×2、策略胜率（启发式，非安全概率）×1」），不伪造进度百分比。

### 3.4 无真实状态转移时的占位（硬约束）

`solver` 全链路不产出「点击某格后 → 开数字1/开数字2/展开空白/踩雷」的分支概率（勘察报告 5.2 缺口 4）。因此：

- `main.py:_preview_payload_for_candidate` 与 `_preview_payload_for_heatmap_cell` 的 `transitions` **恒为 `None`**；
- `ui/preview_popup.py:transitions_text` 只渲染 payload 里真实存在的 `probability`，缺失项直接丢弃（不补 0、不补均值）；
- 无 transitions 时浮层显示 `NO_TRANSITION_TEXT = "暂无真实状态转移分布"` + 提示「当前 solver 不产出点击后的分支概率，此处不显示合成概率」。

原型里的分支概率（`ui-prototype.html:736-751` 的 `makeTransitions`，权重 0.43/0.27/0.18 等）**全部是合成演示数据，一行也没有搬进生产代码**。

## 4. P0 问题（阻塞/需裁决）

**无 P0 阻塞。** 三个批次均落盘且测试通过。

需组织者裁决的**范围边界**（非阻塞，但影响后续批次）：

- **P0-边界-1**：`classify_heatmap_kind` 对非空 `prob` 一律判 `exact_safety`。事实依据：`pbs` 分支与 `win_rate` 分支都会发满格 `prob`（`probability.py:1047`、`:1067`），现有 payload **不含**来源标记，GUI 无法区分。后果：win_rate 自动模式下热力图可能被标成「安全概率」。建议后续在 `_emit_heatmap` 的 payload 增加 `value_kind` 字段（改 `utils/probability.py` + `utils/solver.py`，超出本批授权范围，未做）。

## 5. P1 问题

- **P1-1 热力图 hover 预览的数值语义**：自动页预览用 `prob[(i,j)]` + `classify_payload_kind`，因此同样受 P0-边界-1 影响。缓解措施：`source` 字段写实为「自动热力图 prob」，不宣称是证明。
- **P1-2 帮助页 `update_btn_list` 的候选语义继承旧 bug**：`main.py:956-964` 把 `is_mine or confidence <= 0.01` 判为雷、`:964` 把 `is_best or confidence >= 0.99` 判为可点击——`is_best` 在 `probability.py:1359` 是「枚举确定安全」但 `is_recommend=False`，语义上确实是证明；而 `confidence >= 0.99` 的阈值判断是既有逻辑。本批**未改动**（设计规范 `ui-design-spec.md:202` 要求「只能依据显式证明标记」，但改它等于改行为，需单独评审）。
- **P1-3 固定状态不随新选格复位**：`show_for_cell` 在新格上重建 payload，但 `_pinned` 保持——固定后 hover 另一格会移动浮层且仍固定。行为可接受（固定即"钉住跟随"），但原型是「固定后关闭才复位」。未改，标注差异。

## 6. P2 问题

- **P2-1** 图例文案是纯文本 `■` 符号，非真实色块控件；原型是 HTML 色块。后续可换 `QLabel` + `setPixmap` 或 QSS 背景色。
- **P2-2** 浮层未做 DPI/字体回退处理；offscreen 下 `QFontDatabase` 缺字体目录只报 warning 不影响功能（记忆 #1212c38e 记录的假红陷阱已规避：不依赖 `grab()` 像素比对）。
- **P2-3** 候选详情 `QListWidget` 最大高度硬编码 96px/84px，30 项长列表需滚动；未做自适应高度。
- **P2-4** 状态条仍是 `update_heatmap` 时被动刷新，未接 `end_signal`/`start_signal` 事件驱动（设计规范 6.1 的状态机 13 态未完整实现，本批只做「就绪/自动运行中/帮助模式」三态）。

## 7. 验证（新鲜输出）

### 7.1 编译

```
D:\anaconda3\envs\py310\python.exe -m py_compile main.py ui\preview_popup.py
→ COMPILE OK
```

（环境说明：系统默认 `python` 是 3.11 且无 PyQt5；本仓依赖装在 `D:\anaconda3\envs\py310`，以下测试均用该解释器。）

### 7.2 定向测试：新增用例

```
set QT_QPA_PLATFORM=offscreen
D:\anaconda3\envs\py310\python.exe -m unittest tests.test_ui_preview_popup
→ Ran 40 tests in 0.266s
→ OK
```

40 个用例分组：`TestSampleBoard`（7×7 取样/越界）、`TestValueKindSemantics`（三分类文案互斥）、`TestTransitionsPlaceholder`（占位与真实数据渲染）、`TestPopupPosition`（四向定位）、`TestPreviewPopupWidget`（窗口标志/不抢焦点/Esc/固定/关闭）、`TestAttachHoverPreview`（hover 挂接幂等/异常隔离）、`TestMainWindowCompactUI`（offscreen 实例化 + 真实 payload 接线 + 分类函数）。

### 7.3 定向测试：既有 UI 契约未被破坏

```
D:\anaconda3\envs\py310\python.exe -m unittest tests.test_ui_heatmap -v
→ Ran 7 tests in 0.944s
→ OK
```

6 个既有样式/统计契约用例 + 1 个文案契约用例全部通过，说明 B 批注入未改变热力图渲染与统计栏行为。

### 7.4 全量回归

```
D:\anaconda3\envs\py310\python.exe -m unittest discover -s tests -t .
→ Ran 194 tests in 2.424s
→ OK
```

194 个用例 0 失败 0 错误（改动前基线为 154 个用例，本批 +40）。

### 7.5 未跑项（如实申报）

| 检查 | 状态 | 原因 | 复跑命令 |
|---|---|---|---|
| `format_gate` | **skipped** | 本会话无该工具（`format_gate` 是上游报告 `review/ui-window-fit-update.md:86` 引用的 HTML 纯净度检查工具，对 Python 文件不适用；仓内 `tools/` 无对应脚本） | 若组织者有该工具：`format_gate(path="main.py", checks=["py-purity"], root="D:\python\projects\minesweeper_help")` |
| 真实扫雷窗口联调 | **skipped** | 需真实扫雷进程与屏幕，offscreen 沙箱无法验证 hover 真实鼠标路径 | `python main.py` 后手动 hover 帮助页候选格 |
| 真机 DPI/字体渲染 | **skipped** | offscreen 无字体目录（`QFontDatabase` 报缺目录） | 真机启动后目视检查 |

## 8. 未覆盖与移交

1. **value_kind 上移到发射侧**：当前分类在 GUI 层用 exp 文案匹配，脆弱（exp 文案改动会静默降级为 `unknown`）。根治方案是在 `utils/probability.py` 各发射点写入 `value_kind` 字段，`utils/solver.py:_emit_heatmap` 透传——**超出本批授权（不改算法）**，移交后续批次 P0-边界-1 处理。
2. **真实状态转移分布**：勘察报告 5.2 缺口 4，需算法层按剩余世界枚举计算点击后分支概率，属批次 D，需 architect 评审计算口径。浮层已按「有则显示、无则占位」实现，批次 D 只需填 `transitions` 字段，UI 侧零改动。
3. **状态机 13 态**：设计规范 6.1 的完整状态机（IDLE/SCANNING/DECIDING/CLICKING/PAUSED/STOPPING/ERROR/WON/LOST 等）未实现，当前只有 3 态文案。需与后端事件源对齐（现有 `end_signal`/`pv_signal` 信息量不足以区分 13 态）。
4. **图例色块化**：P2-1，纯文本符号 → 真实色块。
5. **integration / acceptance 层**：按 Verification_Boundary，本席只承担 unit 层（编译 + 类型检查等价物 + 受影响文件定向测试 + 全量单测）。真机 UI 走查与验收探针归 verifier；全量回归矩阵归 test-engineer。

## 9. 首触四问事实（留痕）

### 9.1 `ui/preview_popup.py`（新建）

① **调用方**：本会话新增 `main.py:35`（`from ui.preview_popup import PreviewPopup, attach_hover_preview`）、`tests/test_ui_preview_popup.py:36-46`。已确认无既有文件承担同一职责：`glob ui/*.py` 仅 `bg_rc/edit_setting/screenshot/window/__init__`；`grep "preview|popup|Frameless"` 在 `ui/` 与 `main.py` 零命中（勘察报告 `ui-migration-explore.md:78,102` 亦标注「无/新建」）。
② **公共 API 面**（全新建）：`PreviewPopup`（`set_payload`/`show_for_cell`/`close_popup`/`hide_if_unpinned`/`toggle_pin`/`is_pinned`/`eventFilter`）、`PreviewHoverFilter`、`attach_hover_preview`、纯函数 `summarize_payload`/`describe_value`/`sample_board`/`transitions_text`/`compute_popup_position`、常量 `VALUE_KIND_LABELS`/`NO_TRANSITION_TEXT`/`NO_TRANSITION_HINT`。
③ **数据结构**：payload 字段 `cell:tuple(1-based i,j)`、`value:float|None`、`value_kind:str`、`source:str`、`cell_value:ndarray[h+2][w+2]`（0-8 已开 / 9 未开 / 10 推理雷 / 11 推理安全）、`transitions:list|None`（项含 `label`/`probability`）。**无日期/时间字段**，无文件读写。
④ **用户指令原话**：「A：新增 ui/preview_popup.py，使用独立 Qt.Tool + FramelessWindow 浮层，支持 7×7 局部棋盘展示、标题/来源/暂无状态转移分布提示、边缘定位、Esc/关闭/固定基础交互；通过 eventFilter 可接候选按钮。」

### 9.2 `main.py`（首次 edit）

① **引用方**：`tests/test_ui_heatmap.py:17`（`import main` → `MyMainWindow()`）、`tests/test_config_lifecycle.py:411-415`（`import main` + `MyMainWindow.__new__` 轻量实例）、`ui/window.py:35`（反向 `from main import oklch_grad`）、`bench/bench_ui_render.py:23`、`bench/verify_ui_legend.py:46`、入口 `main.py:__main__`。
② **受影响公共 API**：新增模块级 `classify_value_kind`/`classify_heatmap_kind`/`classify_payload_kind`/`VALUE_KIND_TEXT`/`VALUE_KIND_*` 常量；`MyMainWindow` 新增 `_build_compact_ui`/`set_solver_status`/`update_decision_card`/`_fill_candidate_list`/`_ensure_preview_popup`/`_preview_payload_for_candidate`/`_preview_payload_for_heatmap_cell`/`_attach_preview` 与 9 个新控件属性。**既有方法签名零改动**（`update_heatmap`/`update_btn_list`/`update_image`/`update_screen`/`reload` 等保持原名原参）。
③ **数据文件**：不新增读写；沿用 `cfg.json`（字段 `a,w,h,bx,by,cell_width,limit,path`，无日期字段）与 `image/save.png`。payload/pos_dict 字段见 9.1 与 `utils/probability.py` 发射点。
④ **用户指令原话**：「B：在 main.py 中运行期注入紧凑现代布局组件，不手改 ui/window.py 生成物；保持现有 QStackedWidget/线程工作，增加状态条/决策卡/图例/候选详情或等价信息，确保 offscreen 可实例化。C：从现有 solver payload/pos_dict_list 接入真实数据，并区分 value_kind：exact_safety、uniform_prior、heuristic_win_rate；不要把启发式胜率叫安全概率；真实状态转移概率缺失时显示"暂无真实状态转移分布"，不得放合成概率。」

### 9.3 `tests/test_ui_preview_popup.py`（新建）

① **调用方**：无（unittest 发现机制自动收集；`unittest discover -s tests -t .` 已实测收进 194 个用例）。
② **公共 API 面**：仅测试类，不被生产代码引用。
③ **数据结构**：构造合成 `cell_value`（`np.full((h+2,w+2), 9)` + 局部标记）、payload、`pos_dict_list`；断言文案与几何，**不读生产数据文件**。
④ **用户指令原话**：「新增/更新 PyQt offscreen 测试」（原文见任务书「本次任务」段）。

## 10. 变更清单（file:line）

| 文件 | 位置 | 变更 |
|---|---|---|
| `ui/preview_popup.py` | 全文（新增 497 行） | 浮层控件 + 纯函数层 + hover 过滤器 |
| `main.py` | 62-140 | value_kind 分类纯函数与文案常量 |
| `main.py` | 35 | `from ui.preview_popup import PreviewPopup, attach_hover_preview` |
| `main.py` | 349-560 | `_build_compact_ui` 等 8 个方法（运行期注入 + 预览挂接） |
| `main.py` | 936-940 | `update_btn_list` 开头缓存候选 + 填候选详情 |
| `main.py` | 953-958 | 候选格 hover 预览挂接 |
| `main.py` | 995-1009 | 帮助状态条按 value_kind 分类统计 |
| `main.py` | 1017-1026 | `update_heatmap` 缓存真实 payload / cell_value |
| `main.py` | 1100-1120 | `update_heatmap` 末尾接决策卡 + 状态条 + 热力图格 hover 预览 |
| `tests/test_ui_preview_popup.py` | 全文（新增 40 用例） | offscreen 回归 |
| `review/ui-migration-implementation.md` | 本文件 | 实施报告 |

`ui/window.py`、`ui/window.ui`、`utils/**`、`cfg.json` 均无改动。
