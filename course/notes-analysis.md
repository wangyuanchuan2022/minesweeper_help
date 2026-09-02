# code2course 工作笔记（内部，不交付）

档位：L3 精讲档（用户确认）。读者：工程师/接手者。旧课已归档至 course/archive-0831/。

## 一、隐喻分配表（全课唯一，先查再用）

| 概念 | 隐喻 | 模块 |
|---|---|---|
| cell_value 矩阵 | 棋盘的数字沙盘副本 | m2 |
| 模板匹配 compare_img | 拿 25 张身份证逐一比对，最像的那张说了算 | m1 |
| _TEMPLATE_LAYOUT 结构 | 相册分格抽屉（每格数字一个抽屉，抽屉里放变体） | m1 |
| flood-fill 大面积更新 | 多米诺骨牌连锁倒下 | m3 |
| _cell_changed 变更检测 | 像素指纹比对 | m3 |
| number0 两条规则 | 清点包围圈 | m4 |
| number_3_1 集合推理 | 两份名单互相勾稽 | m5 |
| distanceTransform | 涟漪从数字格向外扩散 | m6 |
| clicks/clicks9 边缘与腹地 | 海岸线与腹地 | m6 |
| checked 局面缓存 | 错题本（算过的局面记下来，翻到就抄） | m6 |
| part_solve 回溯 | 走迷宫时留绳，走不通收绳回头 | m7 |
| win_rate MD5 记忆化 | 给局面发身份证号，进门先查登记簿 | m8 |
| process_bigger_situation | 大场面抽样统计（不数全票，按票站抽样） | m8 |
| OKLCH 概率配色 | 温度计配色 | m9 |
| FailSafeException | 拉闸急停（鼠标甩角落=拍下急停按钮） | m10 |
| 信号节流 | 节水龙头（100ms 内只放一次水） | m10 |
| data.json 胜率校准 | 天气预报员对账本（说 88% 那天，真下了几成） | m10 |
| 校准取点 bx/by | 图纸描点对齐 | m11 |
| cell_width 整数校验 | 尺子刻度必须落在整数格上 | m11 |
| make_solver 跳过 __init__ | 绕过大门的侧门钥匙（不读配置不载图，直接摆好实验台） | m12 |
| main.spec 过时 | 搬了家没改门牌 | m12 |
| is_play 双模式 | 一套引擎两个驾驶舱（自动驾驶/导航建议） | m9 |
| 零隐喻直讲 | rescan_after_click 增量重扫、10/11 标记、坐标换算、深度剪枝 | 各处 |

## 二、术语定义表（核心定义全课一致）

| 术语 | 核心定义 | 上下文补充 |
|---|---|---|
| cell_value | (h+2)×(w+2) 的 int32 矩阵，程序眼里的整个棋盘 | 0=已开空格、1-8=数字、9=未开、10=已标雷、11=已确认安全（仅帮助模式用） |
| 边框 padding | 矩阵四周一圈 0，让邻域循环不用写越界判断 | cell_around 的 m/n 范围检查仍保留 0<=m<=w+1 |
| clicks | 距离数字格 ≤1.5 的未开格（受约束，可枚举） | m6 起用 |
| clicks9 | 距离数字格 >1.5 的未开格（无约束腹地） | m6 起用 |
| limit | cfg.json 的枚举复杂度上限，每加 1 计算时间翻倍 | 默认 80 |
| limitation | 决策路由量：len(clicks9)*0.8 + log2(total)，≤6 走胜率搜索 | m8 |
| checked | dict 缓存：组坐标 tuple → (方案列表, canopen)，一局内复用 | m6 |
| 模板匹配 | cv.matchTemplate TM_SQDIFF_NORMED 归一化平方差，越小越像 | m1 |
| 距离变换 | cv.distanceTransform DIST_L12，每格到最近数字格的距离 | m6 |
| MRO | 方法解析顺序：Solver→Vision→Deduction→Probability→AutoPlayThread→QThread | m12/封面导览 |
| pos_dict_list | 帮助模式的推荐结果列表（pos/confidence/is_mine/is_best/exp） | m9 |
| is_play | True=自动模式（直接点击），False=帮助模式（只标记 11+发信号） | m4 起 |
| 信号与槽 | Qt 跨线程通信：工作线程 emit，主线程槽函数安全更新 GUI | m10 |
| 概率校准 | data.json 按「标称置信度%」分桶统计实际胜率，验证概率引擎是否诚实 | m10 |

