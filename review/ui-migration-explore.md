# 桌面端 UI 迁移接入点勘察报告（explore）

> 生成：2026-11-07 · 只读勘察，未修改任何源代码
> 状态：done（全节完成，162 行，未修改任何源代码）

## 1. 结论摘要

- 现有桌面端是单窗口三页 QStackedWidget 结构（main / 自动 / 帮助），全部 UI 逻辑集中在 main.py 的 MyMainWindow（1314 行），ui/window.py + window.ui 是纯生成的静态布局。
- HTML 原型（review/ui-prototype.html，1525 行）的五个页面（总览/自动运行/获取建议/识别校准/运行设置）与现有三页 + EditWindow/ScreenShot 两个对话框几乎一一对应，可逐页映射迁移。
- 悬浮 7×7 预览（transition-overlay）最佳落点是独立的 frameless QWidget（Qt.Popup|Qt.Tooltip 语义），挂在帮助页/自动页网格按钮的 enterEvent 上；数据上唯一缺口是"点击后状态转移分支"（number/blank/mine + 各自概率），现有 solver 不产出该结构。
- 真实 solver 数据经 heatmap_signal payload（prob/best/best_conf/cell_value/played/win）与 update_btn_list_signal（pos_dict_list）两路到达 UI；原型的 value_kind/rule/explanation/world_count/elapsed_ms 五个字段是缺口，confidence/prob/best 已有。
- 最大风险：原型候选语义（is_proven_safe / uniform_prior / heuristic_win_rate 三分类）与现有 pos_dict_list 的 is_best/is_recommend 二标志不对齐，直接照搬 UI 会对用户谎报"已证明安全"。

## 2. 现有 Qt 页面 / 控件 / 信号 / 布局清单

### 2.1 窗口与页面结构

| 窗口/页面 | 类 | 位置 | 说明 |
|---|---|---|---|
| 主窗口 | MyMainWindow(QMainWindow, Ui_MainWindow) | main.py:63 | 1035×783 固定尺寸（ui/window.py:57-59） |
| 页面 0 主页 | page_main | ui/window.ui:307 | 说明图 + 示例 label |
| 页面 1 自动 | page_2 | ui/window.ui:450 | 热力图卡片 + 统计栏（运行期注入） |
| 页面 2 帮助 | page_3 | ui/window.ui:572 | 棋盘网格 + 提示按钮 |
| 设置对话框 | EditWindow(QDialog, Ui_Dialog) | main.py:987 | w/h/a/bx/limit/path 配置 |
| 截图对话框 | ScreenShot(QWidget, Ui_form) | main.py:1184 | 模板采集工具 |
| 消息框 | MyMessageBox(QMessageBox) | main.py:920 | 带记忆位置的提示框 |

### 2.2 运行期注入的控件（不在 .ui 里，代码手建）

- 帮助页网格：btn_list（h×w 个 QPushButton，setGeometry 绝对定位，24px），main.py:263-289；列头/行头 btn，main.py:264-271、290-298；重建逻辑 reset_btn_list main.py:477-531。
- 自动页热力图：heatmap_btn_list（同结构 24px）+ heatmap_frame 磨砂卡片（main.py:305-314）+ heatmap_label_stats 统计栏（main.py:315-330）。帮助页与自动页网格独立自建、互不复用（注释 main.py:302-304）。
- 样式去重缓存 _hm_last（main.py:346），update_heatmap 中按 (style,text) 跳过未变化按钮（main.py:786-791）。

### 2.3 QThread 信号（utils/solver.py:62-74，AutoPlayThread 定义）

| 信号 | 载荷 | 消费点 |
|---|---|---|
| pv_signal(int) | 进度 0-100（100ms 节流） | pgb / pgb_p3 进度条 main.py:212,234 |
| Visible_signal(bool) | 进度条/停止按钮显隐 | main.py:219-222,235-238 |
| warning_signal(str) | 错误弹窗 | main.py:223,240 |
| warning_signal_2(str) | 可关闭警告（带"不再显示"） | main.py:243 |
| update_btn_list_signal(list) | pos_dict_list 帮助建议 | update_btn_list main.py:647 |
| heatmap_signal(dict) | prob/best/best_conf/cell_value/played/win | update_heatmap main.py:718 |
| start_signal(tuple) | (total, speed, _) 估算进度 | update_pgb main.py:226,244 |
| end_signal(str) | 计算结束 | end_timer main.py:229,247 |

### 2.4 关键槽函数与状态流

