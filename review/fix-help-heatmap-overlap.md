# 帮助页布局遮挡 + 帮助页实时热力图 + 真实 hover 修复报告

> 任务来源：用户真实桌面截图（帮助页新增图例/候选卡覆盖棋盘）+ 用户明确要求帮助页像自动页一样显示实时热力图 + 组织者转达「必须修改原始 UI 文件并修复真实 hover」。
>
> 交付状态：`DELIVERED_WITH_EVIDENCE`（产出已落盘 + 已投组织者）
>
> 本轮改动文件（4 个）：
> `ui/window.ui`、`ui/window.py`、`ui/preview_popup.py`、`main.py`、`tests/test_ui_migration.py`、`tests/test_ui_preview_popup.py`
> **未改**：`utils/**`（算法链）、`cfg.json`、`ui/bg_rc.py`。

---

## 1. 结论先行

| 用户可见症状 | 根因（代码位置） | 是否已修 |
| --- | --- | --- |
| 帮助页图例/候选卡盖住棋盘 | `page_3` 的 `QVBoxLayout` 内容最小高 891px / sizeHint 954px > 内容区 800px，Qt 压缩子项到 minimumSize 以下并让相邻项重叠；棋盘按钮实际画到 y=679 而容器只到 671 | ✅ 已修（套 `QScrollArea`，实测交集全为 False） |
| 帮助页没有实时热力图 | `main.py` 只接了 `auto_play_thread.heatmap_signal`（`main.py:408`），`help_thread` 的同名信号**从未 connect** | ✅ 已修（`main.py:421` 接线 + `update_help_heatmap`） |
| hover 浮层实际不出现 | 只用 `attach_hover_preview` 的 eventFilter + `WA_Hover`；24×24 密集网格上 Qt 常不派发 HoverEnter。且 Leave 立即 `hide_if_unpinned()`，鼠标跨 12px 间隙时浮层已被关掉，用户永远点不到「固定」 | ✅ 已修（`PreviewHoverButton` 覆写真实事件 + 220ms 延迟关闭） |
| 自动页新增卡片挤压/文字被切 | 自动页 sizeHint 815px > 800px；图例卡实际 59px 而 sizeHint 75px（切 16px） | ✅ 已修（同套 `QScrollArea`，卡片不再被压缩） |

**证据边界**：真实物理鼠标 hover 在 offscreen 平台**无法验证 ⇒ NOT_RUN**（第 10 节）。其余结论均有本轮实测读数。

---

## 2. 关键前置事实：`ui/window.ui` 与 `ui/window.py` 已严重脱钩（不允许直接 pyuic5 覆盖）

命令（真实执行）：

```
D:\anaconda3\envs\py310\python.exe -m PyQt5.uic.pyuic ui\window.ui -o .tools\tmp\window_gen.py
git diff --no-index -- .tools/tmp/window_gen.py ui/window.py
```

读数：**274 insertions / 125 deletions**。`ui/window.py` 比 `.ui` 多出的手工语义修订（逐条，均需保留）：

- `ui/window.py:14-48` `_make_gradient_qss()`：图例色带改为从 `main.oklch_grad` 采样 11 点生成，修「图例用 sRGB 线性渐变 vs 现场用 OKLCH 感知均匀渐变 → 用户照图例估色系统性高估 15~25%」。
- `ui/window.py:237-243` `frame_4` min=200/max=160 自相矛盾 → 均改 230（第 7 行安全提示被切 6px），并加 rgba(20,20,20,170) 衬底。
- `ui/window.py:289-302` `page_2` 的 `QPushButton{...}` 后代选择器收窄为 `#end_auto_play_thread, #return_to_main_page`（否则 480 个热力图格继承黑字）。
- `ui/window.py:355-366` `pgb` 补 `color: rgb(20,20,20)` + 改全程浅色渐变（原黑字压深墨绿，对比度 1.31~1.80）。
- `ui/window.py:410-431` `label_4` maxHeight 21→31 + `_make_gradient_qss()`。
- 生成器版本：`.ui` 原生成物头为 5.15.4，当前 pyuic5 是 5.15.9。
- `.ui` 里对象名原为 `end_quto_play_thread`（拼写错误），`window.py` 已修为 `end_auto_play_thread`，`main.py` 全部按后者接线。

