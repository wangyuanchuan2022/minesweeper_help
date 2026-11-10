# 代码评审摘要

【评审身份】对象快照 `04967f60e969c048ccd6e1e5d2d16d6a027bb8fb5ee27245dbe5f625f3e8f7ee`（任务书21个路径+磁盘规格新增的2个包导出文件，共23项；基线 HEAD=`afa3f4c4f350e8057ab4440406148018c8b6f2bb`；含未提交工作区，不能仅以 HEAD 代替对象）｜规格 `17e7133f9752bcf937499206e26f8d1d1a7948fe1512b90f8fc2968424f4a821`｜语言清单 `cpp.md`=`8025dd324cfe81cdb44e88f04708ed5a7f6c4fb0f76503dcce0ee1a1eb85e571`、`python.md`=`84d5cdb69798805e05362aab77542e5b7410f26023febfd64658b981e0af3056`｜模型 `kimi-k3.1-pro`｜实际达成模式：**仅代码**（静态源码 + 不触发真实点击的本机导出探针；评审席位不操作扫雷窗口，故降级为仅代码）。
【覆盖状态】**complete**；23 项 reviewed / 0 skipped / 0 blocked / 0 waived。材料中的历史行数与磁盘快照不一致；以任务书清单和机器规格交集核对后，另补读 ui/__init__.py 与 utils/__init__.py，分母按机器规格闭合为23项。
【隔离与对象处置】未读取其他评审席产出，未采信材料中的作者测试自述。`crgate freeze --help` 未提供 help 分支而按默认参数改写了根目录未被 Git 跟踪的 `review-spec.json`；已留痕，不猜测恢复原内容，不以它取代任务书给定的 `review/review-20261109/review-spec.json`。codegraph 无索引，改用真实 read/grep，不编造调用边。
【证据与断言鉴别力】19 个任务书 Python 文件 AST 全部通过，另2个包导出文件已读；`cppcheck --enable=warning,performance,portability` 已跑两个 C++ 文件（无编译器警告级别的综合门禁）；本机 py310 探针实测 `available=True / has_decide=True / has_transitions_for=True`。缺陷断言均给出具体输入/控制流，至少含 `tied.size()>64`、`world_upper=1 且 C(n9,rem)>DSTAR`、三处不同 C++ 调用参数列表的≥3元素对照；这些为反例/边界静态断言，非新增生产闸门，R5 的“注入必红负向自测”列入各修复项的验收条件，尚未运行；未把自然语言推演称为形式化验证。**形式化验证=NOT_RUN**（任务书未要求形式化验证，未运行求解器/模型检测器）。完整 GUI 实机、点击与全量测试 skipped：只读评审不改动运行状态/测试产物；复跑 `python -m pytest -p no:cacheprovider` 与 `cpp/build_ab_native.bat` 需组织者授权写构建/测试产物。

## 覆盖账

