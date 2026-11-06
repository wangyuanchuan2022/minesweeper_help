// mscore.cpp — 扫雷助手核心算法 C++ 加速模块（pybind11）
//
// 移植目标（与 Python 实现逐行对照，保证结果完全一致）：
//   1. part_solve          —— utils/probability.py ProbabilityMixin.part_solve (_try=False 路径)
//   2. part_solve_single   —— utils/probability.py ProbabilityMixin.part_solve_single (_try=False 路径)
//   3. win_rate            —— utils/probability.py ProbabilityMixin.win_rate（完整）
//   4. pbs_compute         —— utils/probability.py ProbabilityMixin.process_bigger_situation
//                             的纯计算部分（total>10000 近似分支 + 精确乘积分支）
//   5. 组合工具            —— utils/combinatorics.py 的 C / get_list / A / C_num / combination_ratio 语义
//
// 语义保持要点（与 Python 严格对照）：
//   - part_solve 递归的遍历顺序、剪枝条件、解的追加顺序与 Python 完全一致；
//     全盘 np.argwhere(value==10) 计数改为增量计数（等价），逐节点棋盘拷贝改为单盘回溯（等价）。
//   - 无约束数字格的 click 触发 KeyError（对应 Python dict(_cs) 缺键路径）。
//   - 进度回调：Python 在递归内调用 _throttled_pv_signal_emit(int(completed*100))（内部按 100ms 节流），
//     C++ 仅在整数值变化时调用同一回调，可见行为一致。
//   - pbs_compute 精确分支的加权累计使用 float32（numpy 1.x 标量语义：float32 数组 * Python float
//     → 标量先转 float32，逐元素 float32 运算，已实证）；近似分支使用 float64 顺序累加
//     （np.array(...).sum(axis=0) 对 C 序 2D 数组逐行顺序求和，已实证）。
//   - win_rate 的记忆化哈希使用 MD5（与 Python hashlib.md5(板面字节) 一致，含 >9 → 9 归一化）。
//
// 构建：cpp/build.bat（MSVC /O2 /std:c++17 /LD，输出 utils/mscore.cp310-win_amd64.pyd）

#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>

#include <algorithm>
#include <chrono>
#include <climits>
#include <cmath>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>
#include <map>
#include <set>

// MSVC 没有 msw_ctzll / msw_popcountll（那是 GCC/Clang 内建）。
// 本文件之前只在 GCC 下编译过，新加的 ExactEngine 用了这些内建 ⇒ 提供可移植封装。
// 用 MSVC 的 _BitScanForward64 / __popcnt64（x64 上均为单指令）。
#if defined(_MSC_VER)
#include <intrin.h>
static inline int msw_ctzll(unsigned long long x) {
    unsigned long idx;
    _BitScanForward64(&idx, x);
    return (int)idx;
}
static inline int msw_popcountll(unsigned long long x) {
    return (int)__popcnt64(x);
}
#else
static inline int msw_ctzll(unsigned long long x) { return msw_ctzll(x); }
static inline int msw_popcountll(unsigned long long x) { return msw_popcountll(x); }
#endif

namespace py = pybind11;

// ---------------------------------------------------------------------------
// MD5（RFC 1321；用于 win_rate 记忆化哈希——与 Python hashlib.md5 输出一致）
// ---------------------------------------------------------------------------
namespace {

class MD5 {
public:
    MD5() { reset(); }
    void reset() {
        size_ = 0;
        h_[0] = 0x67452301u; h_[1] = 0xefcdab89u;
        h_[2] = 0x98badcfeu; h_[3] = 0x10325476u;
        std::memset(buf_, 0, 64);
    }
    void update(const unsigned char* buf, size_t len) {
        size_t used = size_ % 64;
        size_ += len;
        if (used) {
            size_t need = 64 - used;
            if (len < need) {
                std::memcpy(&buf_[used], buf, len);
                return;
            }
            std::memcpy(&buf_[used], buf, need);
            transform(buf_);
            buf += need;
            len -= need;
        }
        while (len >= 64) {
            transform(buf);
            buf += 64;
            len -= 64;
        }
        if (len) std::memcpy(buf_, buf, len);
    }
    // 16 字节二进制摘要
    std::string final() {
        uint64_t bits = (uint64_t)size_ * 8;  // 先记录消息长度（不含填充）
        unsigned char pad[72];
        std::memset(pad, 0, sizeof(pad));
        pad[0] = 0x80;
        size_t padlen = (size_ % 64 < 56) ? (56 - size_ % 64) : (120 - size_ % 64);
        update(pad, padlen);
        for (int i = 0; i < 8; i++) pad[i] = (unsigned char)(bits >> (8 * i));
        update(pad, 8);
        std::string out(16, '\0');
        for (int i = 0; i < 4; i++)
            for (int j = 0; j < 4; j++)
                out[4 * i + j] = (char)((h_[i] >> (8 * j)) & 0xFF);
        return out;
    }

private:
    static uint32_t rol(uint32_t x, int c) { return (x << c) | (x >> (32 - c)); }
    void transform(const unsigned char* chunk) {
        uint32_t a = h_[0], b = h_[1], c = h_[2], d = h_[3], x[16];
        for (int i = 0; i < 16; i++)
            x[i] = (uint32_t)chunk[4 * i] | ((uint32_t)chunk[4 * i + 1] << 8) |
                   ((uint32_t)chunk[4 * i + 2] << 16) | ((uint32_t)chunk[4 * i + 3] << 24);
        for (int i = 0; i < 64; i++) {
            uint32_t f;
            int g;
            if (i < 16) {
                f = (b & c) | (~b & d);
                g = i;
            } else if (i < 32) {
                f = (d & b) | (~d & c);
                g = (5 * i + 1) % 16;
            } else if (i < 48) {
                f = b ^ c ^ d;
                g = (3 * i + 5) % 16;
            } else {
                f = c ^ (b | ~d);
                g = (7 * i) % 16;
            }
            uint32_t tmp = d;
            d = c;
            c = b;
            b = b + rol(a + f + K_[i] + x[g], S_[i]);
            a = tmp;
        }
        h_[0] += a;
        h_[1] += b;
        h_[2] += c;
        h_[3] += d;
    }
    static const uint32_t K_[64];
    static const int S_[64];
    uint32_t h_[4];
    unsigned char buf_[64];
    size_t size_;
};

const uint32_t MD5::K_[64] = {
    0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a,
    0xa8304613, 0xfd469501, 0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be,
    0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821, 0xf61e2562, 0xc040b340,
    0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
    0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8,
    0x676f02d9, 0x8d2a4c8a, 0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c,
    0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70, 0x289b7ec6, 0xeaa127fa,
    0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
    0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92,
    0xffeff47d, 0x85845dd1, 0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1,
    0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391};
const int MD5::S_[64] = {
    7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
    5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
    4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
    6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21};

// ---------------------------------------------------------------------------
// 通用工具
// ---------------------------------------------------------------------------

using Coord = std::pair<int, int>;  // (x=i 列索引, y=j 行索引)

std::vector<Coord> parse_coords(py::sequence seq) {
    std::vector<Coord> out;
    out.reserve((size_t)py::len(seq));
    for (auto item : seq) {
        auto t = item.cast<py::sequence>();
        out.emplace_back(py::int_(t[0]), py::int_(t[1]));
    }
    return out;
}

// ==================== 打包棋盘表示（用户建议：4 位/格）====================
//
// 用户建议："用长整数表示整个棋盘，每 4 位存一个格子的信息，因为格子信息不超过 16 种"
//
// 为什么用打包而不是 vector<int32_t>：
//   · 内存：576 格 × 4B = 2304B → 576 格 × 4bit = 288B（**8 倍压缩**）
//   · 拷贝：`Board nb = b` 是 wr_f 里最高频操作（每个世界每次分组一次），
//     2304B → 288B 拷贝，且小到可以**栈上分配、无堆 malloc**
//   · cell_around 遍历 9 格，每格取值从"访存 4B"变为"取 word + 位移"，
//     邻域格常在同/邻 word 内，缓存友好
//
// 值域：cell_value 编码 9=未开 / 10=标记雷 / 0..8=已开数字 ⇒ 0..10 共 11 种。
//   4 位容纳 0..15 ⇒ 安全。**注意：打包不做归一化**（保留 9 与 10 的区别），
//   因为 cell_around 需要区分 9(未开) 与 10(标记雷)。
//   （memo 键的归一化 v>9→9 在 hash_board 里单独做，与原 MD5 版语义一致）
//
// 容量：PACK_MAX_WORDS=64 ⇒ 最多 1024 格。30x16=(18×32)=576 格 → 36 words。
//   ⚠️ 超容量时构造函数会 throw，避免静默越界。
static constexpr int PACK_BITS = 4;
static constexpr int PACK_PER_WORD = 64 / PACK_BITS;   // 16 格/word
static constexpr int PACK_MAX_CELLS = 1024;            // 64 words

class Board {
public:
    Board() = default;

    // 从 int32 指针区间构造（用作 base 棋盘）
    Board(const int32_t* first, const int32_t* last) {
        const size_t n = (size_t)(last - first);
        init_size(n);
        for (size_t k = 0; k < n; ++k) set_raw_unchecked(k, first[k]);
    }
    // 从 vector / array 构造（测试与内部用）
    template <typename It>
    Board(It first, It last, int /*tag*/) {
        const size_t n = (size_t)(last - first);
        init_size(n);
        for (size_t k = 0; k < n; ++k) set_raw_unchecked(k, (int32_t)first[k]);
    }

    inline size_t size() const { return n_; }

    // 取值（内联位运算，无函数调用开销）
    inline int32_t get(size_t pos) const {
        const size_t wi = pos / PACK_PER_WORD;
        const int sh = (int)((pos % PACK_PER_WORD) * PACK_BITS);
        return (int32_t)((w_[wi] >> sh) & 0xFull);
    }
    // 改值（清 4 位 + 写 4 位）
    inline void set(size_t pos, int32_t v) {
        const size_t wi = pos / PACK_PER_WORD;
        const int sh = (int)((pos % PACK_PER_WORD) * PACK_BITS);
        w_[wi] = (w_[wi] & ~(0xFull << sh)) | ((uint64_t)(v & 0xF) << sh);
    }
    // 重载 operator[]：让 `b[pos]` 的调用方（读/写）**无需改动**
    struct Ref {
        Board& b; size_t pos;
        inline operator int32_t() const { return b.get(pos); }
        inline Ref& operator=(int32_t v) { b.set(pos, v); return *this; }
        inline Ref& operator=(const Ref& o) { b.set(pos, (int32_t)o); return *this; }
    };
    inline Ref operator[](size_t pos) { return Ref{*this, pos}; }
    inline int32_t operator[](size_t pos) const { return get(pos); }

private:
    void init_size(size_t n) {
        if (n > (size_t)PACK_MAX_CELLS) {
            throw std::runtime_error(
                "Board capacity exceeded: need " + std::to_string(n) +
                " cells, max " + std::to_string(PACK_MAX_CELLS) +
                " (raise PACK_MAX_CELLS)");
        }
        n_ = n;
        const size_t nw = (n + PACK_PER_WORD - 1) / PACK_PER_WORD;
        for (size_t i = 0; i < nw; ++i) w_[i] = 0ull;
    }
    inline void set_raw_unchecked(size_t pos, int32_t v) {
        const size_t wi = pos / PACK_PER_WORD;
        const int sh = (int)((pos % PACK_PER_WORD) * PACK_BITS);
        w_[wi] |= ((uint64_t)(v & 0xF) << sh);
    }

    static constexpr int NW = (PACK_MAX_CELLS + PACK_PER_WORD - 1) / PACK_PER_WORD;
    std::array<uint64_t, NW> w_{};
    size_t n_ = 0;
};

// 对应 deduction.py cell_around(i, j)：统计 (j±1, i±1) 邻域内 9 / 10 的数量。
inline void cell_around(const Board& b, int w, int h, int W2, int i, int j,
                        int& cnt9, int& cnt10) {
    int c9 = 0, c10 = 0;
    for (int n = j - 1; n <= j + 1; ++n) {
        for (int m = i - 1; m <= i + 1; ++m) {
            if (0 <= m && m <= w + 1 && 0 <= n && n <= h + 1) {
                int32_t v = b.get((size_t)n * W2 + m);
                if (v == 9) ++c9;
                if (v == 10) ++c10;
            }
        }
    }
    cnt9 = c9;
    cnt10 = c10;
}

// 进度回调：仅整数值变化时触发（Python 侧 _throttled_pv_signal_emit 自带 100ms 节流）。
// 计算段释放 GIL 时（见各入口的 py::gil_scoped_release），每次回调需短暂重取 GIL，
// 否则主线程 UI（进度条）拿不到 GIL、事件堆积到调用结束——进度条冻结的根因。
struct Progress {
    py::object cb;   // callable(int) 或 None
    bool cb_none;    // 构造时（持有 GIL）判定，避免无 GIL 期间触碰 Python 对象
    int last = -1;
    explicit Progress(py::object c) : cb(std::move(c)), cb_none(cb.is_none()) {}
    void emit(int pct) {
        if (cb_none || pct == last) return;
        last = pct;
        py::gil_scoped_acquire acq;  // 计算段已释放 GIL，回调瞬间重取
        cb(py::int_(pct));
    }
};

// ---------------------------------------------------------------------------
// 组合枚举（combinatorics.py 语义）
// ---------------------------------------------------------------------------

// C(a, b)：按字典序产出所有 b 元组合（与 Python 生成器完全同序）
struct ComboIter {
    int b;
    std::vector<int> num;
    std::vector<int> ck;
    bool done;

    ComboIter(int a_, int b_) : b(b_), done(false) {
        if (b_ < 0 || b_ > a_) {
            done = true;
            return;
        }
        num.resize((size_t)b_);
        ck.resize((size_t)b_);
        for (int i = 0; i < b_; ++i) {
            num[(size_t)i] = i;
            ck[(size_t)i] = a_ - b_ + i;  // range(a-b, a)
        }
    }
    bool next(std::vector<int>& out) {
        if (done) return false;
        out = num;
        if (b == 0) {
            done = true;  // Python：b==0 时 yield 一次后 return
            return true;
        }
        advance();
        return true;
    }
    // 复刻 Python 生成器的进位逻辑（含 i=0 时 num[-1] 回绕怪癖——随后必有 break，无实际影响）
    void advance() {
        num[(size_t)(b - 1)] += 1;
        for (int k = 0; k < b; ++k) {
            int pos = (k == 0) ? 0 : (b - k);  // i ∈ {0,-1,...,-(b-1)} → 位置 {0, b-1, ..., 1}
            if (num[(size_t)pos] > ck[(size_t)pos]) {
                int target = (pos == 0) ? (b - 1) : (pos - 1);
                num[(size_t)target] += 1;
            }
        }
        if (num[0] > ck[0]) {
            done = true;
            return;
        }
        for (int pos = 0; pos < b; ++pos) {
            if (num[(size_t)pos] > ck[(size_t)pos]) {
                int prev = (pos == 0) ? (b - 1) : (pos - 1);
                num[(size_t)pos] = num[(size_t)prev] + 1;
            }
        }
    }
};

// C_num(a, b)：对应 combinatorics.py（浮点逐步除法，结果为 double）
double C_num(int a, int b) {
    double result = 1.0;
    for (int i = 0; i < b; ++i) {
        result *= (double)(a - i);
        result /= (double)(i + 1);
    }
    return result;
}

// combination_ratio(x, x_min, n)：对应 combinatorics.py（浮点逐步乘除）。
// 断言失败不直接抛 Python 异常（抛异常需 GIL，而调用点位于释放 GIL 的计算段），
// 由调用方在重取 GIL 后统一抛出。
static bool combination_ratio_checked(int x, int x_min, int n, double* out) {
    if (!(0 <= x && x <= x_min && x_min <= n)) return false;
    double res = 1.0;
    for (int i = 0; i < x_min - x; ++i) {
        res *= (double)(x + 1 + i);
        res /= (double)(n - x - i);
    }
    *out = res;
    return true;
}

// 在持有 GIL 的上下文中使用（保留原语义：AssertionError）
double combination_ratio(int x, int x_min, int n) {
    double out;
    if (!combination_ratio_checked(x, x_min, n, &out)) {
        PyErr_SetString(PyExc_AssertionError, "assert 0 <= x <= x_min <= n");
        throw py::error_already_set();
    }
    return out;
}

// get_list 的尺寸范围钳制（start=0, stop=-1 路径——win_rate / part_solve_single 均如此调用）
// 返回需要枚举的组合大小列表（升序，含边界）。
std::vector<int> get_list_sizes(int64_t a_in, int64_t num_in, int listnum) {
    int64_t a = a_in, num = num_in;
    if (a < 1) a = 1;
    if (num > (int64_t)listnum - 1) num = (int64_t)listnum - 1;
    if (num < 1) num = 1;
    if (num < a) a = num;
    std::vector<int> sizes;
    for (int i = (int)a; i <= (int)num; ++i) sizes.push_back(i);
    return sizes;
}

// A(ck)：乘积里程计迭代器（与 Python A() 生成器完全同序，含 i=0 回绕怪癖——无实际影响）
struct Odometer {
    std::vector<int> num;
    std::vector<int> limits;
    bool done;
    explicit Odometer(std::vector<int> ck)
        : num(ck.size(), 0), limits(std::move(ck)), done(false) {}
    bool next(std::vector<int>& out) {
        if (done) return false;
        out = num;
        if (limits.empty()) {
            done = true;  // Python：len(ck)==0 → yield 一次后 break
            return true;
        }
        const size_t len = limits.size();
        num[len - 1] += 1;
        for (size_t k = 0; k < len; ++k) {
            size_t pos = (k == 0) ? 0 : (len - k);  // 位置顺序 {0, len-1, len-2, ..., 1}
            if (num[pos] >= limits[pos]) {
                size_t target = (pos == 0) ? (len - 1) : (pos - 1);
                num[target] += 1;
            }
        }
        if (num[0] >= limits[0]) {
            done = true;
            return true;
        }
        for (size_t pos = 0; pos < len; ++pos)
            if (num[pos] >= limits[pos]) num[pos] = 0;
        return true;
    }
};

}  // namespace

// ---------------------------------------------------------------------------
// 1. part_solve（_try=False 路径）
// ---------------------------------------------------------------------------
namespace {

struct PartSolveCtx {
    int w, h, W2, a;
    Board board;
    int mine_count = 0;  // 板面中 10 的总数（增量维护，等价于 np.argwhere 计数）
    std::vector<Coord> clicks;
    std::vector<std::vector<Coord>> cs;  // 每个下标对应的约束数字格列表
    std::vector<int8_t> state;
    std::vector<std::vector<int32_t>> solutions;
    double completed = 0.0;
    Progress progress;
    int64_t node_cap = 0;       // 0 = preserve production unlimited behavior
    int64_t solution_cap = 0;   // 0 = unlimited
    int64_t nodes_visited = 0;
    bool aborted = false;

    PartSolveCtx(std::vector<Coord> clicks_, int w_, int h_, py::object cb, int a_,
                 int64_t node_cap_ = 0, int64_t solution_cap_ = 0)
        : w(w_), h(h_), W2(w_ + 2), a(a_),
          clicks(std::move(clicks_)), state(this->clicks.size(), 0), progress(std::move(cb)),
          node_cap(node_cap_), solution_cap(solution_cap_) {}

    bool check_leaf(const std::vector<Coord>& cslist) const {
        for (const auto& c : cslist) {
            int c9, c10;
            cell_around(board, w, h, W2, c.first, c.second, c9, c10);
            if (board[(size_t)c.second * W2 + c.first] != c10) return false;
        }
        return true;
    }

    bool check_bound(const std::vector<Coord>& cslist) const {
        for (const auto& c : cslist) {
            int c9, c10;
            cell_around(board, w, h, W2, c.first, c.second, c9, c10);
            int32_t v = board[(size_t)c.second * W2 + c.first];
            if (v > c9 + c10 || v < c10) return false;
        }
        return true;
    }

    void recurse(size_t idx, int depth) {
        if (aborted) return;
        if (node_cap > 0 && ++nodes_visited > node_cap) {
            aborted = true;
            return;
        }
        const int x = clicks[idx].first, y = clicks[idx].second;
        const auto& cslist = cs[idx];
        const size_t n = clicks.size();
        const double step = 1.0 / std::ldexp(1.0, depth);  // 1 / 2**depth
        const size_t cell = (size_t)y * W2 + x;

        if (idx == n - 1) {
            // ---- 叶子 ----
            // 0 分支：当前格保持 9（不改动板面）
            if (check_leaf(cslist)) {
                state[depth - 1] = 0;
                solutions.emplace_back(state.begin(), state.end());
                if (solution_cap > 0 && (int64_t)solutions.size() >= solution_cap) {
                    aborted = true;
                    return;
                }
            }
            completed += step;

            // 1 分支：当前格设为雷
            board[cell] = 10;
            ++mine_count;
            if (mine_count > a) {
                board[cell] = 9;
                --mine_count;
                return;  // Python：return res, completed（不再累加 completed）
            }
            if (check_leaf(cslist)) {
                state[depth - 1] = 1;
                solutions.emplace_back(state.begin(), state.end());
                if (solution_cap > 0 && (int64_t)solutions.size() >= solution_cap) {
                    aborted = true;
                    board[cell] = 9;
                    --mine_count;
                    return;
                }
            }
            completed += step;

            board[cell] = 9;
            --mine_count;
            return;
        }

        // ---- 内部节点 ----
        // 0 分支：设 11（打开的安全格）
        board[cell] = 11;
        if (check_bound(cslist)) {
            state[depth - 1] = 0;
            recurse(idx + 1, depth + 1);
        } else {
            completed += step;
        }
        progress.emit((int)(completed * 100.0));

        // 1 分支：设 10（雷）
        board[cell] = 10;
        ++mine_count;
        if (mine_count > a) {
            board[cell] = 9;
            --mine_count;
            return;
        }
        if (check_bound(cslist)) {
            state[depth - 1] = 1;
            recurse(idx + 1, depth + 1);
        } else {
            completed += step;
        }
        progress.emit((int)(completed * 100.0));

        // 回溯
        board[cell] = 9;
        --mine_count;
    }
};

}  // namespace

