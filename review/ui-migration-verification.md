# UI 迁移首批 A+B+C · 收尾验证报告（Verifier）

> 生成：2026-11-07 10:40 · 角色：Verifier（只读取证，未修改任何源文件）
> 验证对象：`main.py`、`ui/preview_popup.py`(新增)、`tests/test_ui_preview_popup.py`(新增)，含并行落盘的 `tests/test_ui_migration.py`
> 冻结快照（本次裁决基线，全部证据基于下列哈希）：

| 文件 | 字节 | mtime | sha256 前 12 |
|---|---|---|---|
| `main.py` | 78361 | 11-07 09:57:31 | `B3AABC9B5221` |
| `ui/preview_popup.py` | 19963 | 11-07 09:32:27 | `EBA9BD992FF2` |
| `tests/test_ui_preview_popup.py` | 20064 | 11-07 09:38:16 | `8B9BD09041E6` |
| `tests/test_ui_migration.py` | 20386 | 11-07 09:55:50 | `669F1ED66DDA` |
| `ui/window.py` | 31972 | 11-03 17:40:28 | `D4B2929BC12F` |
| `ui/window.ui` | 35652 | 06-08 21:08:44 | `FDDA40A52E28` |
| HEAD | — | 2026-11-06 17:31:58 | `d390b4e` |

解释器：`D:\anaconda3\envs\py310\python.exe`（Python 3.10.16）；`QT_QPA_PLATFORM=offscreen`。

## 裁决

**状态：PASS（附 1 项 NOT_RUN、3 项 P1 边界、4 项 P2）**
**置信度：高**（离屏/纯函数/接线层证据充分；真机路径按契约标 NOT_RUN）
**阻塞项：0**

判据：编译通过；40+7+34 定向用例与 228 全量用例在新冻结快照上通过（且冻结后连续 70 次全量复跑零失败）；`ui/window.py`/`ui/window.ui` 与 HEAD 字节一致；三条硬约束（不改生成物/不改算法/不伪造概率）均有可核验证据。唯一 NOT_RUN 是真机扫雷窗口 + 真实鼠标 hover，环境不具备，按任务书要求如实标 NOT_RUN。

## 证据（新鲜输出，均为本次会话亲自执行）

| 相位 | 结果 | 命令 | 输出 |
|---|---|---|---|
| Build | 通过 | `py -m py_compile main.py ui/preview_popup.py tests/test_ui_preview_popup.py tests/test_ui_migration.py` | 退出码 0，无输出 |
| Types | NOT_RUN（工具缺失） | `py -m mypy --version` | `No module named mypy`；仓内无 tsconfig，纯 Python 项目无等价类型闸门。替代门：`py_compile`（通过） |
| Lint | 通过（无新增） | `py -m ruff check --no-cache ui/preview_popup.py tests/test_ui_preview_popup.py` | `All checks passed!`（退出码 0） |
| Lint（main.py） | 3 项**既有**告警 | `py -m ruff check --no-cache main.py` | F401×2(`win32con`、`QPushButton`) + F841×1(`except ValueError as e`)；对 HEAD 版 `main.py` 跑同一命令得到**完全相同 3 项** ⇒ 本批零新增 lint 债 |
| Tests（定向 A） | 通过 | `py -m unittest tests.test_ui_preview_popup` | `Ran 40 tests ... OK`，退出码 0 |
| Tests（定向 B） | 通过 | `py -m unittest tests.test_ui_heatmap` | `Ran 7 tests ... OK`，退出码 0 |
| Tests（定向 C） | 通过 | `py -m unittest tests.test_ui_migration` | `Ran 34 tests ... OK`，退出码 0 |
| Tests（全量） | 通过 | `py -m unittest discover -s tests -t .` | `Ran 228 tests ... OK`，退出码 0；冻结后顺序连跑 **70 次（30+40）零失败**，并行 2 进程 ×4 轮亦全绿 |
| Security | 通过（人工 + 模式扫描） | 对两个新文件扫 `eval(`/`exec(`/`subprocess`/`socket`/`requests`/`open(`/`pickle`/`token` | 0 命中；阳性对照：同文件 `setStyleSheet` 命中 5 次，证明模式有效。新代码不涉网络/写盘/反序列化 |
| Diff | 见下 | `git diff --stat`、`git diff --exit-code HEAD -- ui/window.py ui/window.ui` | `main.py` +533/−24；两个生成物 `git diff` 退出码 0（无变化） |