| 路径 | 结论 | 理由/备注 |
|---|---|---|
| cpp/mscore.cpp | reviewed | 三层决策、ExactEngine、世界物化、门槛、PSEQ、绑定与合并桥接全读；cppcheck 已跑 |
| cpp/ab_native.cpp | reviewed | 对局/延迟布雷/批量点击/桥接全读；cppcheck 已跑；源文件含乱码注释但不影响已核逻辑 |
| ui/__init__.py | reviewed | 三个 UI 导出全读；`from .window import Ui_MainWindow` 与主窗口导入对应；AST 通过 |
| utils/__init__.py | reviewed | pyautogui 全局点击节奏与 Solver 导出全读；AST 通过 |
| utils/probability.py | reviewed | number5_1 两处 decide、_wr_update、失败语义、枚举与 DP 全读；AST 通过 |
| utils/solver.py | reviewed | 自动/帮助主循环、停止/胜负、热力图与配置生命周期全读；AST 通过 |
| utils/vision.py | reviewed | UNKNOWN、模板、批量/逐格、扫描/重扫全读；AST 通过 |
| utils/deduction.py | reviewed | number0/number_3_1 与邻域边界全读；AST 通过 |
| utils/native.py | reviewed | 导出、旧签名回退、失败可见性全读；本机导出探针通过 |
| utils/decide_trace.py | reviewed | 两处生产记录点、白名单、文件上限与只观不动语义全读；AST 通过 |
| utils/analyze_decide_trace.py | reviewed | 分相关、Q1-Q4 统计及边界全读；AST 通过 |
| utils/combinatorics.py | reviewed | C/get_list/A/比值与边界全读；AST 通过 |
| utils/mm0.py | reviewed | 窗口坐标、鼠标线程与辅助进程全读；AST 通过 |
| utils/clicking.py | reviewed | MST 点击排序全读；grep 证实生产无调用点；AST 通过 |
| utils/misc.py | reviewed | JSON 编码与日志输出全读；AST 通过 |
| utils/util.py | reviewed | 聚合导出全读；AST 通过 |
| main.py | reviewed | UI 接线、坐标、预览缓存、配置、批量动作与窗口关闭全读；AST 通过 |
| setting.py | reviewed | 配置/资源根、导入期 cwd 与加载全读；AST 通过 |
| logger.py | reviewed | 文件/控制台日志初始化全读；AST 通过 |
| ui/window.py | reviewed | 手写布局与 QSS 全读；未建议 pyuic5 重生成；AST 通过 |
| ui/preview_popup.py | reviewed | 7×7 数据、几何、缓存消费、控件生命周期与 hover/focus 全读；AST 通过 |
| ui/edit_setting.py | reviewed | 设置对话框控件与 signal 连接全读；AST 通过 |
| ui/screenshot.py | reviewed | 截图控件、坐标与保存全读；AST 通过 |

## 按严重度

- P0 / CRITICAL：3（两处内存安全、1.5GB 硬上限）
- P1 / HIGH：3（合并构建、决策反馈失效、候选动作错误）
- P2 / MEDIUM：6（展示语义、交互/性能、输入边界与观测）
- LOW：0。未以低价值风格项填充报告。

## 问题

位置: cpp/mscore.cpp:3164
问题: [P0] PSEQ 对超过 64 个同安全率前沿候选写栈越界。`counts` 固定 `[64][10]`，而 `nTied=tied.size()` 没有上限；循环按 `ti<nTied` 写 `counts[ti][mn]`。概率值接近时，`tied` 可达全部前沿格，而不是最多 64。
证据: `if (mn <= 8) { counts[ti][mn]++; }`
影响面: 栈内存破坏、错误决策或进程崩溃；写坏的是生产 pbs 并列决策路径，非仅诊断。
严重度: CRITICAL（P0）
置信度: HIGH
修复: 【改进点】两处计数器都改为按 `nTied` 动态分配（如 `vector<array<int,10>>`），并将 `row_vals` 同步改为按 `ordered_clicks.size()` 分配；添加 >64 并列候选的 ASan/受控回归用例。

位置: cpp/mscore.cpp:3499
问题: [P0] win_rate 并列分支重复同样的固定 64 项栈越界。`cnts[64][10]` 对应无界的 `nT`，在 `ti<nT` 下写 `cnts[ti][mn2]`；并列进入此层前的条件只限制 `tied.size()>1`，不限制上界。
证据: `if (mn2 <= 8) cnts[ti][mn2]++;`
影响面: 与前项相同；即便 pbs 路径未触发，win_rate 同胜率并列仍可破坏内存。
严重度: CRITICAL（P0）
置信度: HIGH
修复: 【改进点】与 F1 共用动态容器修复；禁止用“截断到64”掩盖，除非同时明确并测试截断策略对选格的影响。