**因此本轮采用**（已获组织者确认「不跑 pyuic5 重生成」）：

1. `ui/window.ui` 做**结构同构编辑**：加 `QScrollArea` 包裹 + 修正对象名拼写；
2. `ui/window.py` 做**等价手工编辑**，保留全部既有手改；
3. 用 pyuic5 只做**离线生成用于 A/B 对照**（`.tools/tmp/window_gen5.py`），不写回运行时真源。

A/B 对照读数（`.tools/tmp/ab_generated_vs_runtime.py`，生成物 `.tools/tmp/window_gen6.py`）：

```
[structure] generated=58 current=58 mismatch=0
[structure] only_generated=[] only_current=[]
per-layout order identical: True
```

即：`.ui` 现生成物与 `ui/window.py` 的**控件集合、父容器、每个布局的 addWidget 顺序全部一致**——将来若真跑 pyuic5，只需回填第 2 节列的 5 处样式/尺寸手改，结构不会丢。

`ui/window.ui` 本轮 diff 规模：**37 insertions / 1 deletion**（`git diff --stat ui/window.ui`）。为此对内层既有 `<item>` 块**不做重新缩进**（XML 不依赖缩进），只插入 `QScrollArea` 包裹标签——避免产生 300+ 行纯缩进噪声掩盖真实改动。

---

## 3. 首触四问（首次编辑文件前的事实清单）

### 3.1 `ui/window.py`
1. **谁 import 它**：`ui/__init__.py:2`（`from .window import Ui_MainWindow`）→ `main.py:33`（`from ui import ... Ui_MainWindow`）→ `main.py:240`（`class MyMainWindow(QMainWindow, Ui_MainWindow)`）。全树无其它引用（`grep -rn "Ui_MainWindow"` 仅这 3 处）。
2. **影响公共 API**：仅新增生成 UI 对象的属性 `auto_scroll`/`auto_scroll_content`/`verticalLayout_auto_content`/`help_scroll`/`help_scroll_content`/`verticalLayout_help_content`；`frame_explaination`、`frame_2` 的父控件由 `page_3` 改为 `help_scroll_content`。所有既有 objectName、页面索引（3 页）保持不变。
3. **读写数据文件**：否（纯 UI 描述）。
4. **用户指令原话**：「直接调整 ui/window.ui 原始布局，必要时用 pyuic5 重新生成 ui/window.py（不手改生成物）」「帮助页显示 heatmap 主视图且不遮挡棋盘」。

### 3.2 `ui/preview_popup.py`
1. **谁 import 它**：`main.py:34`；`tests/test_ui_migration.py:29-34`；`tests/test_ui_preview_popup.py:29-40`。
2. **影响公共 API**：原有 `PreviewPopup`/`attach_hover_preview`/`compute_popup_position`/`summarize_payload`/`sample_board`/`describe_value`/`transitions_text`/`VALUE_KIND_LABELS`/`NO_TRANSITION_*` **全部保留且语义不变**；新增 `PreviewHoverButton`、`HIDE_DELAY_MS`、`PreviewPopup.schedule_hide/cancel_scheduled_hide`。
3. **读写数据文件**：否；只消费调用方传入的 payload 字典（字段 `cell/value/value_kind/source/cell_value/transitions`）。
4. **用户指令原话**：「将帮助棋盘原始按钮的 enterEvent/focusIn/leaveEvent 真实接到 PreviewPopup，验证悬停时浮层可见，浮层进入时不被 source leave 立即关闭」。

### 3.3 `ui/window.ui`
1. **谁读它**：仅开发期 `pyuic5`；运行期无 Python import（`grep -rn "window.ui"` 仅本报告与取证脚本）。
2. **影响公共 API**：间接——生成物里的 objectName 被 `main.py` 按名引用（`frame_2`/`label_p3`/`frame_3`/`click_all`/`pgb_p3`/`page_2`/`page_3`/`verticalLayout_2`/`verticalLayout_3`），改名即破坏；本轮除修正 `end_quto_play_thread` 拼写外未改任何既有 objectName。
3. **读写数据文件**：否（XML）。
4. **用户指令原话**：「必须修改原始 UI 文件并修复真实 hover」。