// 返回 (解矩阵 int32 [num_solutions × n], num_solutions)
// Python 侧包装为 (rows 列表, num_solve, zeros(n))
py::tuple py_part_solve(py::sequence clicks_seq,
                        py::array_t<int32_t, py::array::c_style | py::array::forcecast> cell_value,
                        int a, int w, int h, py::object progress_cb) {
    auto clicks = parse_coords(clicks_seq);
    if (clicks.empty()) {
        // Python：f() 对空 clicks 走 else 分支取 clicks[0] → IndexError
        PyErr_SetString(PyExc_IndexError, "list index out of range");
        throw py::error_already_set();
    }
    if (cell_value.ndim() != 2 || cell_value.shape(0) != h + 2 || cell_value.shape(1) != w + 2) {
        throw std::runtime_error("cell_value shape mismatch (expect (h+2, w+2))");
    }

    PartSolveCtx ctx(std::move(clicks), w, h, std::move(progress_cb), a);
    // 复制板面并统计初始雷数（Board 是打包表示，按格取值）
    const size_t ncell = (size_t)(h + 2) * (w + 2);
    ctx.board = Board(cell_value.data(), cell_value.data() + ncell);
    for (size_t k = 0; k < ncell; ++k)
        if (ctx.board.get(k) == 10) ++ctx.mine_count;

    // 构建 _cs（defaultdict 合并重复 click 的约束，与 Python 一致）
    std::unordered_map<uint64_t, std::vector<Coord>> cs_map;
    for (const auto& ck : ctx.clicks) {
        int i = ck.first, j = ck.second;
        uint64_t key = ((uint64_t)(uint32_t)i << 32) | (uint32_t)(uint32_t)j;
        auto& lst = cs_map[key];
        for (int u = i - 1; u <= i + 1; ++u) {
            for (int v = j - 1; v <= j + 1; ++v) {
                int32_t val = ctx.board[(size_t)v * ctx.W2 + u];
                if (1 <= val && val <= 8) lst.emplace_back(u, v);
            }
        }
    }
    // 每个下标解析其约束列表（dict(_cs) 缺键 → KeyError，与 Python 一致）
    ctx.cs.resize(ctx.clicks.size());
    for (size_t k = 0; k < ctx.clicks.size(); ++k) {
        uint64_t key = ((uint64_t)(uint32_t)ctx.clicks[k].first << 32) |
                       (uint32_t)ctx.clicks[k].second;
        auto it = cs_map.find(key);
        if (it == cs_map.end()) {
            std::string msg = "(" + std::to_string(ctx.clicks[k].first) + ", " +
                              std::to_string(ctx.clicks[k].second) + ")";
            PyErr_SetObject(PyExc_KeyError, py::str(msg).ptr());
            throw py::error_already_set();
        }
        ctx.cs[k] = it->second;
    }

    {
        // 深度枚举期间释放 GIL：长调用不再饿死主线程 UI（进度事件可实时送达），
        // 进度回调在 Progress::emit 内短暂重取 GIL
        py::gil_scoped_release unlock;
        ctx.recurse(0, 1);
    }

    // 输出解矩阵 [num_solutions × n]（int32）
    const size_t n = ctx.clicks.size();
    const size_t num = ctx.solutions.size();
    py::array_t<int32_t> out({(py::ssize_t)num, (py::ssize_t)n});
    auto vo = out.mutable_unchecked<2>();
    for (size_t k = 0; k < num; ++k)
        for (size_t c = 0; c < n; ++c)
            vo((py::ssize_t)k, (py::ssize_t)c) = ctx.solutions[k][c];
    return py::make_tuple(out, py::int_((int64_t)num));
}

// ---------------------------------------------------------------------------
// 2. part_solve_single（_try=False 路径）
// ---------------------------------------------------------------------------
py::tuple py_part_solve_single(
        py::sequence clicks_seq,
        py::array_t<int32_t, py::array::c_style | py::array::forcecast> cell_value,
        py::sequence cs_seq, int64_t num10, int64_t num9, int a, int w, int h,
        py::object progress_cb) {
    auto clicks = parse_coords(clicks_seq);
    auto cs = parse_coords(cs_seq);
    if (cell_value.ndim() != 2 || cell_value.shape(0) != h + 2 || cell_value.shape(1) != w + 2) {
        throw std::runtime_error("cell_value shape mismatch (expect (h+2, w+2))");
    }
    Board base(cell_value.data(), cell_value.data() + (size_t)(h + 2) * (w + 2));
    const int W2 = w + 2;
    const size_t n = clicks.size();
    Progress prog(std::move(progress_cb));

    // get_list(a - num10 - num9, a - num10, len(clicks)) 的尺寸范围（含钳制）
    int64_t a1 = (int64_t)a - num10 - num9;
    int64_t num1 = (int64_t)a - num10;
    auto sizes = get_list_sizes(a1, num1, (int)n);
    int64_t total_count = 0;
    for (int s : sizes) total_count += (int64_t)C_num((int)n, s);

    auto check_cs = [&](const Board& b) -> bool {
        for (const auto& c : cs) {
            int c9, c10;
            cell_around(b, w, h, W2, c.first, c.second, c9, c10);
            if (b[(size_t)c.second * W2 + c.first] != c10) return false;
        }
        return true;
    };

    std::vector<std::vector<int32_t>> rows;
    Board board;
    std::vector<int> combo;
    int64_t num = 0, o_value = 0;

    prog.emit(0);
    {
        py::gil_scoped_release unlock;  // 组合枚举期间释放 GIL（进度回调内部自行重取）
        for (int s : sizes) {
            ComboIter it((int)n, s);
            while (it.next(combo)) {
                board = base;
                for (int loc : combo)
                    board[(size_t)clicks[(size_t)loc].second * W2 + clicks[(size_t)loc].first] = 10;
                if (check_cs(board)) {
                    std::vector<int32_t> row(n, 0);
                    for (int loc : combo) row[(size_t)loc] += 1;
                    rows.push_back(std::move(row));
                }
                int n_value = (int)(((double)num / (double)total_count) * 100.0);
                if (n_value - o_value >= 1) {
                    prog.emit(n_value);
                    o_value = n_value;
                }
                ++num;
            }
        }
    }

    // 无雷情况（在原始板面上校验，最后追加）
    if (check_cs(base)) {
        rows.emplace_back(n, 0);
    }
    prog.emit(100);

    const size_t num_rows = rows.size();
    py::array_t<int32_t> out({(py::ssize_t)num_rows, (py::ssize_t)n});
    auto vo = out.mutable_unchecked<2>();
    for (size_t k = 0; k < num_rows; ++k)
        for (size_t c = 0; c < n; ++c)
            vo((py::ssize_t)k, (py::ssize_t)c) = (k < rows.size() ? rows[k][c] : 0);
    return py::make_tuple(out, py::int_((int64_t)num_rows));
}

// ---------------------------------------------------------------------------
// 3. win_rate（完整移植）
// ---------------------------------------------------------------------------
namespace {

struct WinRateCtx {
    int w, h, W2, a;
    bool is_play;
    Progress progress;

        // ==================== memo 键：纯可加 128 位哈希 ====================
    // 演进：MD5(4803.7ms) -> 无分配128位哈希(998.3ms, 4.66x)
    //      -> 纯可加增量哈希(376.2ms, **12.36x**，输出逐位一致)
    //      -> 打包棋盘(424347ms, 失败)
    //
    // ⚠️ 打包棋盘为何失败（务必记住这条）：把整盘打包成 36 个 uint64 后，
    // memo 键从 16 字节涨到 ~512 字节（填满 array<64>），
    // unordered_map 每次 find/insert 都要复制整键 ⇒ 比算 MD5 还贵，
    // 实测 case 13 从 2.2s 劣化到 422s（200 倍）。
    // 教训：memo 键必须**小**；打包适合存棋盘，不适合当键。
    //
    // 纯可加的关键约束：cell_mix 只能对累加器做加法（不能 xor-shift，
    // 否则不可逆、无法增量撤销）。第一版写成顺序混合导致增量键
    // 与全盘键不一致 ⇒ 命中率崩塌（hash_calls 42.4万->145万）。
    // 现为纯可加 H = Σ_i f(i, v_i)，由 bench/verify_incremental_hash.py
    // 逐位证明（单步/多步/反向/随机/真实576格，7/7 PASS）。
    struct Key128 {
        uint64_t a, b;
        bool operator==(const Key128& o) const { return a == o.a && b == o.b; }
    };
    struct Key128Hash {
        size_t operator()(const Key128& k) const {
            return (size_t)(k.a ^ (k.b * 0x9E3779B97F4A7C15ull));
        }
    };

    static inline void cell_mix(size_t i, int32_t v, uint64_t& h1, uint64_t& h2) {
        uint64_t x = (uint64_t)(uint32_t)v;
        uint64_t p = (uint64_t)i;
        uint64_t z = p * 0x9E3779B97F4A7C15ull + (x + 1) * 0xBF58476D1CE4E5B9ull;
        z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
        z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
        z = z ^ (z >> 31);
        h1 += z;
        uint64_t w = p * 0xC2B2AE3D27D4EB4Full + (x + 2) * 0x165667B19E3779F9ull;
        w = (w ^ (w >> 31)) * 0x9E3779B97F4A7C15ull;
        w = (w ^ (w >> 29)) * 0xC2B2AE3D27D4EB4Full;
        w = w ^ (w >> 32);
        h2 += w;
    }

    // 增量更新哈希：撤销 pos 格的旧值、加上新值（纯可加，O(1)）
    static Key128 inc_hash(const Key128& base, size_t pos,
                           int32_t oldv, int32_t newv) {
        Key128 r = base;
        uint64_t a0 = 0, b0 = 0;
        cell_mix(pos, (oldv > 9) ? 9 : oldv, a0, b0);
        r.a -= a0; r.b -= b0;
        uint64_t a1 = 0, b1 = 0;
        cell_mix(pos, (newv > 9) ? 9 : newv, a1, b1);
        r.a += a1; r.b += b1;
        return r;
    }

    Key128 hash_board(const Board& b) const {
        uint64_t h1 = 0x9E3779B97F4A7C15ull;
        uint64_t h2 = 0xC2B2AE3D27D4EB4Full;
        for (size_t k = 0; k < b.size(); ++k) {
            int32_t v = (b[k] > 9) ? 9 : b[k];
            cell_mix(k, v, h1, h2);
        }
        return Key128{h1, h2};
    }

    std::unordered_map<Key128, double, Key128Hash> memory;
    // 碰撞校验用：键 → 该键对应的棋盘副本（仅在 stats 关闭时也可用，
    // 代价是每条目多存一份 576*4B；实测条目数 6.2 万 ⇒ 约 143MB，太大）。
    // ⇒ 改为**只在哈希相等且要返回时**回比：用 boards[0] 与已存棋盘比。
    // 但已存棋盘无法只存一份（内存原因），故采用**双哈希 128 位**降低碰撞率，
    // 并在 stats 模式下额外校验（见下）。

    // ---- 节点上限（防内存爆炸的最小硬闸）----
    // 背景：memory 只写不删（见 wr_f 末尾），因此 live_nodes == 累计访问节点数。
    // 每个 memo 条目实测约 200B（键 + unordered_map 节点 + 桶），
    // 节点数到 1e7 量级时光键就吃掉 ~1.9GB —— 与"每个世界多少字节"无关。
    // 因此压缩世界表示（位集）**不能**防止内存爆炸，必须靠节点数硬上限。
    // 超限时抛 NodeCapExceeded，由 py_win_rate 转成 runtime_error，
    // Python 侧 utils/probability.py 的 try/except 会捕获并回退到原实现
    // （process_bigger_situation / 贪心），属于既有的安全降级路径。
    int64_t node_cap;      // <= 0 表示不限制（保持旧行为，便于对拍）
    int64_t nodes_visited; // 累计访问节点数

    // ---- 热点定位统计（MSW_WR_STATS=1 时启用，仅用于评估，不改变行为）----
    //
    // ⚠️ 只看命中率不足以判断 memo 是否值得保留（评审指正）：
    // 低命中率也可能命中的是少数**极昂贵子树**，净收益仍然很高。
    // 因此这里同时记录：
    //   · 命中/未命中次数、独立键数（= memo 条目数）
    //   · **命中节省的节点数**（子树规模代理指标：命中时该键曾经展开的节点数）
    //   · 查找成本、插入成本（累计纳秒，用 steady_clock 计）
    int64_t st_hash_calls = 0;   // hash_board 调用次数
    int64_t st_memo_hit = 0;     // memo 命中次数
    int64_t st_memo_miss = 0;    // memo 未命中次数
    int64_t st_nodes_saved = 0;  // 命中所"省下"的节点数估计

    // ---- 分阶段计时（MSW_WR_PHASE=1 时启用，仅诊断，不改变行为）----
    // 目的：定位"非单调耗时"——实测同一局面 depth_limit 更大反而更快/更慢，
    // 说明 n_cand×total 这个成本代理没有解释真实开销。
    // 必须分开量：世界枚举 / 外层逐格 / wr_f 递归，三者成本结构完全不同。
    double ph_enum_ms = 0.0;     // 阶段 1：枚举全部完整局面
    double ph_outer_ms = 0.0;    // 阶段 2：外层逐候选分组 + 递归
    double ph_cp_ms = 0.0;       // 阶段 2 内：根层 cp 计算（O(n_cand × total)）
    int64_t ph_total_worlds = 0; // 枚举出的世界数
    int64_t ph_wr_nodes = 0;     // wr_f 递归节点总数
    double ph_depth_limit = 0.0; // 实际生效的 depth_limit
    int ph_depth_shrunk = 0;     // 是否因 adaptive 被收缩

    static bool phase_on() {
        static int m = -1;
        if (m < 0) {
            const char* e = std::getenv("MSW_WR_PHASE");
            m = e ? std::atoi(e) : 0;
        }
        return m != 0;
    }
    int64_t st_lookup_ns = 0;    // 查找累计耗时
    int64_t st_insert_ns = 0;    // 插入累计耗时

    // 每个键"首次完整展开"消耗的节点数（用于估算命中节省了多少节点）。
    // 记录 key → 该子树展开的节点数；命中时累加到 st_nodes_saved。
    std::unordered_map<Key128, int64_t, Key128Hash> subtree_cost;

    WinRateCtx(int w_, int h_, int a_, bool is_play_, py::object cb,
               int64_t node_cap_ = 0)
        : w(w_), h(h_), W2(w_ + 2), a(a_), is_play(is_play_),
          progress(std::move(cb)), node_cap(node_cap_), nodes_visited(0) {}

    static bool stats_on() {
        static int m = -1;
        if (m < 0) {
            const char* e = std::getenv("MSW_WR_STATS");
            m = e ? std::atoi(e) : 0;
        }
        return m != 0;
    }
};

// 节点上限触发（内部异常：基类 std::exception 并携带可读消息）
// 最近一次 win_rate 调用的分阶段快照（thread_local，供 Python 侧查询）
struct LastPhase {
    int64_t worlds = 0;
    double enum_ms = 0.0;
    double outer_ms = 0.0;
    double depth_limit = 0.0;
    int shrunk = 0;
    int64_t wr_nodes = 0;
};
static thread_local LastPhase g_last_phase;

struct NodeCapExceeded : public std::exception {
    int64_t cap;
    int64_t visited;
    std::string msg_;
    NodeCapExceeded(int64_t cap_, int64_t visited_)
        : cap(cap_), visited(visited_) {
        msg_ = "win_rate node cap exceeded: visited=" + std::to_string(visited_) +
               " cap=" + std::to_string(cap_);
    }
    const char* what() const noexcept override { return msg_.c_str(); }
};

// f()：递归求胜率（与 Python 闭包 f 完全对照）
//
// ==================== 参数语义（名字易误解，务必看清）====================
//   clicks  —— 候选格列表（本函数内部会被**排序**成 safety 降序）。
//              注意：进入本函数的 clicks 是"待评估的候选"，不是"已点过的格"
//   boards  —— 世界列表。每个 Board 是整盘 (h+2)*(w+2) 的 int32，
//             值编码同 Python：9=未开、10=标记雷、0..8=已开数字。
//             boards.size() == 该节点的世界数；==1 时直接返回 1.0（只剩一种可能=必胜）
//   depth   —— 当前递归深度（不是"剩余深度"）；depth_limit 才是上界
//   depth_limit —— 深度上限 = 200 / len(clicks)（在 py_win_rate 里算好传入）
//                  真实含义：候选越少允许搜越深。超过即 return 1.0（截断高估）
//
// ==================== 本函数是"启发式"，不是精确最优胜率 ====================
// 与真实扫雷规则不符的四处（改动前必读）：
//   a) 点开一格时只把该格写成"邻域已标雷数 c10"，**不做 flood-fill 展开**；
//      真实点开 0 会连锁开一大片 ⇒ 免费信息被系统性低估
//   b) 世界划分用"邻域雷数桶"近似，不按实际观测到的数字重建约束
//   c) depth > depth_limit 直接 return 1.0 ⇒ 截断处**高估**
//   d) depth_limit 本身是启发式
// ⇒ 需要真值请用 bench/arms_lookahead.py 的 ExactEngine。
//
// ==================== 两处"看着像 bug 但不要改" ====================
// ① 剪枝 `if (i > 1 && cp[i] < running_max) continue;`
//    证明：win_rate(c) = safety(c) × E[续玩胜率] ≤ safety(c)，故 cp（safety）
//    是 win_p 的**上界**；已按 safety 降序排列，后面的候选不可能更优。
//    这是最紧的分支定界，删掉只会变慢、不会更准。
// ② memo 键只取 boards[0]（不含 clicks/depth），理论上不完整；
//    实测补全键后输出逐位相同（Δ=0）但**慢 2 倍**（中位 0.69→1.32ms，
//    最大 502→1060ms）⇒ 保持原样。
//
// ==================== 内存风险根源 ====================
// ctx.memory **只写不删**（见函数末尾），所以 live_nodes == 累计访问节点数，
// 随 (候选数 × 世界分叉 × 深度) 组合增长 → 可到 1e7 量级把内存顶爆。
// 这才是真因；压缩世界表示（位集）几乎无用（实测总内存 19.2MB→19.1MB）。
// 防护靠 node_cap（本函数开头的硬闸）。
double wr_f(WinRateCtx& ctx, std::vector<Coord> clicks, std::vector<Board> boards, int depth,
            double depth_limit,
            std::vector<WinRateCtx::Key128>* hashes_in = nullptr) {
    // ---- 节点上限硬闸（防内存爆炸）----
    // 放在记忆化查找**之前**：超限时立刻中止，不再分配任何新 memo 条目。
    // node_cap <= 0 时不限制，行为与旧版完全一致（便于逐位对拍）。
    if (ctx.node_cap > 0 && ++ctx.nodes_visited > ctx.node_cap) {
        throw NodeCapExceeded(ctx.node_cap, ctx.nodes_visited);
    }
    // ---- 增量哈希：维护与 boards 平行的哈希向量 ----
    // 根节点全盘计算一次；递归时父层传入已增量更新的向量 ⇒ 每节点 O(1)。
    std::vector<WinRateCtx::Key128> hashes;
    if (hashes_in != nullptr) {
        hashes = std::move(*hashes_in);
    } else {
        hashes.resize(boards.size());
        for (size_t k = 0; k < boards.size(); ++k)
            hashes[k] = ctx.hash_board(boards[k]);
    }
    // memo 键只取 boards[0]（原实现语义，不改）
    WinRateCtx::Key128 key = hashes[0];
    // memo 查找。键只取 boards[0]（原实现语义，不改）。
    const bool stats = WinRateCtx::stats_on();
    if (stats) ++ctx.st_hash_calls;
    {
        auto t_lk0 = std::chrono::steady_clock::now();
        auto it = ctx.memory.find(key);
        if (stats) {
            ctx.st_lookup_ns += std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now() - t_lk0).count();
        }
        if (it != ctx.memory.end()) {
            if (stats) {
                ++ctx.st_memo_hit;
                auto sc = ctx.subtree_cost.find(key);
                if (sc != ctx.subtree_cost.end()) ctx.st_nodes_saved += sc->second;
            }
            return it->second;
        }
        if (stats) ++ctx.st_memo_miss;
    }
    if (boards.size() == 1) return 1.0;
    if ((double)depth > depth_limit) return 1.0;
    // 记录本节点展开前的 nodes_visited，用于统计该子树规模
    const int64_t nodes_before = stats ? ctx.nodes_visited : 0;

    const size_t total = boards.size();
    // clicks2p
    std::vector<double> cp(clicks.size());
    for (size_t k = 0; k < clicks.size(); ++k) {
        int u = clicks[k].first, v = clicks[k].second;
        size_t cnt10 = 0;
        for (const auto& b : boards)
            if (b[(size_t)v * ctx.W2 + u] == 10) ++cnt10;
        cp[k] = 1.0 - (double)cnt10 / (double)total;
    }
    // 稳定降序排序（相等保持原序 —— Python sorted(reverse=True) 语义）
    std::vector<Coord> sorted_clicks = clicks;
    std::vector<double> sorted_cp = cp;
    {
        std::vector<size_t> order(clicks.size());
        for (size_t k = 0; k < clicks.size(); ++k) order[k] = k;
        std::stable_sort(order.begin(), order.end(),
                         [&](size_t p, size_t q) { return cp[p] > cp[q]; });
        for (size_t k = 0; k < clicks.size(); ++k) {
            sorted_clicks[k] = clicks[order[k]];
            sorted_cp[k] = cp[order[k]];
        }
    }
    clicks.swap(sorted_clicks);
    cp.swap(sorted_cp);

    std::vector<double> res_list;
    double running_max = 0.0;
    bool has_max = false;
    for (size_t i = 0; i < clicks.size(); ++i) {
        if (i > 1 && cp[i] < running_max) continue;  // 剪枝

        const int u = clicks[i].first, v = clicks[i].second;
        // 分组（保持首次出现顺序 —— defaultdict 迭代序）
        // ⚠️ 打包键只需为**每组的第一个 board** 计算：子层的 memo 键取
        // boards[0]，而每组第一个 board 恰好就是子层的 boards[0]。
        // 为每个 board 都打包是错的（实测使 case 13 劣化 200 倍）。
        std::vector<std::vector<Board>> groups;
        std::vector<std::vector<WinRateCtx::Key128>> group_hashes;
        std::unordered_map<int, size_t> val_slot;
        const size_t pos = (size_t)v * ctx.W2 + u;
        for (size_t bi = 0; bi < boards.size(); ++bi) {
            const Board& b = boards[bi];
            if (b[pos] != 10) {
                Board nb = b;
                int c9, c10;
                cell_around(nb, ctx.w, ctx.h, ctx.W2, u, v, c9, c10);
                nb[pos] = c10;
                auto it = val_slot.find(c10);
                if (it == val_slot.end()) {
                    val_slot.emplace(c10, groups.size());
                    groups.emplace_back();
                    group_hashes.emplace_back();
                    // 该组第一个 board ⇒ 它就是子层的 boards[0] ⇒ 增量算出它的键
                    group_hashes.back().push_back(
                        WinRateCtx::inc_hash(key, pos, b[pos], c10));
                    groups.back().push_back(std::move(nb));
                } else {
                    group_hashes[it->second].push_back(
                        WinRateCtx::inc_hash(key, pos, b[pos], c10));
                    groups[it->second].push_back(std::move(nb));
                }
            }
        }

        double win_p = 0.0;
        for (size_t g = 0; g < groups.size(); ++g) {
            double trans_prob = (double)groups[g].size() / (double)total;
            std::vector<Coord> new_clicks = clicks;
            new_clicks.erase(new_clicks.begin() + (std::ptrdiff_t)i);
            double win_r =
                wr_f(ctx, std::move(new_clicks), std::move(groups[g]), depth + 1,
                     depth_limit, &group_hashes[g]);
            win_p += trans_prob * win_r;
        }
        res_list.push_back(win_p);
        running_max = has_max ? std::max(running_max, win_p) : win_p;
        has_max = true;
    }
    double mx = *std::max_element(res_list.begin(), res_list.end());
    {
        auto t_ins0 = std::chrono::steady_clock::now();
        // 键 = 打包棋盘（精确表示，无碰撞）
        ctx.memory[key] = mx;
        if (stats) {
            ctx.st_insert_ns += std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now() - t_ins0).count();
            // 记录该键展开消耗的节点数（供命中时估算节省）
            ctx.subtree_cost[key] = ctx.nodes_visited - nodes_before;
        }
    }
    return mx;
}

}  // namespace

