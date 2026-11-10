# 静默失败专项盲评报告（silent-failure-hunter · squad 第 5 席）

【评审身份】对象快照 = **工作区当前内容**（`head_sha = afa3f4c4f350e8057ab4440406148018c8b6f2bb`；工作区含未提交改动 `M cpp/mscore.cpp` / `M utils/probability.py` / `M utils/vision.py` / `M utils/solver.py` / `M utils/native.py` / `M main.py` 等，评审以磁盘内容为准） | 覆盖分母 **23 文件 / 15,045 行**（crgate freeze：`spec_sha=48d5e2a5331a8b2b`、`selection_hash=2303a1c35ffd7f7f`、`reviewable_count=23`） | 模型 `deepseek-official / deepseek-flash`（档位 MEDIUM 钉死）
【实际达成模式】**仅代码**（静态审阅）。降级理由：本席为只读盲评席，任务书禁止 install/build/跑测试，运行时假设一律写成「验证建议」交组织者；未运行任何 GUI/决策用例。**本轮按组织者要求对 `cpp/mscore.cpp`（4,719 行决策内核）的静默失败面做了加强复审**（全部 `catch`/`throw`/降级返回点、rs3–rs8 thread_local PRNG、`g_last_pbs_diag`、`cost_log2` 链路、env 调参、PSEQ 缓冲上界）。
【断言鉴别力自查】全部断言附 `file:line` 与逐字片段；关键引用已在 crgate 冻结快照（head_sha 同上）上逐条 grep 复核行号；未使用替身口径推断生产行为；低置信度项已移入「开放问题」；「没查」与「查了没问题」在覆盖账逐项可分辨；未获取作者自评与他席结论（隔离污染：无命中）。
【落盘方式说明】本会话**未提供 write/edit 工具**，按任务书失败降级 L3 用 pwsh（.NET `File::WriteAllText`，UTF-8 无 BOM）等价落盘；只写本席报告与 `agent-out/` 产物，未改动任何被评审对象。
【交付状态】`DELIVERED_WITH_EVIDENCE`（报告落盘 + 完成广播；**P0 = 0 项，P1 = 7 项，P2 = 15 项**，共 22 条；SP-1..SP-5 全命中；覆盖 23/23）

---

## 覆盖账

分母 = crgate 冻结 `review-spec.json`（`spec_sha=48d5e2a5331a8b2b`、`selection_hash=2303a1c35ffd7f7f`、`head_sha=afa3f4c4f350e8057ab4440406148018c8b6f2bb`、`reviewable_count=23`、`total_files=788`、`total_insertions=15,045`）。`excluded_files`（765 项，含 `bench/**`、`tests/**`、`sim/**`、`course/**`、`ui/bg_rc.py`、`cpp/inc/**`、`output/**`、`*.pyd/.bak*` 等）不在分母，不评。

| # | 路径 | 状态 | 理由 / 覆盖方式 |
|---|---|---|---|
| 1 | `ui/preview_popup.py` (907) | reviewed | 全文通读（纯函数层 + 渲染/事件层）；边界判定问题见 S-15 |
| 2 | `utils/analyze_decide_trace.py` (177) | reviewed | 全文通读 + 用磁盘真实 `state/decide_trace.jsonl`（367 条）核对输出语义（S-09/S-10） |
| 3 | `utils/decide_trace.py` (147) | reviewed | 全文通读 + 真实 trace 字段核对（`cost_log2` 0/367 → S-17） |
| 4 | `cpp/ab_native.cpp` (860) | reviewed | 全文通读（play_one/arm/stats/桥接接口）；S-07/S-08/S-22 |
| 5 | `cpp/mscore.cpp` (4,719) | reviewed | **本轮加强复审**：全部 30+ 处 `catch`/`throw` 与降级返回点逐一定位读上下文（:2526/:2799/:2927/:2931/:3295/:3557/:3740/:3842/:3915/:4028/:4054/:4523/:4568/:4699 等）；三层决策链（OPTIMAL/win_rate/pbs+PSEQ）；rs3–rs8 thread_local PRNG；`g_last_pbs_diag`；`cost_log2`/`COST_CAP_LOG2`/`DSTAR`/env 调参；PSEQ `counts[64][10]`/`row_vals[256]` 上界。新增 S-16/S-17/S-18/S-19/S-20/S-21（+S-22 跨文件）。未逐行穷读全文（深度如实标注，不冒充全量） |
| 6 | `logger.py` (68) | reviewed | 全文通读（S-04） |
| 7 | `main.py` (2,617) | reviewed | 逐 `except`（34 处）定位读上下文 + UI 线程生命周期/热力图/预览/截图帮助关键段（S-01/S-03/S-12）；未逐行穷读（如实标注） |
| 8 | `setting.py` (78) | reviewed | 全文通读（`normalize_cwd` → S-04 同因） |
| 9 | `ui/__init__.py` (3) | reviewed | **新增项**；全文通读：仅 `from .edit_setting/.window/.screenshot import Ui_*` 的 re-export，无逻辑、无错误处理面 |
| 10 | `ui/edit_setting.py` (164) | reviewed | 全文通读（纯布局，无错误处理面） |
| 11 | `ui/screenshot.py` (127) | reviewed | 全文通读（纯布局，无错误处理面） |
| 12 | `ui/window.py` (652) | reviewed | 手工同步生成物：读文件头/逻辑函数/`retranslateUi`，grep 全量控制流（仅 1 处 `except`，坏值降级为线性渐变有注释声明） |
| 13 | `utils/__init__.py` (18) | reviewed | **新增项**；全文通读：导入期副作用（`pyautogui.PAUSE=setting.sleep`、`MINIMUM_SLEEP=0.001`）+ re-export；无吞错路径。注：`PAUSE` 只在导入期设置一次且不可运行期重配（非失败路径，未立发现） |
| 14 | `utils/clicking.py` (45) | reviewed | 全文通读；`sort_clicks` 无生产调用点（死代码） |
| 15 | `utils/combinatorics.py` (246) | reviewed | 全文通读；`get_list` 静默夹取见 O-04（死链，不立发现） |
| 16 | `utils/deduction.py` (277) | reviewed | 全文通读；生产决策链已删，仅测试/死链可达 |
| 17 | `utils/misc.py` (31) | reviewed | 全文通读 |
| 18 | `utils/mm0.py` (189) | reviewed | 全文通读（S-13） |
| 19 | `utils/native.py` (180) | reviewed | 全文通读（S-03/O-03） |
| 20 | `utils/probability.py` (2,268) | reviewed | 决策入口 `number5_1`/`_decide_unavailable`/`_wr_*`/`_tb_*`/`_ps_*`/decide 循环通读；死链方法（win_rate/process_bigger_situation/part_solve）读定义并核调用点（S-02/S-06/S-14） |
| 21 | `utils/solver.py` (662) | reviewed | 全文通读（play/help/_emit_heatmap/cancel/统计）（S-01/S-13/S-14） |
| 22 | `utils/util.py` (44) | reviewed | 全文通读（纯 re-export） |
| 23 | `utils/vision.py` (544) | reviewed | 全文通读（模板校验/批量识别/_apply_recognition/重扫/locate）（S-05/S-06） |

四态闭合：`reviewed` **23** / `skipped` 0 / `blocked` 0 / `waived` 0。无「静默跳过」。（与上一版材料清单的 delta：新增 `ui/__init__.py`、`utils/__init__.py` 两项，均已 reviewed；其余 21 项同名同源。）

---

## 发现清单

> 每条五字段：location / severity（P0/P1/P2 + 置信度）/ issue（命中类别+判据）/ impact / fix recommendation。
> 判定口径：**有意的安全降级（有可见日志/明确定义/不改变语义）不算发现**，单列在「认定为有意的降级」；「上层无法感知、结果看起来正常但实际错误/无效」才算。

### S-01【P1 · HIGH】自动模式主循环无通用异常兜底，线程退出被 UI 播报成「自动运行完成」

- **location**: `utils/solver.py:437-446`（`play()` 的 try 只有 `except pyautogui.FailSafeException` / `except win32ui.error` + `finally`）；对照 `utils/solver.py:607-621`（`help()` 有 `except Exception` → `logger.error(exc_info=True)` + `warning_signal`）；`main.py:419`（`auto_play_thread.finished.connect(self._on_auto_thread_finished)`）；`main.py:669-680`（handler 无条件写 `set_solver_status("自动运行完成 · 保留最后一帧真实决策")`）
- **severity**: P1，置信度 HIGH（静态可判定；异常种子的触发链见下）
- **issue**: 类别 **SP-1 静默吞错 / 失败被编码成正常返回**。判据：`play()` 抛出的任何非 FailSafe/win32ui 异常都没有本层捕获与告警通道；而 `finished` 信号对「正常跑完」与「异常终止」无区分，前者播报的文案与后者完全相同 ⇒ 上层（UI/用户）无法感知失败。可触发的真实种子：`utils/vision.py:215-222` 的 `_load_locate_template` 在模板缺失时 `raise FileNotFoundError`，经 `solver.py:345/357/387` 的 `_locate(...)` 直接穿出 `play()`；`utils/vision.py:186-206 load_img` 的 `TemplateValidationError` 亦同。
- **impact**: 自动扫雷在任意未预期错误处停止，界面却显示「自动运行完成」并保留最后一帧——用户会以为「局数已玩完」，不再排查；真机日志以外无任何线索（且见 S-04 日志本身可能不存在）。若运行环境是 PyQt 对虚拟方法未捕获异常 aborts 的版本，则表现为进程直接消失（同样没有 UI 诊断），两种分支都违反「失败必须可见」。
- **两种 PyQt5 行为下的修法优先级**（组织者要求写明）：若实测为「打印 traceback 后线程静默终止」→「UI 区分文案 + play() 兜底」为**最高优先**（失败完全无感知，用户被假『完成』误导）；若实测为「进程 abort」→ 属可见崩溃，优先级降为「补 finally 清理 + 最后错误持久化 + 重启提示」，但 play() 兜底仍必须补（覆盖 PyQt 版本差异）。
- **fix recommendation**: ①在 `play()` 尾部补与 `help()` 对称的 `except Exception as e:` → `logger.error(..., exc_info=True)` + `warning_signal.emit(f"自动模式异常退出：{e}")` + 在实例上记 `self._fatal_error = e`；②`_on_auto_thread_finished` 依据 `getattr(thread, "_fatal_error", None)` 分两文案（「异常退出」vs「运行完成」）并显示错误摘要；与 S-05 同属「异常出口收口」一组改。

