# 代码评审摘要 · 陌生仓库全量只读审计

【评审身份】对象快照 `head_sha=afa3f4c4f350e8057ab4440406148018c8b6f2bb`；工作区在该 HEAD 上有大量未提交改动，本报告审的是**当前磁盘快照**而非该提交本身。规格 `review2/review-spec.json`，`spec_sha256=5D5541366335404C39A0A0A775725BD341613FC67CF6E29AD0224BFB7D6A1550`；冻结分母 47 个条目（含 glob 展开为 11 个 tests、10 个 sim 文件）。模型 `buddy/kimi-k3.1-pro`；隔离模式 blind。安装 pyd 实测 `sha256=45C18B2F9871B8917362E2A0D67C29687BB8A7B452D2CFB1D3156C3A4CBD955F`，与任务书 sha8 一致。
【实际达成模式】**仅代码**。降级理由：任务书明确沙箱低完整性级别不能驱动 Arbiter；本轮未进行真实 GUI/Arbiter 操作，也未重编译 C++（会写 `.obj/.pyd`，超出报告写面）。
【语言清单】通用安全/逻辑/错误处理/性能清单 + 任务书五项审计重点。人格所述 `checklists/python.md`、`cpp.md` 未随材料提供，语言专项记 `skipped（工具不可达）`；复跑方式：将两文件放到人格 `checklists/` 后用同一 spec 重跑，不以记忆补写清单。通用审计未因此省略。
【覆盖状态】**complete（静态源码/文档逐项覆盖）**；47/47 条目均 reviewed。动态测试、真实 E2E、C++ 重编译属于未执行验证，不冒充通过。
【对象漂移】报告期间以开篇磁盘快照为基线；未主动更换基线。HEAD 只作身份锚，不代表工作区内容等于 HEAD。

## 最小结论（人可复述）

1. **P0 未闭合**：`nTied > 64` 时 PSEQ 向固定 `counts[64][10]` 越界写栈，存在原生内存损坏风险。
2. 唯一 C++ `decide`、DSTAR=256/帮助×3、精确世界计数、粗筛×4、MID-LOOP GUARD 与失败不回退的主干均已接线；但 1.5GB「不可调」被公开 `mem_budget` 形参穿透。
3. 帮助预览在原生调用失败时，会把 best 格的转移显示成鼠标格的转移；这是错误信息而非“暂无”。
4. 视觉低置信度默认不中止，PSEQ 又将前 200,000 行当完整分布；两者都可能让不完整信息被当成正常决策依据。
5. 测量链仍有仪器错误：runner 用全文件计数控制本轮，分析器混用自动/帮助 DSTAR，且一个 OPTIMAL 断言先过滤再永真；文档还把有 20MB 上限的 trace 写成无上限。

## 阶段 1：规格符合性

| 规格项 | 结论与证据 |
|---|---|
| 唯一 C++ decide | 符合。`utils/probability.py:918-922` 与 `1209-1213` 均调用 `native.decide`；不可用统一进入 `1244-1259`，无 Python 决策回退。`utils/solver.py:733-740` 自动主循环没有 Python 选格分支。 |
| Algorithm 3 / DSTAR | 符合。`cpp/mscore.cpp:2057-2065` 默认 256、帮助×3；`2111-2130` 以精确 `situations <= DSTAR` 准入 OPTIMAL；其余走 pbs+PSEQ。 |
| `count_situations_exact` / 哨兵 | 符合。`1583-1587` 超 cap 返回 cap+1；`2075-2119` 粗筛为 DSTAR×4 并导出真实值/哨兵；`1799-1800` 未计数初值为 -1。 |
| MID-LOOP GUARD | 符合。`2191-2195` 在物化超过 DSTAR 时抛错并保守落到后续路径。 |
| 取消与批量 | 基本符合。自动 decide 循环 `1112-1115`、批量点击 `1152-1155` 有取消检查；certain_safe 在 `1141-1143` 批量取 tied[:64]。帮助 decide 循环没有逐轮取消检查，见 P2-1。 |
| 胜负统计 | 符合统一累加口径。`utils/solver.py:675-717` 四来源合一，先 `win/total` 再累加落盘、发 game_end、重开。 |
| 事件驱动重开 | 代码存在三阶段等待：`511-560` 稳定与 reset、`569-580` 首点；但 reset 的“模板消失”兜底不要求盘面实际变化，真机 ok 比例仍须验证。 |
| 视觉窗口矩形 / 屏幕 DC | 符合。`utils/vision.py:248-270` 使用 `GetDC(0)` + BitBlt；`287-300` 窗口失败回退整屏；`357-383` exit 低分回退整屏。 |
| `wr_threshold` | 符合删除要求。`utils/native.py:118-124` 仅 `_ = wr_threshold`，不转发。 |
| 决策不可用不回退 | 符合。`utils/probability.py:638-679` 日志、警告、全盘重扫、不点击；`1247-1259` 所有不可用出口收敛。 |
| 历史文档标记 | 符合。`docs/实验总报告.md:3-7`、`docs/变量名与算法细节.md:3-7`、`docs/胜率测试套件说明.md:3-8`、`CHANGELOG.md:6-8`、`HANDOFF.md:3-8` 均有历史快照警示。 |

