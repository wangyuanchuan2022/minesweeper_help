# 交接说明 v2：删除全部重复的 Py 计算，只保留 C++ decide 循环

> 交接时间：2026-11-06（v2，取代 v1「handoff-remove-py-decision-chain-20261106.md」
> 中仅覆盖 probability.py 715-1119 的范围）。
> 用户指令原文：「请你完整地读完 solver 中的所有内容啊，重新梳理一下自动和
> 提供帮助人类的交互逻辑和后端运行计算逻辑，把重复的 PY 计算都去掉」。
> 本会话只读，未改任何代码；另一会话正在修改整个项目，本文供其执行。

## 一、完整重读后的架构现状（交互 + 后端计算全景）

### 1.1 自动模式（GUI「自动」按钮，is_play=True，solver.py:243 play()）

```
play(limit)  每局：
  首点角落 (1,1) → while True 主循环：
    ① 弹窗管理：win/lose/ok 弹窗检测与新局重置（solver.py:287-412）
    ② complete_scan 抓屏 OCR（vision.py，全盘模板匹配）
    ③ mine_clear1（deduction.py:26）—— Py 单格规则推理：
       number0：数字格邻域
         - (cnt9+cnt10)==数字 → 标雷（cell_value=10）【Py 逻辑 A1】
         - cnt10==数字 → 点击周围全部 9（确定性安全）【Py 逻辑 A2，点击 :80】
    ④ mine_clear3_1（deduction.py:244）—— Py 1-1/1-2 子集推理：
       number_3_1：双数字格交集差集 → 标雷【A3】/点安全格【A4，:157,:210】
       → 每次点击后 rescan_after_click
    ⑤ 若 ③④ 无进展（sum3==sum2）→ number5_1（probability.py:469）
       ├─ C++ decide 分支（555-713）★ 保留：decide 循环
       │   decide → 点击（certain_safe 批量/其余单点）→ rescan → 再 decide
       └─ Python 决策链（715-1119）★ 死代码（decide 可用时）
    ⑥ _emit_heatmap → 界面渲染
```

### 1.2 帮助模式（GUI「帮助」按钮，is_play=False，solver.py:484 help()）

```
help()：
  complete_scan → mine_clear1/mine_clear3_1 ×2 轮 + mine_clear1（同 A1-A4，
    但 is_play=False：不点击，改为 cell_value=11 标记 + pos_dict_list 提示）
  → 若无推理产出 → number5_1 最多 5 轮（C++ decide 分支，帮助参数放宽：
     wr_threshold=14 / node_cap=500万 / DSTAR×3）
  → update_btn_list_signal 发射 pos_dict_list → GUI 弹窗/按钮渲染
```

### 1.3 后端计算归属现状

| 计算 | 现所在 | 与 C++ decide 重叠？ |
|---|---|---|
| 单格规则推理（确定安全/雷） | Py deduction.py A1/A2 | **重叠**：decide 的 pbs/OPTIMAL 层内含同等信息；certain_safe 分支即「批量点确定安全格」 |
| 1-1/1-2 子集推理 | Py deduction.py A3/A4 | **重叠**（同上，子集推理是 pbs 枚举的特例） |
| 概率计算（前沿格安全率） | Py 链 715-1119 的 part_solve/pbs；C++ decide 内部独立实现 | **重叠**（v1 交接已析） |
| 胜率前瞻 | Py 链 win_rate；C++ decide LAYER 2 | **重叠** |
| 抓屏 OCR/点击/弹窗/新局 | Py vision.py/solver.py | 不重叠（I/O 层，必须 Py） |
| 热力图/提示渲染 | Py main.py | 不重叠（UI 层） |

**结论**：逻辑计算有**三处重叠**——① deduction.py 的 Py 推理（A1-A4），
② probability.py 715-1119 的 Python 决策链，③ help() 复用的同一套 A1-A4。
C++ decide 的三层（OPTIMAL/win_rate/pbs）在数学上完整覆盖 ①② 的全部推理能力。

## 二、删除范围（用户拍板「把重复的 PY 计算都去掉」）

### D1. deduction.py 的 Py 推理调用点（自动模式路径）
- solver.py play() 内 424-429 行：`mine_clear1` / `mine_clear3_1` 调用整段删除
- 连带：sum2/sum3 比较（422-431）简化为「每轮直接进 number5_1」
- deduction.py 本体的 number0/number_3_1 **建议保留文件**（帮助模式仍用，
  见 D3；且 tests/test_solver_core.py 直接测试它们）

### D2. probability.py 715-1119 Python 决策链（v1 交接已详析，不再重复）
- 删 715-1119；`except`（705-712）改为「WARNING + 返回重扫盘面」
- README.md:55-67 回退承诺同步重写
- 保留独立方法：part_solve/part_solve_single/try_solve/open_num5x5（tests/bench
  在用）；win_rate/process_bigger_situation/_pbs* 方法体倾向保留（bench 引用）

