# 首批 PyQt UI 迁移独立代码评审

## 代码评审摘要

【评审身份】对象基线 HEAD=d390b4e87e8334b1acf1c0f29d3437fa69e0277c；交付时对象快照见 review/ui-migration-code-review-spec.json 的 selection_hash 与逐文件 SHA256 | 冻结清单 4 项 | 语言清单 python.md（SHA256-16=84d5cdb69798805e） | 模型 buddy/kimi-k3.1-pro | 隔离模式 blind，未引用作者自评或其他评审席结论。
【覆盖状态】complete（指定 4 项均完成阶段 1/2）；评审文件数 4（reviewed 4 / skipped 0 / blocked 0 / waived 0）。真实窗口验收仍属未覆盖验证面。
【范围冻结】只评 ui/preview_popup.py、main.py 的 UI 迁移段、tests/test_ui_preview_popup.py、review/ui-migration-implementation.md；其他工作区改动不计分母。crgate include 是增量而非排除式筛选，故改用任务书范围手工 manifest，再运行 anchor/finalize。
【对象漂移】评审中 main.py 多次变化，已广播组织者并重读新版；本报告撤销已被新版修复的“帮助页无棋盘/未关闭浮层”旧发现。结论仅对 spec.json 内最终快照负责，不对之后改动负责。

### 覆盖账

| 路径 | 结论 | 理由/备注 |
|---|---|---|
| ui/preview_popup.py | reviewed | 全文、Qt 生命周期、事件过滤、定位、占位与交互 |
| main.py | reviewed | 最新 UI 迁移段、reset、focus/hover、候选/热力图接线和调用方 |
| tests/test_ui_preview_popup.py | reviewed | 40 用例全文及 offscreen 实测；核对反例与生命周期盲区 |
| review/ui-migration-implementation.md | reviewed | 交付声明与源码、调用链和实测逐项核对 |

### 按严重度

- P0（HIGH）：2 项，必须修复后再批准。
- P1（MEDIUM）：4 项。
- P2（LOW）：3 项。
- 已验证通过：无 transitions 的真实占位、不改生成 UI、首屏帮助棋盘接线、关闭/切页清理、三页结构、定向 47 项及此前全量 194 项测试通过。

### 问题

位置: main.py:80
问题: P0：所有 C++ 三层决策帮助候选统一映射 exact_safety，而 C++ reason=win_rate 返回策略胜率；最新代码仍未区分 reason/path。
证据: `("C++ 三层决策", VALUE_KIND_EXACT)`；上游 cpp/mscore.cpp:3330-3331 返回 reason=win_rate 与 maxv，utils/probability.py:600-622 将其拼成同一 exp 前缀。最新探针输出 `CPLUS_WINRATE exact_safety`。
影响面: 帮助候选详情、弹窗和悬浮预览把策略胜率误标为精确安全概率，违反本批核心语义约束并误导选格；现有自动点击逻辑没有被该标签直接改变。
严重度: HIGH
置信度: HIGH（上游路径与直接探针一致）
修复: 【改进点】在 utils/probability.py 的 pos_dict_list 写入结构化 value_kind（由 reason/path 决定），GUI 优先消费该字段；unknown 回退而不是统一前缀猜精确解。tests/test_ui_preview_popup.py 增加 C++ certain_safe/win_rate/random 三分支反例，并同步原弹窗“不是雷的概率”文案。

位置: main.py:737
问题: P0：用 id(button) 永久去重，reset_btn_list 销毁旧按钮后，新按钮可复用 id，被误判为已挂接，导致 hover/focus 过滤器缺失。
证据: `id(button) in self._preview_buttons`；main.py:898-932 每次删除并重建帮助按钮，全文件没有 clear/discard/remove 旧 id。真实 Qt 探针一轮重建输出 `OLD_IDS 480 NEW_IDS 480 REUSED_IDS_BLOCKED 370`。
影响面: 第二次及以后获取建议时大量候选不显示预览；不是重复绑定，而是生命周期导致的漏绑定。新增 focus 过滤器同样被此条件挡住。
严重度: HIGH
置信度: HIGH（真实对象重建可复现）
修复: 【改进点】按对象上的 `_preview_filter`/`_preview_focus_filter` 判断，或 destroyed 时删除对应记录；不要用长期 id 集合。测试执行 attach→reset_btn_list→update_btn_list→Enter/FocusIn 两轮，断言每轮恰一组过滤器且浮层可见。