## 阶段 2：分级发现

### P0-1 · PSEQ 对固定栈数组的越界写

- 位置：`cpp/mscore.cpp:2525-2528`、`2552`、`2578`。
- 问题：`counts` 固定为 64 行，但 `nTied=tied.size()` 没有上限，循环和写入均直接使用无界 `ti`。
- 证据（逐字）：`int counts[64][10];`；`for (size_t ti = 0; ti < nTied; ++ti)`；`if (mn <= 8) { counts[ti][mn]++; }`。`2490-2496` 构造 tied 时同样没有 64 上限。
- 影响面：出现超过 64 个同分候选时写穿栈帧，可能破坏邻近变量、返回地址或导致进程崩溃；不是单纯概率偏差。既有 README 已把它列为未闭合 P0，本轮源码复核确认仍存在。
- 严重度：**P0 / CRITICAL**；置信度：**HIGH（>95%）**。
- 修复：改为 `std::vector<std::array<int,10>> counts(nTied)`，或 `std::vector<std::vector<int>> counts(nTied, std::vector<int>(10,0))`；不得只截断 tied 来掩盖算法集合变化。增加 >64 并列候选的 ASan/受控负向用例。

### P1-1 · 1.5GB 硬闸门仍可由公开参数覆盖

- 位置：`cpp/mscore.cpp:2005`、`3200-3203`。
- 问题：文档称 1.5GB 不可调，但 pybind 的公开 `decide` 暴露 `mem_budget`，并原样传入 `decide_impl`。
- 证据：`e.budget = mem_budget > 0 ? mem_budget : DEFAULT_MEM_BUDGET;`；导出包含 `py::arg("mem_budget") = decision_layer::DEFAULT_MEM_BUDGET`。`utils/native.py:118-124` 虽不提供同名包装参数，却不能阻止直接调用 `native.mscore.decide(..., mem_budget=...)`。
- 影响面：调用方可提高预算让 mem.allowed 放行，违背硬上限；同时 `MSW_DSTAR` 可让 MID-LOOP GUARD 的世界数增大，组合后更容易突破预期资源边界。
- 严重度：**P1 / HIGH**；置信度：**HIGH（>95%）**。
- 修复：从公开 decide 删除 `mem_budget` 形参，决策层始终使用 `DEFAULT_MEM_BUDGET`；诊断用 estimate_mem 如确需可调，应与生产 decide 明确隔离。增加超大 budget 注入必红测试。

### P1-2 · PSEQ 把前 200,000 个里程计行当成完整统计总体

