# 视觉识别契约修复报告（P0 优先）

- 生成时刻：20261106-222238；执行者：executor（session-f4f48dac-f989-4146-affe-bf4494820a30）
- 范围（硬边界）：只改 `utils/vision.py`（+259/-31 行）、`tests/test_scan.py`（+328/-2 行）。
  **未修改** `main.py`、`ui/`、HTML 原型、`utils/solver.py`、`utils/probability.py`、
  `utils/deduction.py`、`bench/`（工作区里这些文件的 M 标记均为他人既有改动，
  mtime 18:16/19:46，早于本席开工的 22:38）。
- 上游依据：`review/全面审查-UI与调用流程-20261106.md:299-329`（4.1 视觉识别：最高风险）、
  `review/视觉域.md:132-175`（P0-3 静默失败）、`:243-265`（P1-2 补丁尺寸）、`:379-385`（P2 测试缺口）。

## 1. 问题清单（改前事实，逐条证据）

| 编号 | 问题 | 证据（改前） |
|---|---|---|
| P0-1 | `load_img` 对模板零校验：`cv.imread` 失败返回 `None` 也照收，通道/dtype/尺寸全不检查 | 旧 `utils/vision.py:61-64`（`self.images = [[cv.imread(...) ...]]` 直接赋值，无任何断言） |
| P0-2 | `compare_img` 无「不认识」分支：`argmin` 后必归入 0~10 之一；模板全 `None` 时仍返回 0 | 旧 `utils/vision.py:126-139`；`review/视觉域.md:142` 实测「25 模板全 None → 480 格全部输出 0，抛异常 0 次」 |
| P0-3 | `compare_cells_batch` 在无可用模板时回退到逐格 `compare_img`，把上一条静默错判放大成整盘 | 旧 `utils/vision.py:177-180`（`if not act: return [...compare_img...]`） |
| P1-1 | `_active_templates` 用标称 `screenshot_w/h` 过滤，与 `_slice_cell` 实得尺寸（各 `//2`）不一致 | 旧 `utils/vision.py:243`（`sh, sw = self.screenshot_h, self.screenshot_w`）；对照 `compare_cells_batch` 自己用的是 `2*(//2)` |
| P1-2 | `compare_img` 的 `result` 数组硬编码 `(11, 3)` 且初值 100 与真实分数（<0.8）混在同一数组，无法区分「未匹配」与「真得分」 | 旧 `utils/vision.py:126`；`review/视觉域.md:404` |
| P2-1 | 测试 dummy 用 `(18,13)`，掩盖了真实补丁 `(18,12)` 的尺寸偏差 | 旧 `tests/test_scan.py:105`；`review/视觉域.md:379` |

## 2. 修复设计

**契约层（新增公开符号，全部为增量，不改既有签名）**

- `UNKNOWN = -1`（`utils/vision.py:41`）：负值刻意避开棋盘既有编码 0..11
  （9=未开 / 10=标记雷 / 11=标记安全）。
- `LOW_CONFIDENCE_THRESHOLD = 0.05`（`:49`）：**可配置常量**，置 `None` 即完全关闭护栏
  回到旧语义。标定依据 `review/视觉域.md:151` 的实测分离度（真数字最优分 ≤0.0026
  vs 纯噪声最小分 ≥0.2519）；本席独立复核同量级（见第 4 节 A/B）。
- `UNKNOWN_ABORT_RATIO = 0.5`（`:53`）：整轮不可识别比例阈值。
- `STRICT_LOW_CONFIDENCE = False`（`:60`）：**默认不抛异常**，理由见下。
- `TemplateValidationError` / `LowConfidenceScanError`（`:63`、`:67`）。
- `_is_low_confidence()`（`:71`）/ `_normalized_score()`（`:76`）。

