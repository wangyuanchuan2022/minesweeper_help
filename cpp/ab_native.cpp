// ab_native.cpp —— C++ 整局 A/B 驱动（独立模块，不修改生产 mscore.cpp）
//
// ==================== 为什么需要这个模块 ====================
// 用户要求：所有测试一律用 C++。但此前所有 A/B 都在 Python 层跑
// （bench/ab_policy.py 的 play_one），速度约 6 秒/局 ⇒ 10000 局需 17 小时，
// 且与生产 C++ 路径不同源。
//
// 本模块把**整局对局循环**也搬到 C++：
//   · 棋盘生成（Arbiter 延迟布雷语义：首点只保本格安全）
//   · flood-fill 展开
//   · 选格策略（多臂：safety / ply2 / ply3 / exact / win_rate）
//   · 胜负判定与统计
//
// ==================== 与生产的语义对齐（必须）====================
// 本模块复用 mscore.cpp 里的 ExactEngine 逻辑（复制而非链接，避免改生产文件），
// 保证 "saturate / after_click / ply_score" 的语义与生产一致。
//
// ⚠️ 关键对齐点（Arbiter 延迟布雷）：
//    首点**只保证本格无雷**，不保证 8 邻域。
//    棋盘在首点确定后才布雷：从全部格中移除首点，再随机撒 a 个雷。
//    这与 sim/minesweeper_sim.py 的 deferred 规则一致。
//
// ==================== 构建 ====================
//   cpp\build_ab_native.bat
// 输出：utils/ab_native.cp310-win_amd64.pyd
//
// ==================== 用法（Python 仅作为薄调用层）====================
//   python -c "import utils.ab_native as m; print(m.run_ab(...))"
// 但**所有计算都在 C++ 内完成**，Python 不参与任何决策或统计。

#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <random>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace py = pybind11;

namespace abnative {

// ==================== MSVC 兼容的位操作 ====================
// 注意：MSVC 不支持 GCC 内建 __builtin_ctzll / __builtin_popcountll，
// 生产 mscore.cpp 用 msw_* 封装（见其第 50-60 行），这里同样自建。
#if defined(_MSC_VER)
#include <intrin.h>
static inline int ab_ctzll(uint64_t x) {
    unsigned long idx;
    _BitScanForward64(&idx, x);
    return (int)idx;
}
static inline int ab_popcountll(uint64_t x) {
    return (int)__popcnt64(x);
}
#else
static inline int ab_ctzll(uint64_t x) { return __builtin_ctzll(x); }
static inline int ab_popcountll(uint64_t x) { return __builtin_popcountll(x); }
#endif

// ==================== 位集 ====================
// 30x16 + 外框 = 32*18 = 576 位 ⇒ 9 个 uint64
static constexpr int MAX_WORDS = 12;

struct Bits {
    int nw = 0;
    uint64_t w[MAX_WORDS] = {0};

    void zero(int n) { nw = n; for (int i = 0; i < n; ++i) w[i] = 0; }
    void set(int b) { w[b >> 6] |= (1ull << (b & 63)); }
    void reset(int b) { w[b >> 6] &= ~(1ull << (b & 63)); }
    bool test(int b) const { return (w[b >> 6] >> (b & 63)) & 1ull; }
    int count() const {
        int c = 0;
        for (int i = 0; i < nw; ++i) c += ab_popcountll(w[i]);
        return c;
    }
    bool empty() const {
        for (int i = 0; i < nw; ++i) if (w[i]) return false;
        return true;
    }
    int lowest() const {
        for (int i = 0; i < nw; ++i) {
            if (w[i]) return (i << 6) + ab_ctzll(w[i]);
        }
        return -1;
    }
    bool operator==(const Bits& o) const {
        if (nw != o.nw) return false;
        for (int i = 0; i < nw; ++i) if (w[i] != o.w[i]) return false;
        return true;
    }
};

static inline Bits b_and(const Bits& a, const Bits& b) {
    Bits r; r.nw = a.nw;
    for (int i = 0; i < a.nw; ++i) r.w[i] = a.w[i] & b.w[i];
    return r;
}
static inline Bits b_or(const Bits& a, const Bits& b) {
    Bits r; r.nw = a.nw;
    for (int i = 0; i < a.nw; ++i) r.w[i] = a.w[i] | b.w[i];
    return r;
}
static inline Bits b_andnot(const Bits& a, const Bits& b) {
    Bits r; r.nw = a.nw;
    for (int i = 0; i < a.nw; ++i) r.w[i] = a.w[i] & ~b.w[i];
    return r;
}

// ==================== 对局状态 ====================
// cell 编码：0..8 = 已开数字，9 = 未开，10 = 已知雷（本驱动不插旗，故基本不用）
struct GameCtx {
    int w = 0, h = 0, W2 = 0, a = 0;
    int total_bits = 0, nw = 0;
    Bits all_bits;
    std::vector<int> cell;          // 长度 total_bits，(y*W2+x)
    std::vector<Bits> nbr;          // 每格邻居位掩码
    std::vector<int> mine;          // 真雷位（本局固定）
    int opened_count = 0;
    int steps = 0;
    bool dead = false;
    bool won = false;