### 3.4 `main.py`
1. **谁 import 它**：入口脚本（`python main.py`）；测试 `tests/test_ui_migration.py:28`、`tests/test_ui_preview_popup.py:28`、`tests/test_ui_heatmap.py:17` 直接 `import main`。
2. **影响公共 API**：新增 `update_help_heatmap`/`_hm_style_for_cell`/`_render_heatmap_grid`；`update_heatmap` 行为等价（渲染逻辑抽函数，去重缓存语义不变）；新增属性 `help_heatmap_btn_list`/`help_heatmap_frame`/`help_heatmap_label_stats`/`_hm_help_last`。
3. **读写数据文件**：读 `cfg.json`（经 `setting.load_cfg()`，字段 `a/w/h/bx/by/cell_width/path/limit/win_name/speed`）；写 `image/save.png`（截图）、`state/*`（由 solver 写）。本轮**未改**这些读写路径。
4. **用户指令原话**：「修复按钮/热力图刷新和切页/帮助线程信号接线，不能破坏现有帮助候选 click_all」。

### 3.5 `tests/test_ui_migration.py`
1. **谁 import 它**：pytest / `unittest discover`，无生产代码引用。
2. **影响公共 API**：无（测试模块）。
3. **读写数据文件**：否。
4. **用户指令原话**：「增加 offscreen geometry 回归：实例化后切换自动/帮助页，断言棋盘区域与新增卡片/热力图区域矩形不相交；注入 payload 后帮助热力图至少有 3 个格子文字/样式更新」。

---

## 4. 修改前 geometry 读数（1035×800 offscreen，Windows / Python 3.10.16 / PyQt5 5.15.10）

取证脚本：`.tools/tmp/probe_geom.py`（`QT_QPA_PLATFORM=offscreen`，`resize(1035,800)`，`processEvents()` × 12 后 `layout().activate()`）

### 4.1 帮助页 `page_3`（修改前）—— 遮挡真因

```
layout geometry (0,0,835,800)   layout.minimumSize 616x891   layout.sizeHint 616x954
  [0] return_to_main_p3       (9,  9,  150, 40)
  [1] frame_explaination      (9, 55,  817, 210)
  [2] frame_2   (棋盘容器)     (9,271, 817, 400)
  [3] uiCard 帮助图例          (9,570, 817, 59)   ← sizeHint 302x91（被压 32px）
  [4] uiCard 帮助候选详情      (9,635, 817, 116)  ← sizeHint 308x131（被压 15px）
  [5] frame_3 (终止/click_all) (9,757, 817, 34)
  [6] pgb_p3                  (0,  0, 580, 35)    ← 未被布局定位（堆在左上角）

棋盘按钮真实包围盒：x=33..753 y=295..679（24px 格，16 行）
frame_2 底边 = 271+400 = 671  ⇒  按钮底边 679 溢出容器 8px
help_legend_card    y 570..629  ∩ 棋盘 y295..679  → 覆盖棋盘 59px
help_candidate_card y 635..751  ∩ 棋盘 y295..679  → 覆盖棋盘 44px
```

⇒ 与用户截图完全一致：两张卡片压在棋盘最后 3~4 行上；且 `pgb_p3` 因布局溢出被丢到 (0,0)。

### 4.2 自动页 `page_2`（修改前）—— 同一机制，症状较轻

```
layout geometry (0,0,835,800)   layout.minimumSize 222x756   layout.sizeHint 600x815
  [0] heatmap_frame           (9,  9, 817, 400)
  [1] heatmap_label_stats     (339,415, 157, 25)
  [2] uiCard 求解状态         (9,446, 817, 43)   hint 204x43
  [3] uiCard 当前决策         (9,495, 817, 81)   hint 190x81
  [4] uiCard 图例             (9,582, 817, 59)   hint 582x75  ← 被压 16px（文字被切）
  [5] uiCard 候选详情         (9,647, 817, 144)  hint 278x143
  [6] end_auto_play_thread    (0,0,150,40)   ← 未被布局定位
  [7] return_to_main_page     (0,0,150,40)   ← 未被布局定位
  [8] pgb                     (0,0,580,35)   ← 未被布局定位
```