**关键决策一：置信判据用 `np.min(响应图)`，不用 `np.sum(响应图)`。**
排序语义保持 `np.sum` 逐位不变（与改动前生产行为一致）；但阈值必须用最优点分数：
① 域报告阈值本来就按 min 标定；② 模板比补丁小时响应图有多偏移点，`sum` 随偏移数
线性放大，会把小模板系统性误判成低置信度（本席实测该错误实现让既有
`bench/verify_vision_accuracy.py` 从 90.9% 跌到 36.4%，改为 min 后恢复 90.9%）。

**关键决策二：不把 `UNKNOWN` 写进 `cell_value`，且默认不抛异常。**
- 棋盘编码 0..11 都有语义，写 -1 会污染 `deduction.py:39`（`0 < v < 8`）、
  `probability.py:514`（`== 10`）等全部下游判断 → 不可识别格保持「未开(9)」。
- 抛异常路径经实测会让**整个进程 abort**：`complete_scan` 的调用链
  （`solver.py:356/443` ← `QThread.run`）没有通用异常捕获，Qt 线程内未处理异常
  实测 exit `-1073740791`。所以默认 `STRICT_LOW_CONFIDENCE=False`：改为
  `logger.error` + `warning_signal.emit()` 让用户在 UI 上**看得见**
  （`main.py:220/237` 已把 warning_signal 接到弹窗），并把整轮识别整体作废
  （不写半真半假的结果）。自动化/测试环境可置 True 升级为硬失败。

**关键决策三：区分「模板缺文件/坏通道」与「另一套标定」。**
本仓 14 个 `*_1.bmp` 是 28×20 的 cw=36 标定集（`review/视觉域.md:176-210` 已证其合法）。
若对尺寸不匹配一律 raise，`Solver.__init__ → load_img`（`solver.py:115`）会直接崩，
把「可见失败」做成「启动即崩」。故：缺失/通道/dtype/零可用模板 → 致命 raise（可诊断）；
尺寸不匹配 → `logger.info` 记文件名+尺寸，由 `_active_templates` 过滤，保留文件不删。

## 3. 变更明细

| 位置 | 变更 |
|---|---|
| `utils/vision.py:41-76` | 新增 `UNKNOWN`/阈值/比例/严格模式常量、两个异常类、两个判定函数 |
| `utils/vision.py:113-124` | `_template_mtime` 的 `except OSError` 加 `logger.debug`（不再无声吞掉，缺文件时仍返回 -1.0 让缓存失效） |
| `utils/vision.py:122-174` | 新增 `_validate_templates()`：缺失/通道/dtype 致命 + 不可用尺寸仅诊断 + 零可用模板致命；错误信息带文件名、实测 shape、当前补丁尺寸与**恢复指引** |
| `utils/vision.py:176-195` | `load_img()` 先加载到局部变量 `images` → 校验 → 通过后才写 `self.images`（坏模板不进缓存） |
| `utils/vision.py:250-293` | `compare_img()`：去掉硬编码 `(11,3)` 数组与 100 哨兵；跨模板按 `np.sum` 排序（语义不变）、按 `np.min` 判置信；无可用模板返回 `UNKNOWN`；补上「模板大于补丁则跳过」过滤（与 `_active_templates` 对齐） |
| `utils/vision.py:300-317` | `_active_templates()`：尺寸判据改用 `2*(screenshot_h//2) × 2*(screenshot_w//2)`，与 `_slice_cell` 实得尺寸一致 |
| `utils/vision.py:319-410` | `compare_cells_batch()`：无可用模板改抛 `TemplateValidationError`（不再回退静默归类）；逐格跟踪 `best_min` 做置信判定，命中返回 `UNKNOWN` |
| `utils/vision.py:417-458` | 新增 `_apply_recognition()`：UNKNOWN 不落盘；少量→warning；比例超阈值→error + warning_signal + 整轮作废（严格模式抛异常） |
| `utils/vision.py:460-527` | `complete_scan`/`rescan_after_click` 改走 `_apply_recognition`；`except LowConfidenceScanError: raise` 置于宽 `except` 之前（否则会被回退路径吞掉） |
| `tests/test_scan.py:105-127` | 既有缓存测试 dummy 尺寸 `(18,13)` → `(18,12)`（修真补丁尺寸） |
| `tests/test_scan.py:130-240` | 新增 `TestLoadImgValidation` 5 例：缺文件/4 通道/float32/另一标定族仅诊断/全尺寸不符 |
| `tests/test_scan.py:243-341` | 新增 `TestRecognitionContract` 7 例：真实模板自匹配不变、噪声/纯色→UNKNOWN、无模板→UNKNOWN、batch≡ref（5 格真模板）、**混合板**（3 真 + 2 噪声，防「全 UNKNOWN 假绿」）、批量低置信度、无模板抛错 |
| `tests/test_scan.py:344-444` | 新增 `TestScanUnknownContract` 5 例：全坏可见且不污染棋盘、warning_signal 真的 emit、严格模式抛错、少量未知保持未开、批量失败回退逐格 |