**Overall：READY（功能性 PASS；真机走查 NOT_RUN）**

### 生成物未被修改（硬约束①）

- `git diff --exit-code HEAD -- ui/window.py ui/window.ui` → 退出码 **0**（git 视角零差异）。
- 字节级复核：`ui/window.py` 工作区与 HEAD blob **均为 31972 字节且逐字节相同**；`ui/window.ui` 工作区 35652B vs HEAD 34550B 的差异**仅 CRLF 展开**（`core.autocrlf=true`，去掉换行差异后内容相等：`content-equal after EOL normalize: True`），git 亦判零差异。
- 批 B 全部走运行期注入：`main.py:570` `_build_compact_ui` 用 `insertWidget`；`main.py:373` 接 `stackedWidget.currentChanged`。

### 批 A 纯函数（任务书点名的四项）

| 检查 | 方法（独立重算，不复用被测实现） | 结果 |
|---|---|---|
| 7×7 取样 | 用 12×24 棋盘按 `(i*7+j*13)%12` 生成 0..11 编码，对 `(ci,cj)=(11,6)` 独立重算 49 格期望值比对 | **49/49 全等 PASS**；`BOARD_SPAN=7`（`preview_popup.py:28`），中心格 = `cv[cj][ci]` |
| 7×7 控件渲染 | 构造已知编码棋盘，逐格比对 `_mini_labels` 文本：9→空、10→「雷」、11→「✓」、0-8→数字、越界→透明 | 49 格文本 **mismatches=0 PASS**；中心框唯一（第 25 格 = r3c3，样式含 `2px solid rgb(0, 109, 119)`），红底/绿底/已开底色分别命中 |
| 边缘定位 | 屏幕 1000×700、浮层 300×200、margin 12，独立算期望值 | 右侧优先 `(252,100)`、右溢出翻左 `(638,100)`、下溢出翻上 `(252,400)`、右下双溢出 `(638,400)`、超大锚点夹取 `(688,12)` —— **4/4 期望值全中且全部落在屏内**（`preview_popup.py:157`） |
| 无 transitions 占位 | 三个缺失 transition 的候选批量断言 | `has_transitions=False`、`transition_text=="暂无真实状态转移分布"`、`rows==[]`；`transitions_text(None)==[]`、`[]==[]`；含 `probability=None` 与非 dict 项被丢弃（`preview_popup.py:101-119`）。占位文案内无任何百分数（同伴用例 `test_popup_placeholder_never_contains_percentages` 亦覆盖） |
| value_kind 文案 | 直接读 `VALUE_KIND_LABELS`（`preview_popup.py:33-37`） | 三文案互异；`heuristic_win_rate` 含「策略胜率」+「非安全概率」且**不含**「安全概率（精确解）」；`uniform_prior` 含「非精确解」；`describe_value(None)` 输出 em dash 而非 0.00% |

### 批 C value_kind 真实接入（含首批 P0 边界的修法验证）