⇒ 自动页矩形上未相交（因为卡片刚好排到 791px），但 sizeHint 815 > 800 已是**临界超限**：图例被压 16px、三个固定控件被丢到 (0,0)。热力图格也溢出容器 8px（`y=384..408` vs 容器 400）。

---

## 5. 修改后 geometry 读数（同一环境，`resize(1038,830)` 后实际 1035×800——窗口 `maximumSize` 上限为 1035×800）

取证脚本：`.tools/tmp/collect_readings.py`，输出保存 `.tools/tmp/fixhelp/readings.txt`

### 5.1 自动页 `page_2`（修改后）

```
scroll=(9,9,817,782)  viewport=(0,0,803,782)  contentH=807  vbar=True
  heatmap_frame          = (9,  9, 785, 408)
  heatmap grid bbox      = (33, 33, 720, 384)
  heatmap_label_stats    = (323,423, 157, 25)   intersects(heatmap)=False
  status_card            = (9,454, 785, 43)     intersects(heatmap)=False
  decision_card          = (9,503, 785, 81)     intersects(heatmap)=False
  legend_card            = (9,590, 785, 59)     intersects(heatmap)=False
  candidate_card         = (9,655, 785, 143)    intersects(heatmap)=False
  heatmap frame contains grid: True
```

### 5.2 帮助页 `page_3`（修改后）

```
scroll=(9,55,817,696)  viewport=(0,0,803,696)  contentH=1289  vbar=True
  fixed_bar(frame_3)     = (9,757, 817, 34)      ← 页面级，固定在页底
  pgb_p3                 = (0,0,580,35)         ← 仍在页底布局里（见 10.3 说明）
  frame_2 (board frame)  = (9,225, 785, 408)
  board buttons bbox     = (33,249, 720, 384)
  help heatmap frame     = (9,670, 785, 408)
  help heatmap grid bbox = (33,694, 720, 384)
  board frame contains buttons: True
  heatmap frame contains grid : True
  heatmap below board         : True (heat.top=670  board.bottom=632)

  frame_explaination       = (9,  9, 785, 210)  ∩board=False  ∩heat=False
  help_heatmap_label_stats = (323,639, 157, 25) ∩board=False  ∩heat=False
  help_legend_card         = (9,1084, 785, 59)  ∩board=False  ∩heat=False
  help_candidate_card      = (9,1149, 785, 131) ∩board=False  ∩heat=False
```

**前后对比结论**：修改前 2 处真实矩形相交（59px / 44px 覆盖棋盘）+ 1 处控件溢出容器 8px + 3 个控件未定位；修改后**全部 0 相交**，所有网格都装在各自容器内，帮助热力图整体位于棋盘下方。

---

## 6. 实现清单（file:line）

### 6.1 `ui/window.py`（运行时真源，结构同构手工编辑）

- `ui/window.py:349-368`：`page_2` 新增 `auto_scroll`（`QScrollArea`，`NoFrame` + `widgetResizable` + 透明样式）+ `auto_scroll_content` + `verticalLayout_auto_content`，以 `addWidget` 插在 `plainTextEdit` 之后、`pgb` 之前（顺序与 `.ui` 一致，A/B 已核）。
- `ui/window.py:410-433`：`page_3` 新增 `help_scroll` / `help_scroll_content` / `verticalLayout_help_content`，以 `addWidget(self.help_scroll, 1)` 吃掉全部剩余高度（stretch=1），使 `frame_3`/`pgb_p3` 固定在页底。
- `ui/window.py:434`、`ui/window.py:523`：`frame_explaination`、`frame_2` 的父控件从 `page_3` 改为 `help_scroll_content`，并改为加进 `verticalLayout_help_content`（滚动内容里）。
- 未改动任何既有手改（第 2 节 5 条全部保留）。

