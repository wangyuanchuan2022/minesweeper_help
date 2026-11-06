// ab_native.cpp 鈥斺€?C++ 鏁村眬 A/B 椹卞姩锛堢嫭绔嬫ā鍧楋紝涓嶄慨鏀圭敓浜?mscore.cpp锛?
//
// ==================== 涓轰粈涔堥渶瑕佽繖涓ā鍧?====================
// 鐢ㄦ埛瑕佹眰锛氭墍鏈夋祴璇曚竴寰嬬敤 C++銆備絾姝ゅ墠鎵€鏈?A/B 閮藉湪 Python 灞傝窇
// 锛坆ench/ab_policy.py 鐨?play_one锛夛紝閫熷害绾?6 绉?灞€ 鈬?10000 灞€闇€ 17 灏忔椂锛?
// 涓斾笌鐢熶骇 C++ 璺緞涓嶅悓婧愩€?
//
// 鏈ā鍧楁妸**鏁村眬瀵瑰眬寰幆**涔熸惉鍒?C++锛?
//   路 妫嬬洏鐢熸垚锛圓rbiter 寤惰繜甯冮浄璇箟锛氶鐐瑰彧淇濇湰鏍煎畨鍏級
//   路 flood-fill 灞曞紑
//   路 閫夋牸绛栫暐锛堝鑷傦細safety / ply2 / ply3 / exact / win_rate锛?
//   路 鑳滆礋鍒ゅ畾涓庣粺璁?
//
// ==================== 涓庣敓浜х殑璇箟瀵归綈锛堝繀椤伙級====================
// 鏈ā鍧楀鐢?mscore.cpp 閲岀殑 ExactEngine 閫昏緫锛堝鍒惰€岄潪閾炬帴锛岄伩鍏嶆敼鐢熶骇鏂囦欢锛夛紝
// 淇濊瘉 "saturate / after_click / ply_score" 鐨勮涔変笌鐢熶骇涓€鑷淬€?
//
// 鈿狅笍 鍏抽敭瀵归綈鐐癸紙Arbiter 寤惰繜甯冮浄锛夛細
//    棣栫偣**鍙繚璇佹湰鏍兼棤闆?*锛屼笉淇濊瘉 8 閭诲煙銆?
//    妫嬬洏鍦ㄩ鐐圭‘瀹氬悗鎵嶅竷闆凤細浠庡叏閮ㄦ牸涓Щ闄ら鐐癸紝鍐嶉殢鏈烘拻 a 涓浄銆?
//    杩欎笌 sim/minesweeper_sim.py 鐨?deferred 瑙勫垯涓€鑷淬€?
//
// ==================== 鏋勫缓 ====================
//   cpp\build_ab_native.bat
// 杈撳嚭锛歶tils/ab_native.cp310-win_amd64.pyd
//
// ==================== 鐢ㄦ硶锛圥ython 浠呬綔涓鸿杽璋冪敤灞傦級====================
//   python -c "import utils.ab_native as m; print(m.run_ab(...))"
// 浣?*鎵€鏈夎绠楅兘鍦?C++ 鍐呭畬鎴?*锛孭ython 涓嶅弬涓庝换浣曞喅绛栨垨缁熻銆?

#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace py = pybind11;

// ==================== 鐢熶骇妗ユ帴澹版槑锛圔1-a-1锛?===================
// 瀹氫箟鍦?mscore.cpp 鏈熬锛堝悓鍚?extern "C"锛夛紝姝ゅ鍙０鏄庛€?
// 鍚堝苟缂栬瘧鏃讹紙build_ab_native.bat 瀹氫箟 AB_NATIVE_MERGED 骞跺悓鏃剁紪璇戜袱涓簮鏂囦欢锛?
// 鐢遍摼鎺ュ櫒瑙ｆ瀽锛涘崟鐙紪璇?ab_native 浼氶摼鎺ュけ璐?鈥斺€?鍥犳蹇呴』鍚堝苟缂栬瘧銆?
extern "C" int abnative_decide(const int32_t* cv, int w, int h, int a,
                                int64_t node_cap, int* out_x, int* out_y,
                                int* out_total_worlds, double* out_best_rate);