- 帮助页候选按 `exp` 真值表分类（`main.py:80-88`），实测映射：`"C++ 三层决策 (optimal)"→exact_safety`、`"枚举得出"→exact_safety`、`"由(3, 4)得出"→exact_safety`（对应 `utils/deduction.py:68,95,145,173,199,226`）、`"胜率计算得出"→heuristic_win_rate`、`"随机选择。"→uniform_prior`、未知/None→`unknown`。
- 决策卡改用 `classify_decision_value_kind`（`main.py:152-189`，接线在 `main.py:1262`）：**pbs 同值→exact、异值→heuristic、prob 空→uniform_prior、best 不在 prob→uniform_prior**。实测：
  - pbs 形态 `{prob[best]=0.995,best_conf=0.995}` → 决策卡「安全概率（精确解）」+ `99.50%`；
  - **win_rate 形态** `{prob[best]=0.55,best_conf=0.62}` → 决策卡「策略胜率（启发式，非安全概率）」+ `62.00%`，且不含「安全概率（精确解）」——**首批报告 P0-边界-1 已被修复**。
- 用真实 `native.decide` 交叉核对前提（`utils/native.py:93`）：`reason=limitation` 时 `prob[best]==confidence`（equal=True）；`reason=clicks9_fallback` 时 `prob` 非空但 **best 不在 prob**（prob_has_best=False）⇒ 与实现的 `uniform_prior` 分支假设一致。

### 硬约束③「不伪造状态转移概率」

- 生产侧 `transitions` **仅有两处赋值且恒为 None**：`main.py:712`、`main.py:728`（`grep '"transitions"'` 在生产文件里仅此两处 + `preview_popup.py:143` 的读取）。
- 原型合成权重 `0.43/0.27/0.18` 与 `makeTransitions` 在 `main.py` + `ui/preview_popup.py` 中命中 **0 次**；阳性对照（同两文件搜 `0.995|0.5|9pt`）命中 37 次，证明扫描有效。
- 行为层：三个候选 payload 的 `transitions` 实测 `[None, None, None]`；热力图格 payload 亦为 `None`（`value` 取自真实 `prob`，无值时是 `None` 而非 0）。

## 验收标准逐条

| # | 标准 | 状态 | 证据 |
|---|---|---|---|
| 1 | `py_compile` 通过 | VERIFIED | 退出码 0（含 4 个文件） |
| 2 | `tests.test_ui_preview_popup` 通过 | VERIFIED | `Ran 40 tests ... OK`，连跑多次稳定 |
| 3 | `tests.test_ui_heatmap` 通过 | VERIFIED | `Ran 7 tests ... OK`；既有热力图色带/统计栏/样式去重契约未破 |
| 4 | 全量 `unittest discover` 通过 | VERIFIED | `Ran 228 tests ... OK`；冻结后 70 次顺序复跑零失败、2×4 轮并行零失败 |
| 5 | popup 7×7 局部棋盘 | VERIFIED | 纯函数 49/49 独立重算一致；控件 49 格文本 0 mismatch；中心框唯一 |
| 6 | 边缘定位（右侧优先/翻折/夹取） | VERIFIED | 4 方向期望值全中 + 4 角夹取在屏内 |
| 7 | 无 transitions 时占位、不含百分比 | VERIFIED | `main.py:712,728` 恒 None；占位文案=「暂无真实状态转移分布」+ 提示「不显示合成概率」；占位文本无 `%` |
| 8 | value_kind 三分类文案互斥且不误导 | VERIFIED | 表见上；`heuristic_win_rate` 不含「安全概率（精确解）」；决策卡端到端同步正确 |
| 9 | `ui/window.py`/`window.ui` 未被修改 | VERIFIED | `git diff --exit-code HEAD` 退出码 0；`window.py` 逐字节 == HEAD blob；`window.ui` 差异仅 CRLF |
| 10 | 真实扫雷窗口联调 | **NOT_RUN** | `win32gui.FindWindow(None,"Minesweeper Arbiter ")==0`（游戏未运行），无真实棋盘可扫；复跑命令：启动扫雷后 `python main.py` → 帮助页 hover 候选格 |
| 11 | 真实鼠标 hover 路径（Enter/Leave 真实事件、`Qt.Tool` 真实置顶/不抢焦点） | **NOT_RUN** | offscreen 平台上 `raise()`/`propagateSizeHints()` 均报「plugin does not support」，hover 由 `eventFilter` 直接注入验证（`QEvent.Enter/Leave` 手动派发），非真实鼠标；复跑命令：同 #10，另需真实指针移动 |
| 12 | 验证不修改源代码 | VERIFIED | 全程只读；断言鉴别力实验一律**运行时改内存并立即还原**，未 write/edit 任何源码；写入仅限本报告 |
| 13 | 报告 ≥50 行、含矩阵/命令结果/P0·P1·P2/file:line | VERIFIED | 本文件 |

