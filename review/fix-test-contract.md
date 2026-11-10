# 测试契约修复报告（P0 剩余项）

- 生成时刻：20261106-225844；执行者：executor（session-941c72cf-d727-4590-b91a-52b826580081）
- 范围（硬边界）：只改 `tests/test_ui_heatmap.py`、`tests/test_cpp_decide_wiring.py`、`review/fix-test-contract.md`。
  **未修改** `main.py`、`utils/`、`ui/`、HTML 原型、`bench/`、`sim/`（工作区里这些文件的 M 标记均为他人既有改动：
  `main.py` mtime 18:16、`utils/probability.py` mtime 19:46，均早于本席开工的 22:58；本席收尾核对了
  `utils/probability.py`/`utils/native.py` 的 SHA256 与修复前一致，见第 5 节 L-3）。
- 上游依据：`review/全面审查-UI与调用流程-20261106.md:58-70`（1.3 失败测试的准确解释）、
  `:343-362`（4.3 C++ decide 连续循环与测试契约）、`:469-480`（实施路线第一批第 4/5 条）、
  `review/代码质量域.md:139-150`（F5 过时断言）、`review/UI交互域.md:281`。
- 解释器：`D:\anaconda3\envs\py310\python.exe`（3.10.16；pywin32/PyQt5/OpenCV 只在该 env 可用；
  系统 python 为 3.11 anaconda，`win32gui` 不可导入，故全部命令用该 env）。

## 1. 问题清单（改前事实，逐条证据）

| 编号 | 问题 | 证据（改前） |
|---|---|---|
| T-1 | 统计栏文案断言停在旧文案，代码已是新文案 | `tests/test_ui_heatmap.py:87` 断言 `"当前点击方格不是雷的概率"`；实现 `main.py:776` 为 `"当前格非雷的概率"` |
| T-2 | 文案变更缺可追溯出处，后续易被再次「反向改代码」 | 提交 `cfb27da`（2026-09-15）提交信息原文含「状态栏文案改为『当前格非雷的概率』」；`git show HEAD:main.py` 显示的已是新文案（该文案**已入库**，不在未提交改动里） |
| T-3 | decide 连续循环测试的 stub 不推进盘面 | 旧 `tests/test_cpp_decide_wiring.py:59-78` 的 `_run_core` 只 stub `complete_scan`，未 stub `rescan_after_click`、未 stub `_locate` → 重扫走真实视觉路径 |
| T-4 | 循环的终止条件零覆盖：旧测试对「循环真的转起来」无鉴别力 | 见第 2 节 A/B 受控对照：`clicks` 个数在「循环只跑一轮」与「循环正确继续」两种情况下**都是 3** |
| T-5 | 「热力图最终契约」无人锁 | 旧 `test_heatmap_contract_shape` 只断言键集合与类型；循环内每轮覆盖 `_last_heatmap`（`utils/probability.py:648-653`），旧测试无法区分首轮/末轮，也无法证明发生过第二轮 |
| T-6 | 旧测试无法区分「正常终止」与「异常跌回 Python 链」 | `_number5_1_core` 的 C++ 分支有宽 `except`（`utils/probability.py:705-712`），任何异常都会静默跌回 Python 链，此时旧断言仍可能成立 |

## 2. 改前实测与根因取证（新鲜输出）

### 2.1 改前定向测试（未修复状态）

```
$ & D:\anaconda3\envs\py310\python.exe -m unittest discover -s tests -t .
Ran 122 tests in 5.384s
FAILED (failures=1)
FAIL: test_stats_line_content (tests.test_ui_heatmap.TestHeatmapUI)
  File "...\tests\test_ui_heatmap.py", line 87, in test_stats_line_content
    self.assertIn("当前点击方格不是雷的概率: 87.50%", t)
AssertionError: '当前点击方格不是雷的概率: 87.50%' not found in
  '当前格非雷的概率: 87.50% ｜ 已完局: 7 ｜ 赢局: 3 ｜ 胜率: 42.86%'
```