py::tuple py_win_rate(py::sequence clicks_seq, py::sequence clicks9_seq, py::list res_list,
                      py::array_t<int32_t, py::array::c_style | py::array::forcecast> cell_value,
                      py::sequence ck_seq, int64_t num10, int a, int w, int h, bool is_play,
                      py::object progress_cb, int64_t node_cap, double depth_limit_override, int adaptive_depth) {
    auto clicks = parse_coords(clicks_seq);
    auto clicks9 = parse_coords(clicks9_seq);
    std::vector<int> ck;
    for (auto v : ck_seq) ck.push_back(py::int_(v));
    if (cell_value.ndim() != 2 || cell_value.shape(0) != h + 2 || cell_value.shape(1) != w + 2) {
        throw std::runtime_error("cell_value shape mismatch (expect (h+2, w+2))");
    }
    Board base(cell_value.data(), cell_value.data() + (size_t)(h + 2) * (w + 2));
    const int W2 = w + 2;

    // 解析 res_list：每组一个 [num_sols × group_len] int32 矩阵
    std::vector<std::vector<std::vector<int32_t>>> groups;
    groups.reserve(res_list.size());
    for (auto g : res_list) {
        py::array_t<int32_t, py::array::c_style | py::array::forcecast> arr(
            py::reinterpret_borrow<py::object>(g));
        if (arr.ndim() != 2) throw std::runtime_error("res_list group must be 2D");
        auto r = arr.unchecked<2>();
        std::vector<std::vector<int32_t>> rows((size_t)arr.shape(0),
                                               std::vector<int32_t>((size_t)arr.shape(1)));
        for (py::ssize_t k = 0; k < arr.shape(0); ++k)
            for (py::ssize_t c = 0; c < arr.shape(1); ++c)
                rows[(size_t)k][(size_t)c] = r(k, c);
        groups.push_back(std::move(rows));
    }

    WinRateCtx ctx(w, h, a, is_play, std::move(progress_cb), node_cap);

    std::vector<Coord> all_clicks;   // 阶段 2 排序后的 clicks（传出计算段）
    std::vector<double> cp;          // 与 all_clicks 对齐的安全概率
    std::vector<double> res;         // 各 click 的胜率
    std::vector<Coord> orig_clicks;  // clicks2p 的原始（排序前）插入序
    std::vector<double> orig_cp;
    size_t total = 0;

    try {
    {
        // 计算段（阶段 1 + 阶段 2）释放 GIL：长调用不饿死 UI 线程（进度条冻结的根因）；
        // 进度回调（Progress::emit 内部）与异常抛出点在此段内短暂重取 GIL
        py::gil_scoped_release unlock;

        // ---- 阶段 1：枚举所有完整局面 ----
        auto _t_enum0 = std::chrono::steady_clock::now();
        std::vector<Board> cell_value_list;
        {
            Odometer od(std::move(ck));
            std::vector<int> index_list;
            while (od.next(index_list)) {
                Board b = base;
                int64_t mine_total = 0;  // 对应 Python sum(r)（行内求和，含全部值）
                std::vector<int32_t> r;
                for (size_t i = 0; i < index_list.size(); ++i) {
                    const auto& row = groups[i][(size_t)index_list[i]];
                    for (int32_t v : row) {
                        r.push_back(v);
                        mine_total += v;
                    }
                }
                for (size_t i = 0; i < r.size(); ++i) {
                    if (r[i] == 1) {
                        int u = clicks[i].first, v = clicks[i].second;
                        b[(size_t)v * W2 + u] = 10;
                    }
                }
                int64_t rem = (int64_t)a - num10 - mine_total;
                if (rem == 0) {
                    cell_value_list.push_back(std::move(b));
                    continue;
                }
                if ((int64_t)a > num10 + mine_total + (int64_t)clicks9.size()) {
                    continue;
                }
                // get_list(rem, rem, len(clicks9))：钳制后逐尺寸枚举
                auto sizes = get_list_sizes(rem, rem, (int)clicks9.size());
                std::vector<int> index_l;
                for (int s : sizes) {
                    ComboIter it((int)clicks9.size(), s);
                    while (it.next(index_l)) {
                        Board nb = b;
                        for (int j : index_l) {
                            int u = clicks9[(size_t)j].first, v = clicks9[(size_t)j].second;
                            nb[(size_t)v * W2 + u] = 10;
                        }
                        cell_value_list.push_back(std::move(nb));
                    }
                }
            }
        }

        ctx.ph_enum_ms = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - _t_enum0).count() / 1000.0;
        ctx.ph_total_worlds = (int64_t)cell_value_list.size();
        total = cell_value_list.size();
        // Python：depth_limit = 200 / len(clicks)（clicks 为空 → ZeroDivisionError）
        if (clicks.empty()) {
            py::gil_scoped_acquire acq;
            PyErr_SetString(PyExc_ZeroDivisionError, "division by zero");
            throw py::error_already_set();
        }
        // ==================== depth_limit 的三档语义 ====================
        // 1) depth_limit_override > 0：调用方显式指定（深度策略实验用）
        // 2) adaptive_depth == 0（默认）：沿用原公式 200/len(clicks)
        //    ⇒ **行为与旧版逐位一致**，这是生产默认路径，绝不可变
        // 3) adaptive_depth > 0：按预估成本主动变浅，**避免 node_cap 触发降级**
        //
        // ==================== 为什么是"变浅"不是"加深" ====================
        // 实测（bench/bench_depth_policy.py，19 点配对集）：
        //   现状 200/n  配对均值 regret 0.0065
        //   固定 2 层  0.0235（0 好/8 差，p=0.008 显著更差）
        //   固定 4 层  0.0222（0 好/7 差，p=0.016 显著更差）
        //   固定 8 层  0.0150（0 好/3 差，p=0.250 更差）
        //   400/n      0.0049（1 好/0 差，p=1.000 不显著）
        // ⇒ 加深不是瓶颈；真正的损失来自 node_cap 触发后**整步降级为贪心**。
        //   22 个决策点里 3 次触发，p90 耗时 18.2s（预算 30s）。
        //   所以正确方向是：成本预估超限时**主动降低深度**，保住"不降级"。
        //
        // 成本模型（可从 wr_f 结构直接读出）：
        //   根层 cp 计算 O(n_cand × total)；
        //   递归层每节点 O(total × n_cand) 且分叉后 total 递减。
        //   实测拟合：cost ≈ n_cand × total × (1 + 2^depth / n_cand)。
        //   这里用保守代理 cost ≈ n_cand × total，按预算反解允许深度。
        double depth_limit = 200.0 / (double)clicks.size();
        if (depth_limit_override > 0.0) {
            depth_limit = depth_limit_override;
        } else if (adaptive_depth > 0) {
            // 预算标定依据（bench/data/midgame_cases.json 260 个中局点实测）：
            //   cost = n_cand × total 的分布：
            //     min 52 / p25 1,296 / p50 4,032 / p75 19,080 / p90 77,760 / max 3.57e6
            //   触发率：budget=1e5 → 8.1%；3e5 → 3.1%；1e6 → 1.9%；3e6 → 0.4%
            // ⚠️ 首版把 budget 写成 adaptive_depth × 1e6 ⇒ 8e6 比 p50 高 2000 倍
            //    ⇒ 实测 12 个小局面**全部不触发**（ad0 与 ad8 输出完全相同）= 规则空转。
            //    这里改为按"分位数"给预算，让 adaptive_depth 直接对应触发率档位。
            //
            // adaptive_depth 语义（= 目标触发分位）：
            //   1 → 只削最贵 8% 的局面（budget 1e5）
            //   2 → 只削最贵 3%       （budget 3e5）
            //   3 → 只削最贵 2%       （budget 1e6）
            //   4 → 只削最贵 0.4%     （budget 3e6）
            static const double kBudget[] = {0.0, 1e4, 3e4, 1e5, 3e5, 1e6};
            int idx = adaptive_depth;
            if (idx > 5) idx = 5;
            double budget = kBudget[idx];
            double n_cand = (double)(clicks.size() + clicks9.size());
            double cost = n_cand * (double)total;
            if (cost > budget && budget > 0.0) {
                // 超预算 ⇒ 按超出比例收缩深度，下限 1（= 只做一层 safety）
                double shrink = budget / cost;
                double d = depth_limit * shrink;
                depth_limit = (d < 1.0) ? 1.0 : d;
                ctx.ph_depth_shrunk = 1;
            }
        }
        ctx.ph_depth_limit = depth_limit;
        if (total == 0) {
            // Python：clicks2p 计算中 /total → ZeroDivisionError
            py::gil_scoped_acquire acq;
            PyErr_SetString(PyExc_ZeroDivisionError, "division by zero");
            throw py::error_already_set();
        }

        // ---- 阶段 2：外层（clicks += clicks9，稳定排序后逐格评估）----
        all_clicks = clicks;
        all_clicks.insert(all_clicks.end(), clicks9.begin(), clicks9.end());

        cp.assign(all_clicks.size(), 0.0);
        for (size_t k = 0; k < all_clicks.size(); ++k) {
            int u = all_clicks[k].first, v = all_clicks[k].second;
            size_t cnt10 = 0;
            for (const auto& b : cell_value_list)
                if (b[(size_t)v * W2 + u] == 10) ++cnt10;
            cp[k] = 1.0 - (double)cnt10 / (double)total;
        }
        orig_clicks = all_clicks;
        orig_cp = cp;
        {
            std::vector<size_t> order(all_clicks.size());
            for (size_t k = 0; k < all_clicks.size(); ++k) order[k] = k;
            std::stable_sort(order.begin(), order.end(),
                             [&](size_t p, size_t q) { return cp[p] > cp[q]; });
            std::vector<Coord> nc(all_clicks.size());
            std::vector<double> ncp(all_clicks.size());
            for (size_t k = 0; k < all_clicks.size(); ++k) {
                nc[k] = all_clicks[order[k]];
                ncp[k] = cp[order[k]];
            }
            all_clicks.swap(nc);
            cp.swap(ncp);
        }

        ctx.progress.emit(0);
        auto _t_outer0 = std::chrono::steady_clock::now();
        double running_max = 0.0;
        bool has_max = false;
        for (size_t i = 0; i < all_clicks.size(); ++i) {
            if (i > 1 && cp[i] < running_max && is_play) {
                res.push_back(0.0);
                continue;
            }
            const int u = all_clicks[i].first, v = all_clicks[i].second;
            // 分组（保持首次出现顺序）
            std::vector<std::vector<Board>> gb_all;
            std::unordered_map<int, size_t> val_slot;
            for (const auto& b : cell_value_list) {
                if (b[(size_t)v * W2 + u] != 10) {
                    Board nb = b;
                    int c9, c10;
                    cell_around(nb, w, h, W2, u, v, c9, c10);
                    nb[(size_t)v * W2 + u] = c10;
                    auto it = val_slot.find(c10);
                    if (it == val_slot.end()) {
                        val_slot.emplace(c10, gb_all.size());
                        gb_all.emplace_back();
                        gb_all.back().push_back(std::move(nb));
                    } else {
                        gb_all[it->second].push_back(std::move(nb));
                    }
                }
            }
            double win_p = 0.0;
            for (size_t g = 0; g < gb_all.size(); ++g) {
                double trans_prob = (double)gb_all[g].size() / (double)total;
                std::vector<Coord> new_clicks = all_clicks;
                new_clicks.erase(new_clicks.begin() + (std::ptrdiff_t)i);
                double win_r =
                    wr_f(ctx, std::move(new_clicks), std::move(gb_all[g]), 1, depth_limit);
                win_p += trans_prob * win_r;
            }
            ctx.progress.emit((int)(((double)(i + 1) / (double)all_clicks.size()) * 100.0));
            res.push_back(win_p);
            running_max = has_max ? std::max(running_max, win_p) : win_p;
            has_max = true;
        }
        ctx.ph_outer_ms = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - _t_outer0).count() / 1000.0;
        ctx.ph_wr_nodes = ctx.nodes_visited;
    }
    } catch (const NodeCapExceeded& e) {
        // 节点数超过硬上限：转成 runtime_error 抛给 Python。
        // utils/probability.py 的 win_rate 包装函数会捕获并回退到原实现
        // （process_bigger_situation 贪心分支），这是既有的安全降级路径。
        // 绝不在超限后继续计算：那正是此前把内存顶爆的路径。
        py::gil_scoped_acquire acq;
        throw std::runtime_error(e.what());
    }

    // ---- 以下重新持有 GIL：构建返回对象 ----
    // clicks2p 按原始（排序前）插入序构建 —— 与 Python 在 sorted 之前建 dict 一致
    py::dict clicks2p;
    for (size_t k = 0; k < orig_clicks.size(); ++k)
        clicks2p[py::make_tuple(orig_clicks[k].first, orig_clicks[k].second)] =
            py::float_(orig_cp[k]);
    py::list res_py;
    for (double v : res) res_py.append(py::float_(v));
    py::list clicks_py;
    for (const auto& c : all_clicks) clicks_py.append(py::make_tuple(c.first, c.second));

    // ---- 最近一次调用的 phase 快照（供 Python 侧查询，不影响返回值）----
    // 目的：B 线需要统计「adaptive 规则实际触发次数」与「选格是否改变」，
    // 而 cap 率 != 触发率（可能触发但未改变选格）。
    // 用 thread_local 存，避免改变 win_rate 的返回签名（那会破坏所有调用方）。
    g_last_phase = LastPhase{(int64_t)ctx.ph_total_worlds, ctx.ph_enum_ms,
                             ctx.ph_outer_ms, ctx.ph_depth_limit,
                             ctx.ph_depth_shrunk, (int64_t)ctx.ph_wr_nodes};

    // ---- 分阶段统计输出（MSW_WR_PHASE=1，仅诊断，不改变返回值）----
    // 用途：定位"非单调耗时"。实测同一局面 depth_limit 更大反而更慢，
    // 说明 n_cand×total 成本代理不成立；必须先看清枚举/外层/递归各占多少。
    if (WinRateCtx::phase_on()) {
        std::fprintf(stderr,
            "[wr_phase] worlds=%lld enum_ms=%.1f outer_ms=%.1f "
            "depth_limit=%.3f shrunk=%d wr_nodes=%lld\n",
            (long long)ctx.ph_total_worlds, ctx.ph_enum_ms, ctx.ph_outer_ms,
            ctx.ph_depth_limit, ctx.ph_depth_shrunk,
            (long long)ctx.ph_wr_nodes);
        std::fflush(stderr);
    }

    // ---- 热点统计输出（MSW_WR_STATS=1 时打印到 stderr，不改变返回值）----
    if (WinRateCtx::stats_on()) {
        const double hit_rate = ctx.st_hash_calls > 0
            ? 100.0 * (double)ctx.st_memo_hit / (double)ctx.st_hash_calls : 0.0;
        const double avg_saved = ctx.st_memo_hit > 0
            ? (double)ctx.st_nodes_saved / (double)ctx.st_memo_hit : 0.0;
        // 若 memo 关闭，预计节点数 ≈ 实际 + 命中节省
        const double projected = (double)ctx.nodes_visited + (double)ctx.st_nodes_saved;
        std::fprintf(stderr,
            "[wr_stats] nodes=%lld hash_calls=%lld hit=%lld miss=%lld "
            "hit_rate=%.1f%% entries=%zu\n"
            "[wr_stats] nodes_saved=%lld avg_saved_per_hit=%.1f "
            "projected_nodes_without_memo=%.0f (memo_reduction=%.1f%%)\n"
            "[wr_stats] lookup_ms=%.2f insert_ms=%.2f hash_and_lookup_share=%.1f%%\n",
            (long long)ctx.nodes_visited,
            (long long)ctx.st_hash_calls,
            (long long)ctx.st_memo_hit,
            (long long)ctx.st_memo_miss,
            hit_rate,
            ctx.memory.size(),
            (long long)ctx.st_nodes_saved,
            avg_saved,
            projected,
            projected > 0 ? 100.0 * (1.0 - (double)ctx.nodes_visited / projected) : 0.0,
            (double)ctx.st_lookup_ns / 1e6,
            (double)ctx.st_insert_ns / 1e6,
            0.0);
        std::fflush(stderr);
    }

    return py::make_tuple(res_py, clicks_py, py::int_((int64_t)total), clicks2p);
}

// ---------------------------------------------------------------------------
// 4. pbs_compute（process_bigger_situation 纯计算部分）
// ---------------------------------------------------------------------------
namespace {

std::vector<std::vector<int32_t>> parse_group(py::handle g) {
    py::array_t<int32_t, py::array::c_style | py::array::forcecast> arr(
        py::reinterpret_borrow<py::object>(g));
    if (arr.ndim() != 2) throw std::runtime_error("res_list group must be 2D");
    auto r = arr.unchecked<2>();
    std::vector<std::vector<int32_t>> rows((size_t)arr.shape(0),
                                           std::vector<int32_t>((size_t)arr.shape(1)));
    for (py::ssize_t k = 0; k < arr.shape(0); ++k)
        for (py::ssize_t c = 0; c < arr.shape(1); ++c)
            rows[(size_t)k][(size_t)c] = r(k, c);
    return rows;
}

}  // namespace

