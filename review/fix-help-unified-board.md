# 修复：帮助页统一单一最新棋盘（+ 热力图残留 / 旧 payload 清除）

> 状态：DELIVERED_WITH_EVIDENCE（代码 + 测试 + 取证读数已落盘）
> 生成时刻：20261107-123235
> 执行者：executor（session-d25657e0-f227-4742-ae72-aa085c31a640）
> 范围：`main.py`、`tests/test_ui_migration.py`、`tests/test_ui_preview_popup.py`、本报告。
> 未改：`utils/*`（算法）、`cfg.json`、`cpp/*`、`ui/window.py` / `ui/window.ui`（生成物与原始布局本批无需再改）。

## 1. 任务与用户诉求（原话）

用户诉求（组织者转达，逐字）：

> 「帮助页上方原始候选网格与下方 heatmap 是两个分离棋盘，且可能不同步。」
> 「将最新 heatmap payload 的 cell_value/prob/best/best_conf 统一渲染到帮助页主棋盘：
> 已开数字、概率色、证明安全/雷、最佳格、未知格语义不能互相覆盖。」
> 「update_btn_list 与 update_help_heatmap 必须共享同一个最新 payload/单一渲染入口，
> 避免旧 pos_dict_list 覆盖新热力图。」
> 「悬浮预览挂在统一主棋盘候选按钮上，使用同一最新 cell_value。」
> 「offscreen 测试断言帮助页可见棋盘数量为 1，注入两个不同 payload 后最终显示最新
> payload（至少断言单元文字/样式/best），不存在旧热力图副本。」
> 「真实鼠标仍标 NOT_RUN。」

用户补充硬约束（2026-11-07 12:34 广播）：

> 「当前热力图有残留，不是真实值。统一帮助棋盘修复必须同时解决状态生命周期：每个新
> heatmap payload 到达先清空上一帧概率样式；prob 为空恢复无数据/事实棋盘样式；best
> 不在最新 payload 清除旧最佳框；cell_value/prob/best 同一 payload 原子刷新；新局/
> 扫描失败/线程停止清旧热力图；测试 payload A→payload B/空 payload，确认 A 的颜色和
> best 框不残留。报告中记录前后读数。」

用户补充（2026-11-07 12:45 广播）：

> 「自动扫雷界面 UI 显示跟真实进度对不上；并要求自动模式不要弹『点击后重扫：9/9 格
> 无法可信识别』。」

## 2. 现状事实（修改前读数）

| 事实 | 读数（修改前） | file:line（修改前） |
|---|---|---|
| 帮助页网格套数 | **2 套**：`btn_list`（16×30=480 格，`frame_2`）+ `help_heatmap_btn_list`（16×30=480 格，`help_heatmap_frame`），合计 960 个格子按钮 | `main.py:440-480`（btn_list 构建）、`main.py:552-594`（第二套网格） |
| 帮助页棋盘容器数 | 2（`frame_2` + `helpHeatmapFrame`） | `ui/window.py:523`、`main.py:553` |
| 帮助页布局项数 | 6（说明 + 棋盘 + 统计栏 + 热力图 + 图例卡 + 候选卡） | `tests/test_ui_migration.py:82` 原断言 |
| 渲染入口 | 3 条互不共享的写入路径：`update_btn_list`（候选逐格 setStyleSheet）、`update_help_heatmap`→`_render_heatmap_grid`（第二套网格）、`update_image`（首屏逐格 setStyleSheet） | `main.py:1162-1253`、`main.py:1263-1292`、`main.py:1119-1129` |
| 旧数据覆盖新数据 | 成立：`update_btn_list` 末尾会用「最近一次 payload」补渲染**另一套**网格；两份网格的 `best` 与候选序号可以互相矛盾 | `main.py:1244-1251` |
| best 框清除路径 | **不存在**：`_hm_style_for_cell` 只按当前 `best` 加框，payload 变空时靠"重算同一格"覆盖，但第二套网格与候选路径都不重算 | `main.py:1346-1351` |
| 已证明安全（cell_value=11） | **与「未开无概率」同款浅灰底**，两个语义视觉不可区分（=互相覆盖） | `main.py:1399`（旧注释自认"11 与无概率一样"） |
| 自动模式扫描噪声弹窗 | `warning_signal` 直连 `QMessageBox.warning`，`点击后重扫：9/9 格无法可信识别` 每局都会弹 | `main.py:401-403`、`utils/vision.py:435` |
| 自动线程"停止"锚点 | 上一版设计误把 `Visible_signal(False)` 当停止信号；实际该信号在 pbs 近似分支与每轮枚举结束都会发（语义=隐藏进度条） | `utils/probability.py:996,1029` |

