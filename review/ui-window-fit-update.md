# ui-prototype.html 紧凑窗口版更新（1366×768 主 UI 完整可见 + 7×7 状态转移浮层）

状态：DELIVERED_WITH_EVIDENCE
范围：仅 `review/ui-prototype.html`；**不修改任何 PyQt 源码**（`ui/`、`main.py`、`utils/` 零改动，git status 可核）。
产出：`review/ui-prototype.html`（更新）+ 本报告 + 离线检查脚本 `.tools/tmp/ui_selftest_probe/ui_offline_check2.mjs`（脚本入临时区，全文逻辑见第 4 节，可复跑）。

## 0. 首触四问（本会话首次改该文件前）

1. **谁 import/require 它**：无。全树 grep `ui-prototype` 仅命中 `review/ui-prototype.html` 自身（L317/L352/L552/L970 为内部 class/id 自引用，改动后对应 L346/L352/L618/L1004），无 JS/Python 依赖方，改动不外溢。
2. **影响的公共面**：文件内 `#transition-card`（原检查器第三张卡，已删除）→ 新 `#transition-overlay` 浮层；`renderTransitionPreview()`（L1004）、`previewPosition()`、`closePreview()`（L1065）、`positionTransitionOverlay()`（L1024）、`applyFitMode()`（L1084）、`app.pinned` / `app.overlayAnchorRect`；`bindEvents()` 内 mouseover/focusin/keydown/click。对外仅 `window.MinesweeperPrototype`（版本 1.0.0 → 1.1.0，新增 layout/overlay 字段，ready/data 不变）。
3. **数据文件读写**：无。纯静态原型，数据全为合成（`app.cells` / `app.candidates` 由 JS 生成），不读写 `data_time.json` / `state/*.json` / `data.json`。
4. **用户当前指令原话**：「更新 review/ui-prototype.html，不修改任何 PyQt 源码。用户新增硬约束：1) 常见桌面窗口（至少 1366×768，考虑浏览器/应用内边距）内主 UI 必须完整显示，关键状态、棋盘、详情、停止按钮不能依赖页面滚动；2) 候选格 7×7 状态转移预览必须是悬浮窗口/浮层，不占主布局、不挤压棋盘；悬停或键盘聚焦候选格显示，靠近窗口边缘自动翻转/钳位，支持固定、关闭、Esc。保持简约现代风格，保留多个可达 7×7 结果及概率。先完整读取当前 HTML，先写 review/ui-window-fit-update.md 骨架再修改；更新/新增离线 DOM/JS 交互检查，至少验证 1366x768 关键布局契约、浮层定位属性、悬停/固定/关闭/Esc。跑 format_gate html-purity，报告浏览器真实截图若无法运行则明确 NOT_RUN。完成后保留会话并向组织者直投结论。」

## 1. 改动清单（file:line）