注意：改前全量是 **122 tests / 1 FAIL**（不是审查报告的 104/3 FAIL）——
审查报告的两个 C++ decide 失败在当前工作树上已不复发（`tests/test_cpp_decide_wiring.py`
此时已是**未跟踪文件**且 7 例全绿）。也就是说 T-4/T-5/T-6 是**测试鉴别力缺陷**而非
运行期红：它们不报红，但也没测到循环。

### 2.2 受控对照：旧测试为什么对循环零鉴别力

在隔离副本中用同一份生产代码、同一响应，唯一变量 = `rescan_after_click` 是否让 `num9` 减少
（脚本：`_mut_tmp/_loop_probe.py`，报告：`_mut_tmp/_loop_probe_report.txt`）：

| 组 | rescan 语义 | decide 调用次数 | clicks 个数 | num9 终值 |
|---|---|---|---|---|
| A | 不改变盘面（现状 UNKNOWN 语义 / 旧 stub） | **1** | 3 | 25（初始 25） |
| B | 把点过的格变成已开（盘面确有变化） | **2** | 3 | 22 |

**关键读数**：`clicks` 在 A/B 都是 3 ⇒ 点击计数**不是**区分「循环只跑一轮」与
「循环真的继续」的可观测量。A 组之所以只跑一轮，是撞上无进展守卫
（`utils/probability.py:681-686`）首轮即断；B 组第 2 轮 decide 返回同一批已开坐标，
生产「已开/已标记跳过」（`:664-665`）把点击全跳过，再因无进展退出。

⇒ 旧测试的绿是**构造性的**：生产循环哪怕退化成「一轮即返回」它也照样绿。
新测试改用三层可观测量（decide 调用次数、num9 终值、末轮热力图契约），
并在 `_run_core` 里断言 `_DECIDE_FAIL_SEQ == 0` 堵住第 1 节 T-6。

## 3. 变更明细

| 位置 | 变更 |
|---|---|
| `tests/test_ui_heatmap.py:81-105` | `test_stats_line_content`：断言文案 `"当前点击方格不是雷的概率"` → `"当前格非雷的概率"`；docstring 注明提交号 `cfb27da` 与不得反向改 main.py 的理由；**新增两条更严的断言**：`｜` 分隔字段数 ≥4（防改文案时结构被改坏）、旧文案必须已消失（`assertNotIn`）。原判据语义（87.50% 数值 + 已完局/赢局/胜率三段）全部保留 |
| `tests/test_cpp_decide_wiring.py`（整体重写，+180/-68 行） | 引入 `_num9()`（与生产 `np.sum(cell_value==9)` 同口径）、`_invalid_response()`（决策放弃终止路径）、`_Capture` 同时记录像素坐标与**反算后的格子**（反算公式 `round((px-bx)/cw)`，**不减 1**——1-based 反算减 1 是 bench/sim 踩过的坑）、`_rescan_opening()`（把本轮点过的格变成已开的最小 flood-fill 模拟）|
| `tests/test_cpp_decide_wiring.py:108-143` | `_run_core` 重写：`responses` 用 `side_effect` 序列驱动多轮决策；stub `rescan_after_click`；stub `_locate`（不触真实屏幕/真实抓屏）；**断言 `_DECIDE_FAIL_SEQ==0`**——C++ 分支一旦抛异常被宽 except 吞掉，测试立刻红，不许靠 Python 回退链偶然变绿 |
| `tests/test_cpp_decide_wiring.py:148-164` | `test_certain_safe_batch_clicks_all_tied_cells`（新）：批量锁 3 个 tied 坐标全部为 `bx + x*cw`；断言盘面**真的推进**（num9 25→22）、rescan 被调用、decide 被调 2 次 |
| `tests/test_cpp_decide_wiring.py:167-183` | `test_single_click_is_one_based_not_zero_based`（新）：1-based 单点最小判别用例，显式 `assertNotIn((100,200))` 排除 off-by-one 错版坐标 |
| `tests/test_cpp_decide_wiring.py:186-206` | `test_help_mode_single_decision_no_click_and_dedup`：帮助模式**单次** decide（`decide.call_count==1`）、零点击、零重扫；重复决策不追加 `pos_dict_list`；并断言盘子未变（num9 仍 25） |
| `tests/test_cpp_decide_wiring.py:209-241` | `test_heatmap_contract_is_last_round_of_loop`：两轮真实决策（首轮批量 3 格、次轮单点），断言 `_last_heatmap` 最终值 = **末轮**快照（键集恰为 4 项、best/best_conf/prob/total 全来自 resp2）、`decide.call_count==3`、clicks=4、新增格子 4 个；并逐次校验每轮 decide 都带自动模式基线参数（wr_threshold=8.0、node_cap=2e6、is_play=True） |
| `tests/test_cpp_decide_wiring.py:244-258` | `test_loop_breaks_when_rescan_shows_no_progress`（新）：无进展守卫正向锁——盘面不变时只点一轮、decide 只调 1 次、不反复点击同一批 |
| `tests/test_cpp_decide_wiring.py:261-336` | 既有 4 例（预算乘数 / 帮助放宽参数 / 旧 pyd 回退 / 新 pyd is_play 透传）原样保留，未放宽任何断言 |