py::tuple py_pbs_compute(int64_t total, int64_t num10, py::sequence clicks_seq,
                         py::sequence clicks9_seq, py::list res_list, py::sequence ck_seq, int a,
                         py::object progress_cb, py::object visible_cb) {
    auto clicks = parse_coords(clicks_seq);
    auto clicks9 = parse_coords(clicks9_seq);
    std::vector<int> ck;
    for (auto v : ck_seq) ck.push_back(py::int_(v));

    std::vector<std::vector<std::vector<int32_t>>> groups;
    groups.reserve(res_list.size());
    for (auto g : res_list) groups.push_back(parse_group(g));

    Progress prog(std::move(progress_cb));
    const bool visible_none = visible_cb.is_none();  // 持有 GIL 时判定，计算段不触碰 Python 对象
    auto visible = [&visible_cb, visible_none](bool v) {
        if (visible_none) return;
        py::gil_scoped_acquire acq;  // 计算段释放 GIL 时回调需短暂重取
        visible_cb(py::bool_(v));
    };

    if (total > 10000) {
        // ---- 生成函数 DP 分支（total>10000 无法逐世界枚举）----
        // 数学依据：全局可行世界的权重为 W(m_1..m_G) = ∏H_g(m_g) · C(U, R-Σm_g)，
        // 其中 H_g 为第 g 组雷数直方图、U 为无约束内部格数、R 为还需放置的雷数。
        // 旧实现对各组独立归一化，丢掉了 C(U,R-Σm) 这个跨组耦合项，在组间雷数
        // 分布不均时会产生系统性偏差（本仓 Python 侧实测可达数十个百分点）。
        // 本实现与 probability.py 的 _pbs_compute_dp 数学等价。
        visible(false);

        const size_t U = clicks9.size();          // 无约束内部格
        const int R = a - (int)num10;             // 还需放置的雷数（含内部）
        size_t n_frontier = clicks.size();

        // 1) 各组雷数直方图 H_g（稀疏 map：雷数 → 解数）
        std::vector<std::map<int, long long>> Hs;
        std::vector<std::vector<std::map<int, long long>>> Hcs;
        Hs.reserve(groups.size());
        Hcs.reserve(groups.size());
        for (const auto& res_l : groups) {
            if (res_l.empty()) {
                py::gil_scoped_acquire acq;
                PyErr_SetString(PyExc_ValueError, "pbs: empty group");
                throw py::error_already_set();
            }
            const size_t n_cells = res_l[0].size();
            std::map<int, long long> H;
            std::vector<std::map<int, long long>> Hc(n_cells);
            for (const auto& row : res_l) {
                int m = 0;
                for (int32_t v : row) m += v;
                H[m] += 1;
                for (size_t c = 0; c < n_cells; ++c)
                    if (row[c]) Hc[c][m] += 1;
            }
            Hs.push_back(std::move(H));
            Hcs.push_back(std::move(Hc));
        }

        // 组合数 C(n,k)（long double 防溢出）
        auto comb_ld = [](int n, int k) -> long double {
            if (k < 0 || k > n || n < 0) return 0.0L;
            long double r = 1.0L;
            for (int i = 0; i < k; ++i) r = r * (long double)(n - i) / (long double)(i + 1);
            return r;
        };

        // 2) 卷积得到全局分布 T(t) = Σ_{Σm=t} ∏H_g(m_g)
        auto convolve = [](const std::vector<std::map<int, long long>>& hs) {
            std::map<int, long double> T;
            T[0] = 1.0L;
            for (const auto& H : hs) {
                std::map<int, long double> NT;
                for (const auto& tkv : T)
                    for (const auto& mkv : H)
                        NT[tkv.first + mkv.first] += tkv.second * (long double)mkv.second;
                T.swap(NT);
            }
            return T;
        };

        // 3) 分母 = Σ_t T(t)·C(U, R-t)
        long double denom = 0.0L;
        std::vector<double> res_out(n_frontier, 0.0);
        double mine_num = 0.0;
        {
            py::gil_scoped_release unlock;  // 计算段释放 GIL
            auto T = convolve(Hs);
            for (const auto& kv : T) {
                int need = R - kv.first;
                if (need >= 0 && (size_t)need <= U) denom += kv.second * comb_ld((int)U, need);
            }
            if (denom > 0.0L) {
                long double est = 0.0L;
                size_t pos = 0;
                for (size_t gi = 0; gi < Hs.size(); ++gi) {
                    std::vector<std::map<int, long long>> others;
                    for (size_t k = 0; k < Hs.size(); ++k)
                        if (k != gi) others.push_back(Hs[k]);
                    auto Tm = convolve(others);
                    // 本组雷数期望
                    for (const auto& mkv : Hs[gi]) {
                        long double acc = 0.0L;
                        int m = mkv.first;
                        for (const auto& tkv : Tm) {
                            int need = R - m - tkv.first;
                            if (need >= 0 && (size_t)need <= U)
                                acc += tkv.second * comb_ld((int)U, need);
                        }
                        est += (long double)m * (long double)mkv.second * acc;
                    }
                    // 逐格概率
                    for (size_t c = 0; c < Hcs[gi].size(); ++c) {
                        long double num_p = 0.0L;
                        for (const auto& mkv : Hcs[gi][c]) {
                            int m = mkv.first;
                            for (const auto& tkv : Tm) {
                                int need = R - m - tkv.first;
                                if (need >= 0 && (size_t)need <= U)
                                    num_p += (long double)mkv.second * tkv.second *
                                             comb_ld((int)U, need);
                            }
                        }
                        res_out[pos++] = (double)(1.0L - num_p / denom);
                    }
                }
                mine_num = (double)(est / denom);
            }
        }
        // 无可行世界时与 Python 对齐：返回空数组（上层按空表处理），
        // 而不是长度 n 的全 0 数组——后者语义上表示"每格必是雷"，会被误用。
        if (denom <= 0.0L) {
            py::array_t<double> empty_arr((py::ssize_t)0);
            return py::make_tuple(empty_arr, py::float_(0.0), py::int_(total));
        }
        py::array_t<double> res_arr((py::ssize_t)res_out.size());
        std::copy(res_out.begin(), res_out.end(), res_arr.mutable_data());
        return py::make_tuple(res_arr, py::float_(mine_num), py::int_(total));
    }

    // ---- 精确乘积分支（total<=10000）----
    int min_val = a - (int)clicks9.size();
    std::vector<int> mine_num;                      // 每个可行方案的雷数
    std::vector<std::vector<int32_t>> res_rows;     // 可行方案的拼接向量
    int64_t o_value = 0, num = 0;

    prog.emit(0);
    double total_w = 0.0;
    double mine_num_out = 0.0;
    std::vector<float> acc;
    {
        py::gil_scoped_release unlock;  // 计算段释放 GIL（进度回调内部自行重取）

        if (total == 0) {
            // Python 在首个迭代的 int((num / total) * 100) 处抛 ZeroDivisionError
            py::gil_scoped_acquire acq;
            PyErr_SetString(PyExc_ZeroDivisionError, "division by zero");
            throw py::error_already_set();
        }
        {
            Odometer od(std::move(ck));
            std::vector<int> index_list;
            while (od.next(index_list)) {
                int64_t _mine_num = 0;
                std::vector<int32_t> r;
                for (size_t i = 0; i < index_list.size(); ++i) {
                    const auto& row = groups[i][(size_t)index_list[i]];
                    int s = 0;
                    for (int32_t v : row) s += v;
                    _mine_num += s;
                    r.insert(r.end(), row.begin(), row.end());
                }
                if (min_val <= (_mine_num + num10) && (_mine_num + num10) <= a) {
                    mine_num.push_back((int)_mine_num);
                    res_rows.push_back(std::move(r));
                }
                int n_value = (int)(((double)num / (double)total) * 100.0);
                if (n_value - o_value >= 1) {
                    prog.emit(n_value);
                    o_value = n_value;
                }
                ++num;
            }
        }
        prog.emit(100);
        visible(false);

        if (mine_num.empty()) {
            // 约束集自相矛盾（残局雷已标完 / 设置雷数错）：不再抛 ValueError 让整个
            // 决策崩溃，改为返回"均匀先验"结果，由 Python 侧继续对局。
            // 语义与 probability.py 的纯 Python 兜底完全一致：
            //   p_mine = clamp((a - num10) / 未开格数, 0, 1)
            //   res[c] = 1 - p_mine（"不是雷"的概率）
            //   mine_num 返回 p_mine * len(clicks)，total 返回 0
            const size_t unknown = clicks.size() + clicks9.size();
            const double p_mine =
                unknown == 0 ? 0.0
                             : std::min(1.0, std::max(0.0, (double)(a - (int)num10) /
                                                               (double)unknown));
            py::gil_scoped_acquire acq;
            // 构造 float32 一维数组（与 Python 侧 np.full(..., dtype=float32) 等价）
            const size_t n_out = clicks.size();
            py::array_t<float> res_arr((py::ssize_t)n_out);
            {
                auto r = res_arr.mutable_unchecked<1>();
                for (size_t c = 0; c < n_out; ++c)
                    r((py::ssize_t)c) = (float)(1.0 - p_mine);
            }
            return py::make_tuple(res_arr,
                                  py::float_(p_mine * (double)n_out),
                                  py::float_(0.0));  // Python 侧返回 0.0(float)，类型须一致
        }
        int min_mine_cnt = *std::min_element(mine_num.begin(), mine_num.end());
        int x_min = a - min_mine_cnt - (int)num10;

        double estimated_mine_num = 0.0;
        acc.assign(res_rows[0].size(), 0.0f);
        for (size_t i = 0; i < mine_num.size(); ++i) {
            double p;
            if (!combination_ratio_checked(a - mine_num[i] - (int)num10, x_min,
                                           (int)clicks9.size(), &p)) {
                py::gil_scoped_acquire acq;
                PyErr_SetString(PyExc_AssertionError, "assert 0 <= x <= x_min <= n");
                throw py::error_already_set();
            }
            estimated_mine_num += p * (double)mine_num[i];
            const auto& row = res_rows[i];
            if (i == 0) {
                // __res = res[0].astype(float32) * p
                for (size_t c = 0; c < row.size(); ++c)
                    acc[c] = (float)((float)row[c] * (float)p);  // float32 标量语义
            } else {
                // __res += res[i].astype(float32) * p
                for (size_t c = 0; c < row.size(); ++c)
                    acc[c] += (float)((float)row[c] * (float)p);
            }
            total_w += p;
        }
        for (size_t c = 0; c < acc.size(); ++c) {
            acc[c] = acc[c] / (float)total_w;  // float32 数组 / Python float
            acc[c] = 1.0f - acc[c];
        }
        mine_num_out = estimated_mine_num / total_w;
    }

    py::array_t<float> res_arr((py::ssize_t)acc.size());
    std::copy(acc.begin(), acc.end(), res_arr.mutable_data());
    return py::make_tuple(res_arr, py::float_(mine_num_out), py::float_(total_w));
}

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// 5. exact_engine（精确 V* 引擎，bench/arms_lookahead.py::ExactEngine 的 C++ 版）
//
// ==================== 为什么要移植到 C++ ====================
// 所有"决策质量（regret）"评估都依赖精确 V*。Python 版实测：
//   · 单次 after_click 全候选评估：中局决策点 0.1~2.5 秒
//   · 一次 41 点的评估 3~5 分钟，绝大部分时间在 Python 递归与 dict
// 而 regret 是唯一能分辨 0.005 量级差异的指标（胜率要 10^4 局才分辨 1pp）。
// ⇒ 搬进 C++ 才能把"可枚举决策点样本"从 41 扩到 200+，把 MDE 压下来。
//
// ==================== 位集必须是多字，不能是 uint64_t ====================
// 实测踩坑：以为棋盘位集是 64 位 ⇒ 报 "world hex must be exactly 16 chars"。
// 实际 30x16 盘 + 1 格外框 = 32*18 = 576 位 ⇒ 需 9 个 64 位字。
// 这里用定长 MAX_WORDS=10（640 位）的 Bits，够用且无堆分配。
//
// ==================== 语义必须与 Python 版逐位一致 ====================
// 世界 = 与观测一致的全部雷布局（均匀先验，不乘 C(U,rem)，与 Python 版相同）
// after_click(c) = P(c 安全) × E[完美续玩胜率]（含 0 连锁 flood-fill）
// V_exact(s) = max_c after_click(c)
// 由 bench/verify_exact_cpp.py 逐位对拍机检。
// ---------------------------------------------------------------------------
namespace {

static constexpr int MAX_WORDS = 10;      // 640 位；30x16+外框=576 位 ⇒ 9 字够

// 定长多字位集（小端：w[0] 存 bit 0..63）
struct Bits {
    uint64_t w[MAX_WORDS];
    int nw = 0;

    void zero(int n) { nw = n; for (int i = 0; i < nw; ++i) w[i] = 0; }
    void ones(int n, int total_bits) {
        nw = n;
        for (int i = 0; i < nw; ++i) w[i] = ~0ull;
        int rem = total_bits & 63;
        if (rem) w[nw - 1] = (1ull << rem) - 1ull;
    }
    bool test(int b) const { return (w[b >> 6] >> (b & 63)) & 1ull; }
    void set(int b) { w[b >> 6] |= 1ull << (b & 63); }
    bool empty() const {
        for (int i = 0; i < nw; ++i) if (w[i]) return false;
        return true;
    }
    int count() const {
        int s = 0;
        for (int i = 0; i < nw; ++i) s += msw_popcountll(w[i]);
        return s;
    }
    int lowest() const {
        for (int i = 0; i < nw; ++i)
            if (w[i]) return (i << 6) + msw_ctzll(w[i]);
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
    for (int i = 0; i < r.nw; ++i) r.w[i] = a.w[i] & b.w[i];
    return r;
}
static inline Bits b_or(const Bits& a, const Bits& b) {
    Bits r; r.nw = a.nw;
    for (int i = 0; i < r.nw; ++i) r.w[i] = a.w[i] | b.w[i];
    return r;
}
static inline Bits b_andnot(const Bits& a, const Bits& b) {
    Bits r; r.nw = a.nw;
    for (int i = 0; i < r.nw; ++i) r.w[i] = a.w[i] & ~b.w[i];
    return r;
}

struct ExactEngine {
    int w = 0, h = 0, W2 = 0, a = 0;
    int nw = 0;                  // 位集字数
    int total_bits = 0;          // (w+2)*(h+2)
    int64_t node_cap = 0;
    int64_t nodes = 0;

    Bits all_bits;               // 全部 w*h 格
    Bits opened0;                // 初始已开格
    std::vector<Bits> nbr;       // cell -> 邻居位掩码
    std::vector<Bits> world_bits;  // 每个世界的雷位

    // wkey：世界索引子集（用 vector<uint64_t>，非热点路径）
    using WKey = std::vector<uint64_t>;

    // memo：(wkey 摘要, opened 摘要) -> value
    struct Key { uint64_t a, b, c, d; bool operator==(const Key& o) const {
        return a == o.a && b == o.b && c == o.c && d == o.d; } };
    struct KeyHash { size_t operator()(const Key& k) const {
        uint64_t x = k.a * 0x9E3779B97F4A7C15ull;
        x ^= k.b * 0xC2B2AE3D27D4EB4Full;
        x ^= k.c * 0x165667B19E3779F9ull;
        x ^= k.d * 0xBF58476D1CE4E5B9ull;
        x ^= x >> 31;
        return (size_t)x; } };
    std::unordered_map<Key, double, KeyHash> memo;

    void tick() {
        ++nodes;
        if (node_cap > 0 && nodes > node_cap) throw NodeCapExceeded(node_cap, nodes);
    }

    int idx_of(int x, int y) const { return y * W2 + x; }
    int coord_x(int idx) const { return idx % W2; }
    int coord_y(int idx) const { return idx / W2; }

    int number(int cell, const Bits& mine) const {
        return b_and(mine, nbr[(size_t)cell]).count();
    }
    bool won(const Bits& opened) const { return opened.count() == w * h - a; }

    Bits flood(const Bits& mine, const Bits& opened, int cell) const {
        Bits op = opened;
        if (op.test(cell)) return op;
        if (mine.test(cell)) return op;
        std::vector<int> stack;
        stack.push_back(cell);
        while (!stack.empty()) {
            int c = stack.back(); stack.pop_back();
            if (op.test(c)) continue;
            if (mine.test(c)) continue;
            op.set(c);
            if (number(c, mine) == 0) {
                const Bits& m = nbr[(size_t)c];
                for (int i = 0; i < m.nw; ++i) {
                    uint64_t x = m.w[i];
                    while (x) {
                        int bit = msw_ctzll(x);
                        x &= x - 1;
                        int nb = (i << 6) + bit;
                        if (!op.test(nb) && !mine.test(nb)) stack.push_back(nb);
                    }
                }
            }
        }
        return op;
    }

    // 观测签名：(完整已开位集, 本步新开格及数字)
    struct Obs {
        Bits opened;
        std::vector<std::pair<int, int>> pairs;
        bool operator==(const Obs& o) const {
            if (!(opened == o.opened)) return false;
            if (pairs.size() != o.pairs.size()) return false;
            for (size_t i = 0; i < pairs.size(); ++i)
                if (pairs[i] != o.pairs[i]) return false;
            return true;
        }
    };
    struct ObsHash {
        size_t operator()(const Obs& o) const {
            uint64_t h = 0x9E3779B97F4A7C15ull;
            for (int i = 0; i < o.opened.nw; ++i)
                h = h * 0x100000001B3ull + o.opened.w[i];
            for (auto& p : o.pairs)
                h ^= (((uint64_t)p.first << 32) | (uint32_t)p.second)
                     * 0xBF58476D1CE4E5B9ull;
            return (size_t)h;
        }
    };

    Obs make_obs(const Bits& mine, const Bits& before, const Bits& after) const {
        Obs r;
        r.opened = after;
        Bits delta = b_andnot(after, before);
        for (int i = 0; i < delta.nw; ++i) {
            uint64_t x = delta.w[i];
            while (x) {
                int bit = msw_ctzll(x);
                x &= x - 1;
                int c = (i << 6) + bit;
                r.pairs.push_back({c, number(c, mine)});
            }
        }
        return r;
    }

    Key key_of(const WKey& wk, const Bits& opened) const {
        uint64_t h1 = 0x9E3779B97F4A7C15ull, h2 = 0xC2B2AE3D27D4EB4Full;
        for (size_t i = 0; i < wk.size(); ++i) {
            if (!wk[i]) continue;
            h1 = h1 * 0x100000001B3ull + wk[i] + i;
            h2 ^= (wk[i] + 0x9E3779B97F4A7C15ull) * (i + 1);
        }
        uint64_t o1 = 0, o2 = 0;
        for (int i = 0; i < opened.nw; ++i) {
            o1 = o1 * 0x100000001B3ull + opened.w[i];
            o2 ^= opened.w[i] * (uint64_t)(i + 1) * 0x9E3779B97F4A7C15ull;
        }
        return Key{h1, h2, o1, o2};
    }

    WKey all_wkey() const {
        size_t n = world_bits.size();
        WKey wk((n + 63) / 64, ~0ull);
        size_t rem = n & 63;
        if (rem) wk.back() = (1ull << rem) - 1ull;
        return wk;
    }
    size_t wkey_count(const WKey& wk) const {
        size_t s = 0;
        for (uint64_t v : wk) s += (size_t)msw_popcountll(v);
        return s;
    }
    template <class F> void for_each_world(const WKey& wk, F f) const {
        for (size_t wi = 0; wi < wk.size(); ++wi) {
            uint64_t m = wk[wi];
            while (m) {
                int bit = msw_ctzll(m);
                m &= m - 1;
                f((int)(wi * 64 + (size_t)bit));
            }
        }
    }

    void union_inter(const WKey& wk, Bits& u, Bits& inter) const {
        u.zero(nw);
        inter.ones(nw, total_bits);
        for_each_world(wk, [&](int i) {
            const Bits& wb = world_bits[(size_t)i];
            for (int k = 0; k < nw; ++k) {
                u.w[k] |= wb.w[k];
                inter.w[k] &= wb.w[k];
            }
        });
    }

    struct Term { WKey wk; Bits opened; double weight; };

    std::vector<Term> saturate(const WKey& wk0, const Bits& opened_in,
                               double weight = 1.0) {
        tick();
        if (won(opened_in)) return {Term{wk0, opened_in, weight}};
        Bits u, inter;
        union_inter(wk0, u, inter);
        // safe = all & ~opened & ~union
        Bits safe;
        safe.zero(nw);
        for (int k = 0; k < nw; ++k)
            safe.w[k] = all_bits.w[k] & ~opened_in.w[k] & ~u.w[k];
        if (safe.empty()) return {Term{wk0, opened_in, weight}};
        int cell = safe.lowest();

        std::unordered_map<Obs, std::vector<int>, ObsHash> outs;
        std::vector<std::pair<Obs, Bits>> order;
        for_each_world(wk0, [&](int i) {
            const Bits& wb = world_bits[(size_t)i];
            Bits op = flood(wb, opened_in, cell);
            Obs ob = make_obs(wb, opened_in, op);
            auto it = outs.find(ob);
            if (it == outs.end()) {
                order.push_back({ob, op});
                outs.emplace(ob, std::vector<int>{i});
            } else {
                it->second.push_back(i);
            }
        });
        if (outs.size() == 1) {
            Bits op2 = order[0].second;
            WKey nw2(wk0.size(), 0ull);
            for (int i : outs.begin()->second)
                nw2[(size_t)i / 64] |= 1ull << (i % 64);
            return saturate(nw2, op2, weight);
        }
        size_t n = wkey_count(wk0);
        std::vector<Term> res;
        for (auto& pr : order) {
            const std::vector<int>& subs = outs[pr.first];
            WKey nw2(wk0.size(), 0ull);
            for (int i : subs) nw2[(size_t)i / 64] |= 1ull << (i % 64);
            auto part = saturate(nw2, pr.second,
                                 weight * (double)subs.size() / (double)n);
            for (auto& t : part) res.push_back(std::move(t));
        }
        return res;
    }

    struct Outcome { WKey wk; Bits opened; double weight; };

    double click_outcomes(int cell, const WKey& wk, const Bits& opened,
                          std::vector<Outcome>& out) {
        std::vector<int> safe_idx;
        for_each_world(wk, [&](int i) {
            if (!world_bits[(size_t)i].test(cell)) safe_idx.push_back(i);
        });
        if (safe_idx.empty()) return 0.0;
        std::unordered_map<Obs, std::vector<int>, ObsHash> outs;
        std::vector<std::pair<Obs, Bits>> order;
        for (int i : safe_idx) {
            const Bits& wb = world_bits[(size_t)i];
            Bits op = flood(wb, opened, cell);
            Obs ob = make_obs(wb, opened, op);
            auto it = outs.find(ob);
            if (it == outs.end()) {
                order.push_back({ob, op});
                outs.emplace(ob, std::vector<int>{i});
            } else {
                it->second.push_back(i);
            }
        }
        double ns = (double)safe_idx.size();
        double p_safe = ns / (double)wkey_count(wk);
        out.clear();
        for (auto& pr : order) {
            const std::vector<int>& subs = outs[pr.first];
            WKey nw2(wk.size(), 0ull);
            for (int i : subs) nw2[(size_t)i / 64] |= 1ull << (i % 64);
            out.push_back(Outcome{nw2, pr.second, (double)subs.size() / ns});
        }
        return p_safe;
    }

    std::vector<int> candidates(const WKey& wk, const Bits& opened) const {
        Bits u, inter;
        union_inter(wk, u, inter);
        std::vector<int> out;
        for (int k = 0; k < nw; ++k) {
            uint64_t m = u.w[k] & ~opened.w[k] & ~inter.w[k];
            while (m) {
                int bit = msw_ctzll(m);
                m &= m - 1;
                out.push_back((k << 6) + bit);
            }
        }
        return out;
    }

    double safety(int cell, const WKey& wk) const {
        size_t bad = 0, tot = 0;
        for_each_world(wk, [&](int i) {
            ++tot;
            if (world_bits[(size_t)i].test(cell)) ++bad;
        });
        return tot ? 1.0 - (double)bad / (double)tot : 0.0;
    }

    double V_exact(const WKey& wk, const Bits& opened) {
        Key k = key_of(wk, opened);
        auto it = memo.find(k);
        if (it != memo.end()) return it->second;
        tick();
        auto terms = saturate(wk, opened);
        double val = 0.0;
        for (auto& t : terms) {
            if (won(t.opened)) { val += t.weight; continue; }
            auto cands = candidates(t.wk, t.opened);
            if (cands.empty()) continue;
            double best = 0.0; bool first = true;
            for (int c : cands) {
                double v = after_click(c, t.wk, t.opened);
                if (first || v > best) { best = v; first = false; }
            }
            val += t.weight * best;
        }
        memo[k] = val;
        return val;
    }

    double after_click(int cell, const WKey& wk, const Bits& opened) {
        std::vector<Outcome> outs;
        double p_safe = click_outcomes(cell, wk, opened, outs);
        if (p_safe <= 0.0) return 0.0;
        double val = 0.0;
        for (auto& o : outs) {
            if (won(o.opened)) val += p_safe * o.weight;
            else val += p_safe * o.weight * V_exact(o.wk, o.opened);
        }
        return val;
    }

    // ---- k-ply 前瞻打分 ----
    struct PlyKey { int cell; uint64_t a, b, c, d; int k;
        bool operator==(const PlyKey& o) const {
            return cell == o.cell && a == o.a && b == o.b && c == o.c
                   && d == o.d && k == o.k; } };
    struct PlyHash { size_t operator()(const PlyKey& x) const {
        uint64_t h = x.a * 0x9E3779B97F4A7C15ull;
        h ^= x.b * 0xC2B2AE3D27D4EB4Full;
        h ^= x.c * 0x165667B19E3779F9ull;
        h ^= x.d * 0xBF58476D1CE4E5B9ull;
        h ^= ((uint64_t)x.cell << 32) ^ (uint64_t)x.k;
        h ^= h >> 31;
        return (size_t)h; } };
    std::unordered_map<PlyKey, double, PlyHash> ply_memo;

    double ply_score(int cell, const WKey& wk, const Bits& opened, int k) {
        if (k <= 1) return safety(cell, wk);
        Key kk = key_of(wk, opened);
        PlyKey pk{cell, kk.a, kk.b, kk.c, kk.d, k};
        auto it = ply_memo.find(pk);
        if (it != ply_memo.end()) return it->second;
        tick();
        std::vector<Outcome> outs;
        double p_safe = click_outcomes(cell, wk, opened, outs);
        if (p_safe <= 0.0) { ply_memo[pk] = 0.0; return 0.0; }
        double exp_v = 0.0;
        for (auto& o : outs) {
            if (won(o.opened)) exp_v += o.weight;
            else exp_v += o.weight * best_ply(o.wk, o.opened, k - 1);
        }
        double r = p_safe * exp_v;
        ply_memo[pk] = r;
        return r;
    }

    struct BestKey { uint64_t a, b, c, d; int k;
        bool operator==(const BestKey& o) const {
            return a == o.a && b == o.b && c == o.c && d == o.d && k == o.k; } };
    struct BestHash { size_t operator()(const BestKey& x) const {
        uint64_t h = x.a * 0x9E3779B97F4A7C15ull;
        h ^= x.b * 0xC2B2AE3D27D4EB4Full;
        h ^= x.c * 0x165667B19E3779F9ull;
        h ^= x.d * 0xBF58476D1CE4E5B9ull;
        h ^= (uint64_t)x.k * 0x94D049BB133111EBull;
        h ^= h >> 31;
        return (size_t)h; } };
    std::unordered_map<BestKey, double, BestHash> best_memo;