## 关键事件与口径（不隐瞒）

### E1 中途出现过一次 8 用例失败（已定位为并行席的运行时变异，非代码缺陷）

- 事实：2026-11-07 **09:56:44** 的一次全量 discover 输出 `Ran 228 tests FAILED (failures=8)`，失败集合为 4 个 `test_ui_migration` + 4 个 `test_ui_preview_popup` 用例，主题全部围绕 `classify_value_kind` 对「胜率计算得出」的映射。
- 同窗口观测到 `main.py` **被外部进程反复改写**：`09:51:05`(77328B) → `09:51:52`(78185B) → `09:52:22`(77959B) → `09:54:58`(78361B 稳定) → `09:57:31`(78361B)；我并未写任何文件。
- 并行席报告 `review/ui-migration-bc.md` 第 5.5 节自述：「用临时脚本对 **main.py 生产代码真值**注入 3 个变异体，逐个跑 tests.test_ui_migration，跑完立即还原并复跑」，其中变异体正是 `("胜率计算得出", VALUE_KIND_WIN_RATE) → VALUE_KIND_EXACT`，且该节列出被抓住的用例含 `test_mislabel_mutant_is_caught`、`test_hover_channel_shows_popup_with_real_board`。
- 我做了**复现实验**：在运行时把 `main._EXP_TO_KIND` 改成该变异体，跑 `test_ui_migration`+`test_ui_preview_popup`（74 例）→ **恰好 8 个失败，且 8 个用例 ID 与 09:56:44 的失败集合完全一致**；还原后恢复 0 失败。原快照（真值表）基线 0 失败。
- 结论（分档表述）：**观测事实确定**（失败窗口内 main.py 在变、失败签名可被该变异体精确复现、同伴自述同窗口注入变异）；**因果为高度一致的推定**，非我直接抓到注入动作。
- 影响：**不构成对产出的否定**。冻结后（`B3AABC9B5221`，09:57:31 之后未再变）全量 228/228 通过，70 次顺序 + 8 次并行复跑零失败。但这是 P1 级**验证条件风险**（见 P1-1）。

### E2 环境事故：py310 解释器一度消失（10:02 → 10:20）

- 10:02 起 `D:\anaconda3\envs\py310\python.exe` 不存在（`envs/` 目录整体消失、`pkgs` 计数 976→3），期间无法执行任何 PyQt 用例；10:20 用户确认「环境已恢复」，解释器恢复（Python 3.10.16，`conda-meta` 487 项），源码哈希在整个过程中未变。
- 处置：恢复后**重跑全部相位**（编译/定向/全量），只在恢复后的运行上做裁决；10:02–10:20 的验证窗口标为「不可用」，未用旧读数顶替。

## P0 / P1 / P2 未覆盖与已知边界

### P0（阻断）— 0 项

### P1（需后继批次处理，不阻断本批）