extern "C" const char* abnative_last_reason();
extern "C" int abnative_decide_batch(const int32_t* cv, int w, int h, int a,
                                     int64_t node_cap, int* out_xy, int cap);

namespace abnative {


// ==================== MSVC 鍏煎鐨勪綅鎿嶄綔 ====================
// 娉ㄦ剰锛歁SVC 涓嶆敮鎸?GCC 鍐呭缓 __builtin_ctzll / __builtin_popcountll锛?
// 鐢熶骇 mscore.cpp 鐢?msw_* 灏佽锛堣鍏剁 50-60 琛岋級锛岃繖閲屽悓鏍疯嚜寤恒€?
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

// ==================== 浣嶉泦 ====================
// 30x16 + 澶栨 = 32*18 = 576 浣?鈬?9 涓?uint64
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

// ==================== 瀵瑰眬鐘舵€?====================
// cell 缂栫爜锛?..8 = 宸插紑鏁板瓧锛? = 鏈紑锛?0 = 宸茬煡闆凤紙鏈┍鍔ㄤ笉鎻掓棗锛屾晠鍩烘湰涓嶇敤锛?
struct GameCtx {
    int w = 0, h = 0, W2 = 0, a = 0;
    int total_bits = 0, nw = 0;
    Bits all_bits;
    std::vector<int> cell;          // 闀垮害 total_bits锛?y*W2+x)
    std::vector<Bits> nbr;          // 姣忔牸閭诲眳浣嶆帺鐮?
    std::vector<int> mine;          // 鐪熼浄浣嶏紙鏈眬鍥哄畾锛?
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

    // flood-fill锛氫粠 cell 寮€濮嬪睍寮€ 0 鍖哄煙
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