### D3. help() 的重复计算（本轮新识别）
帮助模式当前 = Py 推理（A1-A4 提示版）+ 5 轮 number5_1。若「帮助也只信
C++ decide」：
- 删 help() 内 519-528 的 mine_clear 循环，直接 complete_scan → number5_1
- **代价**：帮助模式丢失 A1「标雷提示」与 A3「标雷提示」（decide 不输出
  标雷信息，只有点击坐标）——GUI 上红色雷标记会消失，只显示推荐点击格
- **取舍**：接受（推荐点击格已足够人类操作），或要求 C++ decide 增加标雷
  输出（改 C++，超本轮范围）

### D4. 必须保留的 Py 部分（不可删清单）
- vision.py 全部（OCR 是唯一盘面来源）
- solver.py 弹窗/新局/首点/统计（I/O 层）
- probability.py decide 分支本体 + 预处理 484-553（clicks/clicks9 划分、
  num9/num10 统计——C++ decide 的输入准备；注意 decide 内部也自算一遍，
  这里保留是为了 553 行的空盘早退与 num10 传递）
- main.py UI 渲染/update_btn_list/info 弹窗

## 三、删除后的新交互/后端逻辑（目标态）

```
自动模式 play()：
  首点 → 循环：弹窗管理 → complete_scan → number5_1（C++ decide 循环：
  decide→点击→rescan→再 decide，直至无进展/踩雷/胜利）→ heatmap
  【Py 推理 A1-A4 全部移除；单步决策 = C++ 三层全权】

帮助模式 help()：
  complete_scan → number5_1（单次 decide，放宽参数）→ pos_dict_list → GUI
  【Py 推理移除；提示只剩「推荐点击格 + 置信度」】

后端计算唯一归属：C++ mscore.decide（三层）；Py 只剩 I/O 与渲染。
```

## 四、删除后的行为变化与风险（执行会话必须逐条核）

| # | 变化 | 风险 | 缓解 |
|---|---|---|---|
| R1 | decide 异常无兜底（原跌 Python 链） | 异常炸穿主循环 | except 改 WARNING+返回重扫盘面 |
| R2 | cv 中永远无 10 标记（无人标雷） | decide 输入语义变化：num10 恒 0，世界枚举靠自身推导雷位——**数学上等价但枚举量增大**（少了两两确定的硬约束），中盘 decide 变慢 | 压测口径本就无 10 标记（40% 读数即此状态），故胜率无虞；速度影响需实测 |
| R3 | UI 红雷标记消失（自动+帮助） | 人类观感 | 接受或后续 C++ 加标雷输出 |
| R4 | `data.json` 统计断流 | 统计功能失效 | 决断：删或 decide 循环内补写 |
| R5 | README 回退承诺失效 | 文档不一致 | 同步重写 |
| R6 | `_wr_nodecap_hits`/`checked`/`till_now_winrate` 等链内状态变死 | 死代码 | 顺带清理 |
| R7 | 帮助模式无推理提示兜底：若 decide 返回无效坐标（如空盘早退），help 的 5 轮循环 flag==4 → 弹「请检查设置」 | 帮助模式对识别错误更脆弱 | 保留该警告路径即可 |

## 五、验证清单（执行会话删完必须全跑）

1. `python -m py_compile utils/probability.py utils/solver.py utils/deduction.py`
2. `python -m unittest tests.test_cpp_decide_wiring`（7 测试；mock Python 链
   回退的用例按 R1 新行为调整）
3. `python -m unittest tests.test_solver_core`（open_num5x5/cell_around 等
   纯函数测试应不受影响；若引用 mine_clear 需调整）
4. sim 端到端：`sim/eval_harness.py play_one`（is_play=True）同 seed 对拍
   ——**注意**：删 Py 推理后的生产链 ≈ 压测口径（40%），预期**高于**删除前
   的编排链（~20%）；若反而下降，查 R2（num10 缺失导致枚举膨胀）
5. GUI 冒烟：自动模式 10 局 + 帮助模式 1 次，确认无「两弹窗」复发、热力图
   正常、（R3）红雷标记消失属预期
6. grep 确认无残留调用：`mine_clear1|mine_clear3_1|number0|number_3_1` 在
   solver.py/probability.py 生产路径中应只剩 deduction.py 本体定义与 help()
   （若 D3 执行则 help 也不剩）

## 六、本会话沉淀的关联事实（详细版在记忆/报告）

- 终版胜率：39.85% ±0.23pp（178,690 局纯 decide 循环口径），报告
  `review/winrate-final-20261106.md`
- 点击坐标铁律：全库 `bx + x*cell_width`（1-based）；off-by-one 曾致三症状
- decide 循环已知问题（保留项内待优化）：每点一格全屏 _locate(lose)、
  rescan 阈值 3.0 依赖、_dead_hit 后热力图残留
- 内存监控口径：commit（Private Bytes），RSS 失明；每局 gc.collect()
- 压测口径无 10 标记（同 R2 状态），40% 读数即在此状态下取得