## 3. 设计（单一真源 + 单一渲染入口）

**唯一可见棋盘** = `frame_2` / `btn_list`（原本就存在的帮助主交互棋盘，480 格）。
删除 `help_heatmap_frame` / `help_heatmap_btn_list` / `help_heatmap_label_stats` 整块，
只保留一条**纯文本**统计栏 `help_board_label_stats`（不是第二块棋盘）。

**单一渲染入口** = `main.py:1332 _render_help_board()`。每帧从三个缓存输入全量重算：

```
_last_heatmap_payload   (prob / best / best_conf / cell_value / played / win)
_last_cell_value        (payload 无棋盘时的兜底事实棋盘)
_last_candidates        (pos_dict_list，候选叠加层)
        ↓ 每帧全量重算 (style, text) ×480
_render_help_board()
```

`update_btn_list` 与 `update_help_heatmap` 都只更新各自那一份输入，然后调同一个入口
（`main.py:1212` / `main.py:1240`）——**没有"谁覆盖谁"的路径**，两边看到的是同一帧。

**语义优先级（每格一次成型，不可互相覆盖）**，实现于
`main.py:1544 _hm_style_for_cell()` + `main.py:1290 _help_candidate_cell_style()`：

1. 已开数字 0-8 → 文字=数字（事实层；候选叠加**不得**覆盖，见 `main.py:1358` 的 `v == 9` 守卫）
2. 已证明是雷 10 → 红底 rgb(255,0,0)
3. 已证明安全 11 → 绿底 rgb(30,225,0)（**本批新增**，此前与"无数据"同款，语义互相覆盖）
4. 未开 9 + prob → OKLCH 概率色；`best` 另加 3px 蓝框
5. 未开 9 无 prob → 白半透明 rgba(255,255,255,180)（无数据）
6. 候选叠加（仅未开格）→ 判断次序 num + 红/绿/黄框语义

**残留清零的结构性保证**：每帧对全部 480 格重新生成 `(style, text)` 并与缓存比较后整批
`set`。任何上一帧的样式只要不在**最新** payload 里，必然被覆盖回默认底——残留不可能存在。
另加状态生命周期收口点 `main.py:1465 _clear_heatmap_state(which, reason)`。

## 4. 变更清单（file:line，修改后）

### `main.py`
- `main.py:543-580`：**删除第二套网格**（`help_heatmap_frame` / `help_heatmap_btn_list` /
  `help_heatmap_btn_width` / `_hm_help_last`），改为单一统计栏 `help_board_label_stats`
  + 统一棋盘缓存 `_help_board_last` + 候选点击 handler 集合 `_help_click_buttons`。
- `main.py:401`：自动线程告警改走 `_on_auto_warning`（扫描噪声不弹窗）。
- `main.py:408-411`：删除"`Visible_signal(False)` → 清热力图"的错误锚点，改
  `finished.connect(self._on_auto_thread_finished)`（`main.py:646`）；帮助线程同
  `main.py:655 _on_help_thread_finished`。
- `main.py:452`：帮助线程告警改走 `_on_help_warning`（`main.py:635`）。
- `main.py:616 / 635`：`_on_auto_warning` / `_on_help_warning`——含「无法可信识别」的
  扫描噪声降级为 INFO 日志、不弹窗；设置类错误照常弹窗（保留用户可介入告警）。
- `main.py:646 / 655`：`_on_auto_thread_finished` / `_on_help_thread_finished`——线程退出
  后清概率层（锚 `QThread.finished`，不是 Visible）。
- `main.py:1039 reset_btn_list()`：新增清空 `_help_board_last` / `_help_click_buttons`
  （按钮 `sip.delete` 重建后 id 会被复用，缓存不清会跳过重绘 → 残留）。
- `main.py:1100 update_image()`：首屏扫描后调用 `_clear_heatmap_state("both", "new_game")`
  并走统一入口重绘（原逐格 setStyleSheet 循环删除）。