位置: cpp/mscore.cpp:2536
问题: [P0] 内存门把 `world_upper`（仅为各前沿组解数乘积）当完整世界数，漏乘 `C(n9, rem)`。`mem.allowed` 是 OPTIMAL 与 win_rate 的共同放行条件；实际完整世界物化分别发生在本文件 2780-2790、1160-1170，帮助预览也按完整世界展开。OPTIMAL 的 PRE-GUARD 虽拦完整世界数>DSTAR，但不等价于 1.5GB 字节预算。
证据: `const auto mem = estimate_mem_impl(world_upper, candidates, mem_budget);`
影响面: 例如前沿只有1个解、内部格组合数极大时，前沿门和 `log2(world_upper)` 均可放行，而完整世界物化先占用内存。违反不可调的 1.5GB 上限；不能靠后续 node_cap 阻止枚举阶段的分配。
严重度: CRITICAL（P0）
置信度: HIGH
修复: 【改进点】在所有完整世界物化前，用 `world_upper × C(n9, rem_max)` 的保守规模同时做完整世界数与字节门，并为 `py_win_rate` 阶段1加逐世界计数/字节硬闸；不得建议提高1.5GB。增加“小前沿乘积+大内部组合数”的必拦截负测与峰值 RSS 读数。

位置: cpp/mscore.cpp:4564
问题: [P1] 合并构建调用参数数目不匹配。`decide_impl` 在 2430-2433 要求末尾 `bool is_play`，本调用到 `DEFAULT_MEM_BUDGET` 就结束；4696 的批量调用同样漏参。
证据: `r = decision_layer::decide_impl(board, a, w, h,`
影响面: `AB_NATIVE_MERGED` + `MSW_TRIAL_DECIDE` 的 C++ 调用在编译期报“没有重载接受8个参数”；A/B 桥不能构建。生产 py_decide 的 3998 调用是正确对照。cppcheck 不是编译器，不能替代此验证。
严重度: HIGH（P1）
置信度: HIGH
修复: 【改进点】两桥接显式传 `true`（A/B 是自动执行模式），由组织者运行 `cpp/build_ab_native.bat` 编译验证；为合并构建加 CI 编译门。

位置: utils/probability.py:1056
问题: [P1] 自适应 `_wr_update` 被接入了已失效的 `wr_threshold`。这里持久化并传递阈值，但 C++ 2954-2956 的 `use_win_rate` 只看 `log2_total_est`、`mem.allowed`、`world_upper>DSTAR`；`wr_threshold` 在 2432 后没有决策读取点。反馈指标又是已废弃的 `limitation`，不是真实 `cost_log2`。
证据: `wr_threshold=_auto_wr_threshold,`
影响面: 30s 预算导致的升降档不会改变任何 C++ 选层/深度行为，只写状态文件；与用户要的真实预算反馈不符，且诊断会误以为已调参。
严重度: HIGH（P1）
置信度: HIGH
修复: 【改进点】删除假反馈链，或把预算反馈接到真正的 `cost_log2`/深度/节点预算输入，并添加同盘面、不同预算必须改变门槛或深度的集成断言；不要恢复已知与成本反向的 limitation 门。

位置: main.py:1694
问题: [P1] 帮助候选无条件把决策单点标成 `is_best=True` 后，渲染把 `is_best` 当作确定安全，覆盖真实非1概率。`probability.py:1170-1200` 将每个 rx/ry 加入 `_safe_cells` 且候选 `is_best=True`；这里不看 `confidence<0.99`，直接给 best 蓝框并加入 `self.clicks`。
证据: `9, self._conf(pos_dict),`
影响面: win_rate/pbs 的非确定推荐格在 UI 变成“已证明安全”，并污染点击候选；帮助模式用户可能被引导执行非确定点击。
严重度: HIGH（P1）
置信度: HIGH
修复: 【改进点】区分 `is_recommend` 与数学证明的 `is_proven_safe`；蓝框可保留为“当前最优”，但绿底/确定安全文案/`clicks` 必须由逐格 prob>=0.999999 驱动，不得由 is_best 驱动。