- 位置：`cpp/mscore.cpp:2529-2533`、`2582-2589`。
- 问题：`kMaxRows` 到达上限后静默停止，随后直接以 `counts/total_rows` 计算 s/e/q 并做 PSEQ 过滤；没有 sampled/truncated 标记，也不区分有效世界与无效行。
- 证据：`const int64_t kMaxRows = 200000;  // sound sampling cap`；`while (od.next(index_list) && total_rows < kMaxRows)`；`double p = (double)counts[ti][n] / (double)total_rows;`。`2548-2550` 对无效行也递增 `total_rows`，进一步稀释分布。
- 影响面：超过 200k 行时 P→S→E→Q 不是对全部一致世界计算，截断顺序可能系统性偏向某些候选，影响选格与胜率；返回 `reason` 与诊断看不出已截断。
- 严重度：**P1 / HIGH**；置信度：**HIGH（>90%）**。
- 修复：优先做有界流的精确累计；若确需采样，使用可复现的均匀抽样、仅对有效世界归一，并在返回中暴露 `pseq_sampled/sample_count/total_upper`，文档不得再称完整 PSEQ。用 >3 组且 >cap 的受控输入验证截断标记。

### P1-3 · 帮助预览失败时把另一格的转移冒充当前格

- 位置：`main.py:864-899`，根因辅助点 `utils/native.py:153-159`。
- 问题：鼠标格 `(i,j)` 的 `transitions_for` 失败返回 None 后，UI 回退到 payload 中针对 decide best_cell 的转移；浮层标题仍指向 `(i,j)`。
- 证据：`r = _native_mod.transitions_for(..., i, j)`；随后 `if r is None: result = (payload.get("transitions"), payload.get("transition_meta"))`。native 对所有异常只 debug 后返回 None，调用方无法区分旧 pyd 与本次计算失败。
- 影响面：悬停 B 格却显示 A 格的条件概率/7×7 分支，直接误导人类下一步；违反“真实转移以鼠标所在格为中心、失败不合成概率”的规格。
- 严重度：**P1 / HIGH**；置信度：**HIGH（>95%）**。
- 修复：仅在没有 `transitions_for` 导出且产品明确接受旧 pyd 降级时保留兼容；当前格调用失败必须显示 `reason=hover_transition_failed`，清空 transitions，不回退 best 格。增加 A≠B 且第二次调用抛错的注入必红测试。

### P1-4 · 低置信度扫描默认仍把不完整盘面送入决策

- 位置：`utils/vision.py:68-73`、`631-649`、`676-692`。
- 问题：`STRICT_LOW_CONFIDENCE=False` 时，即使不可识别比例超过中止阈值，也只是返回原棋盘；`complete_scan` 正常返回，调用方继续 `number5_1`。
- 证据：`STRICT_LOW_CONFIDENCE = False`；`if STRICT_LOW_CONFIDENCE: raise LowConfidenceScanError(msg)` 后紧接 `return cell_value  # 整轮作废...`。若输入是全 9，返回值仍是合法形状的全 9，后续一致性校验并不等价于“识别可信”。
- 影响面：遮挡/模板退化时可能把旧格或未识别格当作仍未开，继续给出点击建议；自动主循环只在实际抛 `LowConfidenceScanError` 时有专门安全处理，默认配置绕过了这层。
- 严重度：**P1 / HIGH**；置信度：**HIGH（>90%）**。
- 修复：超阈值路径必须显式失败，或返回带 `trusted=False` 的结果并由 decide 前硬门拒绝；不要以“不崩溃”为理由继续自动点击。增加全 UNKNOWN→decide 零调用的生产契约测试，而不只测“棋盘不写负值”。

### P1-5 · E2E runner 用历史全文件局数控制本轮完成与重启

- 位置：`bench/e2e_900.py:119-133`、`354-375`。
- 问题：`_count_game_ends` 对整个追加写 probe.jsonl 计数，没有按本进程 `t_run_start` 过滤；该全量值直接决定 `n_done`、剩余局数和达标停止。
- 证据：`for line in f: if '"game_end"' in line: n += 1`；调用点 `n_done = _count_game_ends(probe.path())` 与 `elif _count_game_ends(probe.path()) >= args.games:`。同文件 `418-421` 最终汇总反而使用 `r["ts"] >= t_run_start`，前后口径不一致。
- 影响面：重跑/续跑时历史局数会提前达成本轮目标、计算错误剩余量；与文档刚修复的跨轮混算纪律相冲突。
- 严重度：**P1 / HIGH**；置信度：**HIGH（>95%）**。
- 修复：计数函数接收 `since_ts=t_run_start`，逐行 JSON 解析后只统计本轮；resume 的“全局目标”和“本进程目标”拆成两个变量，不用全文件计数替代。