### 6.2 `ui/window.ui`（源头同步，diff 37+/1-）

- `verticalLayout_2` 中新增 `auto_scroll`（含 `auto_scroll_content` / `verticalLayout_auto_content`），位置在 `plainTextEdit` 与 `pgb` 之间（与 `ui/window.py` 的 `addWidget` 顺序一致，A/B 已验证）。
- `page_3` 的 `frame_explaination` + `frame_2` 两个既有 `<item>` 整体包进 `help_scroll`（`help_scroll_content` / `verticalLayout_help_content`），`help_scroll` 加在 `return_to_main_p3` 之后。
- 修正对象名拼写：`end_quto_play_thread` → `end_auto_play_thread`（1 处 `<widget name>`；验证读数：生成物中 `end_auto_play_thread` 出现 10 次、`end_quto` 出现 0 次）。
- CRLF 与编码保持不变（写回前转 LF、写回时还原 CRLF；文件无 BOM）。
- XML 合法性：`ElementTree.parse` 通过；`pyuic5` 生成退出码 0。

### 6.3 `ui/preview_popup.py`（真实 hover 通道）

- `ui/preview_popup.py:229`：新增 `HIDE_DELAY_MS = 220`（鼠标从按钮跨 12px 间隙走进浮层所需窗口）。
- `ui/preview_popup.py:250-256`：`PreviewPopup` 新增单次 `QTimer` `_hide_timer`。
- `ui/preview_popup.py:397-421`：`schedule_hide()`（源按钮 Leave 只起定时器）、`cancel_scheduled_hide()`、`_delayed_hide()`；`close_popup()` 先停表。
- `ui/preview_popup.py:424-431`：`PreviewPopup.enterEvent` 取消待关闭、`leaveEvent` 重新安排——解决「浮层一出现就被 source leave 关掉」。
- `ui/preview_popup.py:550-613`：新增 `PreviewHoverButton(QPushButton)`，覆写 `enterEvent` / `leaveEvent` / `focusInEvent` / `focusOutEvent` **真实接线**到 popup（`bind_preview` + `_show_preview`），`payload_provider` 语义与旧过滤器一致（返回 falsy 不显示、异常静默）。
- `ui/preview_popup.py:456-495`：`PreviewHoverFilter` 保留给非按钮控件与既有单测，`Leave` 仍走 `hide_if_unpinned()`（保持原契约）。

### 6.4 `main.py`（接线 + 渲染复用 + 帮助热力图）

- `main.py:34`：`from ui.preview_popup import PreviewPopup, attach_hover_preview, PreviewHoverButton`。
- `main.py:421`：**新增** `self.help_thread.heatmap_signal.connect(self.update_help_heatmap)` —— 这是「帮助页没有热力图」的直接根因修复（此前只有 `main.py:408` 的自动线程那条）。
- `main.py:446/456/473`：首次构建的棋盘按钮（列头/格/行头）改为 `PreviewHoverButton`。
- `main.py:486-488`：建完网格后按真实行列收紧 `frame_2` 最小尺寸到 `(w+1)*24 × (h+1)*24`（原 `.ui` 写死 400，16 行需 408）。
- `main.py:499-539`：自动页热力图容器/统计栏挂到 `auto_scroll_content`；容器高度改为 `(h+1)*24`（408），消除网格溢出 8px。
- `main.py:541-593`：**新增帮助页实时热力图主视图**——`help_heatmap_frame` / `help_heatmap_btn_list` / `help_heatmap_label_stats` / `_hm_help_last`，全部挂在 `help_scroll_content` 并追加在棋盘之后（`addWidget`），统计栏在网格上方。
- `main.py:650-717`：`_build_compact_ui` 的注入目标改为滚动内容布局；帮助页两张卡改为 `addWidget`（追加在棋盘/热力图之后，永不覆盖棋盘）。
- `main.py:820-847`：`_attach_preview` 按类型分流——`PreviewHoverButton` 只 `bind_preview`（**不挂 eventFilter**，否则过滤器会在 Leave 时立刻 `hide_if_unpinned()` 抵消 220ms 延迟）；其它控件保留旧双通道。
- `main.py:1018/1036/1046`：`reset_btn_list` 重建的按钮同样用 `PreviewHoverButton`（帮助页点「帮助人类」会整批重建网格，漏改这里 hover 会静默失效）。
- `main.py:1254-1261`：`update_btn_list` 末尾用最近 payload **补渲染**帮助热力图（候选先到、heatmap 后到时不再出现「候选有内容、热力图全灰」）。
- `main.py:1263-1305`：**新增** `update_help_heatmap(payload)`——帮助线程 `heatmap_signal` 的槽，消费 `cell_value/prob/best/best_conf/played/win`，写自己的统计栏，不碰自动页状态条/决策卡。
- `main.py:1310-1359`：抽出 `_hm_style_for_cell(v, p, is_best)`——自动页与帮助页**共用同一配色判定**（红 `p<=0.01` / 绿 `p>=0.99` / 其余走 `oklch_grad` 原色带 / `best` 加 3px 蓝框 / 已开格白字数字 / 推理雷红底）。
- `main.py:1361-1390`：抽出 `_render_heatmap_grid(...)`，返回本帧变化格数（供测试断言「确有更新」）。
- `main.py:1392-1410`：`update_heatmap` 改为调用上述共用函数（渲染行为与修改前逐格等价，`_hm_last` 去重语义不变）。