位置: main.py:88
问题: [P2] 所有 C++ 三层候选都被标成“安全概率（精确解）”，包括 pbs DP 和启发式 win_rate。C++ 返回字段没有 value_kind/reason 映射到这里；仅凭 exp 的固定前缀分类。
证据: `("C++ 三层决策", VALUE_KIND_EXACT),`
影响面: 浮层与候选列表把策略胜率/启发式概率误标为精确解；用户在风险决策上得到错误口径。
严重度: MEDIUM（P2）
置信度: HIGH
修复: 【改进点】在 pos_dict/payload 中直接携带 C++ `reason` 与明确 `value_kind`；optimal/pbs 精确安全率与 win_rate 策略胜率分开，未知不默认为精确。

位置: main.py:1466
问题: [P2] 帮助统计栏把 `best_conf` 无条件写成“当前格非雷的概率”。`probability.py:1137` 在帮助模式保存的是 C++ `confidence`，optimal/win_rate 返回的分别是 V*/策略胜率，不一定是 prob[best]。
证据: `f"当前格非雷的概率: {conf_text} ｜ "`
影响面: 数值类型误导，与自动页已经存在的分类逻辑不一致。
严重度: MEDIUM（P2）
置信度: HIGH
修复: 【改进点】复用 `classify_decision_value_kind` 或发射端明确字段，将胜率标为“策略胜率”；只有同源逐格安全概率才使用“非雷概率”。

位置: main.py:880
问题: [P2] 旧 pyd 无 `transitions_for` 或该调用失败时，UI 回退到 best_cell 的 transitions，但标题仍是当前悬停格；`PreviewPopup.show_for_cell` 会改写 cell，而 transition_meta 未指向 best_cell 时无法纠正。
证据: `return (payload.get("transitions"),`
影响面: 显示“点击悬停格之后”的另一格真实分支，概率本身真实但归属错误。该回退与函数注释所承认的语义不对应一致。
严重度: MEDIUM（P2）
置信度: HIGH
修复: 【改进点】非当前格结果禁止回退；显示 `best_cell_not_closed`/“当前格转移不可用”，或保留 best_cell 标题与 meta.cell，绝不合成/换标签。

位置: main.py:1305
问题: [P2] 自动/帮助内层 decide→点击循环没有检查 `_stop_requested()`。停止按钮最多等 3000ms 后 `terminate()`，即使 UI 宣称模式互斥，同一进程也有两个 Solver 共享模块级 `_DECISION_BUDGET_MULT`。
证据: `_iter += 1`
影响面: 长枚举期间停止不协作，可能强杀并绕过 finally；help 的 3×预算可泄漏到自动模式。现有主循环安全点无法中断这段长循环。
严重度: MEDIUM（P2）
置信度: HIGH
修复: 【改进点】在每次 decide 返回后、点击前与重扫后检查取消；把预算乘数改为实例/调用级上下文，finally 恢复原值，不使用跨 Solver 的模块全局。

位置: ui/preview_popup.py:890
问题: [P2] 每个 MouseMove 都完整执行 payload→原生转移→重建分支控件。`_render_transition_boards` 对旧行仅 `setParent(None)`，未 `deleteLater()`；每次悬停重建所有 7×7 分支。
证据: `self._show_preview()`
影响面: 鼠标不动但事件持续/移动时产生大量临时 QObject 与重复 C++ 调用，UI 卡顿、内存短期增长。缓存仅覆盖 transfer 结果，未覆盖渲染。
严重度: MEDIUM（P2）
置信度: HIGH
修复: 【改进点】以 `(cell,payload identity/version,anchor)` 为展示缓存键，同键不重渲染；换帧时 `deleteLater()` 销毁旧分支，并测试连续移动后的子对象数稳定。