**未做的事**：没有为了让测试变绿而改任何生产代码；没有删用例；没有把 `assertEqual` 换成
`assertIn`/`assertGreater` 之类的弱断言（唯一新增的 `assertGreaterEqual(len(split),4)` 是
**在原有字段断言之外**的加固，不是替代）。

## 4. 验证（新鲜输出）

| 检查 | 命令 | 结果 |
|---|---|---|
| 编译 | `& py -m py_compile tests/test_ui_heatmap.py tests/test_cpp_decide_wiring.py` | exit 0 |
| Lint（C++ 接线测试） | `& py -m ruff check tests/test_cpp_decide_wiring.py` | **All checks passed!** exit 0 |
| Lint（UI 测试） | `& py -m ruff check tests/test_ui_heatmap.py` | 1× E402（`import main` 在第 17 行，为 `QApplication` 先建后导入所致）。**改前既有**：`git show HEAD:tests/test_ui_heatmap.py` 落盘后跑同一 ruff，报同一 E402（`:17`），非本席引入、不在本次范围 |
| 定向测试 | `& py -m unittest tests.test_ui_heatmap tests.test_cpp_decide_wiring -v` | **Ran 16 tests … OK**，exit 0（7 个 UI + 9 个接线） |
| 全量回归 | `& py -m unittest discover -s tests -t . -v` | **Ran 124 tests … OK**，exit 0，106 个 `... ok`、0 skipped |
| 改前全量对照 | 同上（改前） | Ran 122 tests，1 FAILED（仅 T-1）；用例数 122→124 因新增 2 例（`test_single_click_is_one_based_not_zero_based`、`test_loop_breaks_when_rescan_shows_no_progress`），其余为旧例改名拆分 |

**注入必红负向自测（14 变异体，隔离副本执行，生产文件零改动）**

脚本 `_mut_tmp/_mutate_check.py`，报告 `_mut_tmp/_mut_report.md`；每个变异体只改一处生产语义，
断言目标用例**必须变红**，最后还原并验证基线变绿：