1. **P1-1 验证期间源码会被并行角色运行时改写**：本仓当前存在多名角色并行落到同一工作树（`review/ui-migration-*.md` 与 `review/ui-migration-code-review*` 均在 09:39–09:58 落盘），且其中一位会对 `main.py` 做**注入必红变异后还原**。后果：任何验证读数都可能落在变异窗口内（本次即抓到一次），"绿"也可能是变异期间偶然为绿。建议：注入必红一律改为**运行时内存变异**（如本次做法）或 `git worktree` 隔离副本；验证方在跑前后各留一次源码哈希（本报告已做到）。
2. **P1-2 热力图 hover 预览的 value_kind 与决策卡不一致的来源口径**：决策卡用 `classify_decision_value_kind(payload)`（`main.py:1262`，按 `best_conf` vs `prob[best]` 比对），而 hover payload 仍用 `classify_payload_kind(payload)`（`main.py:725`，只按 `prob` 是否非空判 `exact_safety`）。实测同一 win_rate 形态 payload：决策卡显示「策略胜率 62.00%」，hover 预览显示「安全概率（精确解） 55.00%」。二者标的是**不同字段**（卡=best_conf、浮层=prob[i,j]），语义上都自洽，但用户在同一屏幕上会看到两个不同标签，有再度误读风险。建议下游批次在 hover payload 增加"该值=逐格安全概率（世界枚举占比）"的显式 `source`/`value_kind` 打标，或统一由发射侧写 `value_kind`。
3. **P1-3 `classify_decision_value_kind` 的"同值 ⇒ pbs"是充分非必要条件**：若 win_rate 分支的 `best_conf` 恰好等于 `clicks2p[best]`（例如两者同为 1.0/0.0 或数值巧合），会被判成 `exact_safety`。构造性验证：`{prob[best]:1.0,best_conf:1.0}` → `exact_safety`。已从「恒错」降为「巧合才错」，但根治仍需发射侧打标（`utils/probability.py` 的 `_last_heatmap` 处加 `value_kind`）。目前**无生产样本**证明该巧合会发生，按边界申报而非缺陷。

### P2（可接受/后续打磨）

1. **P2-1 帮助页候选 hover 的 7×7 可能为空网格**：`_last_cell_value` 仅在 `main.py:1023`（帮助首屏扫描）与 `main.py:1183`（热力图 payload）赋值；若用户未触发过这两条路径，候选浮层显示全透明网格（实测 `board_ok=False`、49 格全透明，不崩、不编造）。可接受，建议空网格加"N 个格无真实棋盘数据"提示。
2. **P2-2 已证明/概率语义的图例仍是纯文本符号**：`main.py:595` 用 `■` 字符而非真实色块；与既有热力图色带未做视觉对齐校验（本批未做像素级验证，规避记忆 #1212c38e 的 offscreen 字体假红）。
3. **P2-3 本批未覆盖 `closeEvent` 的主窗口关闭通道**：`main.py:761-768` 新增 `MyMainWindow.closeEvent`（摘浮层应用级 eventFilter），测试只覆盖 `_hide_preview_popup`（4 处引用）与切页通道，**没有用例直接驱动主窗口 `closeEvent`**（grep `closeEvent` 在 UI 测试中 0 命中）。我做过构造性验证（`win.close()` 不报错、浮层过滤器被摘除），但未落成回归断言。建议补一条 `test_main_close_removes_app_filter`。
4. **P2-4 真实 DPI/字体渲染未验证**：offscreen 下 `QFontDatabase` 报缺字体目录（`.../PyQt5/Qt5/lib/fonts`），字体度量在真实机器上可能不同，浮层 `adjustSize()` 后的尺寸与边缘夹取结果可能有差异。复跑：真机启动后目视 + 打印 `popup.sizeHint()`。

## 断言鉴别力（注入必红负向自测，运行时内存变异，未落盘）

基线：`tests.test_ui_preview_popup` 40/40 绿、`test_ui_migration` 34/34 绿。逐个注入变异体后：