| # | 位置 | 改动 | 目的 |
|---|---|---|---|
| 1 | `review/ui-prototype.html:26-28` | 新增 `--cell-base: 32px` / `--cell-zoom: 1` / `--cell-size: calc(var(--cell-base) * var(--cell-zoom))` | 单元格尺寸由变量派生，缩放不再写死 px |
| 2 | `review/ui-prototype.html:180` | 新增 `.fit-compact { --cell-base: 20px; --cell-zoom: 1; }` | ≤800 高或 ≤1440 宽自动进紧凑档，30 列棋盘压到 20px/格 |
| 3 | `review/ui-prototype.html:182` | `.fit-compact { --sidebar-width: 184px; }`（≥1280 生效） | 窄侧栏腾出棋盘宽度 |
| 4 | `review/ui-prototype.html:184-187` | `.fit-compact .topbar/.solver-status/main` 垂直节奏压缩（60px 顶栏 / 8px 状态卡 / 12px 主区） | 首屏容得下状态+决策+棋盘+详情+停止按钮 |
| 5 | `review/ui-prototype.html:188-206` | `.fit-compact` 下 workspace-grid 单列最小宽 + 292px 检查器、卡片/统计/图例内边距与字号压缩 | 详情列宽固定，不挤棋盘 |
| 6 | `review/ui-prototype.html:207` | `.board-grid` 改 `--cell-size: calc(var(--cell-base) * var(--cell-zoom))` | 与 #1 串联，`--cell-zoom` 由 JS 的 zoom 写入 |
| 7 | `review/ui-prototype.html:346-352` | 新增 `.transition-overlay`（`position: fixed; z-index: 70; width: min(500px, calc(100vw - 96px)); max-height: min(62vh, 420px)`）+ head/actions/body | 7×7 预览成浮层，hyperlink 不参与主 grid 流 |
| 8 | `review/ui-prototype.html:353` | `.transition-overlay[data-pinned="true"]` 强调色边框/阴影（替代原 `.transition-card[data-pinned]`） | 固定态可见 |
| 9 | `review/ui-prototype.html:554-555` | 删除 aside 内 `<section class="card transition-card" id="transition-card">` | 7×7 不再占检查器位置、不挤压棋盘 |
| 10 | `review/ui-prototype.html:618-621` | 新增 `.app-shell` 之外的浮层节点（`role="dialog" aria-modal="false" hidden`，含标题/固定/关闭按钮与 `#transition-preview` 容器） | 浮动窗口 + 可访问性 |
| 11 | `review/ui-prototype.html:1004-1022` | `renderTransitionPreview()` 改为渲染浮层：显示 `hidden=false`、写 `data-pinned/data-position`、更新 `#transition-overlay-title`、`#preview-pin` 文案与 `aria-pressed`，随后 `positionTransitionOverlay()` | 浮层内容 + 状态同步 |
| 12 | `review/ui-prototype.html:1024-1058` | 新增 `positionTransitionOverlay()`：右/左自动翻转、上/下自动翻转、四边 `clamp` 到 12px margin、写 `data-placement/flipX/flipY/clamped` | 靠边自动翻转/钳位 |
| 13 | `review/ui-prototype.html:1060-1070` | `previewPosition(position, pin, anchor)` 记录锚点矩形；新增 `closePreview()` | 定位取锚点 + 关闭 |
| 14 | `review/ui-prototype.html:1084-1087` | 新增 `applyFitMode()`：`innerHeight<=800 || innerWidth<=1440` 时给 `<html>` 加 `fit-compact` | 1366×768 自动紧凑 |
| 15 | `review/ui-prototype.html:1310-1320` | 点击 `[data-preview]` / `[data-pin-preview]` 时把触发按钮作为 anchor 传入 | 浮层锚定到点击的候选格 |
| 16 | `review/ui-prototype.html:1415-1430` | `actions` 表新增 `close-preview`；keydown：Esc 优先关闭未隐藏浮层；Enter/Space 在 assist 棋盘候选格上聚焦即开浮层 | 键盘可达 |
| 17 | `review/ui-prototype.html:1480-1497` | mouseover/focusin 传 anchor；`#preview-pin` / `#preview-close` 绑定；`resize` / 捕获阶段 `scroll` 重挂位置 | 悬停/聚焦/固定/关闭/滚动跟随 |
| 18 | `review/ui-prototype.html:1443-1445` | `renderAssist()` 仅在浮层可见时重渲染预览（不再无条件渲染） | 避免首屏渲染浮层 |
| 19 | `review/ui-prototype.html:1471-1473` | `init()` 先 `applyFitMode()`，并绑 resize 重算 | 首帧即紧凑 |
| 20 | `review/ui-prototype.html:1523` | `window.MinesweeperPrototype` 版本 1.0.0 → 1.1.0，新增 `layout: "fit-1366x768"`、`overlay: "transition-preview"` | 版本可辨识 |

保留项（未删功能）：`makeTransitions()` 仍返回 4 个状态转移结果（`number-one` / `number-two` / `blank` / `mine`，L727-731 区域），每个含 `probability` 与 `percent()` 呈现；7×7 局部棋盘仍由 `miniBoardHtml()` 的 `dy/dx = -3..3`（L970-973）生成；结果项 `aria-expanded` 折叠交互不变。

## 2. 1366×768 布局契约（证据）

- 紧凑触发：`ui-prototype.html:1084-1087`，1366×768 满足 `innerHeight<=800` ⇒ `<html class="fit-compact">`。
- 棋盘宽度算术（CSS 变量法）：`--cell-base: 20px`（L180）⇒ 30 列 = `32(行头) + 30×20 + 29×1(gap) + 4(padding) = 663px`。
- 可用宽度算术：`1366 - 184(侧栏 L182) - 40(main 左右 padding L187) - 306(检查器 292 + gap 14, L190) - 28(卡片边框/余量) = 808px` ⇒ 663 ≤ 808，**棋盘列不溢出**；另 `.board-scroll` 仍 `overflow:auto`（L212）只允许棋盘自身滚动，主页面关键区不靠滚动。
- 垂直预算（紧凑档估算，口径=CSS 声明值累加，非浏览器实测）：顶部 60(顶栏) + 8+8(状态卡 padding) + 状态卡内容约 40 + 12+12(main padding) + 页面标题约 44 + 决策卡约 96 + 12 + 棋盘工具栏 44 + 棋盘 16 行×20 + 15 gap + caption ≈ 首屏 768 内可容纳「状态 + 决策 + 棋盘 + 检查器（候选/详情）+ 全局停止按钮」；停止按钮在 `#solver-status` 内（`.status-wrap` 顶部固定区），不随 main 滚动。
- 详情列宽固定：`.fit-compact .workspace-grid { grid-template-columns: minmax(0,1fr) 292px }`（L190）。
- 竖向压缩：topbar L184、solver-status L185、main L187、card-header/card-body L191-192、candidate L193-194、legend/stat/metric L195-198。