### S-02【P1 · HIGH】自适应 `wr_threshold` 是**空转旋钮**（单向链：写端活跃、读端已断）：Python 侧计算/透传/持久化全在跑，当前 C++ 决策内核**零读取**该参数

- **location**: 写端（Python）：`utils/probability.py:1047-1050`（`tb = …; _auto_wr_threshold = _wr_current_threshold(tb)`）、`:1058`（`native.decide(… wr_threshold=_auto_wr_threshold …)`）、`:1066-1070`（trace 记录该值）、`:1331-1342`（decide 循环每轮重读并传）、`:612-623`（`_wr_current_threshold`）、`:624-650`（`_wr_update` 写 `tb["wr"]` 并 `_tb_save`）、`:1074-1078` 与 `:1353-1359`（`_wr_update` 调用点）——**持久化实证**：磁盘 `state/time_budget.json` = `{"wr":{"t":8.0,"limitation":1.0,"ms":0.0567…}}`。读端（C++，**组织者 2026-11-09 00:49 独立 grep 复核**）：`cpp/mscore.cpp:2432` 仅形参、`:3992`/`:3998` 仅原样转发、`:4099` 仅 `py::arg("wr_threshold")=8.0` 默认值；**`decide_impl` 函数体（:2430-3560）对 `wr_threshold` 零读取**；真正判据在 `:2943-2956`（`log2_total_est <= COST_CAP_LOG2 && mem.allowed && world_upper > DSTAR`，注释逐字「wr_threshold 参数保留仅为方便外部实验覆盖」）。`limitation` 亦已降级为诊断字段（`:4002-4010`）。
- **severity**: P1，置信度 HIGH（**组织者反证实测已完成**：同盘面 × 4 阈值 {0,8,18,100} × 每组合重复 8 次 × 2 个 pyd = 14 个组合，跨阈值的 `reason` 集合完全相同 ⇒ 阈值对**决策层选择**零影响）
- **issue**: 类别 **SP-1 接线沉默 + SP-5 证据漂移**。判据：写端活跃（计算/透传/trace 记录/持久化）+ 读端不存在，构成典型「单向链」。现状可概括为：**reason/决策层选择不随阈值变化（零影响，已实测）**；但**同一 reason 内坐标会抖动**——原因是 `memory_gate`/`limitation`/pbs 层的 PSEQ 残差用 thread_local xorshift PRNG 抽签（`cpp/mscore.cpp:3224-3228` 的 `rs5` 等，见 **S-16**），与 `wr_threshold` 无关。⚠️ 边界必须写清：本发现只断言「决策层选择/reason 不变」，**不断言「坐标逐位不变」**——组织者上一版测试曾因坐标抖动误报「阈值影响输出」，根因即是把 S-16 的随机残差归因给了阈值。
- **impact**: ①任何人用 `state/time_budget.json` 的 `wr.t` 或 trace 的 `wr_threshold` 列做阈值调优/回归归因，结论必然无效（调了没反应→被误读成「该阈值不敏感」）；②`_wr_update` 依据的 `limitation` 已自述「诊断字段、方向相反」；③「已修好自适应链」的注释/文档是误导性描述，会让后续维护者重复排查。
- **fix recommendation**: 二选一并留痕：①**删除**整条自适应链（`_wr_update`/`_wr_current_threshold`/`wr_threshold` 传参/`tb["wr"]` 持久化/trace 字段），注释同步改为「当前无运行时阈值」；②若确要保留实验旋钮，则把阈值真正接回 C++ 判据（新增明确语义并写「阈值改变导致 `use_win_rate` 翻转」测试），trace 记录「该字段是否实际参与决策」。**组织者已认领修复**，修复验收请用其反证脚本口径（同盘面跨阈值 reason 相同 + 显式声明字段废弃/生效）。
### S-03【P1 · HIGH】悬停「按格状态转移」失败时静默回退成**最优格**的转移，把错格数据当该格数据显示

- **location**: `utils/native.py:141-157`（`transitions_for` 内 `except Exception → logger.debug("transitions_for 失败（忽略）") → return None`）；`main.py:894-898`（`r is None` ⇒ `result = (payload.get("transitions"), payload.get("transition_meta"))`，即最近一次 decide 针对 **best_cell** 算的那份）；`main.py:879-881`（旧 pyd 无导出时同样回退）；`main.py:863-874` docstring 自述「旧 pyd 无该导出时回退到 payload 里那一份…那是 best_cell 的、语义不对应」
- **severity**: P1，置信度 HIGH（异常吞没点与错数据回退点均为逐字可判）
- **issue**: 类别 **SP-2 降级为（错误的）合法结果**。判据：失败被降级成一份**格式完全合法、且此前被专门修掉过的错语义数据**；调用方无法区分「该格的真实转移」与「best_cell 的转移」——`main.py` 是唯一消费点，`preview_popup.summarize_payload` 只看到 `transitions` 非空就渲染成「点击 (i,j) 之后」。用户/AI 会据此以为看到了悬停格的前瞻。
- **impact**: 帮助页出现「悬停任意格都显示同一份转移」的旧缺陷形态（本轮修复目标之一）；更糟的是只在 C++ 侧 `transitions_for` 抛异常时出现（例如缓存引擎状态损坏、板面指纹竞态），属低频高迷惑性；错误数据会被当成真值写进操作判断（与 `PROTOCOL`「绝不合成概率」的承诺相悖——这里不是合成，是张冠李戴）。
- **fix recommendation**: `native.transitions_for` 失败时返回显式错误 dict（`{"transitions": None, "transition_meta": {"basis": "unavailable", "reason": "transitions_for_error:…"}, "transition_reason": …}`）而非 `None`；`main.py._transitions_for_cell` 在拿不到按格结果时**不要**回退到 `payload` 的转移（或必须把 `meta["cell"]` 标为 best_cell 并在浮层标题显式写「该分支来自最优格，非悬停格」），且把失败升级为 `logger.warning`（每盘面一次去重）。

### S-04【P1 · HIGH】日志设施可**静默降级为零日志**：相对路径 + `except OSError: pass` + 无 stderr 时无任何替代通道

- **location**: `logger.py:21`（`_DEFAULT_LOG_FILE = os.path.join("logs", "minesweeper.log")`，**相对 cwd**）；`logger.py:37-48`（`RotatingFileHandler` 失败 → `except OSError: pass  # 文件不可写时退化为仅控制台输出`）；`logger.py:51-55`（`if sys.stderr is not None` 才装控制台 handler）；`setting.py:37-57`（`normalize_cwd()` 兜底 `except OSError: pass`，注释自述 vision/logger 仍依赖 cwd 相对路径）
- **severity**: P1，置信度 HIGH（代码路径确定；触发条件为打包窗口模式 sys.stderr=None / cwd 不可写 / cwd 归一失败）
- **issue**: 类别 **SP-1 静默吞错（取证通道自身静默消失）+ SP-5**。判据：文件日志初始化失败被完全吞掉且不重试、不告警；控制台通道在窗口模式（`pythonw`/PyInstaller `--noconsole`，本仓 `main.spec` 即打包入口）`sys.stderr is None` 时同样不装 ⇒ 全仓 `logger.error/warning`（本报告多条「已记录日志」的证据基础）**一条都不落盘**，程序仍「正常运行」。
- **impact**: 所有静默失败类证据的**唯一观测面**可能不存在；排障者看到「没有 logs/minesweeper.log 或为空」会误判「未发生错误」（典型的「未观察到 ≠ 没发生」）；`_decide_unavailable` 的 ERROR、视觉低置信度 WARNING、C++ 降级告警全部无声。与 S-01 叠加：自动线程异常退出既无 UI 也无日志。
- **fix recommendation**: 日志路径改用 `setting.project_path("logs/minesweeper.log")` 绝对路径；初始化失败时按「降级链」保留证据：临时目录文件 → 系统 stderr → `sys.__stderr__` → 至少把失败原因通过 `QMessageBox`/状态条在启动时显式呈现（fail-visible，不 fail-silent）；`normalize_cwd` 失败同样要 `logger.error` + UI 提示（它一失败，`utils/vision.py` 的 `image/` 相对模板加载与 logger 相对路径同时失效）。