- `main.py:1212 update_btn_list()`：改为「更新候选 + 调统一入口」，删除全部逐格样式分支。
- `main.py:1240 update_help_heatmap()`：改为「更新 payload + 调统一入口 + 更新统计栏」。
- `main.py:1258 _update_help_board_stats()`：统计栏（原 `update_help_heatmap` 尾部逻辑）。
- `main.py:1290 _help_candidate_cell_style()`：候选叠加层唯一真源（原 `update_btn_list`
  四个 if/elif 分支迁入，语义逐分支等价）。
- `main.py:1332 _render_help_board()`：**单一渲染入口**，返回本帧变化格数。
- `main.py:1429 _bind_help_candidate_click()` / `main.py:1457 _unbind_help_candidate_click()`：
  统一棋盘候选格的点击 → 详情弹窗（防重复 connect）+ hover 预览挂接。
- `main.py:1465 _clear_heatmap_state()`：状态生命周期收口点（新局 / 扫描失败 / 线程停止）。
- `main.py:1576`：`cell_value == 11`（已证明安全）**新增绿底**，与「无数据」区分开。
- `main.py:1618 update_heatmap()`：无 `cell_value` 且无缓存棋盘时走清理入口，杜绝
  "上一帧概率色停屏"。

### `tests/test_ui_migration.py`
- `tests/test_ui_migration.py:65-100`：`test_generated_ui_not_patched` 同步布局项数
  （帮助页 6→5）并**新增**「不得有第二套网格 / `helpHeatmapFrame`」断言。
- `tests/test_ui_migration.py:524-546`：帮助页遮挡用例的控件清单去掉已删除的第二套网格。
- `tests/test_ui_migration.py:548-585`：**新增** `test_help_page_has_single_board_grid`——
  枚举帮助页滚动内容下所有承载 ≥100 按钮的容器，断言数量 = 1 且按钮数 = w×h+表头。
- `tests/test_ui_migration.py:590-...`：`TestHelpHeatmapPayload` → `TestHelpUnifiedBoard`，
  全部断言载体改为唯一棋盘 `btn_list`，并新增：
  - `test_latest_payload_wins_over_older_one`（A→B，A 的颜色与 best 框不残留）
  - `test_empty_payload_clears_all_probability_style`（空 payload 全清）
  - `test_clear_heatmap_state_wipes_help_board`（线程停止清理）
  - `test_update_btn_list_shares_single_latest_payload`（payload 不被候选覆盖 + 非候选格保色）
  - `test_candidate_overlay_does_not_override_open_digit`（事实层优先级）
  - `test_candidate_click_handler_not_duplicated`（防重复 handler）
  - `test_auto_thread_finished_clears_auto_heatmap`（自动页 finished 清理）
  - `test_visible_signal_false_does_not_clear_heatmap`（**注入必红**：把上一版错误设计钉死）
  - `test_auto_scan_noise_warning_does_not_popup`（噪声不弹窗 + 真实告警仍弹）

### `tests/test_ui_preview_popup.py`
- `tests/test_ui_preview_popup.py:546-563`：`test_main_window_board_buttons_are_preview_buttons`
  改为断言 `btn_list` + `heatmap_btn_list` 都是 `PreviewHoverButton`，并**新增**
  `assertFalse(hasattr(win, "help_heatmap_btn_list"))`。

### 未改（有意为之）
- `ui/window.py` / `ui/window.ui`：第二套网格本来就是 `main.py` 运行期创建的，生成物里
  `ui/window.ui:953` 只有 `frame_2` 一个棋盘容器，无需改生成物。
- `utils/*`、`cfg.json`、`cpp/*`：算法与配置零改动（任务书硬边界）。

## 5. 验证

环境注记：本仓 `cv2` 只装在 `D:\anaconda3\envs\py310`，系统默认 python 无 cv2
（属环境面，非代码面，见 `review/fix-help-heatmap-overlap.md:394`）。