    // 鎵撳紑涓€鏍硷細鑻ユ槸闆峰垯姝伙紝鍚﹀垯 flood 灞曞紑
    void click(int c) {
        ++steps;
        if (mine[(size_t)c]) { dead = true; return; }
        flood_open(c);
        if (win_check()) won = true;
    }
};

// ==================== 涓栫晫鏋氫妇 ====================
// 浠庡綋鍓嶅彲瑙佺洏闈㈡瀯閫犵害鏉熷苟鏋氫妇涓€鑷翠笘鐣屻€?
// 绠€鍖栧疄鐜帮細鍙灇涓惧墠娌挎牸锛坈licks锛夛紝鍐呴儴鏍肩敤缁勫悎鏁板鐞嗭紙璋冪敤鏂瑰彧鍏冲績鍓嶆部锛夈€?
struct WorldSet {
    std::vector<Bits> worlds;       // 姣忎釜鍏冪礌鏄竴涓浄浣嶉泦鍚堬紙鍏ㄧ洏浣嶏級
    int n = 0;
};

// 鎻愬彇绾︽潫锛氭瘡涓凡寮€鏁板瓧鏍?-> 鍏堕偦鍩熸湭寮€鏍奸泦鍚堜笌闆锋暟
struct Constraint {
    std::vector<int> cells;         // 鏈紑鏍间笅鏍?
    int need = 0;                   // 闇€瑕佸嚑棰楅浄
};

static bool enumerate_worlds(const GameCtx& g, WorldSet& out, int64_t cap,
                             std::vector<int>& frontier,
                             std::vector<int>& inner) {
    // 鏀堕泦绾︽潫
    std::vector<Constraint> cons;
    std::vector<int> frontier_set;
    bool seen[4096] = {false};
    for (int y = 1; y <= g.h; ++y) {
        for (int x = 1; x <= g.w; ++x) {
            int i = g.idx_of(x, y);
            if (g.cell[(size_t)i] > 8) continue;   // 鏈紑鎴栨爣璁?
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
                        c.need -= 1;   // 宸叉爣闆锋姷鎵?
                    }
                }
            }
            c.need = g.cell[(size_t)i];
            cons.push_back(std::move(c));
        }
    }
    // 淇 need锛氬噺鍘婚偦鍩熶腑宸叉爣闆锋暟
    for (auto& c : cons) {
        int flagged = 0;
        for (int nb : c.cells) if (g.cell[(size_t)nb] == 10) ++flagged;
        c.need -= flagged;
    }
    // 鍘婚噸 cells锛堝悓涓€鏍艰澶氫釜鏁板瓧鏍煎叡浜級
    // 鐢ㄥ叏灞€ frontier 鍒楄〃 + 姣忎釜绾︽潫鐨勪綅鎺╃爜
    frontier = frontier_set;
    std::sort(frontier.begin(), frontier.end());
    int nf = (int)frontier.size();
    if (nf > 26) return false;   // 澶ぇ锛岃皟鐢ㄦ柟搴旈檷绾?

    std::vector<int> pos_in_frontier((size_t)g.total_bits, -1);
    for (int i = 0; i < nf; ++i) pos_in_frontier[(size_t)frontier[i]] = i;

    // 姣忎釜绾︽潫鐨勪綅鎺╃爜
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

    // 鍐呴儴鏍硷紙鏈浠讳綍绾︽潫娑夊強锛?
    std::vector<char> is_front((size_t)g.total_bits, 0);
    for (int f : frontier) is_front[(size_t)f] = 1;
    inner.clear();
    for (int y = 1; y <= g.h; ++y)
        for (int x = 1; x <= g.w; ++x) {
            int i = g.idx_of(x, y);
            if (g.cell[(size_t)i] == 9 && !is_front[(size_t)i])
                inner.push_back(i);
        }

    // 鏋氫妇 frontier 鐨?2^nf 绉嶈祴鍊硷紙nf <= 26 鏃舵渶澶?6700 涓囷紝杩囧ぇ鍒欐嫆缁濓級
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

// ==================== 绛栫暐鑷?====================
enum class Arm { Safety, Ply2, Ply3, Exact, Production, Oracle };

struct Stats {
    int64_t games = 0, wins = 0, fails = 0;
    int64_t total_steps = 0;
    int64_t cap_hits = 0;
    double wall_ms = 0;
};

// 绠€鍖栫増鍗曠偣鑳滅巼锛氬姣忎釜鍊欓€夌畻 safety锛堜笉閫掑綊锛夛紝渚?Safety 鑷備娇鐢?
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

// 鍓嶇灮鑷傦細瀵规瘡涓€欓€夛紝妯℃嫙"鐐瑰紑鍚庣户缁敤 safety 鐜╁埌搴?鐨勬湡鏈涜儨鐜?
// 娣卞害 k 灞傘€傝繖閲屽疄鐜?k=1锛堢偣寮€鍚庨€夋渶澶?safety锛変笌 k=2銆?
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