- update_screen：50ms QTimer 轮询线程状态切按钮文案/使能（main.py:176-179, 877-899）——原型里 solver-status 状态条的对应物，但现在是轮询而非事件驱动。
- update_btn_list：把 pos_dict_list 渲染成按钮颜色（红=雷/绿=安全/黄框=推荐/OKLCH 色带=概率），并给每个建议按钮挂 clicked→info 弹窗（main.py:647-708）。
- update_heatmap：自动页概率热力图渲染；坐标换算 btn[j][i] ↔ 1-based (i+1,j+1)（main.py:718-806）。
- click_all_func：批量点击 clicks + 右键标雷 mines（main.py:847-875）——对应原型 assist 页的 batch 执行。
- _gui_scan_once：GUI 线程一次性扫描（独立视觉对象，避免与工作线程交叉写 self.img，main.py:611-645）——新增任何 GUI 侧扫描必须沿用此模式。
- 帮助模式预算乘数 _DECISION_BUDGET_MULT=2.0（utils/solver.py:486-490,560）。

### 2.5 cell_value 编码契约（全链路共用）

- 0-8=已开数字；9=未开；10=推理确定雷；11=推理安全格（utils/vision.py:37、main.py:724-725 注释）。
- 1-based 全局坐标：(i,j) 从 1 起，cell_value[j,i]；按钮网格 0-based，映射 btn[j][i] ↔ cell_value[j+1,i+1]（main.py:721-723）。

## 3. HTML 原型 → Qt 控件映射

原型结构（review/ui-prototype.html）：侧边栏导航 data-page 五页（:499-507），顶部 solver-status 状态条（:528-536），主体 workspace-grid 左棋盘右检查列（:168-170）。

| 原型元素 | 原型锚点 | Qt 现状 | 迁移控件建议 |
|---|---|---|---|
| 侧边栏导航（总览/自动/获取建议/校准/设置） | html:499-507 | QStackedWidget 3 页 + 左侧按钮列（window.py:84-119） | QToolButton/QPushButton + QStackedWidget，页数 3→5 |
| solver-status 状态条（DECIDING/耗时/停止） | html:528-536 | update_screen 50ms 轮询（main.py:877-899）+ pgb 进度条 | QFrame 卡片 + QLabel；改事件驱动（信号已具备） |
| decision-card 当前决策卡 | html:158-166, 933 | heatmap_label_stats 单行统计（main.py:315-330） | QFrame + 等宽 QLabel，坐标/概率/value_kind 三段 |
| 棋盘热力图网格 + 缩放 | html:853, 176-179 | heatmap_btn_list 24px 绝对定位（main.py:331-345） | 保留 QPushButton 网格；缩放加 cell 尺寸因子重排 setGeometry |
| 图例 legend | html:565 | 无（帮助页无图例） | 新增 QLabel 行，色块=样式片段 |
| 候选列表（最多5项，#排名/坐标/概率/预览按钮） | html:894, 569 | pos_dict_list 渲染在按钮色上（main.py:647-708），无列表控件 | QListWidget 或 QVBoxLayout of QFrame 行 + 尾部预览 QPushButton |
| 证据详情 inspector（rule/world_count/elapsed_ms/related chips） | html:903, 570 | 仅 info() 正则解析字符串弹窗（main.py:808-826） | QScrollArea + QFormLayout；related chips→Q扁平按钮行 |
| 批量执行 batch-bar（仅已证明项） | html:957-963, 610-613 | click_all_func（main.py:847-875） | 保留现有逻辑，确认对话框 QMessageBox→QDialog 列表确认 |
| 快照 dialog（摘要/JSON 两 tab） | html:604-607, 1231 | 无 | QDialog + QTabWidget + QPlainTextEdit(JSON) |
| 校准页（2 步：边界/模板） | html:591-594, 1301 | ScreenShot 对话框（main.py:1184）+ EditWindow 取点流程（main.py:1062-1120） | 两步向导 QWizard 或双卡页；复用 GetMousePosition |
| 设置页（含危险/高级分组） | html:596-598, 1305 | EditWindow QInputDialog 逐项弹（main.py:1041-1135） | QFormLayout 表单直填 + <details>→QGroupBox 折叠 |
| 状态转移浮层 transition-overlay | html:346-387, 618-620 | 无（本次新增核心） | 见第 4 节 |
| toast 通知 | html:339-341 | QMessageBox.about 弹窗（多处） | QLabel 浮层 + QTimer 自动消退，低优先级 |

## 4. 悬浮 7×7 预览的最佳 PyQt 实现位置

### 4.1 原型行为规格（事实，来自原型代码）

- 触发：候选行预览按钮 hover/click（html:1404-1407）、棋盘格 hover（html:1465,1485,1492）、固定按钮（html:1406-1407）。
- 定位：锚点右侧优先放不下翻左侧，上下夹取视口内（html:1024-1055 positionTransitionOverlay）。
- 内容：4 种 outcome 分支（数字1/数字2/展开空白/踩雷），各带概率 + 7×7 mini-board（html:979-1002 miniBoardHtml：以目标格为中心 dy,dx∈[-3,3]）+ 事实表（html:1019）。
- 关闭：Esc / 移开鼠标（未固定时）/ 关闭按钮（html:1451-1458）。
- 原型自注：局部棋盘为合成数据，非真实求解读数（html:1020）。