## 三、测验溯源对照表（逐题登记，交付前复核）

| 测验 ID | 考察知识点 | 讲解位置 |
|---|---|---|
| m1-q1 | 模板变体存在原因（分辨率/主题）与 _TEMPLATE_LAYOUT 行结构 | m1 翻译块 pair-m1-layout + pair-m1-cache |
| m1-q2 | compare_img 的 no_10 参数为何把 10 塌缩成 9 | m1 翻译块 pair-m1-compare |
| m1-b1（赌注） | cv.error 被 pass 吞掉后 result 保持 100 的后果 | m1 赌注 + pair-m1-compare |
| m2-q1 | 坐标换算链：窗口坐标→屏幕坐标（ClientToScreen 用 GetWindowRect 近似） | m2 翻译块 pair-m2-grab |
| m2-q2 | _slice_cell 为何只裁 5/9×7/9 而不是整格 | m2 翻译块 pair-m2-slice |
| m3-q1 | rescan_after_click 为何扫所有 9 而非固定 5×5（flood-fill） | m3 翻译块 pair-m3-rescan + steps 动画 |
| m3-q2 | _cell_changed 阈值 3.0 的 trade-off | m3 翻译块 pair-m3-changed + 赌注 |
| m4-q1 | number0 两条规则的方向（全雷/全安全） | m4 翻译块 pair-m4-number0 |
| m4-q2 | is_play=False 时为何标 11 而不点击 | m4 翻译块 pair-m4-number0 后半 |
| m4-b1（赌注） | ValueError 在 play() 里 continue 的后果（重扫而非崩溃） | m4 赌注 + pair-m4-raise |
| m5-q1 | x1-x2 == len(x_set) 时 x_set 全雷、z_set 全安全 | m5 翻译块 pair-m5-sets + 沙盘 |
| m5-q2 | 为什么逐对比较 5×5 邻域（i-2..i+2）而非全盘 | m5 翻译块 pair-m5-loop |
| m6-q1 | distanceTransform 1.5 阈值的含义（L12 距离） | m6 翻译块 pair-m6-dist |
| m6-q2 | checked 缓存失效时机（组内有交集就 pop） | m6 翻译块 pair-m6-cache |
| m7-q1 | part_solve 递归出口（len(clicks)==1 的双分支） | m7 栈塔 + 翻译块 pair-m7-base |
| m7-q2 | 剪枝条件 value[j,i] > num9+num10 or < num10 的意义 | m7 翻译块 pair-m7-prune |
| m8-q1 | win_rate 记忆化键（MD5 of >9→9 的矩阵） | m8 翻译块 pair-m8-hash |
| m8-q2 | limitation≤6 与 total>10000 两条分界线的取舍 | m8 翻译块 pair-m8-route + 沙盘 |
| m9-q1 | 帮助模式 pos_dict_list → 按钮颜色分级（OKLCH） | m9 翻译块 pair-m9-color |
| m9-q2 | info 弹窗的推理文本从哪来（exp 字段拼接） | m9 翻译块 pair-m9-exp |
| m10-q1 | sum2==sum3 无进展判断才触发概率引擎 | m10 翻译块 pair-m10-loop |
| m10-q2 | FailSafeException 与 win32ui.error 的收场差异 | m10 翻译块 pair-m10-except |
| m11-q1 | cell_width 非整数（|diff|≥0.11）时为何拒绝 | m11 翻译块 pair-m11-width |
| m11-q2 | 校准流程：鼠标静止 0.8s（count>=8×0.1s）自动采样 | m11 翻译块 pair-m11-mouse |
| m12-q1 | main.spec 引用旧平铺结构（utils.py 已不存在） | m12 翻译块 pair-m12-spec |
| m12-q2 | 加新数字模板变体要改哪些文件（image/ + _TEMPLATE_LAYOUT） | m1 + m12 结业题 |
| m12-q3（结业） | 加「复盘导出」功能要动哪些文件（综合） | 全课 |