### S-05【P2 · HIGH】`locate_exit()` 丢弃匹配分数直接返回坐标，`play()` 无条件点击——胜负弹窗定位失败时盲点

- **location**: `utils/vision.py:234-238`（`locate_exit` 调 `_match_template_on_screen` 后 `_, x, y = …` **丢弃 `_min`**，无阈值判定）；对照 `utils/vision.py:240-244`（`_locate` 有 `return _min < 0.015, x, y`）；`utils/solver.py:300-301`、`:328-329`（`exit_i, exit_j = self.locate_exit(); pyautogui.click(exit_i, exit_j)` 无任何条件）
- **severity**: P2，置信度 HIGH（同一文件内两套定位函数口径不一致，逐字可判）
- **issue**: 类别 **SP-2 危险兜底**。判据：`cv.matchTemplate` 恒返回一个「最相似位置」（哪怕相似度为 0.9），`locate_exit` 把它当坐标返回，调用方直接点击 ⇒ 失败被编码成一次真实鼠标动作。
- **impact**: 胜负弹窗尚未出现/被遮挡/分辨率变化时，点击落在屏幕中某个与 `exit.bmp` 最相似的随机位置——可能是棋盘上的未开格（误点击）、也可能是其他窗口；无日志、无告警，事后只能靠录屏归因。
- **fix recommendation**: 复用 `_locate` 的阈值：`locate_exit` 返回 `(ok, x, y)`；调用方 `if not ok: logger.warning + time.sleep 重试一次 + 跳过点击`；阈值与 `_locate` 统一常量，并在模板更新后重标定（写「验证建议」）。

### S-06【P2 · HIGH（边界判定项）】视觉「部分失败」（不可识别比例 < 25%）不向决策层/取证层传播：`UNKNOWN` 静默保持为未开(9)，decide 在部分错误的盘面上照常输出高置信建议

- **location**: `utils/vision.py:427-470`（`_apply_recognition`：`ratio >= UNKNOWN_ABORT_RATIO(0.25)` 才全盘作废；以下仅 `logger.warning("%d/%d 格无法可信识别（保持未开）")` 然后继续写回）；`utils/vision.py:18-20`（docstring 明示「少量不可识别格保持未开(9)并告警」）；`utils/probability.py:965-1014`（`validate_board_consistency` 只查硬矛盾，无法发现「本该是数字却仍是 9」）
- **severity**: P2，置信度 HIGH（设计行为与「上层不可感知」这一判据均成立；严重度不升 P1 是因为这是文档化的有意降级，且 25% 闸门 + 逻辑一致性校验提供部分兜底）
- **issue**: 类别 **SP-2 降级为合法但错误的输入 + SP-5**。判据：识别失败格被原地保留为「未开」，而 `number5_1`/`decide` 把「未开」当事实求概率；unknown 计数只进日志，不进 decide 入参、不进 `decide_trace`、不进 heatmap payload/状态条 ⇒ 「识别失败 n 格」与「一切正常」在决策与取证面上不可区分。
- **impact**: 少量误识别（典型：局末弹窗边缘、主题/DPI 微变、贴边格模板不匹配）会让 decide 基于错误棋盘给出**高置信度**建议（prob/heatmap 数字全部合法）；用户点下去可能踩雷，事后无法从 trace 复核「当时盘面有多少格不可信」。
- **fix recommendation**: 把 `unknown/total/ratio` 透传：`complete_scan/rescan_after_click` 返回 `(cell_value, scan_meta)` 或写实例字段 `self._last_scan_meta`；`decide_trace.record` 增记 `unknown/ratio`；UI 状态条在 `unknown>0` 时显示「本次识别有 n 格不可信」；`_decide_unavailable` 语义不必改。这可让「识别部分失败」在取证面可分辨，而无需改变既有降级策略。

### S-07【P2 · HIGH】`ab_native` 的 `arm` 参数对 safety/ply2/ply3/exact **静默等价于 production**（A/B 对照臂失效）

- **location**: `cpp/ab_native.cpp:585-643`（`play_one` 内 production 桥接块**无条件执行**并以 `continue` 结束循环）；`:645-696`（含 `switch (arm)` 的旧近似器路径整体**不可达**）；`:507-514`（`run_ab_mines` 只接受 production/oracle）、`:780-790`（`run_ab` 接受 6 个 arm 字符串）
- **severity**: P2，置信度 HIGH（控制流静态可判：`arm` 只在 507/780 处解析，`play_one` 的 production 分支在 `:643` `continue`，后续 switch 永不执行）。不升 P1：当前仓内调用方（`bench/newlayout_phase2_det.py:14`、`paired_expand.py:65`、`paired_weighted_stats.py:22`）只在 `run_ab_mines` 用 `"production"`，`run_ab` 无现役调用点，故目前未污染在册结论。
- **issue**: 类别 **SP-3 测试/实验替身掩盖真实行为**。判据：接口声称按 `arm` 选策略，实际除 `oracle` 外所有臂走同一 production 路径；`R.certain_safe`/`R.cap` 等读数也在同一路径下产出 ⇒ 跑 `arm="safety"` 得到的「对照臂」是 production 的重复样本。
- **impact**: 任何后续用 `run_ab(arm=…)` 做 A/B（该文件存在的目的）都会得到「两臂无差异」的假结论；若历史日志里存在此类对照，其结论同样不可信（需按此复核）。
- **fix recommendation**: 要么删除不可达旧臂并把 `run_ab` 的 `arm` 收窄为 `production|oracle`（非法值 `throw`），要么把 production 桥接放回 `switch` 的 `Arm::Production` 分支、保留其余臂；并在返回 dict 中回显实际生效的 arm，让调用方可断言「我跑的是我要的臂」。

### S-08【P2 · HIGH】`ab_native` 返回的 `fails` 字段恒为 0（声明后从未赋值）——实验遥测假数据

- **location**: `cpp/ab_native.cpp:345-350`（`struct Stats { int64_t games = 0, wins = 0, fails = 0; … }`）；`:816`（`d["fails"] = S.fails;`）；全文无 `S.fails +=` / `S.fails =` 赋值（grep 仅命中声明与输出两处）
- **severity**: P2，置信度 HIGH
- **issue**: 类别 **SP-5 证据漂移（初始化后零赋值的恒值字段）**。判据：字段被 `py::dict` 输出给 Python 调用方，语义名称是「失败局数」，但没有任何代码路径增加它 ⇒ 消费者读到 0 只能说明「该字段没接线」，不能说明「0 局失败」。
- **impact**: 若实验报告/回归脚本用 `fails` 判「无失败局」，会把 abort/超步数/异常退出的局读成成功；真实的死亡/中断只能靠 `dead_per_game`、`steps_per_game` 看（这两个字段有赋值）。属「假绿数字」，正是本席专项要抓的形态。
- **fix recommendation**: 最省事是删掉该字段（避免误读）；若要保留，定义清语义（如 `steps >= max_steps` 或 `dead` 之外的异常终止数）并真实累加，同时在 Python 侧调用处加断言「fails 字段存在且语义为 X」。

### S-09【P2 · HIGH】`analyze_decide_trace.py` 输出**基于过期机制的结论**，且不校验 trace 覆盖的构建/配置即下否定结论（实测：367 条记录跨越决策内核修改时刻、混有 4 种 `wr_threshold` 与两种 `is_play`）

- **location**: `utils/analyze_decide_trace.py:97-104`（打印「reason=win_rate 次数…从未触发。limitation<=wr_threshold 这道门槛在本批真实运行中不起作用。」）、`:79-88`（只按 `session` 分组，不校验记录时点/构建版本/配置）；对照 `cpp/mscore.cpp:2943-2956`（当前门槛已是 `COST_CAP_LOG2/mem/world_upper`，`limitation<=wr_threshold` 已废止）；实测证据（本席只读分析 `state/decide_trace.jsonl`）：**367 条 / 8 个会话，`is_play=True` 341 条 + `is_play=False` 26 条，`wr_threshold` ∈ {8.0×124, 8.5×209, 12.5×8, 18.0×26}；文件 mtime `2026-11-09 00:34:08` 晚于 `cpp/mscore.cpp` 的 mtime `00:32:36`** ⇒ 同一份 trace 必然混有改动前/后两种决策内核产物；`state/time_budget.json` 现为 `{"wr":{"t":8.0,...}}`（组织者口径为 8.5，说明持久值随会话变化）。
- **severity**: P2，置信度 HIGH（机制过期与数据混批均为实测）
- **issue**: 类别 **SP-5 缓存读数漂移（旧证据 + 过期判据）**。判据：①分析器把「本批 win_rate 计数」直接解释成「门槛是否在起作用」，而当前代码的准入判据根本不是 `limitation<=wr_threshold`——结论指向已不存在的机制；②不记录/校验 trace 的时间范围、构建版本与生产配置（`MSW_DECIDE_TRACE=1`、`is_play`、`node_cap=2e6`、wr 值），旧记录与新代码混用即产出「格式合法、数字具体、指向错误」的结论。
- **impact**: 用该脚本跑一遍就能得到看似权威的「当前实现从不触发前瞻」类结论；组织者给的配置条件下本可复核，但记录未携带构建指纹 ⇒ 只得靠文件 mtime 手工比对（本次实测已证明会混批）。
- **fix recommendation**: ①`decide_trace.record` 增构建/配置指纹（至少代码版本或 `cost_log2` 语义标记、`COST_CAP_LOG2`、`DSTAR`、`is_play`、`node_cap`）；②分析器按指纹分组并显式打印「记录时间区间 / 会话列表 / 构建标识」，与当前代码不一致时输出「不代表当前实现」；③Q2 文案改为客观计数 + 注明「当前准入判据为 `cost_log2<=COST_CAP_LOG2`，不是 limitation」。
### S-10【P2 · HIGH】trace 分析对损坏行与缺失字段**静默降级为假数字**