位置: main.py:186
问题: P1：自动决策卡以 best_conf 与 prob[best] 数值是否相等推断语义；胜率与安全概率是不同量但可恰好相等，等值时误标 exact_safety。
证据: `if abs(cell_value - value) <= _BEST_CONF_EPS:` 后直接返回 EXACT；探针输入 best_conf=0.5、prob[best]=0.5 输出 `EQUAL_VALUES exact_safety`。上游 utils/probability.py:1013 与 :1047 明确二者分别可为胜率和安全概率。
影响面: 自动决策卡仍存在把策略胜率叫安全概率的路径；新函数已接线，但没有消除歧义，注释“只用真实字段比对，不猜”并不成立。
严重度: MEDIUM
置信度: HIGH（确定性反例）
修复: 【改进点】由 _last_heatmap 发射侧增加 decision_value_kind，和 prob_value_kind 分开；GUI 无来源字段时标 unknown，不能靠数值相等推断来源。增加等值、相近容差、best 不在 prob 三类测试。

位置: main.py:597
问题: P1：自动页“候选详情（最多 5 项）”只创建，不填充也没有空态说明。
证据: `self.candidate_list = QtWidgets.QListWidget(self.candidate_card)`；唯一填充调用在 main.py:1075，目标为 help_candidate_list，而不是 candidate_list。
影响面: 自动页候选信息承诺未实现，空白卡片长期占布局空间。
严重度: MEDIUM
置信度: HIGH
修复: 【改进点】本批若无自动 candidates 数据则收起卡片或显示“暂未提供逐项候选”；若保留则新增类型化候选快照并填充，不将异类 prob/best_conf 任意混排。

位置: ui/preview_popup.py:482
问题: P1：共享浮层对任何按钮 Leave 都只按 pinned 判断；从候选按钮移动到浮层时，源按钮 Leave 会先关闭未固定浮层，使固定/关闭按钮难以用真实鼠标触达。
证据: `self.popup.hide_if_unpinned()`；没有 owner 追踪，也没有进入 popup 后抑制源 Leave 的逻辑。新增 _PreviewFocusFilter 的 FocusOut 也调用同一关闭逻辑。
影响面: hover 文本可读，但浮层内基础交互可能不可用；offscreen 单按钮 Leave 测试不覆盖 source→popup 的真实事件顺序。
严重度: MEDIUM
置信度: HIGH（代码路径确定；真实窗口鼠标走查待做）
修复: 【改进点】记录 owner，在 Leave/FocusOut 后延迟至下一事件循环检查光标/焦点是否位于 popup，再决定是否关闭；新增 source→popup、source→other、other→source 的真实派发测试。

位置: tests/test_ui_preview_popup.py:268
问题: P1：Esc 测试直接调用 eventFilter，没有通过 QApplication 投递，无法验证应用级过滤器安装与真实键盘链；生命周期和 C++ 反例也未覆盖。
证据: `handled = self.popup.eventFilter(self.popup, ev) `；现有 attach 测试没有 reset 后重建，C++ 样例只有 certain_safe。
影响面: 47 项定向测试全部通过仍可漏掉本报告 P0/P1；这是测试鉴别力不足，不单独证明生产 Esc 已坏。
严重度: MEDIUM
置信度: HIGH
修复: 【改进点】用 QApplication.sendEvent/postEvent + processEvents 测 Esc；每用例清理窗口/事件队列。为 P0/P1 增加修复前必红、修复后通过的回归，不能仅添加正向 fixture。

位置: review/ui-migration-implementation.md:67
问题: P1：实施报告将 id 去重、语义分类等标为完成，但最新代码仍有上述反例，验收声明过度。
证据: `self._preview_buttons`
影响面: 若按报告直接验收，会漏掉第二轮帮助失效与胜率误标；“194 tests OK”只证明已有断言通过。
严重度: MEDIUM
置信度: HIGH
修复: 【改进点】修复后同步报告及变更行号，显式登记生命周期、分类反例、真实鼠标未覆盖项；不把已知风险写成可接受差异。

位置: ui/preview_popup.py:108
问题: P2：transitions 非 list 时会被静默迭代/丢弃，格式错误最终显示“暂无真实分布”，掩盖桥接错误。
证据: `for item in transitions or []:`
影响面: 当前生产恒传 None，不影响当前主流程；后续真实 transitions 接入时会把坏数据伪装为空态。
严重度: LOW
置信度: HIGH
修复: 【改进点】入口只允许 None/list，非法类型显示“转移数据格式错误”并记 warning；补非法 dict 输入的必红测试。