| 变异体 | 变异内容 | 目标用例 | 判定 |
|---|---|---|---|
| M1 | 点击坐标改 `(x-1)*cw`（off-by-one 原 bug） | `test_single_click_is_one_based_not_zero_based` | 变红 ✔ |
| M2 | 批量只点 `tied[0]` | `test_certain_safe_batch_clicks_all_tied_cells` | 变红 ✔ |
| M3 | 热力图契约退化成扁平短键 | `test_heatmap_contract_is_last_round_of_loop` | 变红 ✔ |
| M4 | 热力图只保留首轮（第二轮不覆盖） | 同上 | 变红 ✔ |
| M5 | 帮助模式漏 `appended_pos` 去重 | `test_help_mode_single_decision_no_click_and_dedup` | 变红 ✔ |
| M6 | 帮助模式被误送进自动点击分支 | 同上 | 变红 ✔ |
| M7 | 删除无进展守卫 | `test_loop_breaks_when_rescan_shows_no_progress` | 变红 ✔ |
| M8 | 连续循环退化为「一轮即返回」 | `test_heatmap_contract_is_last_round_of_loop` | 变红 ✔ |
| M9 | 循环内第二轮 decide 参数漂移（wr 6.0/旧 cap） | 同上 | 变红 ✔ |
| M10 | `decision_budget` 丢乘数（恒 30s） | `test_budget_multiplier_roundtrip` | 变红 ✔ |
| M11 | 点击后不 rescan | `test_certain_safe_batch_clicks_all_tied_cells` | 变红 ✔ |
| M12 | `is_play` 未透传（恒 True） | `test_native_decide_new_pyd_passes_is_play` | 变红 ✔ |
| M13 | 旧 pyd 回退静默（不打 WARNING） | `test_native_decide_old_pyd_fallback` | 变红 ✔ |
| M14 | 帮助模式不放宽 wr_threshold（少 +6） | `test_help_mode_passes_relaxed_params` | 变红 ✔ |
| BASELINE | 还原生产代码后重跑 | `test_heatmap_contract_is_last_round_of_loop` | 变绿 ✔ |

**读数：变异体检出 14/14；基线恢复绿 = True。**

**可复核的原始证据落盘在 `review/fix-test-contract-evidence/`**（都是可直接重跑的脚本/读数）：

| 文件 | 内容 |
|---|---|
| `mutate_check.py` | 14 变异体注入必红自测脚本（在仓库的一份隔离副本里跑：`python mutate_check.py`） |
| `mut_report.md` | 上述脚本的原始输出表（14/14 变红 OK + 基线变绿） |
| `loop_probe.py` / `loop_probe_report.txt` | 第 2.2 节 A/B 受控对照脚本与读数（证明 clicks 计数对循环零鉴别力） |
| `full_test_run_before_summary.txt` | 改前全量：`Ran 122 tests … FAILED (failures=1)` |
| `full_test_run_after_summary.txt` | 改后全量：`Ran 124 tests … OK` |
| `targeted_run_after_summary.txt` | 改后定向：`Ran 16 tests … OK` |

（变异测试全程在 `_mut_tmp/` 隔离副本内进行、脚本自动还原，生产文件未被写过；
收尾已核对 `utils/probability.py`/`utils/native.py` 的 SHA256 与本席开工时一致——见 L-3。）

## 5. 未覆盖项、口径边界与产品裁决点