- **location**: `utils/analyze_decide_trace.py:64-73`（逐行 `json.loads`，`except Exception: pass` —— 损坏行被静默丢弃，无 skipped 计数）；`:148`（`big_n9 = [r for r in with_mem if r.get("n9", 0) > 0]` —— 缺 `n9` 的记录按 0 计入「无内部格」）
- **severity**: P2，置信度 HIGH
- **issue**: 类别 **SP-2 降级为合法结果**。判据：文件被截断/写坏（`decide_trace` 是追加写，进程崩溃可留半行）时，输出仍是完整报表且样本量缩水不可见；字段缺失与真实 0 混为同值。
- **impact**: 统计结论的样本量与口径不可审计；「n9=0 的记录占比」等派生读数系统性偏小，可能把「采不到字段」误读成「没有内部无约束格」。
- **fix recommendation**: 统计并打印 `skipped_lines` 与 `missing_field_rate`；`n9` 缺失单独归为 `None` 组，不与 0 合并；`spearman` 等统计在样本量不足时打印明确 N 与判定「样本不足，不下结论」。

### S-11【P2 · MEDIUM】`decide_trace` 记录失败全部走 debug：**「记录点存在」与「真的记录到」不可分辨**

- **location**: `utils/decide_trace.py:131-135`（写文件异常 → `logger.debug("decide_trace 写入失败（忽略）")`）；`:85-94`（`MSW_DECIDE_TRACE=0` / 20MB 上限 → 静默 `return`，上限时仅 `logger.warning` 一次）；`:44-71`（`_board_stats` 两处 `except → logger.debug`）
- **severity**: P2，置信度 MEDIUM（故障概率低，但后果正是取证面沉默）
- **issue**: 类别 **SP-1 静默吞错 + SP-5**。判据：本模块被 `probability.py:1066/1347` 当作争议结论的复核依据；写盘失败（磁盘满/权限/路径）只留 debug，默认 DEBUG 文件 handler 若也失效（见 S-04）则连 debug 都无处可落 ⇒ 复核者面对空 trace 无法区分「没跑」与「跑了没写进去」。
- **impact**: 依赖 trace 的争议裁决（limitation/内存门/前瞻触达率）可能在无证据状态下被「空文件」误导为「未发生」。
- **fix recommendation**: 首次写入失败升级为 `logger.warning` 并在进程内落一个 `trace_error` 计数（可通过一个 debug 接口/启动横幅暴露）；分析器开头校验「文件存在且条数>0 且本会话有记录」，否则显式输出「证据缺失，不得据此下结论」。

### S-12【P2 · MEDIUM】截图帮助保存模板时 `cv2.imwrite` 返回值被忽略：写失败仍弹「图片一保存至…」

- **location**: `main.py:2590-2598`（`_save_img`：`cv2.imwrite(_out, self.img)` 后无条件 `QMessageBox.about(self, "信息", f'图片一保存至"{_out}"')`）；同型：`main.py:2575-2578`（`cv2.imwrite(_temp_bmp, self.img)` 返回值忽略后直接 `QPixmap(_temp_bmp)`）
- **severity**: P2，置信度 MEDIUM（触发需路径不可写/磁盘满；但代码路径确定——`cv2.imwrite` 返回 `bool` 且此处未用）
- **issue**: 类别 **SP-1 静默吞错（失败被编码成成功提示）**。判据：`cv2.imwrite` 失败不抛异常只返回 False；代码忽略返回值后弹成功框 ⇒ 用户以为模板已更新，实际文件未变/被截断。
- **impact**: 模板文件是本应用识别的唯一输入（`utils/vision.py`）；假成功之后用户继续用旧/坏模板截图识别，会表现为「识别突然不准」而无任何因果线索——正是本仓历史事故形态（截图功能覆盖损坏模板）。
- **fix recommendation**: 检查 `imwrite` 返回值：False 时弹错误框并保留原文件（写临时文件 + `os.replace` 原子替换，失败不覆盖），并把失败写 warning 日志。

### S-13【P2 · MEDIUM】`play()/help()` 不校验窗口句柄：`FindWindow` 失败(hwnd=0) 仍继续算坐标并点击，点击落屏

- **location**: `utils/mm0.py:42-49`（`ClientToScreen` 直接 `GetWindowRect(HWND(hwnd))`，无 hwnd 有效性检查）；`utils/solver.py:246-247`（`hwnd = win32gui.FindWindow(None, setting.win_name)` 后直接 `ClientToScreen(hwnd, …)`，未判 0）；`:260-262`、`:308-311`（无条件 `pyautogui.click`）；对照 `main.py:2134-2139`（`click_all_func` 显式 `if hwnd <= 0: … return`，是正确范式）
- **severity**: P2，置信度 MEDIUM（`GetWindowRect(0)` 的行为依赖 API/环境，本席未运行时验证；但「无 hwnd 校验」这一事实静态确定）
- **issue**: 类别 **SP-2 危险兜底（失败退化成绝对坐标）**。判据：窗口不存在时 `ClientToScreen` 返回的偏移量无意义（常见为 (0,0)），后续点击坐标退化为 `(x*cell_width, y*cell_width)` 的屏幕绝对坐标，系列鼠标动作不再指向游戏。
- **impact**: 游戏窗口被关闭/改名/最小化后，自动模式继续在屏幕左上区域点击——可能误点其他窗口，且无告警（日志只有默认的 debug/无）。与 S-01 叠加时用户只看到「完成」。
- **fix recommendation**: `play()/help()` 入口 `if hwnd <= 0: warning_signal.emit("未找到扫雷窗口…") + return`；`ClientToScreen` 失败返回 `None` 并由调用方中止本轮。

### S-14【P2 · MEDIUM】`count` 单一计数混合三类语义，最终统一播报成「请检查设置中总雷数…」——诊断方向被误导

- **location**: `utils/solver.py:283-286`（`if self.count >= 3: self.warning_signal.emit("请检查设置中总雷数，宽度，长度是否输入正确")`）；计数来源混合：`utils/probability.py:787`（`_decide_unavailable` 决策不可用）、`:962`（扫描全空早退）、`:991`/`:1006`/`:1013`（盘面逻辑矛盾/重扫失败）
- **severity**: P2，置信度 MEDIUM（文案是可见告警，不属完全静默；属「失败原因被错误归因」）
- **issue**: 类别 **SP-5 变体（失败归因错误 → 静默排除真实根因）**。判据：三类完全不同的失败（视觉不可信 / 决策不可用 / 设置错）共用同一计数器与同一文案，用户被导向检查设置。
- **impact**: 真因（模板/DPI/遮挡导致的识别失败、C++ decide 连续不可用）被「设置错误」提示掩盖，排障绕路；对自动化核对者也无法从告警文案区分。
- **fix recommendation**: 拆三计数（`scan_fail`/`decide_fail`/`setting_suspect`）并在文案中带计数与首次原因（`_decide_unavailable` 已有 `detail`，把它摘要进告警），主循环判据按类型分别处理。

### S-15【P2 · HIGH】预览浮层局部棋盘边界判定 off-by-one：把 `cell_value` 的 0 哨兵边框渲染成「已开格」

- **location**: `ui/preview_popup.py:104-137`（`sample_board`：`if 1 <= i < ncols and 1 <= j < nrows:`）；`ui/preview_popup.py:339-352`（`mini_cell_style(0)` → 空白已开格样式）；对照 `main.py:1898-1905`（主热力图用 `cv[j+1, i+1]`、索引上限 `self.h/self.w`，即内部区域 1..h/1..w）
- **severity**: P2，置信度 HIGH（索引区间静态可判；`cell_value` 外框恒为 0 的依据：`ab_native.cpp:586-592` 实测注释「production cell_value has a 0-filled sentinel border」、`utils/solver.py:266-269` 构造方式）
- **issue**: 类别 **SP-4 几何/索引正确不等于像素正确**。判据：`cell_value` 形状 `(h+2, w+2)`，合法格是 `1..h × 1..w`；条件允许到 `ncols-1 = w+1`、`nrows-1 = h+1`，即把右/下边框（恒 0）当作棋盘内格读取，`mini_cell_style(0)` 渲染为空白的「已开」格而不是 `None` → `_MINI_OUTSIDE`。
- **impact**: 靠近右/下边缘的悬停格，7×7 预览会多出一列/一行「看似已开」的格，与实际棋盘不符（用户据此判断局部局面）；这是典型的「布局/索引断言全绿但像素语义错」——没有任何断言覆盖边界格。
- **fix recommendation**: 条件改为 `1 <= i <= ncols - 2 and 1 <= j <= nrows - 2`（或按 `w = ncols-2 / h = nrows-2` 显式判断）；补一条边界用例（悬停 (w,h) 时最右列应为 `None`）作为回归断言。