## 4. 验证（新鲜输出）

解释器：`D:\anaconda3\envs\py310\python.exe`（3.10.16；**项目实际依赖 pywin32/PyQt5 只在此 env 可用**，
系统 `python`（3.12 anaconda）`import win32gui` 直接 DLL load failed，
故全部命令用该 env）。

| 检查 | 命令 | 结果 |
|---|---|---|
| 定向测试 | `& py -m unittest tests.test_scan` | **Ran 30 tests … OK**，exit 0 |
| 全量回归 | `& py -m unittest discover -s tests -t .` | **Ran 122 tests，1 FAILED**，exit 1 |
| 编译 | `& py -m py_compile utils/vision.py tests/test_scan.py` | exit 0 |
| Lint | `& py -m ruff check utils/vision.py tests/test_scan.py` | **All checks passed!** |
| 端到端识别 | 8×6 真实模板合成盘 → `complete_scan(no_10=True)` | **48 格 0 错格**，棋盘无负值 |
| batch/ref 等价 | `& py bench/verify_vision_batch_equiv.py` | solid/mixed/noise × 3 seed 全 **0/480 不一致**；18.8x |
| 识别准确率 | `& py bench/verify_vision_accuracy.py` | **10/11 = 90.9%**（与 HEAD 逐项相同） |

**唯一失败项 = 既有失败，非本席引入**：
`tests.test_ui_heatmap.TestHeatmapUI.test_stats_line_content`（期望含「扫雷的」文案，
实际 `main.py` 文案已改为「扫雷的格」，属他席改动遗留）。
证据：HEAD 隔离 worktree（`git worktree add` 到临时目录、补 `image/`+`cfg.json`）跑
同一套测试，该用例同样 FAIL。

**关于 `bench/verify_adversarial_mutation.py` 现在报「变异未检出」的说明（诚实记录）**：
该 bench 的参考端调用 `compare_img`，输入是纯噪声板 → 新版参考端返回 `UNKNOWN(-1)`，
于是与内联复刻（仍返回 0..10）全部 480 格「不一致」，报红。
本席已双向核验这不是生产缺陷：① `LOW_CONFIDENCE_THRESHOLD=None`（关闭护栏）下
该 bench 恢复 **「✅ 变异检出」**，读数与 HEAD 完全一致（正确补丁 0 分歧、错误补丁 1 分歧）；
② 混合板上 batch 与 ref 逐格一致（`test_batch_and_ref_agree_on_mixed_board` 断言真值
`[0, 3, 7, -1, -1]`）。该 bench 属范围外文件（任务书硬边界），未修改；
建议后续让它显式关闭护栏或用真模板板驱动。

**语义不变的 A/B 证据**（HEAD 版 `compare_img` 从 `git show HEAD:utils/vision.py` 动态载入，
与新版逐用例对比，真实生产补丁 18×12）：