## 四、3a 事件链（自动模式主循环，m10 timeline 素材）

auto_play.clicked → auto_play_func()（main.py L681）→ QInputDialog 局数 → minesweeper_run（mm0.py L25：FindWindow→Popen→ShowWindow→SendKeys %）→ set_args(value) → start() → QThread.run（solver.py L131）→ reload() → play(limit)（solver.py L154）：
1. 点中心格开局（L169-171）+ sleep(0.01)（setting.sleep）
2. while True（L183）：
   - 胜利窗口 FindWindow("游戏胜利")（L189）→ sleep 1.2 → locate_exit 点击 → 重置棋盘 → sleep 1.0/0.5 → 点击中心 → checked={} → win/total 计数
   - 失败窗口（L218）同上
   - ok.png 模板匹配（L245）→ 点击 ok → 点击 exit（整屏截图复用）
   - win.bmp / lose.bmp（L256/L286）同上四分支结构几乎相同（HANDOFF 2.7 已知重复）
   - complete_scan(cell_value, True)（L314，vision.py L157）
   - mine_clear1（L317，ValueError → continue 重扫）
   - mine_clear3_1（L320）
   - sum3==sum2 无进展 → number5_1（L324，ImportError → pass）
3. finally: _flush_stats()（data.json 落盘）
真实耗时锚点：胜/负后 sleep 1.2+1.0+0.5+0.1s；扫描间隔无 sleep（受 pyautogui.PAUSE=0.01 约束的点击有 0.01s 节流）。

## 五、3b 数据变形链（m2 数据流动画 + m2 洋葱素材）