| 变异体（运行时改内存） | 结果 | 被谁抓住 |
|---|---|---|
| `VALUE_KIND_LABELS["exact_safety"]` 改写 | **RED（1）** | `test_set_payload_renders_board_and_value` |
| `VALUE_KIND_LABELS["uniform_prior"]` 改写 | **RED（2）** | `test_show_for_cell_and_close`、`test_prior_label_marks_non_exact` |
| `VALUE_KIND_LABELS["heuristic_win_rate"]` → 「安全概率（精确解）」 | **RED（4）** | `test_heuristic_win_rate_label_never_says_safety` 等 |
| `summarize_payload` 在无 transitions 时伪造分支概率 43% | **RED（3）** | `test_missing_transitions_yields_placeholder` 等 |
| `compute_popup_position` 取消翻折/夹取 | **RED（3）** | `test_clamped_inside_screen`、两侧翻折用例 |
| `sample_board` 全置空 | **RED（3）** | `TestSampleBoard` 三例 |
| `sample_board` 改成 6×6 / 映射整体偏移 1 格 | **RED（1）/ RED（2）** | `test_center_cell_returns_49_cells` / `test_known_markers_preserved` |
| `describe_value` 恒输出 0.00% | **RED（2）** | `test_describe_value_none_is_dash_not_zero` 等 |
| `main._EXP_TO_KIND` 把「胜率计算得出」改成 exact | **RED（8）** | 与 09:56:44 事故的失败集合**完全一致** |
| `main.classify_decision_value_kind` 恒返回 exact | **RED（1）** | `test_heatmap_decision_card_labels_win_rate_honestly` |

全部变异后逐一还原并复跑，均回到 0 失败 ⇒ 测试具备真实鉴别力，非"恒绿摆设"。

## 缺口与建议

- 缺口：真实扫雷窗口 + 真实鼠标 hover + 真机 DPI 三项目前**无法在本环境闭环**（游戏未运行、offscreen 无法产生真实指针与置顶窗口）。风险：**中**（纯 UI 逻辑已被离屏覆盖，风险集中在窗口管理器行为与尺寸）。建议：由人手动执行 #10/#11 的复跑命令并回填结果，或补一条 `QTest.mouseMove` + 真实 `QApplication` 的集成探针。
- 缺口：`closeEvent` 无回归断言（P2-3）。风险：**低**。建议补 1 条用例。
- 缺口：`utils/probability.py`/`utils/solver.py` 未写入 `value_kind` 字段，GUI 侧仍靠字段比对推断（P1-3 的根因）。风险：**中**，属后续批次授权范围。建议按首批报告 `review/ui-migration-implementation.md` 第 8 节移交。

## 建议

**APPROVE** — 首批 A+B+C 在冻结快照 `B3AABC9B5221` 上编译通过、228/228 用例全绿（70 次复跑零失败）、生成物零改动、三条硬约束均有可核验证据；首批报告的 P0-边界-1 已实测修复。仅真机联调按环境限制标 NOT_RUN，3 项 P1 边界应交下游批次处理而非回退本批。

## 复跑命令（供组织者/后继席复核）

```
cd D:\python\projects\minesweeper_help
set QT_QPA_PLATFORM=offscreen
D:\anaconda3\envs\py310\python.exe -m py_compile main.py ui\preview_popup.py tests\test_ui_preview_popup.py
D:\anaconda3\envs\py310\python.exe -m unittest tests.test_ui_preview_popup -v
D:\anaconda3\envs\py310\python.exe -m unittest tests.test_ui_heatmap -v
D:\anaconda3\envs\py310\python.exe -m unittest tests.test_ui_migration -v
D:\anaconda3\envs\py310\python.exe -m unittest discover -s tests -t .
D:\anaconda3\envs\py310\python.exe -m ruff check --no-cache ui\preview_popup.py tests\test_ui_preview_popup.py
git diff --exit-code HEAD -- ui/window.py ui/window.ui
```

预期：全部退出码 0；`Ran 228 tests ... OK`；ruff 对新文件 `All checks passed`；git diff 无输出。
判定前提：跑前跑后 `main.py` 的 sha256 前 12 位应稳定为 `B3AABC9B5221`（若变化说明期间有并行写入，读数需重取）。