| 分组 | 用例数 | 旧返回数字 | 新返回数字 | 新 UNKNOWN |
|---|---|---|---|---|
| 真实模板-无扰动 | 11 | 11 | 11 | 0 |
| 真实模板+噪声±30 | 11 | 11 | 11 | 0 |
| 真实模板+亮度±20 | 22 | 22 | 22 | 0 |
| 纯噪声 | 100 | 100 | 0 | **100** |
| 纯色 | 5 | 5 | 1 | **4** |

即：**真实模板路径 0 变化，只有噪声/纯色这类本该拒绝的输入转为 UNKNOWN**。

## 5. 未覆盖项与产品裁决点

| 项 | 状态 | 说明 / 复跑命令 |
|---|---|---|
| 真机屏幕标定阈值 | **未覆盖** | 0.05 来自合成数据（`review/视觉域.md:483` 同 limitation）。真实抗锯齿/主题下需重标。复跑：`& py bench/verify_vision_accuracy.py` |
| 真实截图标注集 | **未覆盖** | 仓库无「真实棋盘截图 + `cell_value` 标注」黄金集（`review/视觉域.md:262` 已列最大验收缺口）。本席未造伪标注集充数。 |
| GUI 端到端（弹窗文案真实可见） | **未覆盖** | 只验证 `warning_signal.emit` 被调用（`test_all_unknown_emits_warning_signal`），未起真实窗口截图取证。属 acceptance 层，建议 verifier 补。 |
| `UNKNOWN_ABORT_RATIO=0.5` 取值 | **产品裁决** | 本席按「过半不可信即整盘不可信」取默认值，无实测支撑。若真机偶发遮挡导致误判过多，应上调。 |
| `STRICT_LOW_CONFIDENCE` 默认值 | **产品裁决** | 现为 False（不崩进程）。若产品要求「宁停不误点」，置 True 并需同时在 `solver.play/help` 补异常捕获，否则进程 abort。 |
| `bench/verify_adversarial_mutation.py` 适配 | **范围外未改** | 该 bench 现在与 `UNKNOWN` 契约不兼容（详见第 4 节），需另行授权修改。 |
| `probability.py` 中 `complete_scan` 失败重试 | **未覆盖** | `probability.py:551` 在识别不到任何未开格时会重建棋盘再扫；若持续 UNKNOWN 会靠 `self.count += 1` 触发上层的「请检查设置」报错。该链路未做端到端实测（属其他算法文件，不可改）。 |

## 6. P0 / P1 / P2 分级汇总

- **P0（已修）**：模板加载零校验（`:122`）；识别无 UNKNOWN 分支（`:250`、`:319`）；
  batch 无模板静默回退放大错判（`:319`）。
- **P1（已修）**：`_active_templates` 标称尺寸与实得尺寸不一致（`:300`）；
  `compare_img` 的 `(11,3)`+100 哨兵与真实分数混用（`:250`）；
  低置信度对用户不可见（`:417` → `logger.error` + `warning_signal`）。
- **P2（已修）**：既有测试 dummy 尺寸错误掩盖缺陷（`tests/test_scan.py:114`）；
  缺 `_template_mtime` 的缺文件日志（`:118`）；batch/ref 等价性无回归守护（新增 3 例）。
- **P2（未修，范围外）**：`bench/verify_adversarial_mutation.py` 与新契约不兼容。

## 7. 结论

`utils/vision.py` 的「坏模板静默跑 + 无法识别时强行归类」两条静默失败链已封住：
坏模板 → `TemplateValidationError`（带文件名/实测 shape/恢复指引）；
无法可信识别 → `UNKNOWN`（不落盘、日志 + UI 告警可见、超比例整轮作废）。
真实模板识别语义经 A/B 逐用例验证 **0 变化**（accuracy 保持 90.9%，batch/ref 480 格 0 分歧）。
范围严守两个文件；阈值与严格模式属产品裁决点，已在第 5 节显式列出。