### P1-6 · E2E 分析器混用自动/帮助 DSTAR，哨兵统计失真

- 位置：`bench/analyze_e2e.py:243-259`。
- 问题：从全部 decision 中取第一个正 DSTAR 当全局唯一值；帮助模式实际 DSTAR=768，与自动 256 混跑时 769 哨兵不会按 768 识别，257 也可能被错归。
- 证据：`dstar = dstar_vals[0] if dstar_vals else None`；`if dstar is not None and s == dstar + 1:`。
- 影响面：`situations` 的真实值、未计数、超限哨兵三态被错分，进而影响 OPTIMAL 比例和性能归因；探针每条记录本身已带 `help_mode` 与 `dstar`，无需全局猜测。
- 严重度：**P1 / HIGH（测量仪器）**；置信度：**HIGH（>95%）**。
- 修复：逐记录用 `d["dstar"]` 判定，或至少按 `(help_mode,dstar)` 分组；加入自动 256/帮助 768 混合输入，断言两类哨兵均被剔除。

### P1-7 · “OPTIMAL 全部 ≤DSTAR”检查先过滤违规值再判违规数

- 位置：`bench/analyze_e2e.py:269-278`。
- 问题：`opt_sits` 的列表推导已经限定 `0 <= situations <= dstar`，后续 `bad = sum(s > dstar)` 结构性恒为 0。
- 证据：过滤条件 `and 0 <= d["situations"] <= (dstar or d["situations"])`，随后才计算 `bad` 并打印“全部 ≤ DSTAR”。
- 影响面：真正越界的 OPTIMAL 记录被静默排除，仪器失去鉴别力，会给出假绿结论。
- 严重度：**P1 / HIGH（质量闸门失效）**；置信度：**HIGH（>95%）**。
- 修复：收集全部 reason=optimal 且有整数 situations 的记录，再断言每一个 `0<=s<=该记录 dstar`；至少注入 1 个越界记录并验证输出/退出状态必红。

### P1-8 · `repro_world_count_split.py` 仍实现已修复的旧钳制，并可据此误报现行 BUG

- 位置：`bench/repro_world_count_split.py:75-85`、`130-139`、`223-239`。
- 问题：文档说该脚本用于复现修复，但 `sizes_from_cpp_clamp` 仍硬编码 `num=n9-1`；输出表把它标作“当前 C++ 行为”。真实 C++ 已在 `cpp/mscore.cpp:433-441` 改为 `num<=listnum`。
- 证据：`if num > n9 - 1: num = n9 - 1`；`bug = (ok_wc is False) or (ok_cons is False)` 后输出 `BUG_CONFIRMED`。脚本将自制旧模型与现行实测并列，读者容易把模型表当作当前实现证据。
- 影响面：现行验证入口失去“已修复”的明确回归语义；无盘面文件时还在 `179-182` 返回 0，呈现退出码成功。
- 严重度：**P1 / HIGH（验证可信度）**；置信度：**HIGH（>95%）**。
- 修复：保留旧算法时必须命名 `sizes_buggy_legacy` 并明确只作负向对照；新增当前实现断言与修复后正例。缺输入返回非零 `NO_SAMPLE`，不得返回成功。

### P1-9 · 文档把实际有 20MB 上限的 decide trace 称为“无上限/唯一完整证据”

- 位置：`docs/现行架构与测量.md:160-162`；实现 `utils/decide_trace.py:38`、`91-97`。
- 问题：文档的完整性保证与实现直接相反。
- 证据：文档写 `无文件上限`、`唯一完整的决策链证据`；实现写 `_MAX_BYTES = 20 * 1024 * 1024` 且超限 `_stopped[0] = True`。
- 影响面：主探针截断后，第二证据链也可能截断；分析者会把缺失决策误当未发生。trace 只有启动 warning，没有结构化 stopped 记录供分析器核销。
- 严重度：**P1 / HIGH（证据完整性）**；置信度：**HIGH（>95%）**。
- 修复：文档改为实际上限与停止语义；trace 在独立 sidecar 写 `stopped/reason/bytes`，分析器必须拒绝把截断文件当完整证据。需要完整长跑时应滚动分片并保留清单，而不是静默停追加。