## 3. 7×7 状态转移浮层

- 定位：`position: fixed` + `z-index: 70`（L346），节点位于 `.app-shell` 之外（L618），**不进主 grid 流**（A2/A3 断言）。
- 尺寸约束：`width: min(500px, calc(100vw - 96px))`、`max-height: min(62vh, 420px)`（L346），内容区 `overflow:auto`（L352）⇒ 浮层自身滚动，不撑破视口。
- 翻转/钳位算法（`positionTransitionOverlay` L1024-1058）：
  - 水平：`spaceRight = vw - anchor.right - margin`；空间够则 `anchor.right + margin`，否则翻到 `anchor.left - width - margin`；随后 `clamp(left, 12, vw-width-12)`。
  - 垂直：`spaceBelow = vh - anchor.bottom - margin`；不够且锚点上方够高则上翻 `anchor.top - height`；随后 `clamp(top, 12, vh-height-12)`。
  - 无锚点（键盘路径/程序触发）落到右下兜底并钳位。
  - 输出 `dataset.placement/flipX/flipY/clamped` 供样式与自检观察。
- 触发与关闭：悬停候选格 `mouseover`（L1480-1485）、键盘聚焦 `focusin`（L1487-1492）、Enter/Space 在 assist 棋盘候选格（L1420-1427）；固定按钮 `#preview-pin`（L1494，`aria-pressed` 同步 L1015）；关闭按钮 `#preview-close`（L1495）；Esc 优先关浮层（L1452-1458 区域，`closePreview()` L1454）。固定后 mouseover/focusin 早退（L1481/L1488）。
- resize / 捕获阶段 scroll 重算位置（L1496-1497）。

## 4. 离线 DOM/JS 交互检查

- 脚本：`.tools/tmp/ui_selftest_probe/ui_offline_check2.mjs`（临时区，非交付物；逻辑：从 HTML 正则抽取源码中 `clamp()` 与 `positionTransitionOverlay()` **逐字**，用 `new Function` 在合成 viewport/锚点上真跑，再对源码做变异体注入）。
- 复跑命令：
  ```
  node .tools/tmp/ui_selftest_probe/ui_offline_check2.mjs "D:\python\projects\minesweeper_help\review\ui-prototype.html"
  ```
- 结果（新鲜输出）：
  ```
  === 离线 DOM/JS 交互检查 ===
  文件: D:\python\projects\minesweeper_help\review\ui-prototype.html
  用例: 46/46 通过；失败 0
    层 A: pass=6 fail=0        （结构/纯度：浮层在 app-shell 外、aside 无 transition-card、role/aria、hidden、无 markdown）
    层 B: pass=10 fail=0       （CSS 契约：fixed/z-index≥60/视口 min 约束/紧凑档/棋盘宽度算术=663≤808/calc 尺寸/垂直压缩/292px 列/board-scroll）
    层 C: pass=14 fail=0       （定位真跑：右置、右缘翻转、下缘翻转、超高浮层垂直钳位、极端右下角四边钳位、无锚点兜底、1280×720）
    层 D: pass=12 fail=0       （接线：mouseover/focusin/Esc/#preview-close/#preview-pin+aria-pressed/pinned 早退/resize+scroll/4 结果/7×7/aria-expanded/概率/applyFitMode）
    层 E: pass=4 fail=0        （变异体必红：删水平翻转 / 删垂直翻转 / 删垂直钳位 3 个变异体全数被杀死）
  判定: PASS
  ```
- 变异体与断言一一对应：E1（删 `spaceRight` 分支）→ 被杀于「右缘空间不足时水平翻转」；E2（删 `spaceBelow` 上翻）→ 被杀于「下缘空间不足时垂直翻转」；E3（删垂直 `clamp`）→ 被杀于「超高浮层被垂直钳位在视口内」。E0 要求 `3/3 killed` 才 PASS，避免「断言无鉴别力」自证。
- 鉴别力说明：层 C 的 7 条定位用例是**真执行**源码逻辑（不是源码正则），删除任一翻转/钳位分支必红，属注入必红负向自测；≥3 元素输入断言覆盖视口×锚点×浮层尺寸三变量组合（1366×768 / 1280×720 / 超高 700px）。