---

### S-16【P1 · HIGH】决策确定性契约与实现矛盾：PSEQ 残差用 thread_local PRNG 抽签，而 `decide()` 自述「PURE / 确定性 / seed 仅作对称」

- **location**: `cpp/mscore.cpp:3213-3228`（pbs 层 PSEQ 残差：`rs3`/`rs4`/`rs5` 抽签）、`:3541-3552`（win_rate 层同型：`rs6`/`rs7`/`rs8`）；契约声明：`:2406-2414`（注释逐字「decide() is a PURE function of cell_value+config; no Python RNG, no hidden state」）、`:3994-3997`（「seed is accepted for harness symmetry but is intentionally unused; tie-break is deterministic」+ `(void)seed`）、`:4098-4102`（`seed` 作为 pybind 参数对外暴露）；`utils/decide_trace.py:111-116`（记录白名单无「是否随机抽签」字段）
- **severity**: P1，置信度 HIGH（契约文字与 PRNG 代码均为逐字可判；`thread_local` 静态状态跨调用累积是 C++ 语义事实）
- **issue**: 类别 **SP-1 接线/契约沉默 + SP-5 证据漂移**。判据：①`decide()` 在 PSEQ 过滤后残差集 >1 时由 thread_local xorshift 抽签（`rs3`/`rs6` 分支；`tied`/`X` 的顺序也来自盘面，抽签结果随**同线程历史调用次数**变化）；②`seed` 形参被 `(void)seed` 忽略却以 `py::arg("seed")` 暴露，调用方以为可复现；③返回 dict 与 `decide_trace` 均无「本次是否随机抽签 / 统计被跳过」字段 ⇒ 同一盘面两次 decide 差异无法归因为「随机残差」还是「缺陷」。关于组织者点名的 `rs5`/`rs8`（`total_rows==0` / `nrows==0` 分支，`:3224-3228` / `:3549-3552`）：这两支在当前结构上被上游 `empty_group` 早退（`:2519-2520` `n<=0` 即返回）结构性遮蔽，**多半不可达**；但它们是「统计失败 → 直接随机选格，reason 仍标 limitation/win_rate」的兜底形态，必须在同一修复里显式化。
- **impact**: 本仓大量实验/回归依赖「同盘面同输出」（`bench/data/*_parity.json`、seed 复现脚本、`decide_trace` 事后重放）；残差并列局面下决策静默不可复现，核对差异时会把随机波动误判成回归，或把回归误判成随机。对本席（静默失败专项）这是「验证面沉默」。
- **fix recommendation**: 二选一并留痕：①保留随机残差 → docstring/注释改正、`seed` 真正驱动（局部 PRNG 而非 thread_local 跨调用状态）、返回 dict 增 `tie_break="random_residual"` 并落 trace；②坚持「PURE」契约 → 残差改用 `deterministic_tie_break`，同步删除 `seed` 形参与相关注释。无论哪种，`rs5`/`rs8` 这种「统计不可用」分支必须返回显式 reason（如 `pseq_stats_unavailable`），禁止落到正常 reason。

### S-17【P1 · HIGH】本轮核心成本门槛 `cost_log2` 在验证面**完全缺失**：C++ 计算并返回、Python 零消费、trace 0/367 记录，分析器仍以已废弃的 `limitation` 为键

- **location**: `cpp/mscore.cpp:4004-4034`（`r["cost_log2"] = lb`；异常时 `-1.0`）、`:2954-2956`（`use_win_rate = log2_total_est <= COST_CAP_LOG2 && mem.allowed && world_upper > DSTAR`）、`:2560-2585`（`log2_total_est` 构造）；**未落盘**：`utils/decide_trace.py:111-116` 白名单 = `reason,x,y,path,confidence,world_upper,estimated_bytes,limitation`（无 cost_log2）；**未消费**：全仓 Python `grep cost_log2` = 0 命中；`utils/probability.py:38-58`（`_decide_diag` 摘要字段也无 cost_log2）；`utils/analyze_decide_trace.py:108-131`（Q1 Spearman 用 `limitation`）、`:97-104`（Q2 用 `limitation<=wr_threshold` 解释 win_rate）
- **severity**: P1，置信度 HIGH（双向可验证：Python 侧 0 消费点 + 磁盘真实 trace 367 条中 `cost_log2` 出现 **0** 次、`limitation` 出现 283 次，实测）
- **issue**: 类别 **SP-5 证据漂移（本轮改动 vs 验证仪器脱节）**。判据：本轮风险声明明确「limitation 门槛换成 cost_log2」，而成本量的唯一消费面（trace/分析器/`_decide_diag`）仍全部以 `limitation` 为键；`cost_log2` 在 C++ 计算后无人接住 ⇒ 用户给的条件（`MSW_DECIDE_TRACE=1`、`is_play=True`、`node_cap=2e6`、wr 从 `state/time_budget.json` 读）下**也无法从真实运行复核「前瞻准入是否按真实成本生效」**。
- **impact**: 本轮门槛改动的验收只剩离线推算；任何「cost_log2 生效/拦截比例」的结论都没有端到端证据；下一轮评审会重复本仓「limitation 与耗时方向相反」式误判。且 `analyze_decide_trace.py` 会继续把 Q1/Q2 结论建立在已判死的指标上（与 S-09 叠加）。
- **fix recommendation**: ①`utils/decide_trace.py:111-116` 白名单加 `cost_log2`（C++ 已产出，零决策改动）；②`_decide_diag` 加 `cost_log2`，ERROR 日志可直接归因；③分析器 Q1 改 `cost_log2`（`limitation` 保留为 deprecated 对照列）；④trace 记录加 schema 版本号，让「旧记录无此字段（None）」与「值为 0」可区分。

### S-18【P2 · HIGH】OPTIMAL 精确层失败全部**静默落层**（`optimal_skip`/节点超限/引擎异常/转移渲染异常），无任何 reason 或诊断字段

- **location**: `cpp/mscore.cpp:2799-2801`（`catch (const std::exception&) { optimal_ok = false; }`，注释逐字「any materialization failure -> fall through」）、`:2751`/`:2795`（`optimal_skip: … exceeds DSTAR` 两处 throw，被上面 catch 吞掉）、`:2927-2933`（`catch (const NodeCapExceeded&)` + `catch (const std::exception&)` 两个空 catch，注释「exact enumeration exceeded node budget -> fall through」/「engine failure -> fall through」）、`:3557-3559`（win_rate 层 PSEQ 后处理 `catch (...) { chosen = deterministic_tie_break(tied); }`）。对照**可见**降级点（有 reason）：`:2526-2532`（`part_solve_error:`）、`:2967-2969`（`pbs_no_feasible_worlds`）、`:3295-3326`（`node_cap_fallback`）
- **severity**: P2，置信度 HIGH（四处 catch 均无日志/字段；注释自述 fall through）
- **issue**: 类别 **SP-2 + SP-1**。判据：这四条异常/守卫路径让「精确最优层」退化为启发式，但返回 dict 的 `reason` 只会是下层结果（`limitation`/`pbs_*`/`win_rate`），与「OPTIMAL 本来就不适用（`world_upper > DSTAR`）」**完全同形**；`decide_trace` 只记 reason ⇒ 无法统计「OPTIMAL 尝试过但失败」的比例。另：OPTIMAL 出口的 `transitions` 渲染（`transition_branch_board`）与最优解计算在**同一个 try** 内（`:2834-2925`），任何渲染异常会把**已算好的最优决策整体丢弃**，代价不对称。
- **impact**: 「精确层实际覆盖率」不可审计；残局本应 100% OPTIMAL 的局面若因物料化/引擎异常降级，外部只看到 heuristic 决策，且转移字段可能同时缺失（被 UI 解释成「暂无真实转移」）。这正是本轮要求猎捕的「表面正常运行、实际降级」。
- **fix recommendation**: 给 `make_response` 增不参与决策的诊断字段 `fallback_from`（取值 `optimal_skip_materialize|optimal_skip_nodecap|optimal_engine_error|pseq_error`），`decide_trace` 落盘；分析器提供「OPTIMAL 尝试/失败/不适用」三态计数；把 transitions 渲染挪出决策 try（渲染失败只丢转移，不丢决策）。