### P1-10 · 公开 transitions 原生接口缺形状校验，短数组触发越界读

- 位置：`cpp/mscore.cpp:3061-3066`；同类辅助路径 `2835-2854`。
- 问题：`py_transitions_for` 只取得裸指针，再按 `(h+2)*(w+2)` 构造 vector；没有 `ndim` 和 shape 检查。`help_exact_transitions` 同样直接读取。
- 证据：`const int32_t* raw = static_cast<const int32_t*>(cell_value.request().ptr);`；`std::vector<int32_t> cv(raw, raw + ncell);`，之前没有任何形状拒绝。
- 影响面：错误尺寸输入造成原生越界读、异常棋盘指纹或进程崩溃；与 `decide_impl:1856-1858` 已有 shape 防护不一致。
- 严重度：**P1 / HIGH**；置信度：**HIGH（>95%）**。
- 修复：两个入口统一调用共享的 `checked_board_copy(cell_value,h,w)`，先验 ndim==2 且 shape==(h+2,w+2)；增加短数组/一维数组注入测试。

### P1-11 · `sim/eval_harness.py` 不再复刻其声明的生产决策环

- 位置：`sim/eval_harness.py:6-12`、`297-317`；对照 `utils/solver.py:733-740`。
- 问题：台架仍先跑 Python `mine_clear1` 与 `mine_clear3_1`，无变化才 number5_1；现行生产自动模式已删除这层编排，每轮直接 number5_1。
- 证据：台架 `cell_value = s.mine_clear1(...)`、`s.mine_clear3_1(...)`、`if sum3 == sum2: ... number5_1(...)`；生产路径只有 `cell_value = self.number5_1(cell_value)`。
- 影响面：该脚本产出的“算法上限/生产复刻”混入已删除 Python 决策来源，不能与当前纯 C++ 结果直接比较；文件头也没有醒目标成历史台架。
- 严重度：**P1 / HIGH（测量口径）**；置信度：**HIGH（>95%）**。
- 修复：要么完全按现行生产删除 Python 前置推理，要么重命名并首行标历史快照，禁止其数字作为当前生产胜率证据。

### P2-1 · 帮助 decide 循环没有取消检查

- 位置：`utils/probability.py:973-1089`。
- 问题：自动循环与批量点击有 `_stop_requested()`，帮助循环 16 轮内没有对应检查；每轮还可能触发昂贵的帮助态转移计算。
- 证据：`while _rounds < _help_loop_max:` 后直接进入 decide/候选处理，下一轮在 `1085-1089` 再调 native.decide；全段无取消判断。
- 影响面：用户停止帮助计算时，最坏仍需跑完当前轮并再进入后续轮次；与“取消通道 decide 循环内都有检查”的文档表述不完全一致。
- 严重度：**P2 / MEDIUM**；置信度：**HIGH（>90%）**。
- 修复：循环顶部与再次 decide 前检查取消，返回前恢复预算乘数由 help finally 保证；增加第二轮前取消即不再调 native 的测试。

### P2-2 · native 模块头仍宣称热点失败会回退纯 Python，且列出已删除实现

- 位置：`utils/native.py:2-19`。
- 问题：文档称模块“带自动回退”，并写 `part_solve / part_solve_single / win_rate / pbs_compute` 异常会由 probability 回退纯 Python；这些 Python 热点链已删除，当前 decide 明确不回退。
- 证据：`任何加载失败...上层自动使用纯 Python实现` 与 `热点方法层...回退到纯 Python 原实现重算`，同时 `14-19` 又描述 decide 不回退，前后容易误读。
- 影响面：维护者可能按模块头恢复或新增隐式回退，破坏唯一 C++ decide 的硬边界。
- 严重度：**P2 / MEDIUM**；置信度：**HIGH（>95%）**。
- 修复：模块头改为当前事实：仅保留历史导出，生产 decide 无 Python 回退；删除已不存在热点链的行为承诺。

### P2-3 · sim 首点规则文案仍与当前 Board 实现相反