### 6.5 测试

- `tests/test_ui_migration.py:69-88`：`test_generated_ui_not_patched` 的**测量点**修正（判据语义不变：卡片仍存在且不在生成物里）——页面级 `verticalLayout_2` 现在只剩滚动区/按钮/进度条，卡片在 `verticalLayout_auto_content`；断言改为「页面级 ≥4 项 + 两个内容布局各 ≥6 项」。文件内已留痕说明改因。
- `tests/test_ui_migration.py:432-678`：新增 `TestGeometryNoOverlap`（`tests/test_ui_migration.py:457`，5 个用例）+ `TestHelpHeatmapPayload`（`tests/test_ui_migration.py:559`，6 个用例）。
- `tests/test_ui_preview_popup.py:444-559`：新增 `TestPreviewHoverButtonRealEvents`（9 个用例）。

---

## 7. 帮助热力图语义一致性（强制要求）

要求：「必须消费最近 heatmap payload 的 cell_value/prob/best/best_conf，并与自动页颜色语义一致」。

实现方式：**不是复制一份配色代码**，而是把判定抽成 `_hm_style_for_cell` 单一真源，自动页 `update_heatmap` 与帮助页 `update_help_heatmap` 都调 `_render_heatmap_grid`。所以「语义一致」是构造性保证，并且有测试逐格断言：

```
test_help_heatmap_colors_match_auto_page_semantics
  同一 payload 下 480 格逐格比较 (text, styleSheet)
  → 不一致数 0 / 480（实测读数：自动页 vs 帮助页逐格不一致数 = 0 / 480）
```

payload 注入实测（`.tools/tmp/collect_readings.py`）：

```
changed cells = 480（阈值 ≥3）
(0,0) text='3'  style 含 rgba(208, 208, 214, 100)（已开格浅灰底）
(1,0) 红色实底（cv=10 推理雷）
(0,1) is_best=(2,1) → style 含 "border: 3px"（最佳格蓝框）
stats = 当前格非雷的概率: 99.50% ｜ 已完局: 5 ｜ 赢局: 2 ｜ 胜率: 40.00%
help_thread.heatmap_signal receivers = 1
auto_thread.heatmap_signal receivers = 1
```

---

## 8. 自动页检查结论

自动页修改前**矩形上未相交**，但存在同一机制的临界症状（第 4.2 节）：`sizeHint 815 > 800`、图例卡被压 16px、3 个固定控件被丢到 (0,0)、热力图格溢出容器 8px。修改后：全部 0 相交、网格装在容器内、卡片按 sizeHint 展开、固定控件回到布局里。

---

## 9. 验证证据

### 9.1 定向测试（修改后，新鲜输出）