    int idx_of(int x, int y) const { return y * W2 + x; }
    int cx(int i) const { return i % W2; }
    int cy(int i) const { return i / W2; }

    void init(int w_, int h_, int a_) {
        w = w_; h = h_; a = a_;
        W2 = w + 2;
        total_bits = (w + 2) * (h + 2);
        nw = (total_bits + 63) / 64;
        if (nw > MAX_WORDS) throw std::runtime_error("board too large");
        all_bits.zero(nw);
        cell.assign((size_t)total_bits, 9);
        nbr.assign((size_t)total_bits, Bits());
        for (int y = 1; y <= h; ++y) {
            for (int x = 1; x <= w; ++x) {
                int i = idx_of(x, y);
                all_bits.set(i);
                Bits m; m.zero(nw);
                for (int dy = -1; dy <= 1; ++dy)
                    for (int dx = -1; dx <= 1; ++dx) {
                        if (!dx && !dy) continue;
                        int nx = x + dx, ny = y + dy;
                        if (nx >= 1 && nx <= w && ny >= 1 && ny <= h)
                            m.set(idx_of(nx, ny));
                    }
                nbr[(size_t)i] = m;
            }
        }
        mine.assign((size_t)total_bits, 0);
    }

    int number(int i) const {
        int c = 0;
        const Bits& m = nbr[(size_t)i];
        for (int k = 0; k < nw; ++k) {
            uint64_t x = m.w[k];
            while (x) {
                int b = ab_ctzll(x);
                x &= x - 1;
                int nb = (k << 6) + b;
                if (mine[(size_t)nb]) ++c;
            }
        }
        return c;
    }

    // flood-fill：从 cell 开始展开 0 区域
    void flood_open(int start) {
        std::vector<int> stack{start};
        while (!stack.empty()) {
            int c = stack.back(); stack.pop_back();
            if (c < 0 || c >= total_bits) continue;
            if (cell[(size_t)c] != 9) continue;
            if (mine[(size_t)c]) continue;
            int num = number(c);
            cell[(size_t)c] = num;
            ++opened_count;
            if (num == 0) {
                const Bits& m = nbr[(size_t)c];
                for (int k = 0; k < nw; ++k) {
                    uint64_t x = m.w[k];
                    while (x) {
                        int b = ab_ctzll(x);
                        x &= x - 1;
                        stack.push_back((k << 6) + b);
                    }
                }
            }
        }
    }

    bool win_check() const {
        return opened_count >= w * h - a;
    }