- **对组织者三问的直接回答**（catch / `make_response(-1,-1,…)` / `optimal_skip` 是否把失败伪装成正常决策）：①`catch (const std::exception&)` —— **带 reason 的出口不伪装**（`:2526-2532` part_solve_error、`:3740-3742` engine_error、`:3842-3844` part_solve_error、`:3915-3917` enumeration_error、`:4054-4061` lookahead_exception，reason 会进返回 dict）；**不带 reason 的四处是真伪装**（`:2799-2801`、`:2927-2933` 两处、`:3557-3559`，见本 S-18），桥接两处（`:4568-4570`、`:4699-4701`）见 S-22。②`make_response(-1,-1,…)` —— **对终端用户不可伪装**：唯一生产消费方 `utils/probability.py:1365-1381` 以 `x<1` 判定并统一走 `_decide_unavailable`（ERROR + warning_signal + 全盘安全重扫），失败全程可见；但**对 trace/bench 消费面有歧义**：失败返回的 `path` 与正常路径同码（`:2520`/`:2530`/`:2968` 的 `path=1` 与正常 pbs 相同），且 dict 无 `ok` 标志 ⇒ 按 `path` 分类的分析会把失败当 pbs 成功（`decide_trace.py:111-116` 已落盘 path，建议同修）。③`optimal_skip` —— **是失败伪装成正常**：`:2751`/`:2795` 两处 throw 被 `:2799` 空 catch 吞成 `optimal_ok=false`，精确层静默降级为启发式而 reason 落到下层，与「OPTIMAL 本就不适用（`world_upper > DSTAR`）」完全同形。

### S-19【P1 · MEDIUM】PSEQ 统计路径的无界下标与静默截断：`tied.size()>64` 时写越界；frontier 超 `256` 时统计行被整批跳过 → 退化为随机抽签且无标记

- **location**: `cpp/mscore.cpp:3111-3114`（`const size_t nTied = tied.size(); int counts[64][10];` —— 无 `nTied<=64` 检查）、`:3138`（`for (size_t ti = 0; ti < nTied; ++ti)` → `counts[ti][mn]++`）、`:3121`（`int row_vals[256]`）、`:3134`（`if (!valid || flat != ordered_clicks.size()) { ++total_rows; continue; }`）；win_rate 层同型：`:3454-3455`（`nT; int cnts[64][10]`）、`:3462`（`int rvals[256]`）、`:3474`（`if (flat2 != ordered_clicks.size()) { ++nrows; continue; }`）
- **severity**: P1，置信度 MEDIUM（缺上界是静态事实 HIGH；但 >64 并列集在真实盘面的触发率本席无法测，故降置信不降级——触发即内存破坏，且与本仓历史 0xC0000005 崩溃形态同族）
- **issue**: 类别 **SP-2（降级为正常结果）+ 内存安全**。判据：`tied` 由 `float(bestv-rr(k))<=0.0005f`（`:3081`）或 win_rate 精确并列（`:3339-3342`）构造，大小随盘面可变，代码无 64 上界；`ordered_clicks` 无 256 上界。后果都「看起来正常」：①`nTied>64` → 栈数组越界写（可能破坏相邻变量 → 随机错决策，或进程级崩溃「该内存不能为 read」）；②frontier>256 → 所有统计行被判无效跳过、`counts` 全 0 → s/e/q 全 0 → 过滤器不生效 → 落到 `rs3` 随机抽签，reason 仍标 `limitation`/`win_rate`。
- **impact**: 大/退化盘面下决策可静默随机化或被内存破坏；两者均无观测标记。与 S-16 叠加后，「随机」与「越界破坏」在外部完全不可分辨。
- **fix recommendation**: ①`nTied`/`nT` 改为 `std::vector<int[10]>`（或加 `if (nTied > 64) → 显式降级 + 诊断字段`）；②`row_vals/rvals` 截断与 `flat!=ordered_clicks.size()` 跳过必须置 `pseq_skipped=true` 诊断并进返回 dict；③补「>256 frontier、>64 tie」等构造性负向单测，断言不越界且诊断可见。

### S-20【P2 · HIGH】环境调参静默回落：`MSW_COST_CAP_LOG2` / `MSW_DSTAR` 非法值直接换默认，无任何告警

- **location**: `cpp/mscore.cpp:2591-2598`（`MSW_COST_CAP_LOG2`：`if (v>=0.0 && v<=40.0) return v;` 否则 `return 20.0;`，注释「可通过环境变量覆盖（验收调参不重编译）」）、`:2634-2641`（`MSW_DSTAR`：`if (v>=0) return v;` 否则 `return 400`）；对照 Python 同类实现**会告警一次**：`utils/probability.py:364-387`（`_wr_node_cap` 非法值 `logger.warning`）
- **severity**: P2，置信度 HIGH
- **issue**: 类别 **SP-2（配置失败降级为默认值）**。判据：这两处是成本门槛/精确层的**唯一运行时旋钮**，均为进程级 magic static（只读一次）；非法/超界值被静默替换成默认 ⇒ 验收实验可能以为「已把 cap 调到 X」，实际跑的是默认值，且事后无日志可核。
- **impact**: 调参实验的参数归属不可验证（典型「参数没到达被测路径」历史事故形态）；若结论依赖 cap/DSTAR，口径不可复现。
- **fix recommendation**: 非法值 `fprintf(stderr, "[mscore] MSW_COST_CAP_LOG2=%s 非法，回落 %.1f")` 一次；`py_decide` 返回 dict 增 `config` 摘要（cost_cap/dstar/help_mode），由 trace 落盘。

### S-21【P2 · MEDIUM】C++ 诊断串只写不导出：`g_last_pbs_diag` 是死信，pbs 分支之争的关键证据永远拿不到

- **location**: `cpp/mscore.cpp:2428`（`static thread_local std::string g_last_pbs_diag;`）、`:3004-3012`（`snprintf(buf,"pbs_n%d_max%.4f", …); g_last_pbs_diag = buf;`）；全文件仅这 2 处引用，`PYBIND11_MODULE`（`:4086-4138`）无任何 `m.def` 暴露它（grep 证据）
- **severity**: P2，置信度 MEDIUM（死信事实确定；影响限于诊断面）
- **issue**: 类别 **SP-5（取证面沉默）**。判据：注释自述该诊断「让整局驱动区分『没有确定安全候选』与『确定安全分支从未运行』」（`:3001-3003`），写入后没有任何导出/消费路径，返回 dict 也无该字段。
- **impact**: 依赖「pbs 分支为何未给 certain_safe」的争议只能靠 reason 字符串里 `pbs_n*_max*` 前缀间接推断，其他诊断维度永远不可见；这是「以为有遥测、实际没有」的假象。
- **fix recommendation**: 返回 dict 增 `pbs_diag` 字段并落 trace，或删除该变量与相关注释（不留假诊断）。

### S-22【P2 · MEDIUM】桥接层把「核心异常」与「无可用决策」都编码成 `return 0`：AB 实验的失败/闸门统计被静默混合

- **location**: `cpp/mscore.cpp:4563-4570`（`abnative_decide`：`catch (const std::exception&) { return 0; // any native failure -> caller falls back safely }`）、`:4582`（`if (x<1||y<1) return 0;`）、`:4694-4701`（`abnative_decide_batch` 同型 `catch → return 0`）；消费方 `cpp/ab_native.cpp:486-498`（`if (ok2==1 && …) … else { ++R.cap; 取第一个未开格点击 }`）
- **severity**: P2，置信度 MEDIUM
- **issue**: 类别 **SP-1（失败被编码成正常返回）+ SP-5**。判据：`abnative_decide` 的返回契约注释（`:4540-4544`）声明 `1=成功 / 0=无可用决策 / 2=预检拒绝`，但**异常路径也返回 0**；`ab_native.cpp` 把 0 记为 `cap`（闸门命中）并做确定性降级点击 ⇒「决策核心抛异常」在实验统计里显示为「正常的规模/内存闸门命中」。
- **impact**: AB 实验 `cap_hits` 指标被污染；若核心在特定局面反复异常（如 S-19 的越界/新异常），整批实验会以「降级点击」而非「失败」形态计入胜率，掩盖真实缺陷（实验面假数据）。
- **fix recommendation**: 返回值增加区分（如 3=core_exception、4=invalid_coords）或让异常继续抛出；`AbResult` 增 `decide_errors` 并输出；统计口径把「异常」与「闸门」分开打印。
## 认定为「有意的安全降级」（不报为发现，但记录判据）

| 位置 | 降级行为 | 为何判为有意（不报） |
|---|---|---|
| `utils/vision.py:472-496`、`:529-543` | 批量识别抛错 → warning + 回退逐格 `compare_img` | 有可见 warning、语义等价（`compare_img` 与批量同判据，docstring 与 `bench/bench_vision_vector.py` 声明逐格等价）、失败面显式 |
| `utils/probability.py:1606-1618`、`:1849-1883` | C++ pbs/win_rate 异常 → warning + 回退纯 Python | 有 warning；且经 grep 核验该链当前**不可达**（生产 `number5_1` 只走 `native.decide`；`process_bigger_situation`/`win_rate` 仅测试/bench 调用，`tests/test_cpp_decide_wiring.py:573` 还断言其零调用）；但见「开放问题 O-02」的文档矛盾 |
| `utils/probability.py:726-788` | `_decide_unavailable`：ERROR 日志 + 首发弹窗 + 全盘安全重扫 + 显式 return | 用户批准方案 A 的显式失败语义，非静默；`_DECIDE_FAIL_SEQ` 可见 |
| `cpp/mscore.cpp:3295-3299`、`:3300-3326` | win_rate 节点数超限 → `wr_ok=false` → pbs 降级（reason=`node_cap_fallback`） | 有专属 reason 字段、Python 侧计数 `_wr_nodecap_hits`/`_WR_NODECAP_TOTAL` 可观测 |
| `cpp/mscore.cpp:2957-3000`、`:2967-2969` | pbs 空可行集 / 内存门 → 明确 reason（`pbs_no_feasible_worlds` 等） | reason 会经 `_decide_diag` 进日志 |
| `cpp/mscore.cpp:4035-4063` | 帮助模式补前瞻失败 → `transition_meta.basis="unavailable"` + `transition_reason` | 原因字段显式，UI 有对应文案表（`ui/preview_popup.py:53-76`） |
| `main.py:639-664` | 「无法可信识别」告警降级为 INFO 日志、不弹窗 | 用户 2026-11-07 明确裁决（局末扫描噪声不得打断自动循环），且非静默（有 INFO 与 S-06 建议的补强） |
| `utils/probability.py:673-709` | 进度心跳异常 → `break`（只影响进度条估计） | 不影响决策/结果，且注释声明「心跳失败不影响计算」 |
| `cpp/mscore.cpp:2799-2801`、`:2927-2933`、`:3557-3559` | OPTIMAL/引擎异常 → 静默 fall through 到下一层（无 reason 标记） | **本轮已升格为发现 S-18**（组织者点名复审后改判：精确层覆盖率不可审计，属「失败伪装成正常」而非纯安全降级） |