## 5. HTML 纯度与语法

- `format_gate(path="review/ui-prototype.html", checks=["html-purity"], root="D:\python\projects\minesweeper_help")` → **PASS**，1 个相关文件 0 个问题（0 发现）。
- 内联 JS 语法：`node --check .tools/tmp/ui_selftest_probe/extracted.js`（从 HTML 用 UTF-8 抽取 `<script>` 正文）→ **exit 0**。
- 说明：首次用 PowerShell `Get-Content -Raw` 抽取时因 PS5 默认编码把中文读成乱码，导致 `node --check` 假红（exit 1，报 `瀹夊叏姒傜巼` 类乱码）。已改用 Node `fs.readFileSync(...,'utf8')` 抽取后 exit 0——该假红不计入失败项，但如实记录：**测量工具的编码必须与文件编码一致**，否则语法检查不可信。

## 6. 浏览器真实截图

**NOT_RUN**。原因（如实记录，未以任何替代物冒充浏览器跑通）：
1. Python Playwright `sync_playwright()` 启动即 `PermissionError: [WinError 5] 拒绝访问`（`create_subprocess_exec` → `windows_utils.Pipe`），属沙箱禁止子进程命名管道捕获（与记忆 L1-01 同类边界），不是脚本错误。
2. Edge headless `--dump-dom` 直连启动：`mojo/public/cpp/platform/platform_channel.cc:187] Check failed: 拒绝访问 (0x5)` + `FATAL`，同样卡在命名管道；崩溃于浏览器进程初始化，未产出 DOM。
3. `Start-Process` 重定向被沙箱拦（`Access is denied`）。
⇒ 无真实浏览器渲染证据。**1366×768 的实际像素级首屏完整显示未验证**，第 2 节的垂直预算是 CSS 声明值累加推算，**不是浏览器实测**；如需强证据，请在有交互桌面会话的环境双击打开 `review/ui-prototype.html`，用 `--window-size=1366,768` 观察并截图。

## 7. 未覆盖项与风险

1. **浏览器像素级验证 NOT_RUN**（见第 6 节）：`1366×768` 首屏是否真的「无需页面滚动即可看全状态/棋盘/详情/停止按钮」只有算术推演，没有真实渲染读数。风险点：`h1` 与 `.page-heading` 实际行高、`#auto-decision` 文案换行导致的高度可能高于估算；中文字体回退（Noto Sans SC 缺失时落到 Microsoft YaHei）可能改变行高。
2. 浮层钳位是**纯 JS 计算**，未在真实布局引擎里验证 `offsetWidth/offsetHeight` 与 CSS `min()/max-height` 的实际取值一致（离线用固定 460×320 与注入 700 高模拟）。
3. 未覆盖 `prefers-reduced-motion` 与高对比模式下紧凑档叠加的视觉回归（仅继承原样式）。
4. 未覆盖 Safari/Firefox（`scrollbar-width: thin`、`<dialog>` 行为差异与本次改动无关但未复测）。
5. 未改动 PyQt 源码，因此**桌面真实窗口**的 DPI 缩放（125%/150%）适配不在本次验证范围。
6. 离线脚本放在 `.tools/tmp/`（gitignored 临时区），若清理临时目录需按第 4 节命令重建；报告已留存其判定逻辑与用例清单。

## 8. 复跑命令汇总

```
# 1) 离线 DOM/JS 交互检查（含变异体必红）
node .tools/tmp/ui_selftest_probe/ui_offline_check2.mjs "D:\python\projects\minesweeper_help\review\ui-prototype.html"

# 2) HTML 纯度
format_gate(path="review/ui-prototype.html", checks=["html-purity"], root="D:\python\projects\minesweeper_help")

# 3) 内联 JS 语法（必须用 Node 按 UTF-8 抽取，PowerShell 5 会把中文读坏导致假红）
node -e "const fs=require('fs');const h=fs.readFileSync('review/ui-prototype.html','utf8');fs.writeFileSync('.tools/tmp/ui_selftest_probe/extracted.js',h.match(/<script>([\s\S]*?)<\/script>/)[1],'utf8');"
node --check .tools/tmp/ui_selftest_probe/extracted.js

# 4) 浏览器真截图（本环境 NOT_RUN，需交互桌面）
"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --window-size=1366,768 "file:///D:/python/projects/minesweeper_help/review/ui-prototype.html"
```