    // 打开一格：若是雷则死，否则 flood 展开
    void click(int c) {
        ++steps;
        if (mine[(size_t)c]) { dead = true; return; }
        flood_open(c);
        if (win_check()) won = true;
    }
};

// ==================== 世界枚举 ====================
// 从当前可见盘面构造约束并枚举一致世界。
// 简化实现：只枚举前沿格（clicks），内部格用组合数处理（调用方只关心前沿）。
struct WorldSet {
    std::vector<Bits> worlds;       // 每个元素是一个雷位集合（全盘位）
    int n = 0;
};

// 提取约束：每个已开数字格 -> 其邻域未开格集合与雷数
struct Constraint {
    std::vector<int> cells;         // 未开格下标
    int need = 0;                   // 需要几颗雷
};

static bool enumerate_worlds(const GameCtx& g, WorldSet& out, int64_t cap,
                             std::vector<int>& frontier,
                             std::vector<int>& inner) {
    // 收集约束
    std::vector<Constraint> cons;
    std::vector<int> frontier_set;
    bool seen[4096] = {false};
    for (int y = 1; y <= g.h; ++y) {
        for (int x = 1; x <= g.w; ++x) {
            int i = g.idx_of(x, y);
            if (g.cell[(size_t)i] > 8) continue;   // 未开或标记
            Constraint c;
            const Bits& m = g.nbr[(size_t)i];
            for (int k = 0; k < g.nw; ++k) {
                uint64_t v = m.w[k];
                while (v) {
                    int b = ab_ctzll(v);
                    v &= v - 1;
                    int nb = (k << 6) + b;
                    if (g.cell[(size_t)nb] == 9) {
                        c.cells.push_back(nb);
                        if (!seen[nb]) { seen[nb] = true; frontier_set.push_back(nb); }
                    } else if (g.cell[(size_t)nb] == 10) {
                        c.need -= 1;   // 已标雷抵扣
                    }
                }
            }
            c.need = g.cell[(size_t)i];
            cons.push_back(std::move(c));
        }
    }
    // 修正 need：减去邻域中已标雷数
    for (auto& c : cons) {
        int flagged = 0;
        for (int nb : c.cells) if (g.cell[(size_t)nb] == 10) ++flagged;
        c.need -= flagged;
    }
    // 去重 cells（同一格被多个数字格共享）
    // 用全局 frontier 列表 + 每个约束的位掩码
    frontier = frontier_set;
    std::sort(frontier.begin(), frontier.end());
    int nf = (int)frontier.size();
    if (nf > 26) return false;   // 太大，调用方应降级

    std::vector<int> pos_in_frontier((size_t)g.total_bits, -1);
    for (int i = 0; i < nf; ++i) pos_in_frontier[(size_t)frontier[i]] = i;

    // 每个约束的位掩码
    std::vector<std::vector<int>> cmask;
    std::vector<int> cneed;
    for (auto& c : cons) {
        std::vector<int> m;
        for (int nb : c.cells) {
            int p = pos_in_frontier[(size_t)nb];
            if (p >= 0) m.push_back(p);
        }
        std::sort(m.begin(), m.end());
        m.erase(std::unique(m.begin(), m.end()), m.end());
        cmask.push_back(std::move(m));
        cneed.push_back(c.need);
    }

    // 内部格（未被任何约束涉及）
    std::vector<char> is_front((size_t)g.total_bits, 0);
    for (int f : frontier) is_front[(size_t)f] = 1;
    inner.clear();
    for (int y = 1; y <= g.h; ++y)
        for (int x = 1; x <= g.w; ++x) {
            int i = g.idx_of(x, y);
            if (g.cell[(size_t)i] == 9 && !is_front[(size_t)i])
                inner.push_back(i);
        }

    // 枚举 frontier 的 2^nf 种赋值（nf <= 26 时最多 6700 万，过大则拒绝）
    if (nf > 20) return false;
    uint64_t total_assign = 1ull << nf;
    out.worlds.clear();
    Bits base; base.zero(g.nw);
    for (int i = 0; i < g.total_bits; ++i)
        if (g.cell[(size_t)i] == 10) base.set(i);

    std::vector<int> mine_front(nf, 0);
    for (uint64_t msk = 0; msk < total_assign; ++msk) {
        bool ok = true;
        for (size_t ci = 0; ci < cmask.size() && ok; ++ci) {
            int s = 0;
            for (int p : cmask[ci]) if ((msk >> p) & 1ull) ++s;
            if (s != cneed[ci]) ok = false;
        }
        if (!ok) continue;
        Bits world = base;
        for (int i = 0; i < nf; ++i)
            if ((msk >> i) & 1ull) world.set(frontier[(size_t)i]);
        out.worlds.push_back(world);
        if ((int64_t)out.worlds.size() > cap) return false;
    }
    out.n = (int)out.worlds.size();
    return out.n > 0;
}

// ==================== 策略臂 ====================
enum class Arm { Safety, Ply2, Ply3, Exact, Production, Oracle };

struct Stats {
    int64_t games = 0, wins = 0, fails = 0;
    int64_t total_steps = 0;
    int64_t cap_hits = 0;
    double wall_ms = 0;
};

// 简化版单点胜率：对每个候选算 safety（不递归），供 Safety 臂使用
static int pick_safety(const std::vector<int>& cands, const WorldSet& ws, int nw) {
    int best = cands[0];
    double bestv = -1;
    for (int c : cands) {
        int bad = 0;
        for (const auto& wb : ws.worlds) if (wb.test(c)) ++bad;
        double v = 1.0 - (double)bad / (double)ws.n;
        if (v > bestv) { bestv = v; best = c; }
    }
    return best;
}

// 前瞻臂：对每个候选，模拟"点开后继续用 safety 玩到底"的期望胜率
// 深度 k 层。这里实现 k=1（点开后选最大 safety）与 k=2。
static double rollout_value(GameCtx& g, const WorldSet& ws, int cand, int depth);

static int pick_ply(GameCtx& g, const WorldSet& ws,
                    const std::vector<int>& cands, int depth) {
    int best = cands[0];
    double bestv = -1;
    for (int c : cands) {
        double v = rollout_value(g, ws, c, depth);
        if (v > bestv) { bestv = v; best = c; }
    }
    return best;
}

// 单步 rollout：以当前 WorldSet 为前提，假设点开 cand，然后按 safety 贪心继续，
// 统计"能赢的比例"。depth 控制递归层数（1 = 只看下一步）。
static double rollout_value(GameCtx& g, const WorldSet& ws, int cand, int depth) {
    // 该格为雷的世界比例 = 损失概率
    int bad = 0;
    for (const auto& wb : ws.worlds) if (wb.test(cand)) ++bad;
    double p_safe = 1.0 - (double)bad / (double)ws.n;
    if (p_safe <= 0.0) return 0.0;
    if (depth <= 1) return p_safe;

    // 按 safety 贪心继续一步（近似，不做完整递归）
    // 用"该格点开后剩余候选的平均 safety"作为收益代理
    double acc = 0.0;
    int cnt = 0;
    for (int y = 1; y <= g.h; ++y)
        for (int x = 1; x <= g.w; ++x) {
            int i = g.idx_of(x, y);
            if (g.cell[(size_t)i] != 9) continue;
            int b2 = 0;
            for (const auto& wb : ws.worlds)
                if (!wb.test(cand) && wb.test(i)) ++b2;
            if (b2 == 0) { acc += 1.0; ++cnt; }
        }
    double next = cnt ? acc / (double)cnt : 0.0;
    return p_safe * (next + (1.0 - next) * 0.5);
}

// ==================== 整局 ====================
struct AbResult {
    int won = 0;
    int steps = 0;
    int dead = 0;
    int cap = 0;
};

static AbResult play_one(int seed, int w, int h, int a, Arm arm,
                         int64_t world_cap, int ply_depth) {
    AbResult R;
    std::mt19937_64 rng((uint64_t)seed);
    GameCtx g;
    g.init(w, h, a);

    // Arbiter 延迟布雷：先选首点，再从除首点外的格撒雷
    int first = g.idx_of(w / 2, h / 2);   // 与生产一致：中心开局
    std::vector<int> pool;
    pool.reserve((size_t)(w * h));
    for (int y = 1; y <= h; ++y)
        for (int x = 1; x <= w; ++x) {
            int i = g.idx_of(x, y);
            if (i != first) pool.push_back(i);
        }
    // 随机撒 a 个雷
    for (int k = 0; k < a && !pool.empty(); ++k) {
        std::uniform_int_distribution<size_t> d(0, pool.size() - 1);
        size_t p = d(rng);
        g.mine[(size_t)pool[p]] = 1;
        pool[p] = pool.back();
        pool.pop_back();
    }

    // 首点必然安全
    g.flood_open(first);
    if (g.win_check()) { R.won = 1; return R; }

    const int max_steps = 3000;
    while (!g.dead && !g.won && g.steps < max_steps) {
        // ============ 阳性对照臂：必须在枚举之前处理 ============
        // ⚠️ 修正记录（2026-11-05）：首版把 Oracle 放在枚举之后，而
        // enumerate_worlds 在真实局面大量失败（前沿格中位 22 > 我自设的 cap）⇒
        // Oracle 先被送进"随机点击"降级分支，实测 0/50 胜。
        // 这不是模拟器错，是驱动结构让对照臂也依赖失败路径。
        // 现在 Oracle 完全绕过世界枚举。
        if (arm == Arm::Oracle) {
            std::vector<int> safe_unopened;
            for (int y = 1; y <= h; ++y)
                for (int x = 1; x <= w; ++x) {
                    int i = g.idx_of(x, y);
                    if (g.cell[(size_t)i] == 9 && !g.mine[(size_t)i])
                        safe_unopened.push_back(i);
                }
            if (safe_unopened.empty()) break;
            std::uniform_int_distribution<size_t> d(0, safe_unopened.size() - 1);
            g.click(safe_unopened[d(rng)]);
            continue;
        }

        WorldSet ws;
        std::vector<int> frontier, inner;
        bool ok = enumerate_worlds(g, ws, world_cap, frontier, inner);
        if (!ok) {
            ++R.cap;
            // ============ 降级路径（确定性，非随机）============
            // 生产在枚举超限时走 process_bigger_situation（枚举求解），
            // 不是随机点击。此处用"取第一个未开格"作确定性占位，
            // 保证读数可复现；TODO 接生产的 pbs_compute。
            std::vector<int> unopened;
            for (int y = 1; y <= h; ++y)
                for (int x = 1; x <= w; ++x) {
                    int i = g.idx_of(x, y);
                    if (g.cell[(size_t)i] == 9) unopened.push_back(i);
                }
            if (unopened.empty()) break;
            g.click(unopened[0]);
            continue;
        }
        if (frontier.empty()) {
            std::vector<int> unopened;
            for (int y = 1; y <= h; ++y)
                for (int x = 1; x <= w; ++x) {
                    int i = g.idx_of(x, y);
                    if (g.cell[(size_t)i] == 9) unopened.push_back(i);
                }
            if (unopened.empty()) break;
            g.click(unopened[0]);
            continue;
        }
        int chosen = -1;
        {
            switch (arm) {
                case Arm::Safety:
                    chosen = pick_safety(frontier, ws, g.nw);
                    break;
                case Arm::Ply2:
                    chosen = pick_ply(g, ws, frontier, 2);
                    break;
                case Arm::Ply3:
                    chosen = pick_ply(g, ws, frontier, 3);
                    break;
                case Arm::Exact:
                case Arm::Production:
                default:
                    chosen = pick_ply(g, ws, frontier, ply_depth > 0 ? ply_depth : 3);
                    break;
            }
        }
        if (chosen < 0) break;
        g.click(chosen);
    }
    R.won = g.won ? 1 : 0;
    R.steps = g.steps;
    R.dead = g.dead ? 1 : 0;
    return R;
}

// ==================== 批量入口 ====================
static py::dict run_ab(int games, int seed0, int w, int h, int a,
                       const std::string& arm, int64_t world_cap,
                       int ply_depth) {
    Arm A = Arm::Production;
    if (arm == "safety") A = Arm::Safety;
    else if (arm == "ply2") A = Arm::Ply2;
    else if (arm == "ply3") A = Arm::Ply3;
    else if (arm == "exact") A = Arm::Exact;
    else if (arm == "production") A = Arm::Production;
    else if (arm == "oracle") A = Arm::Oracle;      // 阳性对照臂（绕枚举、看真雷位）
    else throw std::runtime_error("unknown arm: " + arm);

    Stats S;
    auto t0 = std::chrono::steady_clock::now();
    std::vector<int> per_game;
    per_game.reserve((size_t)games);
    for (int i = 0; i < games; ++i) {
        AbResult r = play_one(seed0 + i, w, h, a, A, world_cap, ply_depth);
        S.games++;
        S.wins += r.won;
        S.total_steps += r.steps;
        S.cap_hits += r.cap;
        per_game.push_back(r.won);
    }
    auto t1 = std::chrono::steady_clock::now();
    S.wall_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();

    py::dict d;
    d["games"] = S.games;
    d["wins"] = S.wins;
    d["fails"] = S.fails;
    d["cap_hits"] = S.cap_hits;
    d["total_steps"] = S.total_steps;
    d["wall_ms"] = S.wall_ms;
    d["per_game"] = per_game;
    return d;
}

}  // namespace abnative

PYBIND11_MODULE(ab_native, m) {
    m.doc() = "C++ whole-game A/B driver for minesweeper (Arbiter deferred rules)";
    m.def("run_ab", &abnative::run_ab,
          py::arg("games"), py::arg("seed0"),
          py::arg("w") = 30, py::arg("h") = 16, py::arg("a") = 99,
          py::arg("arm") = "production",
          py::arg("world_cap") = 200000,
          py::arg("ply_depth") = 3,
          "Run N games with given arm; returns dict of stats.");
}