---

## 「沉默即通过」命中记录

- **SP-1 静默吞错：命中**。证据：S-01（`solver.py:437-446` 无通用兜底 + `main.py:676` 假「完成」文案）、S-04（`logger.py:47-48` 日志初始化失败吞掉）、S-12（`main.py:2594-2595` imwrite 返回值忽略仍报成功）、S-11（`decide_trace.py:134-135` 写失败 debug）、S-18（`mscore.cpp:2799-2801`/`:2927-2933` OPTIMAL 失败静默落层）、S-22（桥接 `return 0` 把核心异常混成「无决策」）。
- **SP-2 降级为空/错结果：命中**。证据：S-03（`native.py:155-157` + `main.py:894-898` 回退成 best_cell 的转移=合法格式错数据）、S-05（`vision.py:234-238` 无阈值坐标=失败编码成点击动作）、S-06（`vision.py:463-465` 部分失败仅 warning、上层不可感知）、S-10（分析器缺字段按 0）、S-19（`mscore.cpp:3111-3114`/`:3134` PSEQ 统计静默截断/越界后仍返回正常 reason）、S-20（env 调参静默回落默认值）。
- **SP-3 测试替身/实验面掩盖：命中**。证据：S-07（`ab_native.cpp:585-643` 所有非 oracle arm 实际都走 production 桥接，`arm` 对照失效）；S-08/S-22（实验遥测 `fails` 恒 0、`cap_hits` 混入核心异常）。注：`tests/**` 在被排除分母内，本席不评估测试替身，故本类只命中实验桥接层。
- **SP-4 几何全绿但像素/语义错：命中**。证据：S-15（`preview_popup.py:129` 边界条件把 0 哨兵边框渲染成已开格，无断言覆盖边界）。另说明：UI 像素级读数本席无运行时手段（只读席），未做真实像素回归，故不宣称「像素已核」。
- **SP-5 缓存读数漂移：命中**。证据：S-02（`wr_threshold` 写端活跃/读端断，读数存在而无意义）、S-08（`ab_native.cpp:816` `fails` 恒 0）、S-09（`analyze_decide_trace.py:97-104` 用过期判据且不校验覆盖时点/构建；**实测 367 条 trace 跨内核修改时刻**）、S-14（失败归因错）、S-17（本轮核心量 `cost_log2` **0 消费点 + 0/367 trace**，分析器仍用已废弃 `limitation`）、S-21（`g_last_pbs_diag` 只写不导出）。

---

## 开放问题（低置信度，呈现不阻塞）

- **O-01**（已升格为发现 **S-18**：组织者点名复审 `optimal_skip`/`catch` 兜底后改判——「静默安全降级」在精确层不可审计即属失败伪装成正常，见发现清单。）
- **O-02** `utils/probability.py:1833-1884`：`win_rate()` docstring 与注释多处声明「绝不回退纯 Python」，但 `:1871-1883` 在 C++ 非 node-cap 异常时 `logger.warning` 后**继续执行下方纯 Python 实现**（代码事实）。当前该路径不可达（生产无调用点），故不报为发现；一旦该链被复活即成 SP-1/SP-2 高危点。**建议**：删除死链或把该 fallback 改为 `raise`。
- **O-03** `utils/native.py:123-133`：`except TypeError` 兼容旧 pyd 的回退同时会吞掉 C++ 内部 `TypeError`（pybind 会把 C++ `py::type_error` 映射成 Python `TypeError`），此时会「去掉 is_play 重试一次」并打「旧签名」warning——归因错误。触发概率低（须 `decide` 内部抛 TypeError），且失败最终仍会到 `_decide_unavailable`，故不单独立 P 级。**建议**：用 `inspect.signature`/尝试绑定形参判断旧 pyd，或把 except 收窄到参数错误消息。
- **O-04** `utils/combinatorics.py:132-145`：`get_list` 对非法入参（负数/超界）**静默夹取**成 `a=1/num=1` 继续枚举，没有任何告警；`p_of_c`/`combination_ratio` 用 `assert` 做参数校验（`-O` 运行时断言被剥离）。当前这些函数只在已删除的 Python 决策链/bench 中使用（生产不可达），故不立发现。**建议**：改为显式 `ValueError`，避免未来复活时静默算错。
- **O-05** `utils/probability.py:458-497`（`_ps_record_ms` 失败 `logger.debug`）、`:595-604`（`_tb_save` 失败 `logger.debug`）：耗时/模型状态落盘失败只 debug，`data_time.json` 与 `state/time_budget.json` 会静默停止更新；若有人用它们做「k 是否收敛」的判断，会拿到陈旧样本（与 SP-5 同族，但当前 `_ps_observe` 已不在生产链，影响有限）。
- **O-06** `cpp/ab_native.cpp` 源码中文注释整体为 mojibake（UTF-8 双重编码：`鈥`/`涓?` 等），运行期字符串均为 ASCII 故无功能影响；按本席职责（静默失败专项）不立发现，仅提示维护风险。
- **O-07** `utils/vision.py:115-129`/`:186-206` 使用相对路径 `os.path.join("image", …)`（依赖 `setting.normalize_cwd()` 在导入期 chdir），与 `setting.py` docstring「资源路径一律不依赖 cwd」表述冲突；归一失败时表现为 `TemplateValidationError`（启动可见），故不立发现，但与 S-04 同因（cwd 归一失败被静默吞）。
- **O-08** `cpp/mscore.cpp:4096-4113`（`#ifdef MSW_TRIAL_DECIDE` 导出 `decide`，`#else` 注释逐字「decide() is intentionally NOT exported in production builds」；pybind 文档串写「TRIAL ONLY」）与生产构建事实矛盾：`cpp/build.bat:31-32` 正是用 `/D MSW_TRIAL_DECIDE` 编译生产 pyd。风险在于命名/注释误导——若有人改用未定义该宏的脚本构建，pyd 将无 `decide` 导出，自动模式每轮「决策不可用」（失败可见但功能全失）。不立发现（属命名/文档与事实不符），建议把宏改名（如 `MSW_DECIDE_ENABLED`）并同步注释。

---

## 建议

1. **裁定：REQUEST CHANGES**（本席不出 APPROVE，合并裁决归 critic）。P0 = 0（无「已/必然造成错误结果」的充分证据）；P1 = 7（S-01/S-02/S-03/S-04/S-16/S-17/S-19）、P2 = 15。**合并前建议处理**：S-02（组织者已认领修复）、**S-17（本轮改动本身的门槛量在验证面缺失——不修则本轮验收无端到端证据）**、S-16/S-19（决策内核的确定性与内存安全契约；S-19 是「失败伪装成正常」的硬缺陷，需先加显式诊断/上界再谈行为验收）；S-01/S-03/S-04 建议同批修或至少加显式标记（都是「失败不可见/错数据当合法数据」）。
2. **覆盖状态：complete**。23/23 文件落 `reviewed`（crgate 冻结分母 15,045 行），无 skipped/blocked/waived；未运行任何测试/构建（只读席纪律），所有运行时假设已写成「验证建议」或降级说明。
3. **给组织者的取证边界**：本报告的「无 X 发生」类断言均限定在静态代码路径内（如「C++ 无 wr_threshold 使用点」= grep 全量 3 处命中均读上下文）；未对真机日志做时间窗/配置覆盖核对（本席不掌握作者跑批配置），因此**不**对「历史上是否真的发生过某现象」下结论，只对「代码当前是否可能发生」下结论。
4. **需组织者另派席的验证项**（本席只读不做）：①S-01 的 PyQt5 异常逃逸行为需在真实窗口模式实测（两种行为对应不同修法优先级，已在 S-01 写明）；②S-02 的反证已由组织者完成（14 组合跨阈值 reason 全同），修复后需复跑该口径；③S-15 的边界渲染需像素级回归（可复用 tests 的 offscreen 手法）；④S-09 需按 trace 的实际构建/配置指纹重核历史结论；⑤**S-16**：同一盘面在同一线程重复调用 `decide` 观察坐标抖动，确认随机残差与契约矛盾的实际影响面；⑥**S-19**：构造 >64 并列集 / >256 frontier 盘面（建议用 ASAN 构建的 trial pyd）验证是否触发越界写与「统计静默跳过」。