屏幕像素 → ImageGrab.grab(bbox) PIL → np.array(RGB) → cv.cvtColor RGB2BGR → self.img ndarray(H*cell_width, W*cell_width, 3) → _slice_cell 裁 (2*(7/9*cell_width//2), 2*(5/9*cell_width//2), 3) 视图 → cv.matchTemplate 25 模板 → result(11,3) SQDIFF → np.argmin → 模板行号（0-10）→ cell_value[y,x] → 推理改写 10/11 →（自动）pyautogui.click(bx+m*cell_width, by+n*cell_width) /（帮助）pos_dict_list → 按钮 stylesheet OKLCH。
被丢弃：颜色通道（只留差平方和）、格子图像本身、no_10=True 时 10→9 塌缩、抓全屏 vs 只抓棋盘（_locate 用整屏）。
洋葱层次（最终→核）：GUI 按钮/点击坐标 → cell_value 数值 → 模板行号 → SQDIFF 矩阵 → 格子像素切片 → 整盘 BGR 截图 → 屏幕像素。

## 六、3c 错误边界（赌注/测验素材）

可恢复：① number0 ValueError → play() continue → complete_scan 重扫（count 累计）；② count>=3 → warning_signal 弹窗 return；③ KeyError（part_solve 组合）→ 全盘重扫 return；④ len(_res)==0 → 重扫 return；⑤ compare_img 内 cv.error → pass（result 留 100=不匹配，静默）；⑥ try_solve except Exception → pass（res=0，静默）；⑦ _flush_stats OSError → logger.warning（静默降级）；⑧ _load_stats OSError/ValueError → stats_data={}（静默）；⑨ logger 文件不可写 OSError → pass 退化纯控制台。
不可恢复：FailSafeException（用户甩鼠标到角落=拉闸）→ 直接 return；win32ui.error（窗口没了）→ return。
静默失败点（重点教学）：⑤⑥⑦——吞掉后行为如常，排查困难。

## 七、3d 配置扩展面（m11 沙盘素材）

cfg.json 字段三分类：
- 必选：w/h/a/bx/by/cell_width/path/win_name
- 可调：limit（枚举上限，+1 → 时间×2）、speed（进度条速度）
- 派生：p=a/(w*h)、screenshot_w=int(cw*5/9)、screenshot_h=int(cw*7/9)
setting.py：sleep=0.01（点击节奏）、num_workers=8（声明未用——技术债）、win_name。
配置加载分散点（HANDOFF 2.4）：main.py L78/L366/L762/L961/L924/L937、solver.py __init__/reload、setting.py 导入时（崩溃风险）。
扩展点：新模板变体=加 bmp+_TEMPLATE_LAYOUT 行；新弹窗=加 image 文件+play() 分支；换游戏=path+win_name；调整难度=w/h/a。
分叉沙盘候选：number5_1 的三路决策（len(clicks)==0 随机 / limitation<=6 胜率 / process_bigger_situation）——参数：limitation 值、len(clicks)。

## 八、真实数字清单（§10 图表用，全部可溯源）

- 目录文件数（清点 2026-09-01）：utils/ 10 py、tests/ 6 py、image/ 33 文件（25 格子模板+ok/exit/win/lose 4 弹窗+temp/save/example 4 工作文件）、icons/ 15、ui/ 5 py（含 bg_rc.py）、根 5 py（main/setting/color/logger/test_accuracy）
- 行数（read 工具 LF 计数，权威）：main.py 1095、probability.py 986、solver.py 430、window.py 527、deduction.py 274、color.py 267、combinatorics.py 246、mm0.py 188、edit_setting.py 164、vision.py 186、screenshot.py 127、clicking.py 45、misc.py 31、util.py 44、logger.py 68、setting.py 8
- 测试：71 个 test 方法（Select-String 'def test_' 计数），5 个测试文件 + helpers
- data.json（读取统计）：48 桶、共 13040 局（胜 11394 / 负 1646）、整体胜率 87.3%；100% 桶 2844/2844=100%；95% 桶实际 93.9%；88% 桶实际 87.8%（1235win/172lose）；79% 桶实际 80.2%；50% 桶实际 51.3%
- 常量锚点：distanceTransform 1.5（probability.py L67）、_cell_changed 3.0（vision.py L150）、节流 100ms（solver.py L90）、depth_limit 200/len(clicks)（probability.py L652）、limitation≤6（L266）、total>10000（L404）、stats 落盘每 20 次（solver.py L352）、日志 5MB×3 滚动（logger.py L42）、timer 50ms（main.py L174）、up_pgb_timer 100ms（main.py L245）、模板 25 张（_TEMPLATE_LAYOUT 清点）、MRO（HANDOFF 2.3 验证）
- README.md L7 声明「高级难度胜率 46%」（出处=README，标注）

## 九、拼接清单（§8.0，逐段回填）

行号锚点修正（已 grep 核实 2026-09-01）：probability.py：number5_1 L30 / try_solve L366 / process_bigger_situation L401 / win_rate L624-747 / open_num5x5 L749 / part_solve_single L769 / part_solve L847-964；deduction.py：number_3_1 L107-241 / mine_clear3_1 L243-253 / get_set_1 L255 / get_set L264；solver.py：play L154-337 / help L339+；main.py：update_image L461 / update_btn_list L532-587 / info L588-607 / click_all_func L627-656 / auto_play_func L681 / get_int_bx L844 / get_pos_1_func L866 / get_pos_2_func L875-902 / ScreenShot L966。每文件测试方法数：test_combinatorics 34 / test_solver_core 21 / test_scan 12 / test_probability 4 = 71。data_time.json 现代码无引用（遗留数据，勿用作图表锚点）。data.json 校准桶（实际胜率已核算）：100%桶 2844/0=100%、95%桶 399/26=93.9%、92%桶 415/38=91.6%、88%桶 1235/172=87.8%、84%桶 102/13=88.7%、80%桶 1195/295=80.2%、50%桶 385/366=51.3%。

并行分工（用户 2026-09-01 指示开启并行；模型 thu-llm/GLM-5.3；错峰 15s 启动）：工人A=m5+m6；工人B=m7+m8；工人C=m9+m10；工人D=m11+m12。主会话已完成 m0-m4，负责监控心跳、质检、组装。

| 文件 | 段 | 模块 | 组件 | 档位核对 | 状态 |
|---|---|---|---|---|---|
| segments/00-m0.html | 1 | #m0 封面 | hero-stage+目录职责图 bars+技术栈标签 | — | ✅ |
| segments/01-m1.html | 2 | #m1 模板仓库 | 翻译块×3+steps+赌注+测验×2 | 译块≥3 图≥2 测≥2 | ✅ |
| segments/02-m2.html | 3 | #m2 屏幕到棋盘 | 探照灯+翻译块×3+洋葱+测验×2 | 同上 | ✅ |
| segments/03-m3.html | 4 | #m3 增量重扫 | 翻译块×2+steps+赌注+测验×2 | 同上 | ✅ |
| segments/04-m4.html | 5 | #m4 单格判定 | 翻译块×3+steps+赌注+测验×2 | 同上 | ✅ |
| segments/05-m5.html | 6 | #m5 双格判定 | 翻译块×3+steps+分叉沙盘+测验×2 | 同上 | ✅ 工人A+主会话补沙盘 |
| segments/06-m6.html | 7 | #m6 区域划分 | 翻译块×3+steps+bars(真实对局构成)+测验×2 | 同上 | ✅ 工人A+主会话补bars |
| segments/07-m7.html | 8 | #m7 回溯枚举 | 翻译块×4(含A生成器)+栈塔+steps(里程表)+测验×2 | 同上 | ✅ 工人B+主会话补steps |
| segments/08-m8.html | 9 | #m8 胜率与大局面 | 翻译块×3+沙盘+bars(depth预算)+测验×2 | 同上 | ✅ 工人B+主会话补bars |
| segments/09-m9.html | 10 | #m9 帮助模式 | 探照灯+翻译块×4+steps+测验×2 | 同上 | ✅ 工人C |
| segments/10-m10.html | 11 | #m10 自动模式 | 翻译块×5+timeline+bars校准+赌注+测验×2 | 同上 | ✅ 工人C |
| segments/11-m11.html | 12 | #m11 设置校准 | 翻译块×4+沙盘+steps(校准三步)+测验×2 | 同上 | ✅ 工人D+主会话补steps |
| segments/12-m12.html | 13 | #m12 工程化 | 翻译块×3+bars测试分布+steps(MRO查找)+测验×3(含结业) | 同上 | ✅ 工人D+主会话补steps |

溯源表勘误：m7 实际 4 个翻译块（第 4 块 A 生成器 combinatorics.py L180-L212，任务书许可的可选块）；m8-q2 的讲解位置是「三路沙盘 + pair-m8-bigger」（非 pair-m8-route，该 id 不存在）；m5-q1 讲解位置为 pair-m5-sets + steps 第 3→4 帧（任务书把沙盘改成了 steps，data-why 双锚点）。m6 bars 数据源：tests/data/test_33.json（test.json 与 show_fig.json 均为 1866 字符截断的坏文件，不可用）。主会话终检时给 m1/m3/m4/m5/m6/m7/m8/m11/m12 共补 9 个引擎组件（m1 bars 模板库存 / m3 pair-m3-complete+调度沙盘 / m4 is_play 沙盘 / m5 勾稽沙盘 / m6 真实对局 bars / m7 里程表 steps / m8 depth 预算 bars / m11 校准三步 steps / m12 MRO 查找 steps），并修复 7 处未定义 CSS 变量（--muted→--text-muted、--bg-2→--accent-soft/--bg-sidebar）与 m4 的 6 处 #FFFDF8 字面色。终检数据：翻译块 41 / 测验+赌注 29 / 逐字 84 块全过 / validate --mask 全绿 / 成品 453.4KB。