### 4.2 推荐 PyQt 实现

**控件形态**：独立顶层 `QWidget`，`Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint`（Qt.Tool 不抢焦点，hover 离开不闪；若需点击内部按钮用 `Qt.Popup`）。子布局：QVBox（标题行 + outcome 折叠列表 + 7×7 网格 QLabel/QFrame）。

**挂接点（按优先级）**：
1. 帮助页：`update_btn_list`（main.py:647）里每个建议按钮已挂 clicked→info；在此处再 `installEventFilter` 捕 enterEvent 即可，按钮→候选的对应关系现成。
2. 自动页：`update_heatmap`（main.py:718）渲染的按钮网格，hover 最佳格/概率非 1 格时显示。
3. 候选列表控件（第 3 节新增）：行尾预览按钮的 enterEvent。

**定位**：`QCursor.pos()` 或 `btn.mapToGlobal()` 为锚，`QScreen.availableGeometry()` 夹取——等价原型 html:1036-1039 逻辑，约 20 行。

**安装位置**：新建 `ui/preview_popup.py` 独立模块（不改 window.ui/window.py——它们是 pyuic 生成物）；主窗口持一个复用实例（避免每次 hover 重建 QWidget）。

**7×7 网格渲染**：49 个 QLabel（25px）单次构建复用，setPixmap/setStyleSheet 刷内容；性能远低于原型的 DOM 重建路径。

### 4.3 禁忌（现有代码已踩过的坑）

- 预览若需自己扫描棋盘，必须走 _gui_scan_once 模式（一次性视觉对象），不得调工作线程实例的扫描方法——跨线程写 self.img 会棋盘错帧（main.py:592-597 注释）。
- 重复 hover 重复 connect 的防重：参考 main.py:659-665（disconnect 后再 connect），或改用 eventFilter 天然幂等（推荐后者）。

## 5. 真实 solver 数据：能提供什么 / 缺什么

### 5.1 已有（可直接喂 UI）

| 原型字段 | 现状 | 证据 |
|---|---|---|
| value（概率/胜率） | confidence（pos_dict_list 项）与 prob dict 均有 | utils/probability.py:616-623, 731-740, 1020-1041, 1066-1071 |
| best 坐标 + best_conf | _last_heatmap["best"]/["best_conf"] | utils/probability.py:1066-1071 |
| 全格概率热力 | heatmap payload["prob"]（1-based 键） | utils/solver.py:459-482 |
| 棋盘事实 | cell_value 编码 0-11 | utils/vision.py:37 |
| played/win 统计 | payload["played"]/["win"] | utils/solver.py:478-479 |
| 决策文案 | exp 字段（"枚举得出"/"随机选择。"/"C++ 三层决策 (reason)"等） | utils/probability.py:622,739,1027,1425 |
| 推荐/确定标志 | is_best / is_recommend | utils/probability.py:621-623,738-740 |

### 5.2 缺口（原型有、solver 无）

1. **value_kind 三分类**（exact_safety/uniform_prior/heuristic_win_rate，html:696-699）：现有只有概率数值，无类型标签。随机分支（probability.py:731-740）实为 uniform_prior、win_rate 分支实为 heuristic_win_rate、枚举分支为 exact_safety——**可在发射处按分支打标，无需新算法**，但 win_rate 分支语义是"策略胜率"不是"安全概率"，混排展示需 UI 层区分。
2. **rule/explanation 结构化依据**：现只有 exp 一句话。原型的"128 个候选世界中有 112 个安全"需要 total 与 per-cell 计数——`_pbs_compute_dp` 返回的 (res, mine_num, total)（probability.py:1189-1201）里有 total，per-cell 安全世界数在 res 计算过程中存在但未外传。
3. **world_count / elapsed_ms**：枚举世界总数与耗时未随候选记录。耗时可在 number5_1 内 time.perf_counter 包住；世界数在 res_list 行数。
4. **状态转移分支（7×7 预览的数据）**：点击某格后 {开数字1:p1, 开数字2:p2, 展开空白:p3, 踩雷:p4} 的分支概率分布，**现有任何代码路径都不产出**。这是唯一需要动算法层的缺口；UI 先行时只能展示占位或隐藏预览按钮。
5. **related_positions（涉及格）**：约束传播用到的相邻已开格未记录；可在枚举时从 clicks 邻接关系导出。

### 5.3 数据流路径（迁移时保持不变）