// 鍗曟 rollout锛氫互褰撳墠 WorldSet 涓哄墠鎻愶紝鍋囪鐐瑰紑 cand锛岀劧鍚庢寜 safety 璐績缁х画锛?
// 缁熻"鑳借耽鐨勬瘮渚?銆俤epth 鎺у埗閫掑綊灞傛暟锛? = 鍙湅涓嬩竴姝ワ級銆?
static double rollout_value(GameCtx& g, const WorldSet& ws, int cand, int depth) {
    // 璇ユ牸涓洪浄鐨勪笘鐣屾瘮渚?= 鎹熷け姒傜巼
    int bad = 0;
    for (const auto& wb : ws.worlds) if (wb.test(cand)) ++bad;
    double p_safe = 1.0 - (double)bad / (double)ws.n;
    if (p_safe <= 0.0) return 0.0;
    if (depth <= 1) return p_safe;

    // 鎸?safety 璐績缁х画涓€姝ワ紙杩戜技锛屼笉鍋氬畬鏁撮€掑綊锛?
    // 鐢?璇ユ牸鐐瑰紑鍚庡墿浣欏€欓€夌殑骞冲潎 safety"浣滀负鏀剁泭浠ｇ悊
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

// ==================== 鏁村眬 ====================
struct AbResult {
    int won = 0;
    int steps = 0;
    int dead = 0;
    int cap = 0;
    // DIAGNOSTIC: how many decisions ended up as certain_safe / limitation /
    // win_rate etc.  Needed to know whether a pbs fix actually fired.
    int certain_safe = 0;
    int degrade_first = 0;
};

// PAIRED COMPARISON (advisor): play a game with an EXPLICIT mine layout
// (1-based (x, y) pairs from the Python side) so both arms play the SAME
// board, bypassing the incompatible RNGs (std::mt19937_64 vs random.Random).
// Mines given in the (x,y) 1-based coordinate contract used everywhere else.
static AbResult play_one_mines(const std::vector<std::pair<int, int>>& mines,
                               int w, int h, int a, Arm arm,
                               int64_t world_cap, int ply_depth) {
    AbResult R;
    GameCtx g;
    g.init(w, h, a);
    int first = g.idx_of(w / 2 + 1, h / 2 + 1);   // 1-based center = Python 0-based (w/2, h/2)
    // DEFERRED-MINING ORDER (matches Python Board.open/generate exactly):
    // mines are placed FIRST (pool = all cells except the first click),
    // THEN flood_open computes numbers with mines present.  (Earlier I
    // reversed this order, which forced the first cell to read 0 and made
    // every layout rejected as "inside the opened region".)
    for (const auto& xy : mines) {
        if (xy.first < 1 || xy.first > w || xy.second < 1 || xy.second > h)
            throw std::runtime_error("mine coordinate out of board");
        int i = g.idx_of(xy.first, xy.second);
        if (i == first)
            throw std::runtime_error("mine on the first-click cell");
        g.mine[(size_t)i] = 1;
    }
    g.flood_open(first);
    if (g.win_check()) { R.won = 1; return R; }

    const int max_steps = 3000;
    while (!g.dead && !g.won && g.steps < max_steps) {
        if (arm == Arm::Oracle) {
            std::vector<int> safe_unopened;
            for (int y = 1; y <= h; ++y)
                for (int x = 1; x <= w; ++x) {
                    int i = g.idx_of(x, y);
                    if (g.cell[(size_t)i] == 9 && !g.mine[(size_t)i])
                        safe_unopened.push_back(i);
                }
            if (safe_unopened.empty()) break;
            g.click(safe_unopened[0]);
            continue;
        }
        // Decision (production bridge), identical to play_one's branch.
        std::vector<int32_t> cv((size_t)g.total_bits, 0);
        for (int y = 1; y <= h; ++y)
            for (int x = 1; x <= w; ++x) {
                int i = g.idx_of(x, y);
                cv[(size_t)i] = (int32_t)g.cell[(size_t)i];
            }
        int batch[2 * 64];
        int nb = abnative_decide_batch(cv.data(), w, h, a, world_cap,
                                       batch, 64);
        if (nb > 0) {
            ++R.certain_safe;
            for (int bi = 0; bi < nb; ++bi) {
                const int bx = batch[2 * bi];
                const int by = batch[2 * bi + 1];
                if (bx < 1 || bx > w || by < 1 || by > h) continue;
                int bidx = g.idx_of(bx, by);
                if (g.cell[(size_t)bidx] == 9) g.click(bidx);
                if (g.dead || g.won) break;
            }
            continue;
        }
        int ox = -1, oy = -1, nw_total = 0;
        double best_rate = 0.0;
        int ok2 = abnative_decide(cv.data(), w, h, a, world_cap,
                                  &ox, &oy, &nw_total, &best_rate);
        if (ok2 == 1 && ox >= 1 && oy >= 1) {
            g.click(g.idx_of(ox, oy));
        } else {
            ++R.cap;
            std::vector<int> unopened;
            for (int y = 1; y <= h; ++y)
                for (int x = 1; x <= w; ++x) {
                    int i = g.idx_of(x, y);
                    if (g.cell[(size_t)i] == 9) unopened.push_back(i);
                }
            if (unopened.empty()) break;
            g.click(unopened[0]);
        }
    }
    R.won = g.won ? 1 : 0;
    R.steps = g.steps;
    R.dead = g.dead ? 1 : 0;
    return R;
}

// Python-visible batch entry: play N games on EXPLICIT Python-provided mine
// layouts.  Returns per-game (won, steps, dead).
static py::list run_ab_mines(py::list mine_layouts, int w, int h, int a,
                             const std::string& arm, int64_t world_cap,
                             int ply_depth) {
    Arm A = Arm::Production;
    if (arm == "production") A = Arm::Production;
    else if (arm == "oracle") A = Arm::Oracle;
    else throw std::runtime_error("unknown arm for run_ab_mines: " + arm);
    py::list out;
    for (auto item : mine_layouts) {
        py::list layout = item.cast<py::list>();
        std::vector<std::pair<int, int>> mines;
        mines.reserve(layout.size());
        for (auto m : layout) {
            auto t = m.cast<py::sequence>();
            mines.emplace_back(py::int_(t[0]), py::int_(t[1]));
        }
        AbResult r = play_one_mines(mines, w, h, a, A, world_cap, ply_depth);
        out.append(py::make_tuple(r.won, r.steps, r.dead));
    }
    return out;
}

static AbResult play_one(int seed, int w, int h, int a, Arm arm,
                         int64_t world_cap, int ply_depth) {
    AbResult R;
    std::mt19937_64 rng((uint64_t)seed);
    GameCtx g;
    g.init(w, h, a);

    // Arbiter 寤惰繜甯冮浄锛氬厛閫夐鐐癸紝鍐嶄粠闄ら鐐瑰鐨勬牸鎾掗浄
    int first = g.idx_of(w / 2 + 1, h / 2 + 1);   // 1-based center = Python 0-based (w/2, h/2)   // 涓庣敓浜т竴鑷达細涓績寮€灞€
    std::vector<int> pool;
    pool.reserve((size_t)(w * h));
    for (int y = 1; y <= h; ++y)
        for (int x = 1; x <= w; ++x) {
            int i = g.idx_of(x, y);
            if (i != first) pool.push_back(i);
        }
    // 闅忔満鎾?a 涓浄
    for (int k = 0; k < a && !pool.empty(); ++k) {
        std::uniform_int_distribution<size_t> d(0, pool.size() - 1);
        size_t p = d(rng);
        g.mine[(size_t)pool[p]] = 1;
        pool[p] = pool.back();
        pool.pop_back();
    }

    // 棣栫偣蹇呯劧瀹夊叏
    g.flood_open(first);
    if (g.win_check()) { R.won = 1; return R; }

    const int max_steps = 3000;
    while (!g.dead && !g.won && g.steps < max_steps) {
        // ============ 闃虫€у鐓ц噦锛氬繀椤诲湪鏋氫妇涔嬪墠澶勭悊 ============
        // 鈿狅笍 淇璁板綍锛?026-11-05锛夛細棣栫増鎶?Oracle 鏀惧湪鏋氫妇涔嬪悗锛岃€?
        // enumerate_worlds 鍦ㄧ湡瀹炲眬闈㈠ぇ閲忓け璐ワ紙鍓嶆部鏍间腑浣?22 > 鎴戣嚜璁剧殑 cap锛夆噿
        // Oracle 鍏堣閫佽繘"闅忔満鐐瑰嚮"闄嶇骇鍒嗘敮锛屽疄娴?0/50 鑳溿€?
        // 杩欎笉鏄ā鎷熷櫒閿欙紝鏄┍鍔ㄧ粨鏋勮瀵圭収鑷備篃渚濊禆澶辫触璺緞銆?
        // 鐜板湪 Oracle 瀹屽叏缁曡繃涓栫晫鏋氫妇銆?
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

        // ==================== 鐢熶骇妗ユ帴鍐崇瓥锛圔1-a-1锛?===================
        // 涓嶅啀浣跨敤鑷埗鐨?enumerate_worlds / pick_* 杩戜技鍣紝鏀逛负鐩存帴璋冪敤
        // mscore.cpp 鏈熬鐨?abnative_decide()锛屽畠鍐呴儴涓茶捣鐢熶骇鐨?
        // 鍒囧垎 -> part_solve -> win_rate -> argmax 閾捐矾銆?
        {
            // CRITICAL data contract (measured, not assumed): production
            // cell_value has a 0-filled sentinel border -- border unique
            // values == [0] -- while the inner (w x h) region holds
            // 0..8 / 9 / 10.  Filling the border with 9 (previous code)
            // corrupts split_frontier's 8-neighbour test for cells adjacent
            // to the border, producing a wrong frontier set and mined picks
            // (observed: 6/6 deaths, 0 wins, while Oracle stays 100%).
            std::vector<int32_t> cv((size_t)g.total_bits, 0);
            for (int y = 1; y <= h; ++y)
                for (int x = 1; x <= w; ++x) {
                    int i = g.idx_of(x, y);
                    cv[(size_t)i] = (int32_t)g.cell[(size_t)i];
                }
            int ox = -1, oy = -1, nw_total = 0;
            double best_rate = 0.0;
            // BATCH FIRST (production semantics): certain-safe cells are all
            // clicked in ONE decision round.  Falling back to per-cell rounds
            // inflated decisions/game 12 -> 159 and cost ~10pp win rate.
            //
            // COORDINATE CONTRACT (aligned with production Python `clicks`):
            //   Python clicks is [(x, y), ...] where x = column, y = row,
            //   both 1-based.  The extern "C" batch buffer is the flattened
            //   form of the SAME list: out_xy[2*k] = x, out_xy[2*k+1] = y.
            //   Keep this order identical to coords_to_pylist() in mscore.cpp
            //   (which emits py::make_tuple(c.first, c.second) = (x, y)).
            int batch[2 * 64];
            int nb = abnative_decide_batch(cv.data(), w, h, a, world_cap,
                                           batch, 64);
            if (nb > 0) {
                ++R.certain_safe;
                for (int bi = 0; bi < nb; ++bi) {
                    const int bx = batch[2 * bi];       // x (column), 1-based
                    const int by = batch[2 * bi + 1];   // y (row),    1-based
                    if (bx < 1 || bx > w || by < 1 || by > h) continue;
                    int bidx = g.idx_of(bx, by);
                    if (g.cell[(size_t)bidx] == 9) g.click(bidx);
                    if (g.dead || g.won) break;
                }
                continue;
            }
            int ok2 = abnative_decide(cv.data(), w, h, a, world_cap,
                                      &ox, &oy, &nw_total, &best_rate);
            if (ok2 == 1 && ox >= 1 && oy >= 1) {
                g.click(g.idx_of(ox, oy));
            } else {
                ++R.cap;
                // 闄嶇骇锛氱‘瀹氭€у彇绗竴涓湭寮€鏍硷紙鐢熶骇璇箟鏄?process_bigger_situation锛?
                std::vector<int> unopened;
                for (int y = 1; y <= h; ++y)
                    for (int x = 1; x <= w; ++x) {
                        int i = g.idx_of(x, y);
                        if (g.cell[(size_t)i] == 9) unopened.push_back(i);
                    }
                if (unopened.empty()) break;
                g.click(unopened[0]);
            }
            continue;
        }

        // ---- 浠ヤ笅涓烘棫鐨勮繎浼煎櫒璺緞锛堜繚鐣欎絾涓嶅啀鎵ц锛屼究浜庡鐓т笌鍥炴粴锛?---
        WorldSet ws;
        std::vector<int> frontier, inner;
        bool ok = enumerate_worlds(g, ws, world_cap, frontier, inner);
        if (!ok) {
            ++R.cap;
            // ============ 闄嶇骇璺緞锛堢‘瀹氭€э紝闈為殢鏈猴級============
            // 鐢熶骇鍦ㄦ灇涓捐秴闄愭椂璧?process_bigger_situation锛堟灇涓炬眰瑙ｏ級锛?
            // 涓嶆槸闅忔満鐐瑰嚮銆傛澶勭敤"鍙栫涓€涓湭寮€鏍?浣滅‘瀹氭€у崰浣嶏紝
            // 淇濊瘉璇绘暟鍙鐜帮紱TODO 鎺ョ敓浜х殑 pbs_compute銆?
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

// ==================== 鎵归噺鍏ュ彛 ====================
// DEBUG ONLY: expose the exact cell_value array the driver feeds to
// abnative_decide() for game 0, so the data contract can be diffed against
// the Python production capture byte-for-byte.
// Board source: either a seed (RNG, C++ mt19937_64) OR an explicit mine
// layout (1-based (x,y) pairs -- SAME layout Python generated), so the
// contract diff can use identical boards (advisor requirement).
static py::list debug_first_board(int seed, int w, int h, int a) {
    GameCtx g;
    g.init(w, h, a);
    std::mt19937_64 rng((uint64_t)seed);
    int first = g.idx_of(w / 2 + 1, h / 2 + 1);   // 1-based center = Python 0-based (w/2, h/2)
    std::vector<int> pool;
    pool.reserve((size_t)(w * h));
    for (int y = 1; y <= h; ++y)
        for (int x = 1; x <= w; ++x) {
            int i = g.idx_of(x, y);
            if (i != first) pool.push_back(i);
        }
    for (int k = 0; k < a && !pool.empty(); ++k) {
        std::uniform_int_distribution<size_t> d(0, pool.size() - 1);
        size_t p = d(rng);
        g.mine[(size_t)pool[p]] = 1;
        pool[p] = pool.back();
        pool.pop_back();
    }
    g.flood_open(first);
    std::vector<int32_t> cv((size_t)g.total_bits, 0);
    for (int y = 1; y <= h; ++y)
        for (int x = 1; x <= w; ++x) {
            int i = g.idx_of(x, y);
            cv[(size_t)i] = (int32_t)g.cell[(size_t)i];
        }
    py::list out;
    for (int y = 0; y < h + 2; ++y) {
        py::list row;
        for (int x = 0; x < w + 2; ++x) row.append(cv[(size_t)y * (w + 2) + x]);
        out.append(row);
    }
    return out;
}

// DEBUG: first-frame cell_value on an EXPLICIT Python-provided mine layout.
// This is the SAME board the paired comparison plays on.
static py::list debug_first_board_mines(py::list mines, int w, int h, int a) {
    GameCtx g;
    g.init(w, h, a);
    int first = g.idx_of(w / 2 + 1, h / 2 + 1);   // 1-based center = Python 0-based (w/2, h/2)
    // DEFERRED-MINING ORDER (matches Python Board.open/generate):
    // mines first (pool = all except the first click), then flood.
    for (auto m : mines) {
        auto t = m.cast<py::sequence>();
        int x = py::int_(t[0]), y = py::int_(t[1]);
        if (x < 1 || x > w || y < 1 || y > h)
            throw std::runtime_error("mine coordinate out of board");
        int i = g.idx_of(x, y);
        if (i == first)
            throw std::runtime_error("mine on the first-click cell");
        g.mine[(size_t)i] = 1;
    }
    g.flood_open(first);
    std::vector<int32_t> cv((size_t)g.total_bits, 0);
    for (int y = 1; y <= h; ++y)
        for (int x = 1; x <= w; ++x) {
            int i = g.idx_of(x, y);
            cv[(size_t)i] = (int32_t)g.cell[(size_t)i];
        }
    py::list out;
    for (int y = 0; y < h + 2; ++y) {
        py::list row;
        for (int x = 0; x < w + 2; ++x) row.append(cv[(size_t)y * (w + 2) + x]);
        out.append(row);
    }
    return out;
}

static py::dict run_ab(int games, int seed0, int w, int h, int a,
                       const std::string& arm, int64_t world_cap,
                       int ply_depth) {
    Arm A = Arm::Production;
    if (arm == "safety") A = Arm::Safety;
    else if (arm == "ply2") A = Arm::Ply2;
    else if (arm == "ply3") A = Arm::Ply3;
    else if (arm == "exact") A = Arm::Exact;
    else if (arm == "production") A = Arm::Production;
    else if (arm == "oracle") A = Arm::Oracle;      // 闃虫€у鐓ц噦锛堢粫鏋氫妇銆佺湅鐪熼浄浣嶏級
    else throw std::runtime_error("unknown arm: " + arm);

    Stats S;
    auto t0 = std::chrono::steady_clock::now();
    std::vector<int> per_game;
    std::vector<int> steps_per_game;
    std::vector<int> dead_per_game;
    int certain_safe_total = 0;
    per_game.reserve((size_t)games);
    for (int i = 0; i < games; ++i) {
        AbResult r = play_one(seed0 + i, w, h, a, A, world_cap, ply_depth);
        S.games++;
        S.wins += r.won;
        S.total_steps += r.steps;
        S.cap_hits += r.cap;
        certain_safe_total += r.certain_safe;
        per_game.push_back(r.won);
        steps_per_game.push_back(r.steps);
        dead_per_game.push_back(r.dead);
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
    // DIAGNOSTICS: separate "lost by mine" from "loop exit".
    d["steps_per_game"] = steps_per_game;
    d["dead_per_game"] = dead_per_game;
    d["deaths"] = std::accumulate(dead_per_game.begin(), dead_per_game.end(), 0);
    d["certain_safe_hits"] = certain_safe_total;
    return d;
}

}  // namespace abnative

// Merged build (AB_NATIVE_MERGED): the module is named mscore, matching
// PyInit_mscore in mscore.cpp.  Standalone build keeps the name ab_native.
#ifdef AB_NATIVE_MERGED
PYBIND11_MODULE(mscore, m) {
#else
PYBIND11_MODULE(ab_native, m) {
#endif
    m.doc() = "C++ whole-game A/B driver for minesweeper (Arbiter deferred rules)";
    m.def("debug_first_board", &abnative::debug_first_board,
          py::arg("seed"), py::arg("w") = 30, py::arg("h") = 16, py::arg("a") = 99,
          "DEBUG: first cell_value array fed to abnative_decide (contract diff).");
    m.def("debug_first_board_mines", &abnative::debug_first_board_mines,
          py::arg("mines"), py::arg("w") = 30, py::arg("h") = 16,
          py::arg("a") = 99,
          "DEBUG: first-frame cell_value on an explicit Python mine layout.");
    m.def("run_ab_mines", &abnative::run_ab_mines,
          py::arg("mine_layouts"), py::arg("w") = 30, py::arg("h") = 16,
          py::arg("a") = 99, py::arg("arm") = "production",
          py::arg("world_cap") = 200000, py::arg("ply_depth") = 3,
          "Play N games on EXPLICIT Python-provided mine layouts "
          "(list of layouts, each a list of 1-based (x,y) pairs); "
          "returns per-game (won, steps, dead).");
    m.def("run_ab", &abnative::run_ab,
          py::arg("games"), py::arg("seed0"),
          py::arg("w") = 30, py::arg("h") = 16, py::arg("a") = 99,
          py::arg("arm") = "production",
          py::arg("world_cap") = 200000,
          py::arg("ply_depth") = 3,
          "Run N games with given arm; returns dict of stats.");
}