| # | 命令 | 结果 |
|---|---|---|
| 1 | `D:\anaconda3\envs\py310\python.exe -m compileall -q main.py ui tests utils` | 退出码 0（无输出 = 无错误） |
| 2 | `$env:QT_QPA_PLATFORM="offscreen"; D:\anaconda3\envs\py310\python.exe -m unittest tests.test_ui_migration tests.test_ui_preview_popup tests.test_ui_heatmap` | **Ran 106 tests … OK** |
| 3 | `$env:QT_QPA_PLATFORM="offscreen"; D:\anaconda3\envs\py310\python.exe -m unittest discover -s tests` | **Ran 271 tests in 5.4s … OK**（退出码 0） |
| 4 | 取证脚本 `.tools/tmp/probe_unified_board.py`（offscreen，480 格真实 payload 注入） | 见第 6 节读数 |

复跑命令（原样可复制）：

```powershell
cd D:\python\projects\minesweeper_help
D:\anaconda3\envs\py310\python.exe -m compileall -q main.py ui tests utils
$env:QT_QPA_PLATFORM="offscreen"
D:\anaconda3\envs\py310\python.exe -m unittest discover -s tests
D:\anaconda3\envs\py310\python.exe .tools\tmp\probe_unified_board.py
```

## 6. payload A → B → 空 payload 前后读数

取证脚本 `.tools/tmp/probe_unified_board.py`，offscreen，`resize(1035,800)`，
`stackedWidget.setCurrentIndex(2)`，全部为**合成 payload**（仅作渲染证据，不代表真实局面）。

### [A] 帮助页可见棋盘网格计数（**修改后 = 1**）

```
>=100 按钮的容器数 = 1
  容器 'frame_2' 按钮数=526          # 480 格 + 30 行头 + 16 列头
帮助页格子按钮总数（w*h） = 480
btn_list 行数 x 列数 = 16 x 30
hasattr(help_heatmap_btn_list) = False
findChild('helpHeatmapFrame') = None
verticalLayout_help_content.count() = 5
```

对照修改前：`help_heatmap_btn_list` 存在（480 格，第二套）、`helpHeatmapFrame` 存在、
布局项数 6、格子按钮总数 **960**。⇒ 可见棋盘数量 **2 → 1**。

### [B] 注入 payload A（`prob={(3,4):0.0, (5,5):1.0}`, `best=(3,4)`）

```
btn(3,4).text()=''
btn(3,4).style=... border: 3px solid rgb(0, 0, 255); background-color:rgba(255, 0, 0, 255)   ← best 蓝框 + 精确红底
btn(5,5).style=... border: 2px solid rgb(0, 0, 255); background-color:rgba(0, 255, 0, 255)   ← 精确绿底
stats=当前格非雷的概率: 0.00% ｜ 已完局: 1 ｜ 赢局: 0 ｜ 胜率: 0.00%
```

### [C] 注入 payload B（`prob={(5,5):0.5}`, `best=(5,5)`）→ A 必须清零

```
btn(3,4).text()=''   （A 的 red 是否残留：False）
btn(3,4).style=... border: none; background-color:rgba(255, 255, 255, 180)   ← A 的红底已消失
btn(3,4) 含 3px 蓝框 = False          ← A 的 best 框已消失
btn(5,5).style=... border: 3px solid rgb(0, 0, 255); background-color:rgba(249, 147, 0, 255)
btn(5,5) 含 3px 蓝框 = True           ← B 的 best 框已就位
stats=当前格非雷的概率: 50.00% ｜ 已完局: 2 ｜ 赢局: 1 ｜ 胜率: 50.00%
```

⇒ **最新 payload 单一真源成立**：A 的 `(3,4)` 红底与 best 蓝框零残留，B 的 best 落在 `(5,5)`。

### [D] 空 payload（solver 新局 `clear=True` 形态）→ 概率层全清

```
btn(3,4).style=... background-color:rgba(255, 255, 255, 180)
btn(5,5).style=... background-color:rgba(255, 255, 255, 180)
含 3px 蓝框的格子数 = 0
含 rgba(255, 0, 0 的格子数 = 0
含 rgba(0, 255, 0 的格子数 = 0
stats=当前格非雷的概率: — ｜ 已完局: 3 ｜ 赢局: 1 ｜ 胜率: 33.33%
```

### [E] 语义不可互相覆盖（事实层优先级）

```
(1,1) 已开数字 → text='4'          # 候选 num=9 未能覆盖事实数字
(1,1).style=... background-color:rgba(208, 208, 214, 100)   # 事实层样式，未被候选色覆盖
(2,2) 证明雷   → background-color:rgba(255, 0, 0, 255)
(3,3) 证明安全 → background-color:rgba(30, 225, 0, 255)     # 本批新增，与「无数据」区分
(1,2) 候选证明雷 → text='8'，2px 蓝框 + 红底（候选叠加层）
clicks=[]  mines=[(1, 2)]
```