位置: ui/preview_popup.py:304
问题: P2：note_label 在没有 payload 的构造阶段就陈述 solver 不产出分支概率，初始瞬态与展示边界不一致。
证据: `self.note_label = QtWidgets.QLabel(NO_TRANSITION_HINT, self)`
影响面: 文案一致性问题，不影响已核实的无合成概率逻辑。
严重度: LOW
置信度: HIGH
修复: 【改进点】初始使用中性说明，set_payload 后再按真实/缺失状态切换。

位置: ui/preview_popup.py:59
问题: P2：describe_value/transitions_text 不拒绝 NaN/Inf 或越界概率，会显示 nan%、inf% 或超过 100%。
证据: `pct = f"{float(value) * 100:.2f}%"`
影响面: 正常 solver 输出通常位于 [0,1]，属未来桥接/异常 payload 边界，不是当前主流程回归。
严重度: LOW
置信度: HIGH
修复: 【改进点】math.isfinite + 范围校验，异常显示“无效估值”并记录；真实 transitions 检查总和容差，不在 UI 中归一化掩盖错误。

### Open Questions（低置信度发现，呈现不阻塞）

- 无低置信度 CRITICAL/HIGH 阻塞项。Windows 真实 Qt.Tool 的焦点、置顶、多屏 DPI 与鼠标事件顺序仍需人工验收。

### 正面观察

- main.py:703 与 :719 传 transitions=None，ui/preview_popup.py:318-324 显示“暂无真实状态转移分布”，未把原型合成概率带入生产。
- 最新 main.py:1022 已把首屏帮助扫描写入 `_last_cell_value`；旧版“帮助棋盘必然为空”的发现已撤销，不再计入问题数。
- 最新 main.py:372 与 :760-767 已挂接切页/主窗口关闭清理；旧版“无关闭通道”的发现已撤销。
- ui/preview_popup.py:420-434 成对安装/摘除应用级过滤器；PreviewHoverFilter 由 button parent 与属性持有，未发现每 hover 新建 popup 的泄漏。
- ui/preview_popup.py:157-183 边缘定位为纯函数，含左右翻转、四边夹取测试。
- main.py 保持运行期注入，git diff 对 ui/window.py/ui/window.ui 为空，QStackedWidget 三页和既有 Solver 信号结构未替换。
- 本席旧快照全量 194 tests OK；最新快照定向 47 tests OK。两者分别标注，不把旧全量读数冒充最新全量验证。

### 验证与未覆盖项

- 已运行：`QT_QPA_PLATFORM=offscreen D:\anaconda3\envs\py310\python.exe -m unittest tests.test_ui_preview_popup tests.test_ui_heatmap` → 最新定向 47 tests OK。
- 已运行：旧快照全量 `python -m unittest discover -s tests -t .` → 194 tests OK；main.py 后续漂移，最新全量需复跑命令同上。
- 已运行：旧快照 py_compile 三个 Python 文件 exit 0；最新 main.py 语法由定向导入/测试覆盖。未把 py_compile 冒充独立静态类型检查。
- 已运行：真实 Qt 重建探针 → 370/480 新按钮 id 与旧集合重合；C++ win_rate 分类 → exact_safety；best_conf=prob[best] 的混合语义反例 → exact_safety。
- skipped：真实扫雷窗口、真实鼠标、多屏/DPI/字体与真实 Esc；原因=offscreen 不具备桌面输入/窗口管理器。复跑=`D:\anaconda3\envs\py310\python.exe main.py`，走两轮帮助、自动→帮助、源格→浮层、固定/关闭/Esc。
- skipped：QWidget.grab 像素比对与视觉对比度；原因=offscreen 缺字体目录且不能替代真实窗口，复跑同上并检查 100%/125%/150% DPI。
- 未评审：其他算法、配置、C++、bench 改动；仅作只读调用方证据，不纳入分母。

### 建议

裁定: REQUEST CHANGES。两项 P0 均为高置信度、可复现、已定位问题，不能因测试全绿批准。
覆盖状态: complete（指定 4 项均 reviewed；真实桌面验证另列 skipped）。
优先顺序：发射侧分别提供 decision_value_kind/prob_value_kind 并修帮助 reason 映射；修按钮生命周期；再补自动候选与真实事件测试。未修改任何被评审源文件。