---

### 材料完整性备注（取证卫生）

- **本报告已按 crgate 冻结 spec 重写覆盖账**：`spec_sha=48d5e2a5331a8b2b`、`selection_hash=2303a1c35ffd7f7f`、`reviewable_count=23`、`total_insertions=15,045`、`head_sha=afa3f4c4f350e8057ab4440406148018c8b6f2bb`（已逐项核对确认）。
- **与上一版材料的 delta**：新版新增 `ui/__init__.py`、`utils/__init__.py` 两项（本报告已 reviewed）；其余 21 项同名同源，本席上一版报告已审。**事实更正**：上一版 21 项材料清单中本就含 `cpp/mscore.cpp`（4,719 行，旧报告覆盖账第 1 行已审），与组织者「旧版漏掉 mscore」的口径不同；该差异只影响审计叙事，不影响本报告覆盖——本轮仍按组织者要求对 `cpp/mscore.cpp` 静默失败面做了加强复审（新增 S-16/S-17/S-18/S-19/S-20/S-21，另 S-22 跨文件）。
- **`review-spec.json` 被改写的归属已澄清**：系组织者用 `crgate freeze` 重新生成（00:36 手写 21 项 → 00:41 crgate freeze 23 项）。前版报告曾以「材料异常/被其它进程改写」上报，**此处更正：非异常，归属组织者**。
- **同目录 `code-reviewer*.md/json`** 为本席开始后由他席产出；按盲评纪律本席**未读取**其内容（隔离污染：无命中）。
- **行号基准**：本报告所有 `file:line` 针对 `head_sha=afa3f4c…` 的工作区快照（关键引用逐条 grep 复核）；`cpp/mscore.cpp` 行号以磁盘当前 4,719 行为准（crgate 口径 4,720 含末尾空行）。

## 【改进点】

> 逐条映射到具体文件/动作，供 executor 排期；按「先止血（不可见→可见）→ 再接线正确性 → 再实验/取证卫生」排序。

| # | 文件:行 | 动作 | 验收判据 |
|---|---|---|---|
| 1 | `utils/solver.py:437-446` + `main.py:669-680` | `play()` 补通用 `except Exception`（ERROR 日志 + warning_signal + `_fatal_error` 标记）；`_on_auto_thread_finished` 按标记区分「异常退出/完成」 | 注入一个必抛异常（如临时缺 `image/exit.bmp`）后：UI 出现错误提示且状态条不当「完成」；日志有 ERROR |
| 2 | `utils/probability.py:1038-1050,1073-1078,1353-1359,612-650` + `cpp/mscore.cpp:2943-2956` | 二选一：删整条 wr 自适应链（含 trace 字段与 `state/time_budget.json` 的 `wr`）或让 C++ 真正消费阈值 | 若删除：全仓无 `wr_threshold` 传递/无 `tb["wr"]` 写入；若接线：单元测试证明同一盘面不同阈值导致 `use_win_rate` 翻转 |
| 3 | `utils/native.py:155-157` + `main.py:894-898` | 失败返回显式 `reason` dict，禁止回退 best_cell 的转移（或显式标注来源格） | 模拟 `transitions_for` 抛错：浮层显示「该格转移不可用」而非显示其他格数据 |
| 4 | `logger.py:21,37-48,51-55` + `setting.py:37-57` | 日志改绝对路径 + 失败降级链 + 启动可见告警；`normalize_cwd` 失败 ERROR | 从任意 cwd/不可写目录启动，日志仍落到 `PROJECT_ROOT/logs/` 或 UI 明确提示「日志不可用」 |
| 5 | `utils/vision.py:234-238` + `utils/solver.py:300-301,328-329` | `locate_exit` 复用阈值并返回 `ok` 标志；调用方按 ok 决定点击/重试 | 在无 exit 弹窗的屏幕上调用：不产生点击，日志 warning |
| 6 | `utils/vision.py:427-470` + `utils/decide_trace.py:74-135` + `main.py:1918-1997` | 把 `unknown/total/ratio` 透传到 scan_meta → trace → UI 状态条 | trace 每行含 `unknown/ratio`；状态条在 ratio>0 时显示「n 格不可信」 |
| 7 | `cpp/ab_native.cpp:585-643,645-696,780-790` | 删除不可达旧臂或让 production 走 `switch`；`run_ab` 回显实际生效 arm | `run_ab(arm="safety")` 与 `arm="production"` 在已知差异局面输出不同；非法 arm 抛错 |
| 8 | `cpp/ab_native.cpp:346,816` | 删除 `fails` 或真实赋值并写清语义 | grep 该字段有写入点；Python 侧回归断言其语义 |
| 9 | `utils/analyze_decide_trace.py:97-104` + `utils/decide_trace.py:97-107` | trace 增配置/构建指纹；分析器按指纹分组、输出记录时间区间；Q2 文案去因果断言 | 用旧 trace 跑出「非当前配置」提示；同批分析含时间区间与构建标识 |
| 10 | `utils/analyze_decide_trace.py:64-73,148` | 统计 skipped_lines/missing_field_rate；缺 `n9` 归 None 组 | 损坏文件分析输出 skipped 行数；样本不足时拒绝下结论 |
| 11 | `utils/decide_trace.py:131-135` | 首次写失败升级 warning + 进程内 `trace_error` 计数 | 人为把 `_PATH` 设为不可写路径：warning 可见 |
| 12 | `main.py:2575-2578,2590-2598` | `cv2.imwrite` 返回值检查 + 临时文件原子替换 + 失败弹错误 | 只读目录下保存：弹失败提示，原模板不变 |
| 13 | `utils/mm0.py:42-49` + `utils/solver.py:246-247,515-516` | `ClientToScreen` 失败返回 None；`play/help` 入口校验 `hwnd>0` 并告警退出 | 关闭游戏窗口后点自动：弹「未找到窗口」且不点击 |
| 14 | `utils/solver.py:283-286` + `utils/probability.py:787,962,991` | `count` 拆三计数；告警文案带类型+首因摘要 | 三类失败分别注入：告警文案可区分 |
| 15 | `ui/preview_popup.py:129` | 边界条件改 `<= ncols-2 / <= nrows-2`；补边界用例 | 悬停 (w,h)/(w,1)/(1,h)：最外圈显示 `_MINI_OUTSIDE` 样式（None 分支） |
| 16 | `cpp/mscore.cpp:3213-3228,3541-3552,2406-2414,3994-4102` | 二选一：随机残差显式化（seed 真正驱动 + 返回 `tie_break` 字段 + trace 落盘）或改确定性 tie-break 并删 seed 形参/改注释；`rs5`/`rs8` 类「统计不可用」分支改显式 reason | 同盘面重复调用 decide 输出稳定（或每次带 `tie_break="random_residual"` 标记）；`seed` 语义与实现一致 |
| 17 | `utils/decide_trace.py:111-116` + `utils/probability.py:38-58` + `utils/analyze_decide_trace.py:108-131` | 白名单加 `cost_log2`；`_decide_diag` 加该字段；分析器 Q1 改用 `cost_log2`（limitation 标 deprecated）；trace 加 schema 版本号 | 新 trace 每行含 `cost_log2`；分析器输出以 cost_log2 为主口径且旧记录标 None；本轮门槛改动可从真实运行复核 |
| 18 | `cpp/mscore.cpp:2799-2801,2927-2933,3557-3559` + `make_response` | 增 `fallback_from` 诊断字段（materialize/nodecap/engine/pseq_transitions）；`make_response` 失败返回增 `ok=false`/`path=-1`（禁止与 pbs 正常路径同码）；transitions 渲染移出决策 try | 构造物料化失败/引擎异常：决策仍给出且 `fallback_from` 非空；渲染失败只丢 transitions 不丢决策 |
| 19 | `cpp/mscore.cpp:3111-3114,3134,3454-3455,3474` | `counts/cnts` 改 `std::vector` 或加上界分支；`flat!=ordered_clicks.size()` 跳过时置 `pseq_skipped` 诊断字段 | >64 并列 / >256 frontier 构造用例不越界（ASAN 干净）且诊断字段可见 |
| 20 | `cpp/mscore.cpp:2591-2598,2634-2641` | 非法 env 值打一次 stderr 警告；`py_decide` 返回 `config` 摘要（cost_cap/dstar/help_mode）供 trace 落盘 | 设非法 `MSW_COST_CAP_LOG2=abc`：stderr 可见告警 + trace 记录实际生效 config |
| 21 | `cpp/mscore.cpp:2428,3004-3012` + pybind 导出区 | 返回 dict 增 `pbs_diag` 并落 trace，或删除该变量与注释 | grep `pbs_diag` 有导出/消费，或两处引用全删 |
| 22 | `cpp/mscore.cpp:4563-4570,4582,4694-4701` + `cpp/ab_native.cpp:486-498` | 返回值区分 core_exception/invalid_coords（或让异常抛出）；`AbResult` 增 `decide_errors` 并输出 | AB 跑批输出中「核心异常」与「cap_hits」分开计数 |
（报告完 · 本席只读，未修改任何被评审对象；落盘仅：本文件 + `agent-out/silent-failure-hunter-20261109-003809.md`）