```
cd D:\python\projects\minesweeper_help
$env:QT_QPA_PLATFORM="offscreen"
D:\anaconda3\envs\py310\python.exe -m unittest tests.test_ui_migration tests.test_ui_preview_popup tests.test_ui_heatmap
→ Ran 100 tests in 3.031s
→ OK
```

### 9.2 全量测试（新鲜输出）

```
D:\anaconda3\envs\py310\python.exe -m unittest discover -s tests
→ Ran 255 tests in 9.325s
→ OK
```

（修改前基线同为 255 项；本轮新增 20 个用例、修改 1 个断言载体，项数变化来自新增用例。）

### 9.3 编译检查

```
D:\anaconda3\envs\py310\python.exe -m compileall -q main.py ui\window.py ui\preview_popup.py tests\test_ui_migration.py tests\test_ui_preview_popup.py
→ compileall exit=0
```

`.ui` XML 合法性：`xml.etree.ElementTree.parse` 通过；`pyuic5` 生成退出码 0。

### 9.4 截图为证据（**不是**真实鼠标验收）

生成脚本：`.tools/tmp/shoot_after.py`（`QT_QPA_PLATFORM=offscreen`，`resize(1038,830)`；Qt 上限把实际窗口夹到 1035×800；payload 为**合成数据，仅作渲染证据**）。输出目录 `.tools/tmp/fixhelp/`：

| 文件 | 内容 |
| --- | --- |
| `win_auto_after.png` | 自动页整窗（含热力图+统计栏+4 卡，首屏） |
| `page_auto_after_top.png` / `_bottom.png` | 自动页滚动顶/底 |
| `win_help_after.png` | 帮助页整窗（棋盘在首屏，卡片在下方不覆盖） |
| `page_help_after_top.png` | 帮助页滚动顶（返回键 + 说明 + 棋盘） |
| `page_help_after_heatmap.png` | 帮助页滚到热力图处（实时热力图主视图） |
| `page_help_after_bottom.png` | 帮助页底部（图例 + 候选详情，位于棋盘之外） |
| `readings.txt` | 第 5 节全部几何读数原文 |

⚠️ offscreen 下 `QFontDatabase: Cannot find font directory` ⇒ 字体缺失、文字形态不可信；因此**不做像素比对**，只把截图当布局形态证物，判据一律用几何矩形与控件状态（这也是为什么第 5 节给的是矩形读数而不是截图 diff）。

---

## 10. 未覆盖与边界（逐条如实申报）

### 10.1 真实物理鼠标 hover —— **NOT_RUN**

- offscreen 平台没有窗口系统，`QEvent::Enter/Leave` 不会被鼠标真实合成。本轮验证到的是「事件派发到 `enterEvent/focusInEvent` 后浮层可见、`leaveEvent` 后延迟关闭、`popup.enterEvent` 取消关闭」这一层（`TestPreviewHoverButtonRealEvents` 9 个用例）。
- **必须在真实桌面上人工复验**：鼠标移到帮助页棋盘候选格 → 浮层出现 → 移进浮层 → 浮层不消失 → 点「固定」/「关闭」正常；移开 → 约 220ms 后消失。
- 复跑命令（真实桌面，不要加 offscreen）：
  ```
  cd D:\python\projects\minesweeper_help
  python main.py
  ```

### 10.2 真实扫雷对局数据下的帮助热力图 —— **NOT_RUN**

- 本轮热力图渲染验证用的是**合成 payload**（480 格真实编码契约 + 3 个概率 + 1 个推理雷 + 1 个最佳格）。真实帮助线程的 `heatmap_signal` 需要扫雷程序在跑才能发射，无法在 offscreen 环境产生。
- 已用代码级证据补强：`help_thread.heatmap_signal.receivers() == 1`（确认接线真实存在，不是空槽）；`update_btn_list` 末尾补渲染路径有专门用例。
- 复跑方式：真实桌面启动 `python main.py` → 点「帮助人类」→ 观察帮助页棋盘下方热力图是否随每轮计算刷新。

### 10.3 `pgb_p3` 的 `(0,0,580,35)` 读数说明（已定位，非缺陷）