位置: cpp/mscore.cpp:1706
问题: [P2] 配置允许的 10000×10000 与原生固定容量不一致，且缺口不在同一处。Board 最多 1024 格会抛异常；ExactEngine 的 `MAX_WORDS=10` 只有640位，而 decide 的 OPTIMAL/并列路径直接 `eng.nw=(ncell+63)/64` 后写位，没有 py_exact_eval 那样的容量检查。
证据: `static constexpr int MAX_WORDS = 10;      // 640 位；30x16+外框=576 位 ⇒ 9 字够`
影响面: 合法通过设置 UI 的较大 w/h 可在原生路径越界写 `Bits::w`；不能用“默认30×16足够”替代边界检查。
严重度: MEDIUM（P2）
置信度: HIGH
修复: 【改进点】统一入口先校验 `(w+2)*(h+2)` 对所有引擎容量，超限明确不支持/安全重扫；不要扩大内存上限。添加刚好跨640/1024边界的负测。

位置: utils/decide_trace.py:89
问题: [P2] 20MB 上限只在 `_seq%200==0` 检查。已有超过20MB文件时，默认开启的记录器仍会继续写最多199条，随后才停止；`reset_session` 只重置序号，不撤销上限状态。
证据: `if _seq[0] % 200 == 0 and os.path.exists(_PATH):`
影响面: 观测文件突破声明的磁盘上限，且不同会话停写点不一致；只影响观测，不应影响决策。
严重度: MEDIUM（P2）
置信度: HIGH
修复: 【改进点】每条或按真实字节偏移检查，追加前计算 `当前大小+待写长度` 并 fail-closed；为多进程追加说明锁/独占语义。

## 越界发现

无新增文件纳入分母。不另将碰撞理论风险或固定容量告警凑作发现。`cpp/build_ab_native.bat` 只用于确认合并构建会进入上述漏参调用，不列为覆盖项。

## Open Questions（低置信度发现，呈现不阻塞）

无。以上均为已读周边代码且能给出具体触发条件的发现；未把猜测填入正式问题。

## 正面观察

- C++ 与 Python 的必需返回字段 `reason/x/y/confidence/world_upper/prob/tied/limitation/cost_log2/transitions/transition_meta` 已在 `make_response` 与 `py_decide` 对照：常规出口字段名一致，prob 坐标键为 tuple，transition 为条件概率；Python 使用 `.get` 处理可选转移。
- `log2 C(n,k)=lgamma(n+1)-lgamma(k+1)-lgamma(n-k+1)` 公式与除 `ln2` 正确；`DEPTH_CAP` 反解 `floor(log2(B/base-1))+1` 与代码声明的成本模型一致。此结论是数学核查，不是形式化验证。
- 分组包含数字8、前沿组列映射到本组坐标、part_solve 回溯恢复雷数、全 9 安全重扫不点击等关键不变量已落实。
- `native.py` 的旧 `is_play` 签名回退会 warning，不是静默失效；本机 py310 实际导出 decide/transitions_for。
- `help_human.clicked` 用 lambda 丢弃 bool，避免覆盖 `update_image(start_help=True)` 默认值；其他已读 clicked 连接未发现同类覆盖。
- 视觉 UNKNOWN 不写入棋盘，批量路径与逐格共享尺寸/最优点置信判据；批量失败有日志并回退逐格。

## 【改进点】

1. **先修内存与构建阻断（P0/P1）**：`cpp/mscore.cpp` 动态化 PSEQ 计数器；统一完整世界物化的世界数+字节硬闸；两处 AB 调用补 `is_play=true`。验收包含 >64 并列、巨大 C(n9,rem)、合并编译三项必过/必拦负测。
2. **修真正预算反馈与帮助动作（P1）**：`utils/probability.py` 将反馈接至有效 cost/depth 参数或删除假自适应；`main.py` 将“最优推荐”与“数学证明安全”拆开，批量点击只信逐格安全概率。
3. **修跨语言/UI 语义（P2）**：发射端携带 reason/value_kind，统计栏区分胜率与安全率；旧 pyd/失败时不得把 best_cell 分支冒充悬停格。
4. **修生命周期与边界（P2）**：内层 decide 循环增加取消点、预算改实例级；预览同键不重渲染且释放旧控件；原生入口统一容量校验；trace 按追加后的字节数限长。

裁定: **REQUEST CHANGES**（HIGH 置信度下存在 CRITICAL/HIGH）；覆盖状态: **complete**。