### [F] 线程生命周期清理（锚 `QThread.finished`，不是 `Visible_signal`）

```
自动页 btn(2,1) 初始 style=... border: 3px solid rgb(0, 0, 255); background-color:rgba(0, 255, 0, 255)
Visible_signal(False) 后 style=（完全一致）
→ 是否保持不变（应 True，Visible 只是隐藏进度条）：True
finished 槽后 style=... border: none; background-color:rgba(255, 255, 255, 180)
自动页 stats=已停止 · 无实时数据
决策卡=建议点击 — · 来源：无实时数据（已清空） / 无估值数据
```

### [G] 两页语义一致性（同一 payload，无候选叠加时逐格比对）

```
逐格不一致的格数 = 0
```

## 7. 未覆盖与 NOT_RUN（如实申报）

| 项 | 状态 | 说明 / 复跑方式 |
|---|---|---|
| **真实鼠标 hover** | **NOT_RUN** | offscreen 平台无真实鼠标，本轮只验证「事件派发到 `enterEvent` 后浮层可见、`leaveEvent` 后延迟关闭」这一层。真实桌面复验：`python main.py` → 帮助人类 → 鼠标移到统一棋盘候选格 → 浮层出现/固定/关闭。另：本席**未改** `ui/preview_popup.py` 的悬停事件链（组织者 13:06 广播要求勿覆盖，其根因修复由组织者席位完成）。 |
| 真实扫雷进程联动（help_thread 端到端） | **NOT_RUN** | offscreen 无扫雷窗口/无真实截图；`update_image` 的 `_gui_scan_once` 在本环境走失败兜底（返回全 9）。真实链路需 `D:\Arbiter_0.52.3\ms_arbiter.exe` 在场，由人工冒烟。 |
| 自动模式胜率/决策链路回归 | NOT_RUN | 未改 `utils/*`，该链路由 `utils/tests` 既有用例覆盖（全量 271 通过）；本席未跑真实对局。 |
| 大局面 pbs 分支的 `Visible_signal(False)` 语义 | 已核对源码 | 确认为"隐藏进度条"（`utils/probability.py:996,1029`），故清理锚点改为 `QThread.finished`；未在真实大局面下观测。 |
| 帮助页统计栏文案在真实帮助流程中的刷新 | NOT_RUN | 只用合成 payload 验证字段格式；真实 `played/win/best_conf` 来自 solver。 |
| 像素级截图比对 | **NOT_RUN（有意不做）** | 记忆 #1212c38e：offscreen 无字体，`grab()` 像素比对会假红；本批一律用控件文字/样式/矩形读数取证。 |

## 8. 剩余风险

1. **`_render_help_board` 每帧全量 480 格**：帮助线程只在候选到达/热力图到达时触发，频率远低于 60fps；`setUpdatesEnabled(False)` 包裹 + 样式去重后实测全量测试 5.4s 无异常。若未来帮助线程改为高频刷新，需复测开销（`bench/bench_ui_render.py` 可用于测帧耗时）。
2. **候选点击 handler 用 `id(btn)` 记账**：与既有 `_preview_buttons`（WeakSet）不同，这里是普通 set。`reset_btn_list()` 已同步清空（`main.py:1039`）；若未来新增其它按钮重建路径，须一并清。
3. **`_clear_heatmap_state` 会清 `_last_candidates`**：即"线程停止/新局"后统一棋盘回到纯事实。若产品希望在停止后仍保留候选序号，需要把候选快照与 payload 快照分离——当前按用户"不得残留"的要求选择了全清。
4. **自动模式告警过滤按文案子串**：「无法可信识别」是 `utils/vision.py:435` 的固定文案；若上游改文案，过滤会失效（弹窗回归）。已在测试里固定该子串，改文案时会红。
5. **两页共享 `_last_heatmap_payload`**：先跑自动模式再切帮助页，帮助页在首个帮助 payload 到达前显示的是自动页的棋盘缓存（`_last_cell_value`）。本批保持既有行为（`review/fix-help-heatmap-overlap.md:355` 已记录），未改。