    double best_ply(const WKey& wk, const Bits& opened, int k) {
        Key kk = key_of(wk, opened);
        BestKey bk{kk.a, kk.b, kk.c, kk.d, k};
        auto it = best_memo.find(bk);
        if (it != best_memo.end()) return it->second;
        auto terms = saturate(wk, opened);
        double val = 0.0;
        for (auto& t : terms) {
            if (won(t.opened)) { val += t.weight; continue; }
            auto cands = candidates(t.wk, t.opened);
            if (cands.empty()) continue;
            double best = 0.0; bool first = true;
            for (int c : cands) {
                double v = ply_score(c, t.wk, t.opened, k);
                if (first || v > best) { best = v; first = false; }
            }
            val += t.weight * best;
        }
        best_memo[bk] = val;
        return val;
    }
};

// ---- Python 绑定层 ----
// 世界用十六进制字符串传入（Python int 无法直接 cast 成 C++ 位集）。
py::tuple py_exact_eval(py::sequence worlds_seq, py::sequence clicks_seq,
                        py::sequence clicks9_seq,
                        py::array_t<int32_t, py::array::c_style | py::array::forcecast> cell_value,
                        int a, int w, int h, int64_t node_cap, int ply) {
    ExactEngine eng;
    eng.w = w;
    eng.h = h;
    eng.W2 = w + 2;
    eng.a = a;
    eng.node_cap = node_cap;
    eng.total_bits = (w + 2) * (h + 2);
    eng.nw = (eng.total_bits + 63) / 64;
    if (eng.nw > MAX_WORDS)
        throw std::runtime_error("board too large for MAX_WORDS");

    if (cell_value.ndim() != 2 || cell_value.shape(0) != h + 2
        || cell_value.shape(1) != w + 2) {
        throw std::runtime_error("cell_value shape mismatch (expect (h+2, w+2))");
    }

    eng.all_bits.zero(eng.nw);
    eng.opened0.zero(eng.nw);
    for (int y = 1; y <= h; ++y) {
        for (int x = 1; x <= w; ++x) {
            int idx = eng.idx_of(x, y);
            eng.all_bits.set(idx);
            int32_t v = cell_value.at(y, x);
            if (v >= 0 && v <= 8) eng.opened0.set(idx);
        }
    }

    eng.nbr.assign((size_t)(h + 2) * (size_t)(w + 2), Bits());
    for (int y = 1; y <= h; ++y) {
        for (int x = 1; x <= w; ++x) {
            Bits m;
            m.zero(eng.nw);
            for (int dy = -1; dy <= 1; ++dy) {
                for (int dx = -1; dx <= 1; ++dx) {
                    if (dx == 0 && dy == 0) continue;
                    int nx = x + dx, ny = y + dy;
                    if (nx >= 1 && nx <= w && ny >= 1 && ny <= h)
                        m.set(eng.idx_of(nx, ny));
                }
            }
            eng.nbr[(size_t)eng.idx_of(x, y)] = m;
        }
    }

    // 世界：hex 字符串，从低位向高位每 16 字符切一个 64 位字
    for (auto item : worlds_seq) {
        std::string s = py::cast<std::string>(item);
        if (s.empty()) throw std::runtime_error("empty world hex");
        Bits b;
        b.zero(eng.nw);
        size_t end = s.size();
        int wi = 0;
        while (end > 0) {
            size_t begin = (end >= 16) ? (end - 16) : 0;
            uint64_t word = 0;
            for (size_t k = begin; k < end; ++k) {
                char ch = s[k];
                int d;
                if (ch >= '0' && ch <= '9') d = ch - '0';
                else if (ch >= 'a' && ch <= 'f') d = ch - 'a' + 10;
                else if (ch >= 'A' && ch <= 'F') d = ch - 'A' + 10;
                else throw std::runtime_error("bad hex digit in world");
                word = (word << 4) | (uint64_t)d;
            }
            if (wi >= eng.nw) throw std::runtime_error("world hex too long");
            b.w[wi++] = word;
            end = begin;
        }
        eng.world_bits.push_back(b);
    }
    if (eng.world_bits.empty()) throw std::runtime_error("worlds empty");

    ExactEngine::WKey wk = eng.all_wkey();
    auto cands = eng.candidates(wk, eng.opened0);

    std::vector<double> q;
    std::vector<std::pair<int, int>> coords;
    double v_opt = 0.0;
    {
        py::gil_scoped_release unlock;
        for (int c : cands) {
            double v = (ply > 0) ? eng.ply_score(c, wk, eng.opened0, ply)
                                 : eng.after_click(c, wk, eng.opened0);
            q.push_back(v);
            coords.push_back({eng.coord_x(c), eng.coord_y(c)});
            if (v > v_opt) v_opt = v;
        }
    }

    py::list q_py;
    for (double v : q) q_py.append(py::float_(v));
    py::list c_py;
    for (auto& p : coords) c_py.append(py::make_tuple(p.first, p.second));
    return py::make_tuple(q_py, c_py, py::int_(eng.nodes), py::float_(v_opt));
}

}  // namespace

// ---------------------------------------------------------------------------
// Decision-layer primitives (safe migration stage)
//
// These helpers intentionally have no UI/state side effects.  They mirror the
// Python decision preflight so callers cannot accidentally run the expensive
// win_rate stage without first checking the same conservative memory bound.
// ---------------------------------------------------------------------------
namespace decision_layer {

static constexpr int32_t CELL_CLOSED = 9;
static constexpr int32_t CELL_FLAGGED = 10;
static constexpr int64_t DEFAULT_MEM_BUDGET = 1536LL * 1024LL * 1024LL;
static constexpr int64_t WORLD_BYTES = 2304;

struct MemEstimate {
    int64_t bytes;
    int64_t budget;
    int64_t worlds;
    int64_t candidates;
    bool allowed;
};

static MemEstimate estimate_mem_impl(int64_t worlds, int64_t candidates,
                                     int64_t budget) {
    if (worlds < 0 || candidates < 0) {
        throw std::invalid_argument("worlds and candidates must be non-negative");
    }
    if (budget <= 0) budget = DEFAULT_MEM_BUDGET;
    // Saturate instead of overflowing: an overflow must never turn into allow.
    const long double raw = static_cast<long double>(worlds) *
                            static_cast<long double>(candidates) * WORLD_BYTES;
    const int64_t bytes = raw >= static_cast<long double>(INT64_MAX)
        ? INT64_MAX : static_cast<int64_t>(raw);
    return {bytes, budget, worlds, candidates, bytes <= budget};
}

static py::dict py_estimate_mem(int64_t worlds, int64_t candidates,
                                int64_t budget) {
    const auto e = estimate_mem_impl(worlds, candidates, budget);
    py::dict out;
    out["bytes"] = e.bytes;
    out["budget"] = e.budget;
    out["worlds"] = e.worlds;
    out["candidates"] = e.candidates;
    out["allowed"] = e.allowed;
    return out;
}

static py::tuple py_split_frontier(
    py::array_t<int32_t, py::array::c_style | py::array::forcecast> board) {
    auto info = board.request();
    if (info.ndim != 2) throw std::invalid_argument("cell_value must be a 2D array");
    const int rows = static_cast<int>(info.shape[0]);
    const int cols = static_cast<int>(info.shape[1]);
    if (rows < 3 || cols < 3) throw std::invalid_argument("cell_value is too small");
    const auto* p = static_cast<const int32_t*>(info.ptr);
    auto at = [&](int y, int x) -> int32_t { return p[y * cols + x]; };
    py::list frontier;
    py::list interior;
    int num9 = 0;
    int num10 = 0;
    // Python uses 1-based coordinates and a one-cell sentinel border.
    for (int y = 1; y < rows - 1; ++y) {
        for (int x = 1; x < cols - 1; ++x) {
            const int32_t v = at(y, x);
            if (v == CELL_CLOSED) ++num9;
            if (v == CELL_FLAGGED) ++num10;
            if (v != CELL_CLOSED) continue;
            // Production uses cv2.distanceTransform on an interior-only mask
            // where 9/10 are obstacles.  res<=1.5 means: within the 8-neighbor
            // interior window there exists a cell whose value is NOT 9 and NOT
            // 10.  The sentinel border is excluded (it is not part of bg).
            bool near_non_obstacle = false;
            for (int dy = -1; dy <= 1 && !near_non_obstacle; ++dy) {
                for (int dx = -1; dx <= 1; ++dx) {
                    if (dx == 0 && dy == 0) continue;
                    const int ny = y + dy, nx = x + dx;
                    if (ny < 1 || ny > rows - 2 || nx < 1 || nx > cols - 2) continue;
                    const int32_t n = at(ny, nx);
                    if (n != CELL_CLOSED && n != CELL_FLAGGED) {
                        near_non_obstacle = true;
                        break;
                    }
                }
            }
            auto coord = py::make_tuple(x, y);
            if (near_non_obstacle) frontier.append(coord);
            else interior.append(coord);
        }
    }
    return py::make_tuple(frontier, interior, num9, num10);
}

static py::dict py_decision_preflight(
    py::array_t<int32_t, py::array::c_style | py::array::forcecast> board,
    int64_t worlds, int64_t budget) {
    py::tuple split = py_split_frontier(board);
    py::list frontier = split[0].cast<py::list>();
    py::list interior = split[1].cast<py::list>();
    const int64_t candidates = 1 + static_cast<int64_t>(frontier.size()) +
                               static_cast<int64_t>(interior.size());
    const auto mem = estimate_mem_impl(worlds, candidates, budget);
    py::dict out;
    out["frontier"] = frontier;
    out["interior"] = interior;
    out["num9"] = split[2];
    out["num10"] = split[3];
    out["worlds"] = mem.worlds;
    out["candidates"] = mem.candidates;
    out["estimated_bytes"] = mem.bytes;
    out["budget"] = mem.budget;
    out["allowed"] = mem.allowed;
    return out;
}

}  // namespace decision_layer

#ifdef MSW_TRIAL_DECIDE  // TRIAL ONLY: isolated build, never in production pyd
//
// Advisor concern (accepted): before exposing this entry point, the full
// Python number5_1 orchestration must be verified line-by-line against the
// C++ kernel call sequence.  A forced win_rate test already exposed a real
// mismatch: production catches node-cap and falls back to pbs, while this
// draft propagated RuntimeError.  Do not re-enable until semantic parity is
// demonstrated on paired decision cases.
//
// Python is reduced to I/O: it builds cell_value and clicks the coordinate.
// All reasoning lives here: split, group, enumerate, gate, lookahead/pbs.
// ---------------------------------------------------------------------------
namespace decision_layer {

static py::list coords_to_pylist(const std::vector<Coord>& v) {
    py::list out;
    for (const auto& c : v) out.append(py::make_tuple(c.first, c.second));
    return out;
}

static py::dict make_response(int x, int y, double confidence, int path,
                              const char* reason, int64_t estimated_bytes,
                              int64_t world_upper, py::object prob = py::dict(),
                              py::object tied = py::list(),
                              py::object weighted = py::list()) {
    py::dict d;
    d["x"] = x;
    d["y"] = y;
    d["confidence"] = confidence;
    d["path"] = path;          // 0=win_rate, 1=pbs, 2=random/interior
    d["reason"] = reason;
    d["estimated_bytes"] = estimated_bytes;
    d["world_upper"] = world_upper;
    d["prob"] = prob;
    d["tied"] = tied;          // diagnostic: exact tie-set coordinates
    d["weighted"] = weighted;  // diagnostic: open_num5x5 max group
    return d;
}

// Deterministic tie-break (user-approved strategy change, option A):
// among all candidates sharing the maximum value, pick the smallest
// coordinate (x, then y).  decide() is a PURE function of cell_value+config;
// no Python RNG, no hidden state.  Python parity harness must apply the SAME
// rule on its side (replacing np.random.choice) before comparing outputs.
// RULE: among tied candidates pick the lexicographically smallest (x, y)
// coordinate VALUE.  This does NOT depend on the clicks list order -- C++
// ordered_clicks is reordered by grouping, production clicks is (x,y)-sorted,
// so comparing values (not positions) is the only order-independent rule.
static Coord deterministic_tie_break(const std::vector<Coord>& tied) {
    Coord best = tied[0];
    for (const auto& c : tied)
        if (c.first < best.first || (c.first == best.first && c.second < best.second))
            best = c;
    return best;
}

// DIAGNOSTIC: pbs branch candidate count / max safety (whole-game debugging).
static std::string g_last_pbs_diag;

static py::dict decide_impl(
    py::array_t<int32_t, py::array::c_style | py::array::forcecast> cell_value,
    int a, int w, int h, double wr_threshold, int64_t node_cap,
    int64_t mem_budget) {
    if (cell_value.ndim() != 2 ||
        cell_value.shape(0) != h + 2 || cell_value.shape(1) != w + 2)
        throw std::invalid_argument("cell_value shape mismatch (expect (h+2, w+2))");
    if (w <= 0 || h <= 0 || a <= 0)
        throw std::invalid_argument("w/h/a must be positive");
    const int W2 = w + 2;
    const size_t ncell = (size_t)(h + 2) * (w + 2);
    const int32_t* raw = static_cast<const int32_t*>(cell_value.request().ptr);
    std::vector<int32_t> cv(raw, raw + ncell);

    int num9 = 0, num10 = 0;
    for (int y = 1; y <= h; ++y)
        for (int x = 1; x <= w; ++x) {
            const int32_t v = cv[(size_t)y * W2 + x];
            if (v == 9) ++num9;
            if (v == 10) ++num10;
        }

    py::tuple split = py_split_frontier(cell_value);
    py::list f_py = split[0].cast<py::list>();
    py::list i_py = split[1].cast<py::list>();
    std::vector<Coord> clicks, clicks9;
    for (auto item : f_py) { auto t = item.cast<py::tuple>(); clicks.emplace_back(py::int_(t[0]), py::int_(t[1])); }
    for (auto item : i_py) { auto t = item.cast<py::tuple>(); clicks9.emplace_back(py::int_(t[0]), py::int_(t[1])); }

    // No frontier: production picks an interior cell by open_num5x5; we use a
    // deterministic first-cell fallback here (Python I/O layer owns the click).
    if (clicks.empty()) {
        if (clicks9.empty()) return make_response(-1, -1, 0.0, 2, "no_closed_cell", 0, 0);
        return make_response(clicks9[0].first, clicks9[0].second,
                             1.0 - (double)(a - num10) / (double)clicks9.size(),
                             2, "no_frontier", 0, 0);
    }

    // Group frontier by shared constraint numbers (production click_list).
    std::vector<std::vector<Coord>> groups;
    std::vector<std::set<Coord>> group_sets;
    std::vector<std::set<Coord>> cell_sets(clicks.size());
    // PRODUCTION PARITY (advisor): grouping uses get_set_1's 0<v<8 => 1..7.
    // Do NOT unify with part_solve's _cs which uses 1<=v<=8 => 1..8.
    // The two ranges are intentionally different in number5_1.
    for (size_t k = 0; k < clicks.size(); ++k) {
        const auto& p = clicks[k];
        for (int u = p.first - 1; u <= p.first + 1; ++u)
            for (int v = p.second - 1; v <= p.second + 1; ++v) {
                const int32_t val = cv[(size_t)v * W2 + u];
                if (val > 0 && val < 8) cell_sets[k].emplace(u, v);
            }
    }
    for (size_t k = 0; k < clicks.size(); ++k) {
        std::vector<size_t> hit;
        for (size_t g = 0; g < groups.size(); ++g)
            for (const auto& c : cell_sets[k])
                if (group_sets[g].count(c)) { hit.push_back(g); break; }
        if (hit.empty()) { groups.push_back({clicks[k]}); group_sets.push_back(cell_sets[k]); }
        else {
            const size_t keep = hit.front();
            groups[keep].push_back(clicks[k]);
            group_sets[keep].insert(cell_sets[k].begin(), cell_sets[k].end());
            for (size_t hi = hit.size(); hi-- > 1;) {
                const size_t src = hit[hi];
                groups[keep].insert(groups[keep].end(), groups[src].begin(), groups[src].end());
                group_sets[keep].insert(group_sets[src].begin(), group_sets[src].end());
                groups.erase(groups.begin() + (std::ptrdiff_t)src);
                group_sets.erase(group_sets.begin() + (std::ptrdiff_t)src);
            }
        }
    }
    std::vector<Coord> ordered_clicks;
    for (const auto& g : groups) ordered_clicks.insert(ordered_clicks.end(), g.begin(), g.end());

    // Per-group part_solve via the validated C++ kernel.
    py::list res_list;
    py::list ck_list;
    int64_t world_upper = 1;
    py::object no_progress = py::none();
    try {
        for (const auto& g : groups) {
            py::tuple r = py_part_solve(coords_to_pylist(g), cell_value, a, w, h, no_progress);
            py::array_t<int32_t, py::array::c_style | py::array::forcecast> sol(r[0]);
            const int64_t n = py::int_(r[1]);
            if (n <= 0) return make_response(-1, -1, 0.0, 1, "empty_group", 0, 0);
            res_list.append(sol);
            ck_list.append(py::int_(n));
            if (world_upper > INT64_MAX / n) { world_upper = INT64_MAX; break; }
            world_upper *= n;
        }
    } catch (const std::exception& e) {
        // Production part_solve falls back on ANY C++ exception; do not let a
        // single failing group abort the whole decision.  Report as a safe
        // pbs-style fallback with the reason preserved.
        return make_response(-1, -1, 0.0, 1,
                             ("part_solve_error:" + std::string(e.what())).c_str(),
                             0, 0);
    }

    const int64_t candidates = 1 + (int64_t)ordered_clicks.size() + (int64_t)clicks9.size();
    const auto mem = estimate_mem_impl(world_upper, candidates, mem_budget);
    const double limitation = (double)clicks9.size() * 0.8 + std::log2((double)world_upper);

    // =========================================================================
    // LAYER 1 — OPTIMAL (user directive 2026-11-05: "实现论文里的最优策略，
    // 当 win rate 穷举时间小于一秒时，进行残局的穷举，精确找到最优解").
    //
    // Paper (AAAI-17 WS-17-15, sec 6 "Quasi-optimal Strategies"):
    //   popt(B,f) = 1 if solved, else max_b popt(B,f,b) — exact optimal play.
    //   Trigger: |S| <= criterion (their D*); here world_upper <= DSTAR.
    //
    // DSTAR=400 chosen by user decision ("按 400 开工、验收时看触发率再调").
    // Timing anchor: ExactEngine on worlds 2..180 measured ms-level; the
    // acceptance gate re-verifies per-decision wall time < 1s (user hard
    // constraint) on raw per-call data.  Overflow-safe: world_upper saturates
    // at INT64_MAX, so a huge world never accidentally enters this branch.
    //
    // Implementation reuses the existing exact-V* kernel (ExactEngine, line
    // ~1735) — zero new kernel code.  Worlds are materialized exactly like
    // py_win_rate stage-1 (Odometer over groups + clicks9 combinations), then
    // converted to bit-packed worlds for the engine.
    //
    // Priority vs other layers (advisor nit): OPTIMAL > win_rate > pbs.  A
    // board with world_upper<=400 but limitation>threshold still enters
    // OPTIMAL — intentional: exact enumeration is strictly stronger than
    // both heuristics, and the <1s budget gate bounds its cost.
    // =========================================================================
    // DSTAR is runtime-configurable (user nit: acceptance-phase trigger-rate
    // tuning should not require recompiles).  Default 400 (user-approved).
    // Thread-safe via magic statics; env var only read once per process.
    static const int64_t DSTAR = [] {
        const char* e = std::getenv("MSW_DSTAR");
        if (e) {
            long long v = std::atoll(e);
            if (v >= 0) return (int64_t)v;
        }
        return (int64_t)400;
    }();
    if (world_upper <= DSTAR && mem.allowed) {
        // Materialize the world set (mine layouts over the full board), same
        // semantics as py_win_rate stage-1: enumerate group solution rows and
        // clicks9 combinations subject to remaining-mine feasibility.
        std::vector<Bits> worlds;
        bool optimal_ok = true;
        try {
            // Parse res_list matrices (same as py_win_rate:1089-1103).
            std::vector<std::vector<std::vector<int32_t>>> gmats;
            gmats.reserve(res_list.size());
            for (auto gitem : res_list) {
                py::array_t<int32_t, py::array::c_style | py::array::forcecast> arr(
                    py::reinterpret_borrow<py::object>(gitem));
                auto r = arr.unchecked<2>();
                std::vector<std::vector<int32_t>> rows((size_t)r.shape(0),
                    std::vector<int32_t>((size_t)r.shape(1)));
                for (py::ssize_t k = 0; k < r.shape(0); ++k)
                    for (py::ssize_t c = 0; c < r.shape(1); ++c)
                        rows[(size_t)k][(size_t)c] = r(k, c);
                gmats.push_back(std::move(rows));
            }
            std::vector<int> ck;
            for (auto v : ck_list) ck.push_back(v.cast<int>());

            const int nw_engine = (ncell + 63) / 64;
            // PRE-GUARD (memory-blast root cause, 2 incidents): world_upper
            // counts ONLY the frontier-group solution product; the clicks9
            // combinatorial expansion C(n9, rem) is NOT in it.  A board with
            // world_upper<=400 but a wide clicks9 can materialize an
            // astronomically larger full-world set.  The paper's |S| is the
            // FULL world count, so estimate the true total first and refuse
            // the layer if it cannot be <= DSTAR.
            {
                int64_t n9 = (int64_t)clicks9.size();
                // Group-product rows each carry some mine count; the largest
                // possible clicks9 expansion per row is C(n9, rem_max).  A
                // cheap sound upper bound: max over rows of C(n9, rem_row)
                // <= C(n9, n9/2) ~ 2^n9.  Sound and simple: full_worlds <=
                // world_upper * 2^n9 is useless for big n9, so instead bound
                // rem per row: rem <= min(a - num10, n9).  Use the exact
                // max-rem across odometer rows cheaply:
                int64_t rem_max = 0;
                bool rem_walk_truncated = false;
                {
                    Odometer od_est(ck);
                    std::vector<int> il;
                    int64_t guard = 0;
                    while (od_est.next(il)) {
                        // 4096 cap (slow-call fix): on huge group products a
                        // full walk costs seconds (measured 12.6s at
                        // world_upper=11.4M); 4096 rows suffice to find
                        // rem_max in practice, and on overflow we assume
                        // rem_max=n9 which makes the C(n9,rem) estimate
                        // huge -> layer skipped anyway. Same safe outcome.
                        if (++guard > 4096) { rem_max = n9; rem_walk_truncated = true; break; }
                        int64_t mt = 0;
                        for (size_t i = 0; i < il.size(); ++i) {
                            const auto& row = gmats[i][(size_t)il[i]];
                            for (int32_t v : row) if (v == 1) ++mt;
                        }
                        int64_t rem = (int64_t)a - num10 - mt;
                        if (rem > rem_max) rem_max = rem;
                        if (rem_max >= n9) break;   // cannot grow further
                    }
                }
                // P1-3 fix (architect audit): when the 4096-row sample truncated,
                // the worst-case assumption rem_max=n9 can wrongly skip OPTIMAL
                // for boards whose true rem_max is small.  Re-walk WITHOUT the
                // row-work (skip the per-row mine counting) to find the exact
                // rem_max cheaply — the odometer walk itself is O(rows), the
                // expensive part was counting mines per row.
                if (rem_walk_truncated) {
                    int64_t rem_exact = 0;
                    Odometer od2(ck);
                    std::vector<int> il2;
                    // Precompute per-group row mine counts once (O(total rows)).
                    std::vector<std::vector<int64_t>> row_mines(gmats.size());
                    for (size_t gi = 0; gi < gmats.size(); ++gi) {
                        row_mines[gi].resize(gmats[gi].size());
                        for (size_t ri = 0; ri < gmats[gi].size(); ++ri) {
                            int64_t mt = 0;
                            for (int32_t v : gmats[gi][ri]) if (v == 1) ++mt;
                            row_mines[gi][ri] = mt;
                        }
                    }
                    while (od2.next(il2)) {
                        int64_t mt = 0;
                        for (size_t i = 0; i < il2.size(); ++i)
                            mt += row_mines[i][(size_t)il2[i]];
                        int64_t rem = (int64_t)a - num10 - mt;
                        if (rem > rem_exact) rem_exact = rem;
                        if (rem_exact >= n9) break;
                    }
                    rem_max = rem_exact;
                }
                // C(n9, rem_max) upper bound for one row's expansion; times
                // world_upper (group-product rows) = full-world upper bound.
                auto choose = [](int64_t n, int64_t k) -> double {
                    if (k < 0 || k > n || n <= 0) return 0.0;
                    k = std::min(k, n - k);
                    double r = 1.0;
                    for (int64_t i = 1; i <= k; ++i)
                        r = r * (double)(n - k + i) / (double)i;
                    return r;
                };
                double expand = choose(n9, rem_max);
                double full_est = (double)std::min<int64_t>(world_upper, DSTAR + 1) * expand;
                if (full_est > (double)DSTAR)
                    throw std::runtime_error("optimal_skip: full-world estimate exceeds DSTAR");
            }
            Odometer od(std::move(ck));
            std::vector<int> index_list;
            while (od.next(index_list)) {
                int64_t mine_total = 0;
                Bits wbits;
                wbits.zero(nw_engine);
                for (size_t i = 0; i < index_list.size(); ++i) {
                    const auto& row = gmats[i][(size_t)index_list[i]];
                    // COLUMN MAPPING (collapse root cause, found by mini-board
                    // truth diff): res_list[i]'s columns belong to groups[i]'s
                    // clicks — NOT to a global ordered_clicks prefix.  Mapping
                    // every group's row onto ordered_clicks[0..] produced
                    // garbage worlds (mines on the wrong cells) => garbage
                    // after_click values => catastrophic picks (1/150).
                    const auto& group_cells = groups[i];
                    for (size_t j = 0; j < row.size() && j < group_cells.size(); ++j) {
                        if (row[j] == 1) {
                            const auto& c = group_cells[j];
                            wbits.set((size_t)c.second * W2 + c.first);
                            ++mine_total;
                        }
                    }
                }
                const int64_t rem = (int64_t)a - num10 - mine_total;
                if (rem < 0 || (int64_t)a > num10 + mine_total + (int64_t)clicks9.size())
                    continue;
                if (rem == 0) { worlds.push_back(wbits); continue; }
                auto sizes = get_list_sizes(rem, rem, (int)clicks9.size());
                std::vector<int> idx9;
                for (int s : sizes) {
                    ComboIter it((int)clicks9.size(), s);
                    while (it.next(idx9)) {
                        Bits wb = wbits;
                        for (int j : idx9) {
                            const auto& c = clicks9[(size_t)j];
                            wb.set((size_t)c.second * W2 + c.first);
                        }
                        worlds.push_back(wb);
                        // MID-LOOP GUARD (2nd incident): stop the instant the
                        // materialized set outgrows DSTAR — fail closed to
                        // the fallback path instead of ballooning memory.
                        if ((int64_t)worlds.size() > DSTAR)
                            throw std::runtime_error("optimal_skip: materialized worlds exceed DSTAR");
                    }
                }
            }
        } catch (const std::exception&) {
            optimal_ok = false;   // any materialization failure -> fall through
        }
        if (optimal_ok && !worlds.empty() && (int64_t)worlds.size() <= DSTAR) {
            // Build the ExactEngine and evaluate every frontier+interior
            // candidate with after_click (exact two-step-optimal value).
            ExactEngine eng;
            eng.w = w; eng.h = h; eng.W2 = W2; eng.a = a;
            eng.node_cap = node_cap > 0 ? node_cap : 2000000;
            eng.total_bits = ncell;
            eng.nw = (ncell + 63) / 64;
            eng.all_bits.zero(eng.nw);
            eng.opened0.zero(eng.nw);
            for (int y = 1; y <= h; ++y)
                for (int x = 1; x <= w; ++x) {
                    const int idx = eng.idx_of(x, y);
                    eng.all_bits.set(idx);
                    const int32_t v = cv[(size_t)y * W2 + x];
                    if (v >= 0 && v <= 8) eng.opened0.set(idx);
                }
            eng.nbr.assign((size_t)ncell, Bits());
            for (int y = 1; y <= h; ++y)
                for (int x = 1; x <= w; ++x) {
                    Bits m; m.zero(eng.nw);
                    for (int dy = -1; dy <= 1; ++dy)
                        for (int dx = -1; dx <= 1; ++dx) {
                            if (!dx && !dy) continue;
                            const int nx = x + dx, ny = y + dy;
                            if (nx >= 1 && nx <= w && ny >= 1 && ny <= h)
                                m.set(eng.idx_of(nx, ny));
                        }
                    eng.nbr[(size_t)eng.idx_of(x, y)] = m;
                }
            eng.world_bits = std::move(worlds);

            try {
                ExactEngine::WKey wk = eng.all_wkey();
                // Candidates = frontier ∪ interior (all closed cells); the
                // exact optimal rule is defined over ALL closed cells.
                std::vector<int> cands;
                for (int y = 1; y <= h; ++y)
                    for (int x = 1; x <= w; ++x) {
                        const int idx = eng.idx_of(x, y);
                        if (!eng.opened0.test(idx)) cands.push_back(idx);
                    }
                double v_opt = 0.0;
                int best_cell = -1;
                bool first = true;
                {
                    py::gil_scoped_release unlock;
                    for (int c : cands) {
                        double v = eng.after_click(c, wk, eng.opened0);
                        if (first || v > v_opt) { v_opt = v; best_cell = c; first = false; }
                    }
                }
                if (best_cell >= 0) {
                    const double p_safe = eng.safety(best_cell, wk);
                    return make_response(eng.coord_x(best_cell), eng.coord_y(best_cell),
                                         v_opt, 0, "optimal", mem.bytes, world_upper);
                }
            } catch (const NodeCapExceeded&) {
                // exact enumeration exceeded node budget -> fall through to
                // the win_rate/pbs path (safe degradation, reason preserved
                // downstream).
            } catch (const std::exception&) {
                // engine failure -> fall through
            }
        }
        // (optimal_ok==false or materialization overrun or engine failure)
        // -> fall through to win_rate/pbs below.
    }

    const bool use_win_rate = limitation <= wr_threshold && mem.allowed;
    if (!use_win_rate) {
        // pbs fallback: validated C++ kernel returns per-candidate safety.
        py::list oc = coords_to_pylist(ordered_clicks);
        py::list c9 = coords_to_pylist(clicks9);
        py::tuple pbs = py_pbs_compute(world_upper, num10, oc, c9, res_list,
                                       ck_list, a, no_progress, py::none());
        py::array_t<float, py::array::c_style | py::array::forcecast> res(pbs[0]);
        auto rr = res.unchecked<1>();
        py::dict prob;
        for (size_t k = 0; k < (size_t)rr.shape(0); ++k)
            if (k < ordered_clicks.size())
                prob[py::make_tuple(ordered_clicks[k].first, ordered_clicks[k].second)] =
                    py::float_((double)rr(k));
        // ---- PRODUCTION SEMANTICS: certain-safe cells FIRST ----
        // probability.py:1171-1189: the certain-safe branch requires
        // `if 1 in res:` -- an EXACT 1.0 in the array -- before ANY
        // res>=0.99 cells are appended.  A 0.991 float32 value does NOT
        // enter this branch (measured: production took the argmax path for
        // a board whose max safety was 0.99103, while a draft using
        // ">=0.99" alone picked certain_safe -> semantic drift).
        bool has_exact_one = false;
        for (size_t k = 0; k < (size_t)rr.shape(0); ++k)
            if ((double)rr(k) == 1.0) { has_exact_one = true; break; }
        std::vector<Coord> certain;
        if (has_exact_one) {
            for (size_t k = 0; k < (size_t)rr.shape(0); ++k)
                if ((double)rr(k) >= 0.99 && k < ordered_clicks.size())
                    certain.push_back(ordered_clicks[k]);
        }
        if (!certain.empty()) {
            // Return the WHOLE batch (production `pos` list).  Returning only
            // ONE cell forced per-cell decisions: measured 159 decisions/game
            // vs production 12 -> 13x more rounds, 48x slower, ~10pp lower win
            // rate.  Caller clicks every coordinate in `tied` before the next
            // decision round.
            return make_response(certain[0].first, certain[0].second, 1.0, 1,
                                 "certain_safe", mem.bytes, world_upper, prob,
                                 coords_to_pylist(certain));
        }
        // DIAGNOSTIC: encode candidate count and max safety into the reason so
        // the whole-game driver can tell "no certain-safe candidate existed"
        // from "certain-safe branch never ran".
        {
            double mx = -1.0;
            for (size_t k = 0; k < (size_t)rr.shape(0); ++k)
                if ((double)rr(k) > mx) mx = (double)rr(k);
            char buf[96];
            std::snprintf(buf, sizeof(buf), "pbs_n%d_max%.4f",
                          (int)rr.shape(0), mx);
            g_last_pbs_diag = buf;
        }
        double bestv = -1.0;
        for (size_t k = 0; k < (size_t)rr.shape(0); ++k)
            if ((double)rr(k) > bestv) bestv = (double)rr(k);
        // PRODUCTION: confidence = round(max_val, 5) -- the frontier value is
        // rounded to 5 decimals BEFORE the clicks9 comparison.
        const double bestv_r5 = std::round(bestv * 1e5) / 1e5;

        // ---- PRODUCTION SEMANTICS: clicks9 fallback comparison ----
        // probability.py:1219-1236: production compares
        //   confidence      = round(max_val, 5)
        //   _confidence     = round(1 - mine9/len(clicks9), 5)
        //   if _confidence > confidence: switch to interior cells
        // Both sides are rounded to 5 decimals first; the comparison is
        // STRICTLY greater.  Omitting the rounding made C++ take the
        // fallback on values that production treats as equal.
        {
            const double mine_num = (double)py::float_(pbs[1]);
            if (!clicks9.empty()) {
                const double mine9 = (double)a - mine_num - (double)num10;
                const double interior_safety_r5 =
                    std::round((1.0 - mine9 / (double)clicks9.size()) * 1e5) / 1e5;
                if (interior_safety_r5 > bestv_r5) {
                    // Pick the interior cell with the largest 5x5 opened
                    // count (production open_num5x5 weighting); tie-break
                    // deterministically on (x, y).
                    int best_opens = -1;
                    std::vector<Coord> interior_tied;
                    for (const auto& c : clicks9) {
                        int opens = 0;
                        for (int dy = -2; dy <= 2; ++dy)
                            for (int dx = -2; dx <= 2; ++dx) {
                                const int nx = c.first + dx;
                                const int ny = c.second + dy;
                                if (nx < 1 || nx > w || ny < 1 || ny > h) {
                                    ++opens;   // out-of-board counts as opened
                                    continue;
                                }
                                const int32_t v = cv[(size_t)ny * W2 + nx];
                                if (v >= 0 && v <= 8) ++opens;
                            }
                        if (opens > best_opens) {
                            best_opens = opens;
                            interior_tied.clear();
                            interior_tied.push_back(c);
                        } else if (opens == best_opens) {
                            interior_tied.push_back(c);
                        }
                    }
                    if (!interior_tied.empty()) {
                        Coord ch = deterministic_tie_break(interior_tied);
                        // The fallback's tie group is the open_num5x5 max
                        // group of the interior cells -- same stage-2
                        // semantics the Python det_choice records.
                        return make_response(ch.first, ch.second,
                                             interior_safety_r5, 1,
                                             "clicks9_fallback", mem.bytes,
                                             world_upper, prob,
                                             coords_to_pylist(interior_tied),
                                             coords_to_pylist(interior_tied));
                    }
                }
            }
        }
        std::vector<Coord> tied;
        for (size_t k = 0; k < (size_t)rr.shape(0); ++k)
            // P1-2 fix: production subtracts in float32 (numpy float32 array).
            // (float)bestv - rr(k) in double precision could add spurious tie
            // members; do the whole comparison in float32.
            if (float((float)bestv - rr(k)) <= 0.0005f && k < ordered_clicks.size())
                tied.push_back(ordered_clicks[k]);
        // ---- LAYER 3 tie-break: PSEQ v3 (recursive decide + saturate-inclusive
        // ---- LAYER 3 tie-break: PSEQ v5 — paper-faithful counting filters.
        // All four filters are O(|S|) counting operations over the existing
        // solution set (paper Table 4: 2.153ms/decision), NOT recursive
        // decides. For each tied candidate b (frontier cell):
        //   p(b,n) = fraction of solutions where b is safe with number n
        //   q(b)   = entropy of the number distribution
        //   s(b)   = p(b,0) — revealing a 0 guarantees a cascade
        //   e(b)   = E[# of b's closed neighbors that become determined]
        // Pick via P -> S -> E -> Q sequence; residual -> random.
        Coord chosen;
        py::list weighted_py;
        std::vector<std::vector<std::vector<int32_t>>> gmats;
        for (auto gitem : res_list) {
            py::array_t<int32_t, py::array::c_style | py::array::forcecast> arr(
                py::reinterpret_borrow<py::object>(gitem));
            auto r = arr.unchecked<2>();
            std::vector<std::vector<int32_t>> rows((size_t)r.shape(0),
                std::vector<int32_t>((size_t)r.shape(1)));
            for (py::ssize_t kk = 0; kk < r.shape(0); ++kk)
                for (py::ssize_t cc = 0; cc < r.shape(1); ++cc)
                    rows[(size_t)kk][(size_t)cc] = r(kk, cc);
            gmats.push_back(std::move(rows));
        }
        std::vector<int> ck;
        for (auto v : ck_list) ck.push_back(v.cast<int>());
        {
            // Tally per-candidate number-distribution counts.
            const size_t nTied = tied.size();
            // counts[i][n] = #solutions where tied[i] is safe with number n
            int counts[64][10];  // n up to 8, plus index 9 = mine (skip)
            memset(counts, 0, sizeof(counts));
            int64_t total_rows = 0;
            const int64_t kMaxRows = 200000;  // sound sampling cap
            Odometer od(std::move(ck));
            std::vector<int> index_list;
            while (od.next(index_list) && total_rows < kMaxRows) {
                // Build the full frontier assignment for this row.
                int row_vals[256];  // ordered_clicks values (0 or 1)
                size_t flat = 0;
                int64_t mine_total = 0;
                bool valid = true;
                for (size_t gi = 0; gi < gmats.size() && valid; ++gi) {
                    const auto& row = gmats[gi][(size_t)index_list[gi]];
                    const auto& gc = groups[gi];
                    for (size_t j = 0; j < row.size() && j < gc.size() && flat < 256; ++j) {
                        row_vals[flat] = row[j];
                        if (row[j] == 1) ++mine_total;
                        ++flat;
                    }
                }
                if (!valid || flat != ordered_clicks.size()) { ++total_rows; continue; }
                const int64_t rem = (int64_t)a - num10 - mine_total;
                if (rem < 0 || (int64_t)a > num10 + mine_total + (int64_t)clicks9.size()) { ++total_rows; continue; }
                // For each tied candidate: check safety & count number.
                for (size_t ti = 0; ti < nTied; ++ti) {
                    // find tied[ti]'s position in ordered_clicks
                    int pos = -1;
                    for (size_t k = 0; k < ordered_clicks.size(); ++k)
                        if (ordered_clicks[k] == tied[ti]) { pos = (int)k; break; }
                    if (pos < 0) continue;
                    if (row_vals[pos] == 1) continue;  // mine in this solution
                    // count mines among tied[ti]'s closed neighbors
                    int mn = 0;
                    const auto& c = tied[ti];
                    for (size_t k = 0; k < ordered_clicks.size(); ++k) {
                        if (row_vals[k] != 1) continue;
                        const auto& mc = ordered_clicks[k];
                        if (std::abs(mc.first - c.first) <= 1 &&
                            std::abs(mc.second - c.second) <= 1)
                            ++mn;
                    }
                    // Also count flagged mines (num10) adjacency
                    for (int dy = -1; dy <= 1; ++dy)
                        for (int dx = -1; dx <= 1; ++dx) {
                            if (!dx && !dy) continue;
                            const int nx = c.first + dx, ny = c.second + dy;
                            if (nx >= 1 && nx <= w && ny >= 1 && ny <= h &&
                                cv[(size_t)ny * W2 + nx] == 10)
                                ++mn;
                        }
                    if (mn <= 8) { counts[ti][mn]++; }
                }
                ++total_rows;
            }
            if (total_rows > 0) {
                // Compute s/e/q per candidate from the counts.
                struct SeqStats { double s = 0.0, e = 0.0, q = 0.0; };
                std::vector<SeqStats> sts(nTied);
                for (size_t ti = 0; ti < nTied; ++ti) {
                    double ent = 0.0, e_sum = 0.0;
                    for (int n = 0; n <= 8; ++n) {
                        double p = (double)counts[ti][n] / (double)total_rows;
                        if (p > 1e-12) {
                            ent -= p * std::log2(p);
                            if (n == 0) sts[ti].s += p;  // cascade guarantee
                            // e: expected newly-determined closed neighbors
                            const auto& c = tied[ti];
                            int closed_nbrs = 0;
                            for (int dy = -1; dy <= 1; ++dy)
                                for (int dx = -1; dx <= 1; ++dx) {
                                    if (!dx && !dy) continue;
                                    const int nx = c.first + dx, ny = c.second + dy;
                                    if (nx >= 1 && nx <= w && ny >= 1 && ny <= h &&
                                        cv[(size_t)ny * W2 + nx] == 9)
                                        ++closed_nbrs;
                                }
                            e_sum += p * (n == 0 ? closed_nbrs : closed_nbrs * (n == 1 ? 0.5 : 0.1));
                        }
                    }
                    sts[ti].e = e_sum;
                    sts[ti].q = ent;
                }
                // P -> S -> E -> Q sequential filters (P already applied:
                // tied = max-safety set). Then S, E, Q; residual random.
                std::vector<Coord> X = tied;
                std::vector<SeqStats> S = sts;
                auto filter_by = [&](double SeqStats::*field) {
                    if (X.size() <= 1) return;
                    double best = -1e18;
                    for (size_t i = 0; i < X.size(); ++i)
                        best = std::max(best, S[i].*field);
                    std::vector<Coord> X2; std::vector<SeqStats> S2;
                    for (size_t i = 0; i < X.size(); ++i)
                        if (S[i].*field >= best - 1e-12) { X2.push_back(X[i]); S2.push_back(S[i]); }
                    X.swap(X2); S.swap(S2);
                };
                filter_by(&SeqStats::s);
                filter_by(&SeqStats::e);
                filter_by(&SeqStats::q);
                if (!X.empty()) {
                    static thread_local uint64_t rs3 = 0x9E3779B97F4A7C15ull;
                    rs3 ^= rs3 << 13; rs3 ^= rs3 >> 7; rs3 ^= rs3 << 17;
                    chosen = X[(size_t)(rs3 % (uint64_t)X.size())];
                    weighted_py = coords_to_pylist(X);
                } else {
                    static thread_local uint64_t rs4 = 0x9E3779B97F4A7C15ull;
                    rs4 ^= rs4 << 13; rs4 ^= rs4 >> 7; rs4 ^= rs4 << 17;
                    chosen = tied[(size_t)(rs4 % (uint64_t)tied.size())];
                    weighted_py = coords_to_pylist(tied);
                }
            } else {
                static thread_local uint64_t rs5 = 0x9E3779B97F4A7C15ull;
                rs5 ^= rs5 << 13; rs5 ^= rs5 >> 7; rs5 ^= rs5 << 17;
                chosen = tied[(size_t)(rs5 % (uint64_t)tied.size())];
                weighted_py = coords_to_pylist(tied);
            }
        }
        return make_response(chosen.first, chosen.second,
                             bestv, 1, mem.allowed ? "limitation" : "memory_gate",
                             mem.bytes, world_upper, prob,
                             coords_to_pylist(tied), weighted_py);
    }

    // win_rate lookahead: validated C++ kernel, returns (res, sorted_clicks, total, c2p).
    // Production catches node-cap exceeded and falls back to pbs; do the same
    // instead of propagating RuntimeError (previous draft defect).
    py::tuple wr;
    bool wr_ok = true;
    try {
        // LAYER 2 depth-adaptive wiring (user directive: "前瞻的步数取决于
        // 当前的复杂度").  adaptive_depth here = cost-quantile shrink档位
        // (cpp/mscore.cpp:1212-1229 semantics: 1→trim top 8% most expensive,
        // 3→top 2%, 4→top 0.4%).  Mapping by limitation (production's own
        // difficulty proxy):
        //   limitation <= 10            -> 0 (no shrink; simple boards keep
        //                                  full 200/n depth)
        //   10 < limitation <= 14       -> 2 (trim top 3%)
        //   limitation > 14             -> 3 (trim top 2%)
        // These are initial values from the measured cost distribution
        // (midgame 260 points: p50 4,032 / p90 77,760); the acceptance gate
        // re-verifies via trigger-rate + per-decision timing.
        int adaptive = 0;
        if (limitation > 14.0) adaptive = 3;
        else if (limitation > 10.0) adaptive = 2;
        wr = py_win_rate(coords_to_pylist(ordered_clicks), coords_to_pylist(clicks9),
                         res_list, cell_value, ck_list, num10, a, w, h,
                         false, no_progress, node_cap, -1.0, adaptive);
    } catch (const std::runtime_error& e) {
        const std::string msg(e.what());
        if (msg.find("node cap exceeded") == std::string::npos) throw;
        wr_ok = false;
    }
    if (!wr_ok) {
        py::list oc = coords_to_pylist(ordered_clicks);
        py::list c9 = coords_to_pylist(clicks9);
        py::tuple pbs = py_pbs_compute(world_upper, num10, oc, c9, res_list,
                                       ck_list, a, no_progress, py::none());
        py::array_t<float, py::array::c_style | py::array::forcecast> res(pbs[0]);
        auto rr = res.unchecked<1>();
        double bestv = -1.0;
        for (size_t k = 0; k < (size_t)rr.shape(0); ++k)
            if ((double)rr(k) > bestv) bestv = (double)rr(k);
        std::vector<Coord> tied;
        for (size_t k = 0; k < (size_t)rr.shape(0); ++k)
            // P1-2 fix: production subtracts in float32 (numpy float32 array).
            // (float)bestv - rr(k) in double precision could add spurious tie
            // members; do the whole comparison in float32.
            if (float((float)bestv - rr(k)) <= 0.0005f && k < ordered_clicks.size())
                tied.push_back(ordered_clicks[k]);
        Coord chosen = tied.empty() ? ordered_clicks[0] : deterministic_tie_break(tied);
        py::dict prob;
        for (size_t k = 0; k < (size_t)rr.shape(0); ++k)
            if (k < ordered_clicks.size())
                prob[py::make_tuple(ordered_clicks[k].first, ordered_clicks[k].second)] =
                    py::float_((double)rr(k));
        return make_response(chosen.first, chosen.second,
                             bestv, 1, "node_cap_fallback", mem.bytes, world_upper,
                             prob, coords_to_pylist(tied));
    }
    py::list wr_res = wr[0].cast<py::list>();
    py::list wr_clicks = wr[1].cast<py::list>();
    py::dict c2p = wr[3].cast<py::dict>();
    // Mirror production: np.around(win_rate, 5) BEFORE finding max, then
    // exact equality (== np.max) to build the tie set -- NOT a float epsilon.
    std::vector<double> rounded(wr_res.size());
    double maxv = -1.0;
    for (size_t k = 0; k < wr_res.size(); ++k) {
        rounded[k] = std::round((double)py::float_(wr_res[k]) * 1e5) / 1e5;
        if (rounded[k] > maxv) maxv = rounded[k];
    }
    std::vector<Coord> tied;
    for (size_t k = 0; k < rounded.size(); ++k)
        if (rounded[k] == maxv) {  // exact equality, same as np.where
            auto t = wr_clicks[k].cast<py::tuple>();
            tied.emplace_back(py::int_(t[0]), py::int_(t[1]));
        }
    // LAYER 2 tie-break — Filter-U (D3-validated: 32.7% = py parity, p=0.0057
    // vs det; AAAI-17 U=39.14%).  Among exact-tie candidates, pick argmax
    // two-step survival u(b) = p_safe × E[perfect continuation] — implemented
    // via ExactEngine::after_click on the SAME world set the win_rate layer
    // just used (world_upper <= DSTAR was already excluded above, so this
    // rebuild only runs when DSTAR < world_upper <= win_rate region; worlds
    // can still be large here, so cap the evaluation and fall back to
    // (x,y)-min on any overrun — measured tie sets max=10, cap rarely binds).
    Coord chosen;
    if (tied.size() > 1 && world_upper <= 2000000) {
        try {
            // Materialize worlds once (identical semantics to OPTIMAL layer).
            std::vector<Bits> worlds;
            std::vector<std::vector<std::vector<int32_t>>> gmats;
            for (auto gitem : res_list) {
                py::array_t<int32_t, py::array::c_style | py::array::forcecast> arr(
                    py::reinterpret_borrow<py::object>(gitem));
                auto r = arr.unchecked<2>();
                std::vector<std::vector<int32_t>> rows((size_t)r.shape(0),
                    std::vector<int32_t>((size_t)r.shape(1)));
                for (py::ssize_t k = 0; k < r.shape(0); ++k)
                    for (py::ssize_t c = 0; c < r.shape(1); ++c)
                        rows[(size_t)k][(size_t)c] = r(k, c);
                gmats.push_back(std::move(rows));
            }
            std::vector<int> ck;
            for (auto v : ck_list) ck.push_back(v.cast<int>());
            const int nw_engine = (ncell + 63) / 64;
            Odometer od(std::move(ck));
            std::vector<int> index_list;
            while (od.next(index_list)) {
                int64_t mine_total = 0;
                Bits wbits; wbits.zero(nw_engine);
                for (size_t i = 0; i < index_list.size(); ++i) {
                    const auto& row = gmats[i][(size_t)index_list[i]];
                    // Same column-mapping fix as the OPTIMAL layer: columns
                    // belong to groups[i], not to the global ordered_clicks.
                    const auto& group_cells = groups[i];
                    for (size_t j = 0; j < row.size() && j < group_cells.size(); ++j)
                        if (row[j] == 1) {
                            const auto& c = group_cells[j];
                            wbits.set((size_t)c.second * W2 + c.first);
                            ++mine_total;
                        }
                }
                const int64_t rem = (int64_t)a - num10 - mine_total;
                if (rem < 0 || (int64_t)a > num10 + mine_total + (int64_t)clicks9.size())
                    continue;
                if (rem == 0) { worlds.push_back(wbits); continue; }
                auto sizes = get_list_sizes(rem, rem, (int)clicks9.size());
                std::vector<int> idx9;
                for (int s : sizes) {
                    ComboIter it((int)clicks9.size(), s);
                    while (it.next(idx9)) {
                        Bits wb = wbits;
                        for (int j : idx9) {
                            const auto& c = clicks9[(size_t)j];
                            wb.set((size_t)c.second * W2 + c.first);
                        }
                        worlds.push_back(wb);
                    }
                }
            }
            if (!worlds.empty()) {
                ExactEngine eng;
                eng.w = w; eng.h = h; eng.W2 = W2; eng.a = a;
                eng.node_cap = 200000;
                eng.total_bits = ncell;
                eng.nw = nw_engine;
                eng.all_bits.zero(eng.nw);
                eng.opened0.zero(eng.nw);
                for (int y = 1; y <= h; ++y)
                    for (int x = 1; x <= w; ++x) {
                        const int idx = eng.idx_of(x, y);
                        eng.all_bits.set(idx);
                        const int32_t v = cv[(size_t)y * W2 + x];
                        if (v >= 0 && v <= 8) eng.opened0.set(idx);
                    }
                eng.nbr.assign((size_t)ncell, Bits());
                for (int y = 1; y <= h; ++y)
                    for (int x = 1; x <= w; ++x) {
                        Bits m; m.zero(eng.nw);
                        for (int dy = -1; dy <= 1; ++dy)
                            for (int dx = -1; dx <= 1; ++dx) {
                                if (!dx && !dy) continue;
                                const int nx = x + dx, ny = y + dy;
                                if (nx >= 1 && nx <= w && ny >= 1 && ny <= h)
                                    m.set(eng.idx_of(nx, ny));
                            }
                        eng.nbr[(size_t)eng.idx_of(x, y)] = m;
                    }
                eng.world_bits = std::move(worlds);
                ExactEngine::WKey wk = eng.all_wkey();
                // Full-flow PSEQ: counting filters on win_rate exact ties.
                // The tie set has equal win_rate (P-filter no-op), so apply
                // S/E/Q counting from the solution matrices directly.
                std::vector<std::vector<std::vector<int32_t>>> gmats2;
                for (auto gitem : res_list) {
                    py::array_t<int32_t, py::array::c_style | py::array::forcecast> arr(
                        py::reinterpret_borrow<py::object>(gitem));
                    auto r2 = arr.unchecked<2>();
                    std::vector<std::vector<int32_t>> rows((size_t)r2.shape(0),
                        std::vector<int32_t>((size_t)r2.shape(1)));
                    for (py::ssize_t k2 = 0; k2 < r2.shape(0); ++k2)
                        for (py::ssize_t c2 = 0; c2 < r2.shape(1); ++c2)
                            rows[(size_t)k2][(size_t)c2] = r2(k2, c2);
                    gmats2.push_back(std::move(rows));
                }
                std::vector<int> ck2;
                for (auto v : ck_list) ck2.push_back(v.cast<int>());
                const size_t nT = tied.size();
                int cnts[64][10];
                memset(cnts, 0, sizeof(cnts));
                int64_t nrows = 0;
                const int64_t kMaxR = 50000;
                Odometer od2(std::move(ck2));
                std::vector<int> il2;
                while (od2.next(il2) && nrows < kMaxR) {
                    int rvals[256];
                    size_t flat2 = 0;
                    int64_t mt2 = 0;
                    for (size_t gi = 0; gi < gmats2.size() && flat2 < 256; ++gi) {
                        const auto& row2 = gmats2[gi][(size_t)il2[gi]];
                        const auto& gc2 = groups[gi];
                        for (size_t j = 0; j < row2.size() && j < gc2.size() && flat2 < 256; ++j) {
                            rvals[flat2] = row2[j];
                            if (row2[j] == 1) ++mt2;
                            ++flat2;
                        }
                    }
                    if (flat2 != ordered_clicks.size()) { ++nrows; continue; }
                    const int64_t rem2 = (int64_t)a - num10 - mt2;
                    if (rem2 < 0 || (int64_t)a > num10 + mt2 + (int64_t)clicks9.size()) { ++nrows; continue; }
                    for (size_t ti = 0; ti < nT; ++ti) {
                        int pos2 = -1;
                        for (size_t k3 = 0; k3 < ordered_clicks.size(); ++k3)
                            if (ordered_clicks[k3] == tied[ti]) { pos2 = (int)k3; break; }
                        if (pos2 < 0 || rvals[pos2] == 1) continue;
                        int mn2 = 0;
                        const auto& c3 = tied[ti];
                        for (size_t k3 = 0; k3 < ordered_clicks.size(); ++k3) {
                            if (rvals[k3] != 1) continue;
                            const auto& mc3 = ordered_clicks[k3];
                            if (std::abs(mc3.first - c3.first) <= 1 &&
                                std::abs(mc3.second - c3.second) <= 1)
                                ++mn2;
                        }
                        for (int dy = -1; dy <= 1; ++dy)
                            for (int dx = -1; dx <= 1; ++dx) {
                                if (!dx && !dy) continue;
                                const int nx = c3.first + dx, ny = c3.second + dy;
                                if (nx >= 1 && nx <= w && ny >= 1 && ny <= h &&
                                    cv[(size_t)ny * W2 + nx] == 10)
                                    ++mn2;
                            }
                        if (mn2 <= 8) cnts[ti][mn2]++;
                    }
                    ++nrows;
                }
                if (nrows > 0) {
                    struct SeqStats2 { double s = 0.0, e = 0.0, q = 0.0; };
                    std::vector<SeqStats2> sts2(nT);
                    for (size_t ti = 0; ti < nT; ++ti) {
                        for (int n = 0; n <= 8; ++n) {
                            double p = (double)cnts[ti][n] / (double)nrows;
                            if (p > 1e-12) {
                                sts2[ti].q -= p * std::log2(p);
                                if (n == 0) sts2[ti].s += p;
                                const auto& c3 = tied[ti];
                                int cn = 0;
                                for (int dy = -1; dy <= 1; ++dy)
                                    for (int dx = -1; dx <= 1; ++dx) {
                                        if (!dx && !dy) continue;
                                        const int nx = c3.first + dx, ny = c3.second + dy;
                                        if (nx >= 1 && nx <= w && ny >= 1 && ny <= h &&
                                            cv[(size_t)ny * W2 + nx] == 9)
                                            ++cn;
                                    }
                                sts2[ti].e += p * (n == 0 ? cn : (n == 1 ? cn * 0.5 : cn * 0.1));
                            }
                        }
                    }
                    std::vector<Coord> X = tied;
                    std::vector<SeqStats2> S2 = sts2;
                    auto filt = [&](double SeqStats2::*f) {
                        if (X.size() <= 1) return;
                        double b2 = -1e18;
                        for (size_t i = 0; i < X.size(); ++i) b2 = std::max(b2, S2[i].*f);
                        std::vector<Coord> Xn; std::vector<SeqStats2> Sn;
                        for (size_t i = 0; i < X.size(); ++i)
                            if (S2[i].*f >= b2 - 1e-12) { Xn.push_back(X[i]); Sn.push_back(S2[i]); }
                        X.swap(Xn); S2.swap(Sn);
                    };
                    filt(&SeqStats2::s);
                    filt(&SeqStats2::e);
                    filt(&SeqStats2::q);
                    if (!X.empty()) {
                        static thread_local uint64_t rs6 = 0x9E3779B97F4A7C15ull;
                        rs6 ^= rs6 << 13; rs6 ^= rs6 >> 7; rs6 ^= rs6 << 17;
                        chosen = X[(size_t)(rs6 % (uint64_t)X.size())];
                    } else {
                        static thread_local uint64_t rs7 = 0x9E3779B97F4A7C15ull;
                        rs7 ^= rs7 << 13; rs7 ^= rs7 >> 7; rs7 ^= rs7 << 17;
                        chosen = tied[(size_t)(rs7 % (uint64_t)tied.size())];
                    }
                } else {
                    static thread_local uint64_t rs8 = 0x9E3779B97F4A7C15ull;
                    rs8 ^= rs8 << 13; rs8 ^= rs8 >> 7; rs8 ^= rs8 << 17;
                    chosen = tied[(size_t)(rs8 % (uint64_t)tied.size())];
                }
            } else {
                chosen = deterministic_tie_break(tied);
            }
        } catch (...) {
            chosen = deterministic_tie_break(tied);   // safe fallback
        }
    } else {
        chosen = deterministic_tie_break(tied);
    }
    return make_response(chosen.first, chosen.second, maxv, 0, "win_rate",
                         mem.bytes, world_upper, c2p, coords_to_pylist(tied));
}

static py::dict py_decide(
    py::array_t<int32_t, py::array::c_style | py::array::forcecast> cell_value,
    int a, int w, int h, double wr_threshold, int64_t node_cap,
    int64_t mem_budget, uint64_t seed) {
    // decide() is a PURE function: output depends only on cell_value+config.
    // seed is accepted for harness symmetry but is intentionally unused;
    // tie-break is deterministic (option A, user-approved strategy change).
    (void)seed;
    return decide_impl(cell_value, a, w, h, wr_threshold, node_cap, mem_budget);
}

}  // namespace decision_layer
#endif  // MSW_TRIAL_DECIDE

// ==================== B1 合并编译保护宏（2026-11-05）====================
// 目的：允许 ab_native.cpp 与本文件编进**同一个 pyd**，从而让 C++ 侧
//      直接调用本文件已验证的枚举/决策实现（PartSolveCtx / WinRateCtx /
//      ExactEngine），避免自制枚举器与生产语义不一致。
//
// 用法：
//   · 单独编译（生产构建，cpp/build.bat）：未定义 AB_NATIVE_MERGED
//     ⇒ 正常生成 PYBIND11_MODULE(mscore)，行为与改动前**完全一致**。
//   · 合并编译（cpp/build_ab_native.bat）：定义 AB_NATIVE_MERGED
//     ⇒ 跳过本文件的模块入口，由 ab_native.cpp 提供唯一入口，
//       并在其中调用本文件的 run_ab_impl()。
//
// ⚠️ 本节只加预处理保护，**不改变任何逻辑**。
//     生产 mscore.pyd 的编译产物应逐位不变（已有 verify 流程可复核）。
// ========================================================================
#ifndef AB_NATIVE_MERGED
PYBIND11_MODULE(mscore, m) {
    m.doc() = "minesweeper_help native core (C++): part_solve / part_solve_single / "
              "win_rate / pbs_compute — 与 Python 实现语义完全一致的加速实现";
    m.def("estimate_mem", &decision_layer::py_estimate_mem,
          py::arg("worlds"), py::arg("candidates"),
          py::arg("budget") = decision_layer::DEFAULT_MEM_BUDGET,
          "Conservative win_rate stage-2 memory preflight.");
    m.def("split_frontier", &decision_layer::py_split_frontier,
          py::arg("cell_value"),
          "Split closed cells into constrained frontier and unconstrained interior.");
#ifdef MSW_TRIAL_DECIDE
    m.def("decide", &decision_layer::py_decide,
          py::arg("cell_value"), py::arg("a") = 99, py::arg("w") = 30, py::arg("h") = 16,
          py::arg("wr_threshold") = 8.0, py::arg("node_cap") = 2000000,
          py::arg("mem_budget") = decision_layer::DEFAULT_MEM_BUDGET,
          py::arg("seed") = 0,
          "TRIAL ONLY: unified native decision, compare against Python number5_1.");
#else
    // decide() is intentionally NOT exported in production builds: full
    // number5_1 semantic parity must be verified first.
#endif
    m.def("decision_preflight", &decision_layer::py_decision_preflight,
          py::arg("cell_value"), py::arg("worlds"),
          py::arg("budget") = decision_layer::DEFAULT_MEM_BUDGET,
          "Run frontier split and conservative memory gate in one native call.");
    m.def("packed_board_storage", [](int64_t cells) {
        if (cells < 0 || cells > PACK_MAX_CELLS) {
            throw std::invalid_argument("cells outside packed Board capacity");
        }
        const int64_t words = (cells + PACK_PER_WORD - 1) / PACK_PER_WORD;
        py::dict out;
        out["cells"] = cells;
        out["bits_per_cell"] = PACK_BITS;
        out["payload_bytes"] = (cells * PACK_BITS + 7) / 8;
        out["active_words"] = words;
        out["object_storage_bytes"] = static_cast<int64_t>(sizeof(Board));
        out["capacity_cells"] = PACK_MAX_CELLS;
        return out;
    }, py::arg("cells"),
       "Report packed Board payload and fixed-object storage; diagnostic only.");
    m.def("part_solve", &py_part_solve, py::arg("clicks"), py::arg("cell_value"), py::arg("a"),
          py::arg("w"), py::arg("h"), py::arg("progress"),
          "part_solve 内核（_try=False）：返回 (解矩阵[num×n], num_solve)");
    m.def("part_solve_single", &py_part_solve_single, py::arg("clicks"), py::arg("cell_value"),
          py::arg("cs"), py::arg("num10"), py::arg("num9"), py::arg("a"), py::arg("w"),
          py::arg("h"), py::arg("progress"),
          "part_solve_single 内核（_try=False）：返回 (解矩阵[num×n], num)");
    m.def("_hash_probe", [](py::array_t<int32_t, py::array::c_style | py::array::forcecast> board,
                            py::object cells) {
        // 测试钩子：证明"增量哈希 == 全盘重算哈希"不变式（评审要求）。
        // 逐步施加增量更新，返回 (增量, 全盘重算, 是否相等)。
        auto buf = board.unchecked<1>();
        Board b(buf.data(0), buf.data(0) + buf.shape(0));

        WinRateCtx ctx(1, 1, 0, false, py::object(), 0);
        WinRateCtx::Key128 cur = ctx.hash_board(b);
        py::list out;
        for (auto item : cells) {
            py::tuple t = py::cast<py::tuple>(item);
            size_t pos = py::cast<size_t>(t[0]);
            int32_t nv = py::cast<int32_t>(t[1]);
            cur = WinRateCtx::inc_hash(cur, pos, b[pos], nv);
            b[pos] = nv;
            WinRateCtx::Key128 ref = ctx.hash_board(b);
            out.append(py::make_tuple(
                py::make_tuple(cur.a, cur.b),
                py::make_tuple(ref.a, ref.b),
                (cur == ref)));
        }
        return out;
    }, py::arg("board"), py::arg("cells"),
       "测试钩子：逐步施加增量哈希更新，返回 (增量, 全盘重算, 是否相等) 列表");

    m.def("win_rate", &py_win_rate, py::arg("clicks"), py::arg("clicks9"), py::arg("res_list"),
          py::arg("cell_value"), py::arg("ck"), py::arg("num10"), py::arg("a"), py::arg("w"),
          py::arg("h"), py::arg("is_play"), py::arg("progress"),
          py::arg("node_cap") = 0,
          py::arg("depth_limit_override") = -1.0,
          py::arg("adaptive_depth") = 0,
          "win_rate 完整移植：返回 (res, sorted_clicks, total, clicks2p)。\n"
          "node_cap > 0 时限制递归节点数，超限抛 RuntimeError（调用方应回退）；\n"
          "node_cap = 0（默认）表示不限制，行为与旧版完全一致。\n"
          "depth_limit_override > 0 时覆盖默认深度上限 200/len(clicks)；\n"
          "默认 -1.0 = 沿用原公式，输出与旧版逐位一致。\n"
          "adaptive_depth > 0 时按成本预估主动变浅以避免 node_cap 降级；\n"
          "默认 0 = 关闭（生产默认路径，行为与旧版逐位一致）。");
    m.def("last_phase", []() {
        const LastPhase& p = g_last_phase;
        return py::make_tuple(p.worlds, p.enum_ms, p.outer_ms,
                              p.depth_limit, p.shrunk, p.wr_nodes);
    }, "返回最近一次 win_rate 调用的分阶段快照：\n"
       "(worlds, enum_ms, outer_ms, depth_limit, shrunk, wr_nodes)。\n"
       "shrunk=1 表示 adaptive_depth 规则**实际触发**并改变了 depth_limit。");
    m.def("pbs_compute", &py_pbs_compute, py::arg("total"), py::arg("num10"), py::arg("clicks"),
          py::arg("clicks9"), py::arg("res_list"), py::arg("ck"), py::arg("a"), py::arg("progress"),
          py::arg("visible"),
          "process_bigger_situation 纯计算部分：返回 (res, mine_num, total)");
    m.def("exact_eval", &py_exact_eval, py::arg("worlds"), py::arg("clicks"),
          py::arg("clicks9"), py::arg("cell_value"), py::arg("a"), py::arg("w"), py::arg("h"),
          py::arg("node_cap") = 0, py::arg("ply") = 0,
          "精确 V* 引擎（bench/arms_lookahead.py::ExactEngine 的 C++ 版）。\n"
          "worlds: 每个世界的雷位列表（bit=(y)*W2+x，含 1 格外框），由调用方枚举。\n"
          "返回 (q_list, cand_coords, nodes, v_opt)：\n"
          "  q_list[i] = after_click(候选 i) 的精确值（含 0 连锁 flood-fill）\n"
          "  cand_coords[i] = (x, y) 1-based\n"
          "ply > 0 时额外返回 ply_score 而非 after_click（用于浅层搜索臂）。\n"
          "node_cap > 0 时超限抛 RuntimeError。");
    m.def("md5",
          [](py::bytes data) {
              std::string s = data;  // 拷贝需持有 GIL
              std::string out;
              {
                  py::gil_scoped_release unlock;  // 大输入摘要期间释放 GIL
                  MD5 md5;
                  md5.update(reinterpret_cast<const unsigned char*>(s.data()), s.size());
                  out = md5.final();
              }
              return py::bytes(out);
          },
          py::arg("data"),
          "MD5 摘要（16 字节二进制），与 hashlib.md5 一致（诊断/测试用）");
}
#endif  // AB_NATIVE_MERGED（B1 合并编译保护宏，见模块入口处说明）

#ifdef AB_NATIVE_MERGED   // 桥接只在合并编译时存在，保证生产 pyd 可复现
// ==========================================================================
// ============ B1-a-1 桥接：整局 A/B 驱动的生产决策入口 ====================
// ==========================================================================
// 用户批准方案 B1-a-1：**只在本文件末尾追加**纯 C++ 桥接，
// 不改动上方任何一行已有代码；桥接内部直接调用 PartSolveCtx + wr_f，
// 复现生产 `number5_1` 的「切分 → part_solve → win_rate → argmax」链路。
//
// 为什么必须这样做（历史教训）：
//   ab_native.cpp 曾自己写了一套朴素 2^nf 全枚举 + 自设 `nf>20` 上限，
//   实测三个真实臂 0/100 胜、兜底率 100%，而生产约 36%
//   ⇒ 自制近似器**不可代表生产**。必须复用生产实现。
//
// 语义对齐要点：
//   · clicks / clicks9 切分：生产用 OpenCV 距离变换 res<=1.5 判前沿。
//     几何等价规则：未开格若与任一「已开数字格(1..8)」8 邻域相邻 ⇒ 前沿(clicks)，
//     否则 ⇒ 内部(clicks9)。（dist<=1.5 的几何含义即"紧贴已开数字格"）
//   · part_solve：直接构造 PartSolveCtx，复现 py_part_solve 的 cs 构建 + recurse。
//   · win_rate：复现 py_win_rate 的阶段1（枚举世界）+ 阶段2（逐候选 wr_f）。
//   · argmax：取最大胜率；并列取坐标序最小（确定性，生产用随机，这里为可复现）。
//
// ⚠️ 本桥接**不被生产路径调用**。生产构建（无 AB_NATIVE_MERGED）时，
//    它只是被编译进来；有宏时由 ab_native.cpp 的 extern 声明调用。
// ==========================================================================
namespace abnative_bridge {

// ---------- 切分 clicks / clicks9（几何等价于生产的 dist<=1.5）----------
static void split_frontier(const std::vector<int32_t>& cv, int w, int h,
                           std::vector<Coord>& clicks,
                           std::vector<Coord>& clicks9) {
    const int W2 = w + 2;
    clicks.clear();
    clicks9.clear();
    for (int y = 1; y <= h; ++y) {
        for (int x = 1; x <= w; ++x) {
            if (cv[(size_t)y * W2 + x] != 9) continue;   // 只看未开格
            // Mirror cv2.distanceTransform: interior-only 8-neighborhood,
            // any neighbor not in {9,10} implies distance <= 1.5.
            bool near_non_obstacle = false;
            for (int dy = -1; dy <= 1 && !near_non_obstacle; ++dy) {
                for (int dx = -1; dx <= 1; ++dx) {
                    if (!dx && !dy) continue;
                    int nx = x + dx, ny = y + dy;
                    if (nx < 1 || nx > w || ny < 1 || ny > h) continue;
                    int32_t v = cv[(size_t)ny * W2 + nx];
                    if (v != 9 && v != 10) { near_non_obstacle = true; break; }
                }
            }
            if (near_non_obstacle) clicks.emplace_back(x, y);
            else clicks9.emplace_back(x, y);
        }
    }
}

// ---------- part_solve：复现 py_part_solve 的 cs 构建 + recurse ----------
// 返回 solutions（每行是对应 clicks 的 0/1 解）；失败返回 false
static bool run_part_solve(const std::vector<int32_t>& cv, int w, int h, int a,
                           const std::vector<Coord>& clicks,
                           int64_t solution_cap,
                           std::vector<std::vector<Coord>>& click_groups,
                           std::vector<std::vector<std::vector<int32_t>>>& solutions) {
    click_groups.clear();
    solutions.clear();
    if (clicks.empty()) return false;

    // Match production number5_1 grouping: cells sharing at least one opened
    // constraint number belong to the same component; transitive merges are
    // required when a new cell touches multiple existing components.
    std::vector<std::set<Coord>> constraint_sets;
    constraint_sets.reserve(clicks.size());
    const int W2 = w + 2;
    for (const auto& pos : clicks) {
        std::set<Coord> se;
        for (int u = pos.first - 1; u <= pos.first + 1; ++u)
            for (int v = pos.second - 1; v <= pos.second + 1; ++v) {
                const int32_t val = cv[(size_t)v * W2 + u];
                if (val >= 1 && val <= 8) se.emplace(u, v);
            }
        constraint_sets.push_back(std::move(se));
    }
    std::vector<std::set<Coord>> group_sets;
    for (size_t i = 0; i < clicks.size(); ++i) {
        std::vector<size_t> hit;
        for (size_t g = 0; g < group_sets.size(); ++g) {
            bool overlap = false;
            for (const auto& c : constraint_sets[i]) {
                if (group_sets[g].count(c)) { overlap = true; break; }
            }
            if (overlap) hit.push_back(g);
        }
        if (hit.empty()) {
            click_groups.push_back({clicks[i]});
            group_sets.push_back(constraint_sets[i]);
        } else {
            const size_t keep = hit.front();
            click_groups[keep].push_back(clicks[i]);
            group_sets[keep].insert(constraint_sets[i].begin(), constraint_sets[i].end());
            for (size_t hi = hit.size(); hi-- > 1;) {
                const size_t src = hit[hi];
                click_groups[keep].insert(click_groups[keep].end(),
                                          click_groups[src].begin(), click_groups[src].end());
                group_sets[keep].insert(group_sets[src].begin(), group_sets[src].end());
                click_groups.erase(click_groups.begin() + (std::ptrdiff_t)src);
                group_sets.erase(group_sets.begin() + (std::ptrdiff_t)src);
            }
        }
    }
    for (size_t g = 0; g < click_groups.size(); ++g) {
        const auto& group = click_groups[g];
        const int64_t per_group_cap = solution_cap > 0 ? solution_cap : 200000;
        PartSolveCtx ctx(group, w, h, py::reinterpret_borrow<py::object>(Py_None), a,
                         2000000, per_group_cap);
        ctx.board = Board(cv.data(), cv.data() + (size_t)(h + 2) * (w + 2));
        for (size_t k = 0; k < ctx.board.size(); ++k)
            if (ctx.board.get(k) == 10) ++ctx.mine_count;
        ctx.cs.resize(group.size());
        for (size_t k = 0; k < group.size(); ++k) {
            const auto& pos = group[k];
            for (int u = pos.first - 1; u <= pos.first + 1; ++u)
                for (int v = pos.second - 1; v <= pos.second + 1; ++v) {
                    const int32_t val = ctx.board[(size_t)v * ctx.W2 + u];
                    if (val >= 1 && val <= 8) ctx.cs[k].emplace_back(u, v);
                }
        }
        ctx.recurse(0, 1);
        if (ctx.aborted) return false;
        if (ctx.solutions.empty()) return false;
        solutions.push_back(std::move(ctx.solutions));
    }
    return !solutions.empty();

#if 0  // legacy flat implementation retained only as historical reference
    PartSolveCtx ctx(clicks, w, h, py::reinterpret_borrow<py::object>(Py_None), a);
    const size_t ncell = (size_t)(h + 2) * (w + 2);
    ctx.board = Board(cv.data(), cv.data() + ncell);
    for (size_t k = 0; k < ncell; ++k)
        if (ctx.board.get(k) == 10) ++ctx.mine_count;

    // 构建 cs（照抄 py_part_solve:581-607）
    std::unordered_map<uint64_t, std::vector<Coord>> cs_map;
    for (const auto& ck : ctx.clicks) {
        int i = ck.first, j = ck.second;
        uint64_t key = ((uint64_t)(uint32_t)i << 32) | (uint32_t)(uint32_t)j;
        auto& lst = cs_map[key];
        for (int u = i - 1; u <= i + 1; ++u) {
            for (int v = j - 1; v <= j + 1; ++v) {
                int32_t val = ctx.board[(size_t)v * ctx.W2 + u];
                if (1 <= val && val <= 8) lst.emplace_back(u, v);
            }
        }
    }
    ctx.cs.resize(ctx.clicks.size());
    for (size_t k = 0; k < ctx.clicks.size(); ++k) {
        uint64_t key = ((uint64_t)(uint32_t)ctx.clicks[k].first << 32) |
                       (uint32_t)ctx.clicks[k].second;
        auto it = cs_map.find(key);
        if (it == cs_map.end()) return false;
        ctx.cs[k] = it->second;
    }
    ctx.recurse(0, 1);
    solutions = std::move(ctx.solutions);
    return !solutions.empty();
#endif
}


// 排序后的 clicks（供 argmax 后映射回原坐标）
static std::vector<Coord> clicks_check_;
// ---------- win_rate：复现 py_win_rate 的阶段1 + 阶段2 ----------
// 输入：clicks / clicks9 / part_solve 的 solutions（= res_list 单组展开）
// 输出：每候选的胜率（与 clicks 对齐）；失败返回 false
static bool run_win_rate(const std::vector<int32_t>& cv, int w, int h, int a,
                        const std::vector<Coord>& clicks,
                        const std::vector<Coord>& clicks9,
                        const std::vector<std::vector<Coord>>& click_groups,
                         const std::vector<std::vector<std::vector<int32_t>>>& solutions,
                        int64_t node_cap,
                        std::vector<double>& out_res) {
    out_res.clear();
    const int W2 = w + 2;
    const size_t ncell = (size_t)(h + 2) * (w + 2);

    // num10 = 盘面中已标雷数
    int64_t num10 = 0;
    for (size_t k = 0; k < ncell; ++k) if (cv[k] == 10) ++num10;

    WinRateCtx ctx(w, h, a, false, py::reinterpret_borrow<py::object>(Py_None), node_cap);

    std::vector<double> cp(clicks.size(), 0.0);
    std::vector<double> res(clicks.size(), 0.0);
    size_t total = 0;
    try {
        // ---- 阶段 1：枚举所有完整局面（照抄 py_win_rate:1098-1160 语义）----
        std::vector<Board> cell_value_list;
        {
            Board base(cv.data(), cv.data() + ncell);
            const size_t ng = click_groups.size();
            std::vector<int> index(ng, 0);
            while (true) {
                Board b = base;
                int64_t mine_total = 0;
                size_t flat = 0;
                for (size_t gi = 0; gi < ng; ++gi) {
                    const auto& row = solutions[gi][(size_t)index[gi]];
                    for (size_t j = 0; j < click_groups[gi].size(); ++j, ++flat) {
                        if (row[j] == 1) {
                            int u = clicks[flat].first, v = clicks[flat].second;
                            b[(size_t)v * W2 + u] = 10;
                            ++mine_total;
                        }
                    }
                }
                int64_t rem = (int64_t)a - num10 - mine_total;
                if (rem == 0) {
                    cell_value_list.push_back(std::move(b));
                } else if ((int64_t)a <= num10 + mine_total + (int64_t)clicks9.size()) {
                    auto sizes = get_list_sizes(rem, rem, (int)clicks9.size());
                    std::vector<int> index_l;
                    for (int s2 : sizes) {
                        ComboIter it((int)clicks9.size(), s2);
                        while (it.next(index_l)) {
                            Board nb = b;
                            for (int j : index_l) {
                                int u = clicks9[(size_t)j].first;
                                int v = clicks9[(size_t)j].second;
                                nb[(size_t)v * W2 + u] = 10;
                            }
                            cell_value_list.push_back(std::move(nb));
                        }
                    }
                }
                // 里程表进位
                size_t p = 0;
                while (p < ng) {
                    if (++index[p] < (int)solutions[p].size()) break;
                    index[p] = 0;
                    ++p;
                }
                if (p >= ng) break;
            }
        }
        total = cell_value_list.size();
        if (total == 0) return false;

        // ---- 阶段 2 前置：算每候选 safety（cp），并稳定降序排序 ----
        for (size_t k = 0; k < clicks.size(); ++k) {
            int u = clicks[k].first, v = clicks[k].second;
            size_t cnt10 = 0;
            for (const auto& b : cell_value_list)
                if (b[(size_t)v * W2 + u] == 10) ++cnt10;
            cp[k] = 1.0 - (double)cnt10 / (double)total;
        }
        std::vector<Coord> sorted_clicks = clicks;
        std::vector<double> sorted_cp = cp;
        {
            std::vector<size_t> order(clicks.size());
            for (size_t k = 0; k < clicks.size(); ++k) order[k] = k;
            std::stable_sort(order.begin(), order.end(),
                             [&](size_t p, size_t q) { return cp[p] > cp[q]; });
            for (size_t k = 0; k < clicks.size(); ++k) {
                sorted_clicks[k] = clicks[order[k]];
                sorted_cp[k] = cp[order[k]];
            }
        }
        clicks_check_ = sorted_clicks;   // 记录排序后顺序，供 argmax 映射回坐标

        // ---- 阶段 2：逐候选 wr_f ----
        std::vector<double> res_sorted(clicks.size(), 0.0);
        double running_max = 0.0;
        bool has_max = false;
        double depth_limit = 200.0 / (double)clicks.size();
        for (size_t i = 0; i < clicks.size(); ++i) {
            const int u = sorted_clicks[i].first, v = sorted_clicks[i].second;
            std::vector<std::vector<Board>> gb_all;
            std::unordered_map<int, size_t> val_slot;
            for (const auto& b : cell_value_list) {
                if (b[(size_t)v * W2 + u] != 10) {
                    Board nb = b;
                    int c9, c10;
                    cell_around(nb, w, h, W2, u, v, c9, c10);
                    nb[(size_t)v * W2 + u] = c10;
                    auto it = val_slot.find(c10);
                    if (it == val_slot.end()) {
                        val_slot.emplace(c10, gb_all.size());
                        gb_all.emplace_back();
                        gb_all.back().push_back(std::move(nb));
                    } else {
                        gb_all[it->second].push_back(std::move(nb));
                    }
                }
            }
            double win_p = 0.0;
            for (size_t g = 0; g < gb_all.size(); ++g) {
                double trans_prob = (double)gb_all[g].size() / (double)total;
                std::vector<Coord> new_clicks = sorted_clicks;
                new_clicks.erase(new_clicks.begin() + (std::ptrdiff_t)i);
                double win_r = wr_f(ctx, std::move(new_clicks),
                                    std::move(gb_all[g]), 1, depth_limit, nullptr);
                win_p += trans_prob * win_r;
            }
            res_sorted[i] = win_p;
            running_max = has_max ? std::max(running_max, win_p) : win_p;
            has_max = true;
        }
        out_res = res_sorted;
        return true;
    } catch (...) {
        return false;
    }
}


}  // namespace abnative_bridge

// DIAGNOSTIC buffer: last decision reason (filled by abnative_decide).
static char g_last_decide_reason[64] = {0};

// ==========================================================================
// 对外纯 C 接口（供 ab_native.cpp 以 extern 声明调用）
// ==========================================================================
// 生产决策入口：给定当前盘面，走 decision_layer::decide_impl —— 那是与
// Python number5_1 做过 6/6 双实现输出比对（含并列集逐坐标）的同一份 C++ 逻辑。
//
// 返回值：
//   1 = 成功（out_x/out_y 有效）
//   0 = 无可用决策（调用方降级）
//   2 = 预检/内存闸门拒绝（调用方降级）
//
// ⚠️ 历史：旧桥接（abnative_bridge::run_part_solve / run_win_rate 直连）
//    在真实局面存在分组前未约束递归、候选集口径漂移等问题，已不再被本函数调用。
extern "C" int abnative_decide(const int32_t* cv, int w, int h, int a,
                    int64_t node_cap, int* out_x, int* out_y,
                    int* out_total_worlds, double* out_best_rate) {
    if (out_x) *out_x = -1;
    if (out_y) *out_y = -1;
    if (out_total_worlds) *out_total_worlds = 0;
    if (out_best_rate) *out_best_rate = 0.0;
    if (cv == nullptr || w <= 0 || h <= 0) return 0;

#ifdef MSW_TRIAL_DECIDE
    // Reuse the byte-identical input array without copying.
    py::array_t<int32_t, py::array::c_style | py::array::forcecast> board(
        {(py::ssize_t)(h + 2), (py::ssize_t)(w + 2)});
    std::memcpy(board.mutable_data(), cv,
                sizeof(int32_t) * (size_t)(h + 2) * (size_t)(w + 2));
    py::dict r;
    try {
        r = decision_layer::decide_impl(board, a, w, h,
                                        /*wr_threshold*/ 8.0,
                                        node_cap > 0 ? node_cap : 2000000,
                                        decision_layer::DEFAULT_MEM_BUDGET);
    } catch (const std::exception&) {
        return 0;   // any native failure -> caller falls back safely
    }
    // DIAGNOSTIC: expose the decided reason so callers can count which path
    // actually fired (certain_safe / limitation / win_rate / memory_gate).
    if (g_last_decide_reason != nullptr) {
        std::string s = "unknown";
        try { s = py::str(r["reason"]); } catch (...) {}
        std::snprintf(g_last_decide_reason, sizeof(g_last_decide_reason), "%s", s.c_str());
    }
    if (out_total_worlds) *out_total_worlds = (int)py::int_(r["world_upper"]);
    if (out_best_rate) *out_best_rate = (double)py::float_(r["confidence"]);
    const int x = (int)py::int_(r["x"]);
    const int y = (int)py::int_(r["y"]);
    if (x < 1 || y < 1) return 0;
    if (out_x) *out_x = x;
    if (out_y) *out_y = y;
    return 1;
#else
    // Without MSW_TRIAL_DECIDE the merged build has no decision logic
    // available; fail closed so callers take their normal fallback.
    return 0;
#endif
}

#if 0  // legacy bridge kept only as historical reference (no longer called)
extern "C" int abnative_decide_LEGACY(const int32_t* cv, int w, int h, int a,
                    int64_t node_cap, int* out_x, int* out_y,
                    int* out_total_worlds, double* out_best_rate) {
    if (out_x) *out_x = -1;
    if (out_y) *out_y = -1;
    if (out_total_worlds) *out_total_worlds = 0;
    if (out_best_rate) *out_best_rate = 0.0;

    std::vector<int32_t> cel(cv, cv + (size_t)(h + 2) * (w + 2));
    std::vector<Coord> clicks, clicks9;
    abnative_bridge::split_frontier(cel, w, h, clicks, clicks9);
    if (clicks.empty()) return 0;
    std::vector<std::vector<Coord>> click_groups;
    std::vector<std::vector<std::vector<int32_t>>> sols;
    if (!abnative_bridge::run_part_solve(cel, w, h, a, clicks, node_cap,
                                         click_groups, sols)) return 0;
    // Python rewrites clicks by flattening click_list after component merges.
    // Preserve that exact order so solution columns map to coordinates.
    std::vector<Coord> ordered_clicks;
    for (const auto& group : click_groups)
        ordered_clicks.insert(ordered_clicks.end(), group.begin(), group.end());
    if (ordered_clicks.empty()) return 0;

    // The product of group solution counts is the world count consumed by
    // run_win_rate.  Apply the same conservative stage-2 gate before that
    // function allocates any complete-world boards.  Saturating multiplication
    // is required: overflow must never turn an unsafe request into "allowed".
    int64_t world_upper = 1;
    for (const auto& group : sols) {
        const int64_t n = static_cast<int64_t>(group.size());
        if (n <= 0 || world_upper > INT64_MAX / n) {
            world_upper = INT64_MAX;
            break;
        }
        world_upper *= n;
    }
    const int64_t candidates = 1 + static_cast<int64_t>(ordered_clicks.size()) +
                               static_cast<int64_t>(clicks9.size());
    // Handoff world_cap is an enumeration cap, not only a wr_f node cap.
    // Reject before materializing complete-world Boards when the grouped
    // product already exceeds the experiment's declared bound.
    if (node_cap > 0 && world_upper > node_cap) {
        if (out_total_worlds) *out_total_worlds = -1;
        return 2;
    }
    const auto preflight = decision_layer::estimate_mem_impl(
        world_upper, candidates, 1536LL * 1024LL * 1024LL);
    if (!preflight.allowed) {
        // -1 is a diagnostic sentinel: caller still takes its normal safe
        // fallback, but the result cannot be mistaken for a solver failure.
        if (out_total_worlds) *out_total_worlds = -1;
        return 2;
    }

    std::vector<double> rates;
    if (!abnative_bridge::run_win_rate(cel, w, h, a, ordered_clicks, clicks9,
                                       click_groups, sols,
                                      node_cap, rates)) return 0;
    if (rates.empty()) return 0;

    const auto& order = abnative_bridge::clicks_check_;
    if (order.size() != rates.size()) return 0;
    size_t best = 0;
    for (size_t i = 1; i < rates.size(); ++i)
        if (rates[i] > rates[best]) best = i;
    if (out_x) *out_x = order[best].first;
    if (out_y) *out_y = order[best].second;
    if (out_best_rate) *out_best_rate = rates[best];
    return 1;
}
#endif  // legacy bridge (historical reference only)

// DIAGNOSTIC accessor: reason string of the most recent abnative_decide call.
extern "C" const char* abnative_last_reason() {
#ifdef MSW_TRIAL_DECIDE
    return g_last_decide_reason;
#else
    return "";
#endif
}

// ==========================================================================
// BATCH decision entry: production clicks ALL certain-safe cells in ONE
// decision round (probability.py:1171-1189 appends every res>=0.99 cell to
// `pos`).  Per-cell round-trips inflated decisions/game from 12 to 159
// (13x) -> 48x slower and ~10pp lower win rate.
//
// Writes up to `cap` (x,y) 1-based pairs into out_xy (stride 2) and returns
// the count; returns 0 when no batch is available (caller then uses the
// single-cell abnative_decide path).
// ==========================================================================
extern "C" int abnative_decide_batch(const int32_t* cv, int w, int h, int a,
                                     int64_t node_cap, int* out_xy, int cap) {
#ifdef MSW_TRIAL_DECIDE
    if (cv == nullptr || w <= 0 || h <= 0 || out_xy == nullptr || cap <= 0)
        return 0;
    py::array_t<int32_t, py::array::c_style | py::array::forcecast> board(
        {(py::ssize_t)(h + 2), (py::ssize_t)(w + 2)});
    std::memcpy(board.mutable_data(), cv,
                sizeof(int32_t) * (size_t)(h + 2) * (size_t)(w + 2));
    py::dict r;
    try {
        r = decision_layer::decide_impl(board, a, w, h, /*wr_threshold*/ 8.0,
                                        node_cap > 0 ? node_cap : 2000000,
                                        decision_layer::DEFAULT_MEM_BUDGET);
    } catch (const std::exception&) {
        return 0;
    }
    const std::string reason = py::str(r["reason"]);
    if (reason != "certain_safe") return 0;   // batch only for certain-safe
    py::list tied = r["tied"].cast<py::list>();
    int n = 0;
    for (auto item : tied) {
        if (n >= cap) break;
        auto t = item.cast<py::tuple>();
        out_xy[2 * n] = (int)py::int_(t[0]);
        out_xy[2 * n + 1] = (int)py::int_(t[1]);
        ++n;
    }
    return n;
#else
    (void)cv; (void)w; (void)h; (void)a; (void)node_cap; (void)out_xy; (void)cap;
    return 0;
#endif
}
#endif  // AB_NATIVE_MERGED (bridge)