- 第 5.2 节里 `pgb_p3 = (0,0,580,35)` 是因为**探查时它处于隐藏态**（`main.py:362-364` 构造即 `setVisible(False)`，`update_pgb` 才会显示），Qt 不参与布局的隐藏控件 `geometry()` 保持初值。
- 显式 `setVisible(True)` 后复测：

```
pgb_p3 visible = False  geom (0, 0, 580, 35)     ← 探查默认态
after show: visible = True  geom (9, 756, 817, 35)
help_scroll geom (9, 55, 817, 655)   ← 让位给进度条后自动缩短
frame_3     geom (9, 716, 817, 34)
```

⇒ 进度条本身在布局里正常工作，且让位后 `help_scroll` 底部 710 < `frame_3` 顶部 716，**无重叠**。这是既有显隐逻辑，本轮未改。

### 10.4 帮助热力图格未挂 hover 预览（设计取舍）

- 自动页热力图格挂了 hover 预览（`main.py:1450-1458` 的循环）。帮助页热力图格**故意不挂**：帮助页主交互是棋盘按钮（已挂），热力图与棋盘是**同一批坐标**，两套浮层会互相覆盖。
- 若用户希望帮助热力图格也能 hover 看数值：这是**未实现**，不是回归。

### 10.5 帮助热力图与自动热力图共享 `_last_cell_value`

- 两者都写同一个缓存（`_last_cell_value` 供候选 hover 预览的 7×7 局部棋盘用）。如果用户先跑自动模式再切到帮助模式，帮助页在帮助线程发出第一个 payload 之前，可能短暂显示**上一个自动模式的 payload**。
- 影响面：仅首帧观感，帮助线程 `mine_clear1/mine_clear3_1` 会立刻 `_emit_heatmap`（`utils/deduction.py:41,255`）覆盖。未做「切页清空」是为了不引入新的状态机（最小改动原则）。

### 10.6 `ui/window.py` 的手改仍会在未来 pyuic5 重生成时丢失

- 本轮把 `.ui` 的结构同步好了（A/B：58 控件 / 0 mismatch / 布局顺序一致），但第 2 节列的 5 处**样式与尺寸**手改没有回填到 `.ui`。
- 回填清单已写在第 2 节；**下次真要跑 pyuic5 前必须先回填这 5 处**，否则会静默回退视觉/对比度修复。

### 10.7 未做的事（明确排除）

- 未改 `utils/probability.py` / `utils/solver.py` / `utils/deduction.py` 任何一行（算法链零改动）。
- 未改 `cfg.json`、未新增依赖、未跑 `pyuic5` 写回。
- 未做全量回归矩阵与验收终验（按 Verification_Boundary：executor 只承担 unit 层；integration/acceptance 归 test-engineer / verifier）。

---

## 11. 复跑命令汇总

```powershell
cd D:\python\projects\minesweeper_help
$env:QT_QPA_PLATFORM = "offscreen"

# 几何 + payload 回归（本轮新增）
D:\anaconda3\envs\py310\python.exe -m unittest tests.test_ui_migration tests.test_ui_preview_popup tests.test_ui_heatmap -v

# 全量
D:\anaconda3\envs\py310\python.exe -m unittest discover -s tests

# 编译
D:\anaconda3\envs\py310\python.exe -m compileall -q main.py ui\window.py ui\preview_popup.py

# 读数复现
D:\anaconda3\envs\py310\python.exe .tools\tmp\collect_readings.py

# .ui ↔ window.py 结构 A/B
D:\anaconda3\envs\py310\python.exe -m PyQt5.uic.pyuic ui\window.ui -o .tools\tmp\window_gen5.py
D:\anaconda3\envs\py310\python.exe .tools\tmp\ab_generated_vs_runtime.py
```

> 环境注记：本仓依赖的 `cv2` 只装在 `D:\anaconda3\envs\py310`，系统默认 `python`（anaconda base）**没有 cv2**，因此以上命令一律用 `D:\anaconda3\envs\py310\python.exe`。首次用默认 python 跑会直接 `ModuleNotFoundError: No module named 'cv2'`（属环境面，非代码面）。