| 编号 | 项 | 状态 | 说明 / 复跑命令 |
|---|---|---|---|
| L-1 | 真实扫雷窗口下的连续循环 | **未覆盖** | 全部测试用 stub 决策/点击/重扫，不触真实屏幕。真实链路（点击→flood-fill→真识别→再决策）未在本轮验证，属 acceptance 层，建议 verifier 用真实窗口补。复跑：`& py -m unittest tests.test_cpp_decide_wiring -v` |
| L-2 | 踩雷终止路径（`_dead_hit`） | **未覆盖** | `utils/probability.py:671-680` 的踩雷检测依赖 `_locate("./image/lose.bmp")` 为真；本轮 `_run_core` 把 `_locate` 恒 stub 成 `(False,0,0)`，该分支未被执行（新增覆盖需另造 lose 弹窗 stub，属范围外扩展） |
| L-3 | 生产文件零改动核验 | **已核验** | 变异测试在隔离副本 `_mut_tmp/`（工作区内 scratch，收尾已删）跑；`utils/probability.py` SHA256 `FEC084CC…DEE1D1A78` 与修复前一致，`utils/native.py` 亦一致 |
| L-4 | UI 文案的产品语义 | **产品裁决** | 本席按提交 `cfb27da` + `review/代码质量域.md:145-149` 的结论，认定代码是新真相、测试是旧断言，故改测试。若产品口径要保留「点击方格」措辞，须由需求方裁决后**同时**改 `main.py` 与测试——本席未改 `main.py`（任务书硬边界） |
| L-5 | `confidence` 在启发式分支的语义 | **范围外未改** | `review/全面审查…:364-391` 指出 UI 把 `confidence` 统一显示为「非雷概率」可能在启发式胜率分支失真；这属生产语义改造，需 `value_kind` 字段，超出本任务测试契约范围 |
| L-6 | `tests/test_ui_heatmap.py` 的 E402 | **改前既有，未修** | 只在 `import main` 前建 `QApplication`（offscreen 需要），是刻意结构；修它要动文件头与导入顺序，与本次契约修复无关，未纳入 |
| L-7 | 失败弹窗/512 次上限终止 | **部分覆盖** | 512 次上限（`_loop_max`）与无效坐标终止路径已由 `_invalid_response()` 覆盖「无效坐标」一路；512 次打满未造用例（需 512 层响应序列，成本高且属防御性分支），记为未覆盖 |

## 6. P0 / P1 / P2 分级汇总

- **P0（已修）**：T-1 统计栏文案断言与实现对齐（`tests/test_ui_heatmap.py:81-105`）；
  T-3/T-4 连续循环测试的 stub 不推进盘面、对循环零鉴别力——改为「响应序列 + 真实推进盘面
  + decide 调用次数 + num9 终值」四重锁定（`tests/test_cpp_decide_wiring.py:148-258`）。
- **P1（已修）**：T-5 热力图**末轮**契约被锁（`:209-241`）；T-6 「绿来自异常跌回 Python 链」
  被 `_DECIDE_FAIL_SEQ==0` 断言堵住（`:138-142`）；帮助模式「单次决策 + 不点击 + 去重」
  由同一用例同时锁死（`:186-206`）。
- **P2（已修）**：新增 14 变异体注入必红自测（第 4 节），把「测试是否假绿」从人工判断
  变成可复跑证据。
- **P2（未修，范围外）**：`tests/test_ui_heatmap.py` 的 E402（L-6）；踩雷终止路径（L-2）；
  512 次上限（L-7）。

## 7. 结论

两条测试契约漂移已在**只改测试**的边界内收敛：
① UI 统计栏文案与 `main.py:776` 对齐，并附提交 `cfb27da` 的出处与「不得反向改生产」的理由，
判据语义一条未放宽、另加两条加固断言；
② C++ decide 连续循环测试重写为真正会推进盘面的状态机测试，分别锁定
**批量点击坐标 / 单次帮助决策 / 热力图末轮契约 / 无进展终止**四件事，并加 `_DECIDE_FAIL_SEQ==0`
护栏防止「异常跌回 Python 链」伪造绿；14 个生产侧变异体全部被检出（含 3 个专门打向
「只跑一轮」「只保留首轮」「参数漂移」的假绿形态），基线还原后回绿。

全量回归从「122 跑 / 1 失败」变为 **「124 跑 / 0 失败」**（`& py -m unittest discover -s tests -t .`）。
剩余未覆盖项全部落表（第 5 节 L-1/L-2/L-7），其中真实窗口与踩雷路径属 acceptance 层，
建议移交 verifier；`confidence` 语义与 UI 文案措辞属产品裁决点（L-4/L-5），本席不代拍板。