Solver(QThread).run → play()/help() → _emit_heatmap（唯一发射口，solver.py:459）/ update_btn_list_signal.emit（solver.py:548）→ GUI 槽。**跨线程边界只有这两个信号口 + warning/pv/visible**，新 UI 字段一律扩 payload dict，不新增跨线程直读。

## 6. 风险清单

| # | 风险 | 等级 | 证据/依据 |
|---|---|---|---|
| R1 | 候选语义错报：把 win_rate（策略胜率）标成"安全概率"或把随机选择标成"推荐" | 高 | probability.py:731（confidence=1-剩余雷密度 是均匀先验）、1020-1028（win_rate 进 is_recommend） |
| R2 | 悬浮预览抢焦点/闪烁：Qt.Tool vs Qt.Popup 选错导致 hover 死循环（预览抢焦点→按钮 leaveEvent→预览关闭→再 enter） | 中 | Qt 焦点语义，无本仓证据；须实现时验证 |
| R3 | 10000 局大棋盘网格性能：原型 zoom 重排若逐按钮 setGeometry 会卡；现 24px×491 格已有 _hm_last 去重防闪（main.py:786-791）——新缩放逻辑必须沿用去重 | 中 | main.py:346,786-791 |
| R4 | update_screen 轮询与信号驱动混用：迁移状态条时若保留 50ms 轮询又加事件驱动，会出现状态抖动 | 中 | main.py:176-179,877-899 |
| R5 | .ui 与代码双源：window.ui/window.py 是生成物，改布局必须改 .ui 再 pyuic 重生成，手改 window.py 会被覆盖 | 高 | window.py:54 起与 window.ui 结构逐字对应 |
| R6 | 协作式取消链路：新 UI 的停止按钮必须走 request_cancel→超时 terminate 顺序（_stop_solver_thread），不能直接 terminate | 中 | main.py:402-423 |
| R7 | 帮助页网格 reset_btn_list 用 sip.delete 硬删，若预览控件持有旧按钮引用会崩 | 中 | main.py:479-488 |
| R8 | 现有测试契约：test_ui_heatmap.py 6 用例锁定了磨砂样式/去重/清空渲染行为，迁移改动会红——改 UI 前先跑一遍做基线 | 低 | tests/test_ui_heatmap.py:41-115 |

## 7. 分批迁移建议（4 步）

1. **批次 A（纯壳，不动数据链）**：新增 ui/preview_popup.py（悬浮 7×7 预览控件，先接合成/空数据 + 占位文案），挂帮助页按钮 enterEvent；QStackedWidget 加"总览"页壳。验收：hover 不抢焦点、Esc 关闭、test_ui_heatmap 全绿。
2. **批次 B（信号 payload 扩展）**：_emit_heatmap/pos_dict_list 增发 value_kind + exp 结构化（按发射分支打标，不改算法）；候选列表控件 + 证据详情面板读新字段。验收：自动/帮助两页概率着色与现状逐像素一致（回归基线）。
3. **批次 C（页面迁移）**：设置页表单化（EditWindow 改 QFormLayout 直填）、校准页向导化（并入 ScreenShot 逻辑）、状态条事件驱动化（删 update_screen 轮询）。此批动 EditWindow/ScreenShot，风险集中在坐标标定流程（main.py:1062-1120），需保留旧路径开关。
4. **批次 D（状态转移预览真数据）**：solver 侧产出点击后分支概率（动算法层，单独评审），预览浮层接真数据。此批前置依赖批次 B 的 value_kind，且需要 architect 评审分支概率的计算口径（是否按剩余世界枚举）。

## 8. 证据索引（关键 file:line 汇总）

- main.py:63 MyMainWindow；176-179 update_screen 定时器；190,232 双 Solver 实例；212-230,234-247 信号连接；263-298 帮助网格；305-346 热力图+统计栏；402-423 协作停止；611-645 _gui_scan_once；647-708 update_btn_list；718-806 update_heatmap；808-826 info 弹窗；847-875 click_all_func；877-899 update_screen 轮询；920-984 MyMessageBox；987-1181 EditWindow；1184-1295 ScreenShot。
- ui/window.py:54-552 setupUi（生成物）；ui/window.ui:307/450/572 三页；utils/solver.py:62-74 信号定义；136 Solver 组合；208-215 run 分派；243-457 play；459-482 _emit_heatmap；484-560 help。
- utils/vision.py:37 编码契约；utils/probability.py:411 ProbabilityMixin、490/609-623/731-740/1066-1071 数据发射点；review/ui-prototype.html:346-387 浮层样式、618-620 浮层 DOM、979-1002 miniBoard、1004-1022 渲染、1024-1055 定位、1404-1407 触发、696-699 候选四类型样例。
- tests/test_ui_heatmap.py:20-115 热力图 UI 契约测试；tests/test_scan.py:373-378 warning_signal 契约。