- 位置：`sim/algorithm_winrate.py:5-8`、`99-104`、`139`；`sim/baseline_winrate.py:9-13`、`143-144`；实现 `sim/minesweeper_sim.py:106-132`。
- 问题：两个台架宣称“首点 3×3 安全”，但 Board 当前只排除首点本格；这些台架没有历史快照首行警示。
- 证据：algorithm 写 `真实棋盘规则...首点 3x3 安全`；baseline 写 `首点及其 8 邻域无雷`；Board 的 pool 只 `if not (x == first_x and y == first_y)`。
- 影响面：读者会按错误规则解释胜率与开局形态，和现行文档“只保证首格”冲突。
- 严重度：**P2 / MEDIUM**；置信度：**HIGH（>95%）**。
- 修复：统一改为“仅首点本格安全”，历史读数标口径隔离；不要只改打印而保留错误断言。

### P2-4 · 设置校验接受非整数浮点并静默截断

- 位置：`setting.py:96-105`。
- 问题：`_as_int` 接受 float，`int(v)` 后直接校验和写回，没有检查 `v == iv`。
- 证据：`not isinstance(v, (int, float))` 后 `iv = int(v)`，例如 w=2.9 会变成 2。
- 影响面：手工 JSON 或异常调用方传入非整数时，保存的是另一个配置；违背“非法配置 fail-closed，不静默填默认值”的本函数声明。
- 严重度：**P2 / MEDIUM**；置信度：**HIGH（>95%）**。
- 修复：仅接受非 bool 的 `int`，或要求有限浮点且 `v.is_integer()`；补 2.5、NaN、True、字符串的负向用例。

## 【改进点】（落地顺序）

1. 先修 P0-1 与 P1-10 的原生边界，再用 ASan/短数组/>64 tied 做注入必红；这两项未修前不建议长局自动跑。
2. 将资源上限从可覆盖形参改为 decide 内部常量；把 PSEQ 截断状态显式纳入返回契约，不能静默当全量。
3. 帮助预览只对当前格结果负责；低置信度盘面在 decide 前 fail-closed。两项都应补“错误输入绝不产生动作”的端到端契约。
4. 统一测量主键：runner 以 run_id/时间窗切段，分析器逐记录读 dstar；所有 PASS 都配一个违规样本证明会失败。
5. 清理文档与旧台架的口径，不把历史 Python 推理、旧钳制模型或截断 trace 当现行证据。

## 覆盖账

四态合计 47 = reviewed 47 / skipped 0 / blocked 0 / waived 0。以下“reviewed”均表示阶段 1+2 的静态审查完成，不表示动态测试通过。

| 路径/冻结条目 | 结论 | 理由/备注 |
|---|---|---|
| main.py | reviewed | GUI、预览、批量动作、取消与样式；P1-3、P2 相关。 |
| setting.py | reviewed | 配置路径/原子保存/校验；P2-4。 |
| logger.py | reviewed | 滚动日志与控制台降级，无新增阻断发现。 |
| color.py | reviewed | OKLCH 转换与渐变；`steps=1` 除零仅通用库边界，生产固定 100，不升级。 |
| ui/window.py | reviewed | Qt 布局/QSS/手工同步说明已核。 |
| ui/window.ui | reviewed | XML 解析实测通过；与 window.py 的关键尺寸/QSS 抽查。 |
| ui/edit_setting.py | reviewed | 生成 UI 与资源引用已核。 |
| ui/preview_popup.py | reviewed | 条件概率展示、7×7、键盘与关闭通道已核。 |
| ui/screenshot.py | reviewed | 截图/模板保存与异常路径已核。 |
| utils/__init__.py | reviewed | pyautogui 全局节奏与导入副作用已核。 |
| utils/solver.py | reviewed | play 胜负统计、重开、热键、OSError、help 主链全覆盖。 |
| utils/probability.py | reviewed | number5_1、校验、取消、批量、失败出口全覆盖。 |
| utils/vision.py | reviewed | DC BitBlt、整屏回退、模板匹配置信门；P1-4。 |
| utils/native.py | reviewed | decide 包装、wr_threshold 不转发、异常语义；P1-3/P2-2。 |
| utils/e2e_probe.py | reviewed | JSONL 上限、span、内存采样、summary 已核。 |
| utils/decide_trace.py | reviewed | 20MB 停止与 schema 已核；P1-9。 |
| utils/deduction.py | reviewed | Python 推理保留兼容但生产 help/play 不调用；与 sim 误用区分。 |
| utils/combinatorics.py | reviewed | get_list 修复、组合生成与边界已核。 |
| utils/mm0.py | reviewed | 窗口坐标、进程与鼠标辅助已核。 |
| utils/clicking.py | reviewed | MST 点击排序为无生产调用兼容函数；空集 `degree_one_nodes[0]` 属潜在边界，不进入当前生产裁定。 |
| utils/misc.py | reviewed | JSON 编码与打印已核。 |
| utils/util.py | reviewed | re-export 清单已核；模块头旧 API 描述属文档残留，未另起阻断项。 |
| utils/analyze_decide_trace.py | reviewed | 相关性/内存分析含已删层历史栏目；按历史诊断工具看待。 |
| cpp/mscore.cpp | reviewed | 决策、世界枚举、PSEQ、转移、导出与原生边界；P0/P1 证据均逐字核对。 |
| cpp/build.bat | reviewed | MSW_TRIAL_DECIDE、C++17、输出与错误码已核；未重编译。 |
| tests/*.py | reviewed | 展开 11 文件：helpers、test_combinatorics、test_config_lifecycle、test_cpp_decide_wiring、test_probability、test_real_io、test_scan、test_solver_core、test_ui_heatmap、test_ui_migration、test_ui_preview_popup；检查断言覆盖与鉴别力，未执行。 |
| tools/coverage_probe.py | reviewed | `--list-only` 实测 22 模块/5287 行；生成资源 ui/bg_rc.py 明确排除，未误入分母。 |
| bench/e2e_900.py | reviewed | 真实 runner、resume、跨轮过滤与统计；P1-5。 |
| bench/analyze_e2e.py | reviewed | span 嵌套、墙钟、哨兵与内存口径；P1-6/P1-7。 |
| bench/audit_dead_code.py | reviewed | AST 引用与 C++ 导出账、重复 QSS 检测已核；未把其输出当真实运行证明。 |
| bench/verify_getlist_fix.py | reviewed | 世界数/概率守恒、无样本分支已核。 |
| bench/verify_prob_truth.py | reviewed | 暴力真值、截当作废与阳性对照已核。 |
| bench/verify_paper_layers.py | reviewed | OPTIMAL/PSEQ 可达与内存检查已核；仍含历史注释，不按注释替代源码。 |
| bench/repro_world_count_split.py | reviewed | 旧钳制复现与当前修复语义；P1-8。 |
| bench/dstar_10k_serial.py | reviewed | 串行、同布局、McNemar、增量落盘已核。 |
| bench/run_2000_after_cleanup.py | reviewed | 纯 C++ 循环、内存采样、逐局 rows 已核。 |
| sim/*.py | reviewed | 展开 10 文件：algorithm_winrate、baseline_winrate、diagnose_engine、engine_adapter、eval_harness、first_click_rules、minesweeper_sim、play_matches；规格 glob 当前实测为 10 个。P1-11/P2-3。 |
| README.md | reviewed | 唯一 decide、常量、失败策略、历史链接与未闭合项逐项对照。 |
| docs/现行架构与测量.md | reviewed | 当前权威声明逐项对照；P1-9。 |
| docs/实验总报告.md | reviewed | 历史快照与作废标记已核；不采信其自评，只核文档一致性。 |
| docs/变量名与算法细节.md | reviewed | 历史快照/已删 API 警示已核。 |
| docs/胜率测试套件说明.md | reviewed | 已标历史快照和不可运行。 |
| CHANGELOG.md | reviewed | 历史快照与旧 API 警示已核。 |
| HANDOFF.md | reviewed | 历史交接点与现行文档优先级已核。 |
| cfg.json | reviewed | 当前 30×16/99、坐标与路径字段符合 schema。 |
| requirements.txt | reviewed | 运行依赖含 networkx/matplotlib；验证脚本使用 psutil 但未声明，见盲区。 |
| .gitignore | reviewed | state、生成 pyd/obj、缓存排除已核。 |

## 验证记录与断言鉴别力自查

- 实际运行 `python -B tools/coverage_probe.py --list-only`：22 modules、5287 executable lines，与 spec 一致；该命令不跑测试。
- 实际对 269 个根/utils/ui/tests/sim/tools/bench Python 文件执行 AST parse：全部通过。AST 不是类型检查，也不是行为测试。
- 实际解析 `ui/window.ui` XML：通过。
- 实际核对 spec SHA256、HEAD 与安装 pyd SHA256；pyd sha8 为 45C18B2F。
- 项目未提供统一 mypy/pyright 配置；未运行动态 unittest，避免其临时目录、日志和状态写盘越过本轮“只许报告”的写面。复跑命令（用户终端/可写隔离副本）：`python -B -m unittest discover -s tests -q`。
- 未运行 C++ 编译；复跑需在可写隔离副本执行 `cpp/build.bat`，再运行 >64 tied、短数组、mem_budget 注入的专项负向测试。
- 本轮未新增解析器/分类器/闸门/测试，故 R5 的“新增闸门注入必红”不适用；但已指出 P1-7 是现有断言无鉴别力的实证。
- 形式化验证：**NOT_RUN**。本报告没有以类型检查、AST 或自然语言推演冒充形式化 PASS。

## Open Questions（呈现但不单独阻塞）

1. `cpp/mscore.cpp:2530` 的 200k 截断在实际专家盘的触发率未实测；P1-2 的“会截断且语义改变”由代码确定，频率与胜率影响大小未量化。
2. `utils/solver.py:546-555` 的 reset 兜底只看胜负模板消失，不要求棋盘变化；弹窗遮挡/点击失败时可能把 ok=False 的等待误当完成。需真机采集 `game_end.reset.ok` 与后续首点失败关联，当前不把概率性场景升级为独立 HIGH。
3. `requirements.txt:1-10` 未列 psutil，而 `bench/e2e_900.py:245-247` 在无 psutil 时把锁中 pid 视为 alive；这是可选依赖的降级策略，是否影响目标环境未实机确认。
4. `main.py:873-875` 对“旧 pyd 无导出”仍有 best 格回退；本报告 P1-3 覆盖当前格调用失败，旧 pyd 是否彻底不再支持需产品裁决，但错误语义最好同样去掉。

## 正面观察

- 决策不可用出口集中且有错误日志、用户警告、全盘重扫、不点击，未发现隐藏 Python 决策回退。
- C++ 精确计数与物化均有上限，组合数/世界数使用饱和或早退，避免整数溢出反向放行。
- 胜负统计四来源已统一，热键和 GUI 停止共用协作式取消；自动批量循环有安全点。
- 视觉批量识别与参考实现共用实际补丁尺寸和 UNKNOWN 编码，模板损坏有显式校验。
- 历史文档首行普遍有快照/作废提醒；测量文档明确写嵌套 span 不可相加、样本截断与配对检验纪律。
- 多个测试已经包含 ≥3 元素批量断言、真实控件离屏测试和错误语义检查，基础契约较丰富。

## 盲区说明（本报告漏了什么）

- 没有真实 GUI/Arbiter 驱动，因此不能证明热键真实触发、模板在 DPI/主题/遮挡下可靠、局末重开三判据实际成功。
- 没有重编译或对安装 pyd 做动态反证；源码与安装 pyd 仅 sha8 与任务书一致，未建立二进制与当前源码的可重现构建等式。
- 没有跑完整单元回归与现有验证脚本，报告中的问题来自源码与只读机检；既有测试“绿”不是本报告证据。
- 未逐字证明 AAAI-17 全部数学语义，只核了任务书指定的 Algorithm 3 分层和关键常量；PSEQ 截断与 nTied 说明实现并未完全满足论文语义。
- C++ 3766 行按决策主链、世界枚举、转移与公开导出审计；历史 bench 残留导出与纯模拟器没有进行全量动态差分。
- 对 UI 的 QSS/布局是源码与 XML 静态核对，没有截图像素验收。

## 裁定

裁定：**REQUEST CHANGES**。依据：P0-1 是高置信度原生栈越界，另有多项高置信度 P1；覆盖状态与裁定正交。覆盖状态：**complete（47/47 静态条目）**；动态验证状态：未执行，不计通过。
