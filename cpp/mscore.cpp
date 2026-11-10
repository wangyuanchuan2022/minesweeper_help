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
#include <limits>
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
// ⚠️ 修复（2026-11-09 安全评审 P2 #6）：原实现 `return msw_ctzll(x);` 是**调用自身**
// ⇒ 无限递归直至栈耗尽（GCC/Clang 构建下调用位扫描即崩）。
// 正确做法：调用真正的编译器内建。语义对齐 MSVC 分支：
//   · msw_ctzll(x)：_BitScanForward64 在 x==0 时返回 idx 未定义但调用方保证 x!=0；
//     这里对 x==0 返回 64（"无置位"约定），调用方正常只在 x!=0 时使用。
//   · msw_popcountll：__popcnt64 = __builtin_popcountll。
static inline int msw_ctzll(unsigned long long x) {
    return x ? __builtin_ctzll(x) : 64;
}
static inline int msw_popcountll(unsigned long long x) {
    return __builtin_popcountll(x);
}
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

// get_list 的尺寸范围钳制（start=0, stop=-1 路径——part_solve_single / 世界物化均如此调用）
// 返回需要枚举的组合大小列表（升序，含边界）。
//
// ⚠️ 2026-11-09 正确性修复（用户指令「看看计算是否准确」）：
//   原实现 `if (num > listnum - 1) num = listnum - 1;` 把上限钳到 **listnum-1**，
//   导致「从 listnum 个格里选 listnum 个」（即全部选中）这一**唯一合法情形**被
//   替换成「选 listnum-1 个」⇒ 枚举出的组合数从 C(N,N)=1 变成 C(N,N-1)=N，
//   且每个组合都比正确解**少一个元素**（少一个雷）。
//
//   实证（bench/repro_world_count_split.py，真实 30x16 盘面）：
//     · 物化世界数 36 ≠ 准入精确计数 ‖S‖ 30（同一批 odometer 行，必须相等）
//     · Σ P(该格是雷) = 98.6667 ≠ 剩余雷数 99（概率守恒律，纯数学恒等式）
//     · 分叉量 6 恰等于「rem==n9 的行数 6 × (n9-1) 」，三条算术完全闭合
//   影响：OPTIMAL 层在 rem==n9 的 odometer 行上物化出雷数不足的世界，
//        逐格概率因此错误（真实对局 200 局 / 1333 次 optimal 中触发 5 次 = 0.38%）。
//
//   正确语义：num 的上限是 listnum（不是 listnum-1）；`a` 的钳制保留（下界 1）。
//   注：`a < 1 → a = 1` 保持不变 —— 那是「至少枚举 1 个雷」的调用约定，
//   调用方在 rem==0 时已单独走 `worlds.push_back(wbits)` 分支，不经过本函数。
std::vector<int> get_list_sizes(int64_t a_in, int64_t num_in, int listnum) {
    int64_t a = a_in, num = num_in;
    if (a < 1) a = 1;
    if (num > (int64_t)listnum) num = (int64_t)listnum;   // ← 修复：原为 listnum - 1
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
            // 没有可行组合时返回可观察异常，由 Python 层走既有回退；
            // 禁止原生继续执行 num / total。
            py::gil_scoped_acquire acq;
            PyErr_SetString(PyExc_ValueError, "pbs_compute: no feasible combinations");
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
        if (!(total_w > 0.0)) {
            // 过滤后没有正权重组合；不要让原生模块除以 0，
            // 交给 Python 侧的统一不可用/先验路径处理。
            py::gil_scoped_acquire acq;
            PyErr_SetString(PyExc_ValueError, "pbs_compute: zero total weight");
            throw py::error_already_set();
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
    // ⚠️ 2026-11-09 墙钟时间闸（见 tick()）：<=0 表示不启用。
    // 由调用方在构造后设置：time_budget_sec + t_start（进入计算前的时间点）。
    double time_budget_sec = 0.0;
    std::chrono::steady_clock::time_point t_start{};

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
        // ⚠️ 2026-11-09 墙钟时间闸（真机实测：2^19 世界时单次 after_click 递归
        // 可跑 100+ 秒，而 node_cap 是节点数上限、与墙钟不成正比）。
        // 每 4096 节点查一次 steady_clock（避免每节点都查时钟拖慢）。
        // time_budget_sec <= 0 表示不启用。
        if (time_budget_sec > 0.0 && (nodes & 0xFFF) == 0) {
            const double elapsed = std::chrono::duration<double>(
                std::chrono::steady_clock::now() - t_start).count();
            if (elapsed > time_budget_sec)
                throw NodeCapExceeded(node_cap, nodes);   // 复用同一异常→安全降级
        }
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

};


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

// ---------------------------------------------------------------------------
// 前瞻/OPTIMAL 准入用的**解析组合数上界**
//
// ⚠️ 2026-11-09 方向性错误修复（真机实测：300 局 8525 决策，LAYER 2 进入 1531 次
//   ，其中 1529 次(99.9%)是 45ms 空烧后降级 pbs，只有 2 次成功）。
//
// ==================== 旧逻辑错在哪 ====================
// 旧代码把「组解乘积 × C(n9, rem_max)」当规模上界，其中
//     rem_max = max over 行 of (a - num10 - mine_total)
// 但 C(N, r) 是**单峰函数**（峰值在 r ≈ N/2），因此
//     max_r C(N, r) ≠ C(N, rem_max)
// 当 rem_max 靠近 N 时（残局常见：剩余雷几乎都能塞进内部格），
//     C(N, rem_max) = C(N,N) = 1，
// 而真正的峰值是 C(N, N/2)，两者相差可达**指数级**。
// 实测最坏样本：N=29、rem_max=29 ⇒ 旧值 1、真实峰值 C(29,14)=77,558,760
//   ⇒ 低估 7.76e7 倍；300 局扫描中最大低估达 log2 95.35（≈5e28 倍）。
//
// 后果：准入闸把「根本算不动的盘面」判为「规模很小」⇒ 放行进 LAYER 2 ⇒
//   物化时撞 HARD_CAP ⇒ 抛异常 → 降级 pbs（白烧时间，收益为零）。
//   这不是性能问题、也不是内存/节点/时间闸问题（把 node_cap 提到 500 万后
//   仍然 45ms 失败，failure reason 是 `lookahead_skip: hard cap`）。
//
// ==================== 正确上界 ====================
// 物化时对每个 odometer 行的 rem 走 get_list_sizes，其取值为
//     s ∈ [max(1, min(rem, N)), min(rem, N)]     （见 :436 钳制，2026-11-09 修复后）
// ⇒ 任一行的展开数 ≤ max_{s ∈ [0, min(R, N)]} C(N, s)，其中 R = a - num10。
// 而 C(N, s) 在 s ≤ floor(N/2) 上单调增 ⇒ 该上界 = C(N, min(R, N, floor(N/2))).
// 这是**sound** 上界（不漏任何合法行），且 O(1) 计算、无需枚举 odometer 行
//（旧 PRE-GUARD 曾用 4096 行截断采样，既有误差又慢）。
//
// ⚠️ 2026-11-09 修复 off-by-one：原上界用 N-1（复刻旧的错误钳制），
//    修复后 s 可达 N（即「内部格全是雷」，C(N,N)=1）。因为 C(N,N)=1 ≤ 任何
//    C(N,s)，把 s_hi 从 N-1 放到 N **不会放大**该上界（只多考虑一个值为 1 的
//    端点），所以这个修正是保守的、不会误杀。
//
// 实测误杀风险 = 0（300 局里「修正上界 > cap」但真实 win_rate 成功的次数为 0），
// 因此直接用解析上界作准入是安全的最小修法。
// ---------------------------------------------------------------------------
static double choose_log2_upper(int64_t n, int64_t r) {
    // 返回 log2 C(n, r)；非法/越界返回 -inf（调用方按 0 处理）。
    if (n <= 0 || r < 0 || r > n) return -std::numeric_limits<double>::infinity();
    // lgamma 路径，n 可达 480+，直接乘会溢出
    const double v = (std::lgamma((double)n + 1.0)
                      - std::lgamma((double)r + 1.0)
                      - std::lgamma((double)(n - r) + 1.0)) / std::log(2.0);
    return v < 0.0 ? 0.0 : v;
}

// 内部无约束格 clicks9（大小 N）在最坏情况下的展开数上界（log2）。
// rem_cap = a - num10（剩余雷数上限）。
static double inner_expand_log2_upper(int64_t N, int64_t rem_cap) {
    if (N <= 0) return 0.0;
    // get_list_sizes 的钳制：num = min(rem, N)，且 a>=1；故有效 s 上限 N
    // （2026-11-09 修复：原为 N-1，与旧错误钳制同步；C(N,N)=1 不放大上界）
    int64_t s_hi = rem_cap;
    if (s_hi > N) s_hi = N;
    if (s_hi < 0) s_hi = 0;
    // C(N, s) 在 s ∈ [0, floor(N/2)] 单调增
    int64_t s_star = s_hi;
    if (s_star > N / 2) s_star = N / 2;
    const double lb = choose_log2_upper(N, s_star);
    if (!std::isfinite(lb)) return 0.0;
    return lb < 0.0 ? 0.0 : lb;
}

// ===========================================================================
// 论文 ‖S‖ 的**精确**计数（2026-11-09，用户指令「对齐论文」）
//
// ==================== 论文原文（AAAI-17 WS-17-15, Algorithm 1/3）============
//   Algorithm 1 line 5-7:
//       S ← { s | s ∈ P(C), ‖s‖ = m }            // 未开格 C 的幂集，取恰好 m 雷
//       for all b ∈ B:  S ← { s ∈ S | ‖s ∩ U(b)‖ = f(b) }   // 逐数字过滤
//   Algorithm 3 line 2:
//       if ‖S‖ ≤ criterion then return OPTIMAL(...)
//
// ⇒ **‖S‖ = 与当前全部观测一致的、完整的雷布局总数**（all consistent worlds）。
//   它是**精确值**，不是上界、不是近似。论文用它当准入判据，因为 OPTIMAL 的
//   递归成本在结构上正比于 ‖S‖（要遍历每个世界）⇒ 判据与成本天然正相关。
//
// ==================== 本项目的旧实现错在哪 ====================
// 旧判据用 `world_upper`，它只是**前沿各组解数的乘积**：
//       world_upper = ∏ ck[i]                    （漏掉内部格组合！）
// 而真实的 ‖S‖ 是：
//       ‖S‖ = Σ_{组解组合} C(n9, rem)  ，rem = a - num10 - 该行的雷数
// 二者关系：‖S‖ ≥ world_upper（当 n9=0 或 rem 恒为 0/1 时才相等）。
//
// 实测后果（300 局）：world_upper 与 OPTIMAL 真实耗时的 Spearman 仅 **0.738**；
// 而含 C(n9,rem) 的 cost_log2 是 **0.888**。差的那 0.15 就是被漏掉的项。
// 更糟的是极端局面：n9=29、rem 逐行落在 0..29，世界数可差 7.7e7 倍——
// 当年 LAYER 2 的 45ms 空烧与 20GB OOM 都是这个漏项的后果。
//
// ==================== 本实现 ====================
// 复刻 Python 参考实现 bench/compare_2ply_vs_winrate.py::count_worlds_exact：
//   total = Σ_{odometer 行} [ rem==0 ? 1 : (0<rem<=n9 ? C(n9,rem) : 0) ]
// 饱和到 INT64_MAX（绝不溢出成"允许"）。
// 成本：odometer 行走 O(world_upper) 行，每行查表 C(n9,·) 为 O(1)（预计算）。
// ⇒ 在 DSTAR 量级（≤ 数千）下行走成本可忽略（实测 per-decision < 0.1ms）。
//
// ⚠️ 与 get_list_sizes 的钳制保持一致（2026-11-09 修复后：s ∈ [1, min(rem, n9)]），
//    计数口径按"可行世界数"算（rem==0 → 1 个；0<rem≤n9 → C(n9,rem)；
//    rem>n9 → 0）。修复前钳制为 n9-1，导致 rem==n9 的行计数成 C(n9,n9-1)=n9
//    （正确应为 C(n9,n9)=1）⇒ 准入计数与物化数分叉。现两者口径一致。
// ---------------------------------------------------------------------------
// 预计算 C(n9, k) 表（k=0..min(n9, rem 上限)），用 double 存以免溢出；
// 超出 INT64_MAX 时置 saturate 标记。
static std::vector<double> make_choose_table(int64_t n9, int64_t k_max,
                                             bool* saturates) {
    std::vector<double> tbl;
    *saturates = false;
    if (n9 <= 0) return tbl;
    if (k_max > n9) k_max = n9;
    if (k_max < 0) k_max = 0;
    tbl.assign((size_t)k_max + 1, 1.0);
    double acc = 1.0;
    for (int64_t k = 1; k <= k_max; ++k) {
        acc = acc * (double)(n9 - k + 1) / (double)k;   // C(n9,k) 递推
        if (!(acc > 0.0) || acc > 9.0e18) {              // 溢出/失稳 → 饱和
            *saturates = true;
            acc = 9.0e18;
        }
        tbl[(size_t)k] = acc;
    }
    return tbl;
}

static int64_t count_situations_exact(
    const std::vector<std::vector<std::vector<int32_t>>>& gmats,
    const std::vector<int>& ck, int64_t a, int64_t num10, int64_t n9,
    int64_t cap, int64_t* rows_walked) {
    // Σ_{odometer 行} 该行的可行世界展开数；一旦 > cap 立即返回 cap+1（哨兵）。
    //
    // ⚠️ 2026-11-09 性能修复（对齐论文首版实测 3.8 倍退化）：
    //   首版的早退阈值是 `total > 9e18`（只防 int64 溢出），而调用方只关心
    //   「是否 ≤ DSTAR(256)」。二者差 16 个数量级 ⇒ 55.4% 的计数是
    //   **走完全程才发现超限**的白烧（实测 4097-16384 桶平均 30ms/决策，
    //   2000 局总耗时从 129s 涨到 490s）。
    //   ⇒ 现按调用方的 cap 早退：超 cap 立刻返回，不再走剩余 odometer 行。
    int64_t total = 0;
    bool sat = false;
    const int64_t k_cap = std::min<int64_t>(n9, std::max<int64_t>(0, a - num10));
    std::vector<double> ctab = make_choose_table(n9, k_cap, &sat);

    // 预计算每行雷数（避免每行重扫矩阵）
    std::vector<std::vector<int64_t>> row_mines(gmats.size());
    for (size_t gi = 0; gi < gmats.size(); ++gi) {
        row_mines[gi].resize(gmats[gi].size(), 0);
        for (size_t ri = 0; ri < gmats[gi].size(); ++ri) {
            int64_t mt = 0;
            for (int32_t v : gmats[gi][ri]) if (v == 1) ++mt;
            row_mines[gi][ri] = mt;
        }
    }

    // ---- O(1) 严格下界预判（2026-11-09 性能优化，实测可省 56% 的白烧计数）----
    // 原理：任一行对 ‖S‖ 的贡献是 C(n9, rem_row)（rem_row>0）或 1（rem_row==0）。
    //   而 C(n9, k) 对 1 ≤ k ≤ n9 的最小非平凡值是 C(n9,1)=n9（k=1）与 C(n9,n9-1)=n9；
    //   中间的 k 只会更大（C 在 k≤n9/2 单调增）。
    // ⇒ **若存在任意一行使 rem_row ≥ 1，则 ‖S‖ ≥ n9**。
    //   （若所有行 rem_row==0，则 ‖S‖ = 行数，可能远小于 n9，此下界不适用。）
    // ⇒ 当 n9 > cap 且存在 rem≥1 的行时，可直接判定 ‖S‖ > cap，
    //    无需走 odometer。实测 1469 决策中 501 次（34.1%）可这样提前拒绝。
    //
    // ⚠️ 这不是"上界"而是"下界"，方向安全：下界已超 cap ⇒ 真实值必超 ⇒ 拒绝正确。
    if (n9 > cap) {
        bool any_rem_ge1 = false;
        for (size_t gi = 0; gi < row_mines.size() && !any_rem_ge1; ++gi)
            for (size_t ri = 0; ri < row_mines[gi].size(); ++ri) {
                if (a - num10 - row_mines[gi][ri] >= 1) { any_rem_ge1 = true; break; }
            }
        if (any_rem_ge1) {
            if (rows_walked) *rows_walked = 0;   // 未走 odometer
            return cap + 1;
        }
    }

    const int64_t cap_sentinel = cap + 1;
    Odometer od(ck);
    std::vector<int> il;
    int64_t walked = 0;
    while (od.next(il)) {
        ++walked;
        int64_t mt = 0;
        for (size_t i = 0; i < il.size() && i < row_mines.size(); ++i)
            mt += row_mines[i][(size_t)il[i]];
        const int64_t rem = a - num10 - mt;
        int64_t add = 0;
        if (rem == 0) {
            add = 1;
        } else if (rem > 0 && rem <= n9) {
            if (sat) {
                // 组合数已饱和 ⇒ 结果必然 > cap ⇒ 提前返回
                if (rows_walked) *rows_walked = walked;
                return cap_sentinel;
            }
            const double c = ctab[(size_t)rem];
            if (c >= 9.0e18) {
                if (rows_walked) *rows_walked = walked;
                return cap_sentinel;
            }
            add = (int64_t)c;
        }   // rem > n9 或 rem < 0 ⇒ 该行不产生可行世界（0）
        if (total > INT64_MAX - add) {
            if (rows_walked) *rows_walked = walked;
            return cap_sentinel;
        }
        total += add;
        // 早退：已超调用方的 cap ⇒ 不必走完剩余行
        if (total > cap) {
            if (rows_walked) *rows_walked = walked;
            return cap_sentinel;
        }
    }
    if (rows_walked) *rows_walked = walked;
    return total;
}

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

// 前向声明：定义在文件后部（帮助前瞻小节），OPTIMAL 分支在更早的位置调用。
static py::list transition_branch_board(const ExactEngine& eng,
                                        const std::vector<int32_t>& cv,
                                        const ExactEngine::WKey& wk,
                                        const Bits& opened,
                                        int cx, int cy);

static py::list coords_to_pylist(const std::vector<Coord>& v) {
    py::list out;
    for (const auto& c : v) out.append(py::make_tuple(c.first, c.second));
    return out;
}

// ---- 诊断：最近一次 decide 的论文 ‖S‖ 与生效 DSTAR（2026-11-09 对齐论文）----
// thread_local：帮助线程与 GUI 线程会并发调 decide，全局变量会数据竞争
// （本项目 2026-11-08 已因同类问题触发过 0xC0000005）。
static thread_local int64_t g_last_situations = -1;   // 精确 ‖S‖；-1=未计数
static thread_local int64_t g_last_dstar = -1;        // 本次生效 DSTAR

static py::dict make_response(int x, int y, double confidence, int path,
                              const char* reason, int64_t estimated_bytes,
                              int64_t world_upper, py::object prob = py::dict(),
                              py::object tied = py::list(),
                              py::object weighted = py::list(),
                              py::object unused = py::none(),
                              py::object transitions = py::none(),
                              py::object transition_meta = py::none()) {
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
    // 真实状态转移（仅 OPTIMAL 层提供；缺省 None ⇒ UI 明确显示"暂无"）。
    // transitions[i] = {probability(条件概率: 给定该格安全), new_cells, label}
    // transition_meta = {p_click_safe, basis, world_count}
    d["transitions"] = transitions;
    d["transition_meta"] = transition_meta;
    return d;
}

// Deterministic tie-break (user-approved strategy change, option A):// among all candidates sharing the maximum value, pick the smallest
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
// ⚠️ 必须 thread_local（2026-11-08 崩溃修复）：自动线程与帮助线程会**同时**
// 调用 decide（日志实测两线程交错）。全局 std::string 被两线程并发赋值会
// 触发堆重分配竞争 → 内存损坏 → 进程级 0xC0000005（"该内存不能为 read"）。
// 本模块其余诊断状态（g_last_phase 等）同样用 thread_local，此处对齐。
static thread_local std::string g_last_pbs_diag;

static py::dict decide_impl(
    py::array_t<int32_t, py::array::c_style | py::array::forcecast> cell_value,
    int a, int w, int h, int64_t node_cap,
    int64_t mem_budget, bool is_play) {
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
    // 分组依据：邻域已开数字必须与 part_solve 建约束的口径一致（1..8，含 8）。
    // ⚠️ 修复（2026-11-08，根因见 output/_minrepro_8_consistent.py）：
    // 原实现沿用 get_set_1 的 0<v<8（排除 8），而 run_part_solve 建约束用
    // 1<=v<=8（含 8）。数字 8 是「8 邻格全是雷」的最强约束，排除它会让贴着 8
    // 的 frontier 格各自独立成组，组内 cs 却仍含 8——recurse 的 check_leaf /
    // check_bound 恒不满足 ⇒ part_solve 解集为空 ⇒ decide 返回
    // reason=empty_group / x=y=-1，即真机日志里的「决策不可用」。
    // 中局不显形（frontier 格通常还贴着 1..7 可合组）；残局只剩零星格时必炸。
    for (size_t k = 0; k < clicks.size(); ++k) {
        const auto& p = clicks[k];
        for (int u = p.first - 1; u <= p.first + 1; ++u)
            for (int v = p.second - 1; v <= p.second + 1; ++v) {
                const int32_t val = cv[(size_t)v * W2 + u];
                if (val >= 1 && val <= 8) cell_sets[k].emplace(u, v);
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

    // =========================================================================
    // 前瞻层规模估算（诊断字段 cost_log2 的来源）。
    //
    // 历史（保留供追溯，勿据此新增逻辑）：
    //   · 旧准入阈值 limitation = 0.8*n9 + log2(world_upper) 与真实成本**方向相反**
    //     （实测 Spearman = -0.575），且与 OPTIMAL 层形成空集互斥
    //     ⇒ 旧 reason=win_rate 实测 0 次触发。该阈值已废弃。
    //   · 旧规模估算 log2_total = log2(world_upper) + log2 C(n9, rem_max) 有
    //     **方向性错误**：C(N,r) 单峰，rem_max 靠近 N 时严重低估（最坏 5e28 倍）。
    //   · 该错误导致 LAYER 2 放行「根本算不动的盘面」，45ms 白烧后降级 pbs
    //     （300 局实测 1529/1531 全中）。LAYER 2 已于 2026-11-09 按用户裁决移除。
    //
    // 现实现（正确、sound、O(1)）：
    //   log2_total_est = log2(world_upper) + max_s log2 C(N, s)
    //   s = min(R, N, N/2)，R = a - num10
    //   ⇒ 任一 odometer 行的展开数都被它界住（见 inner_expand_log2_upper）。
    //
    // 两种 rem 口径（历史坑，勿混用）：
    //   · 实际物化用逐行 rem = a - num10 - mine_total（逐行不同）；
    //   · 估算器只能用全局上界 R = a - num10（枚举行太贵）。
    // =========================================================================
    int64_t rem_cap_wr = (int64_t)a - num10;
    if (rem_cap_wr < 0) rem_cap_wr = 0;
    double log2_total_est = std::log2((double)std::max<int64_t>(1, world_upper));
    log2_total_est += inner_expand_log2_upper((int64_t)clicks9.size(), rem_cap_wr);

    // ⚠️ 2026-11-09 修正（评审 D2 + 真机 OOM 根因，见 :2545 注释）：
    // 用**与 COST_CAP 同口径**的真实世界数重算 mem：
    //   真实世界数 = world_upper × C(n9, rem_max)（饱和）
    //   每世界字节 = sizeof(Bits) ≈ 88 B（ExactEngine 的表示）
    // ⇒ 这样 mem.allowed 与 log2_total_est <= COST_CAP 是两个**同集合**的检查：
    //   log2_total_est 按「世界个数」限规模，mem 按「世界个数 × 88B」限内存，
    //   二者一致且都以 1.5GB 硬上限为界（88B 时 1.5GB 可容 ~1,807 万世界，
    //   远大于 COST_CAP=699k ⇒ 实际由 COST_CAP 主导，mem 是冗余双保险）。
    const auto mem = [&]() -> MemEstimate {
        const double bits_bytes = 88.0;      // sizeof(Bits)
        // log2_worlds → 世界数（饱和到 INT64_MAX，绝不溢出成"允许"）
        const int64_t worlds_true =
            log2_total_est >= 63.0
                ? INT64_MAX
                : (int64_t)std::llround(std::pow(2.0, log2_total_est));
        const long double raw = (long double)worlds_true * (long double)bits_bytes;
        const int64_t bytes = raw >= (long double)INT64_MAX
                                  ? INT64_MAX : (int64_t)raw;
        MemEstimate e;
        e.bytes = bytes;
        // P1/P0-4 修复（review2 code-reviewer P1-1）：1.5GB 是用户铁律的
        // 硬上限，不允许被公开 decide 的 mem_budget 形参穿透。决策层一律
        // 使用 DEFAULT_MEM_BUDGET；诊断用的 estimate_mem 仍可调（它不参与
        // 决策准入）。形参保留仅为兼容既有 pybind 签名，不再消费其数值。
        e.budget = DEFAULT_MEM_BUDGET;
        e.worlds = worlds_true;
        e.candidates = candidates;
        e.allowed = bytes <= e.budget;
        return e;
    }();

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
    // Implementation reuses the existing exact-V* kernel (ExactEngine) —
    // zero new kernel code.  Worlds are materialized exactly like
    // py_win_rate stage-1 (Odometer over groups + clicks9 combinations), then
    // converted to bit-packed worlds for the engine.
    //
    // 层级关系（2026-11-09 现状）：OPTIMAL 是唯一的前瞻型决策层；
    // 其余情况走 pbs + PSEQ。论文 D* 的另一半「启发式 PSEQ」即此。
    // =========================================================================
    // 【对齐论文 2026-11-09】DSTAR 判据改用论文的 ‖S‖（完整一致世界数）
    //
    // 论文 Algorithm 3 原文：`if ‖S‖ ≤ criterion then return OPTIMAL(...)`
    // 其中 ‖S‖ = { s | s ∈ P(C), ‖s‖ = m, ∀b: ‖s∩U(b)‖=f(b) } 的总数，
    // 即**与全部观测一致的完整雷布局数**（精确值，非上界）。
    //
    // 旧实现用 world_upper（前沿各组解数乘积）漏掉了内部格组合 C(n9,rem)，
    // 导致判据与实际成本相关性只有 0.738（含 C(n9,rem) 的 cost_log2 是 0.888），
    // 且极端局面低估达 7.7e7 倍（LAYER 2 空烧与 OOM 的同一病根）。
    //
    // 新准入（两级）：
    //   级 1（廉价粗筛）：world_upper ≤ DSTAR*K —— 因 ‖S‖ ≥ world_upper，
    //        world_upper 本身超限就已确定进不去，免走 odometer。
    //        取 K=64 是经验上限（C(n9,rem) 对 n9≤480 极少超过 2^64；
    //        超了会在级 2 精确算出后拒绝，不影响正确性只影响一点速度）。
    //   级 2（精确判据）：count_situations_exact(...) ≤ DSTAR —— 论文语义。
    // 成本：级 2 走 O(world_upper) 行 odometer，实测 DSTAR 量级下 < 0.1ms。
    //
    // DSTAR 默认值：论文选用 PSEQ-D256（criterion=256 世界）。
    //   本项目历史默认 400，现在对齐论文改 256（可用 MSW_DSTAR 覆盖）。
    //   帮助模式仍 ×3（1200）——帮助推理可接受更大穷举。
    // =========================================================================
    const bool help_mode_dstar = !is_play;
    static const int64_t DSTAR_AUTO = [] {
        const char* e = std::getenv("MSW_DSTAR");
        if (e) {
            long long v = std::atoll(e);
            if (v >= 0) return (int64_t)v;
        }
        return (int64_t)256;   // 论文 PSEQ-D256 的 criterion
    }();
    const int64_t DSTAR = help_mode_dstar ? DSTAR_AUTO * 3 : DSTAR_AUTO;

    // ---- 级 1：world_upper 廉价粗筛（必要条件）----
    // K=4：若 world_upper > 4*DSTAR，则 ‖S‖ ≥ world_upper 必然超限。
    // ⚠️ 2026-11-09 性能修复：首版取 K=64（=16384），实测该桶
    //    （world_upper 4097-16384）平均 30ms/决策，是所有桶里最贵的白烧区
    //    —— 因为计数要走完 4096~16384 行的 odometer 才发现超限。
    //    收紧到 K=4 后，最多只走 4*DSTAR(≈1024) 行，且配合 cap 早退，
    //    单次计数的上界成本降到微秒级。
    //    正确性不受影响：‖S‖ ≥ world_upper，故 world_upper > cap 时必然超限。
    const int64_t coarse_cap = DSTAR * 4;
    bool situations_ok = false;
    int64_t situations = 0;     // 精确 ‖S‖（超 cap 时为 cap+1 哨兵）
    g_last_situations = -1;     // 诊断：本次精确 ‖S‖（-1 = 未计数）
    g_last_dstar = DSTAR;       // 诊断：本次生效的 DSTAR

    // ---- gmats 只解析一次，两级判据与物化共用 ----
    // ⚠️ 2026-11-09 性能修复：首版在级 2 里解析一份 gmats_pre、物化时又解析一份
    //    gmats，而这是 res_list → vector<vector<vector<int32_t>>> 的**全量拷贝**
    //    （每决策点多组 × 多解 × 组宽），实测是性能退化的次要但确定的来源。
    //    现解析一次，按需传递；只有真正进入物化才需要它，故用 unique_ptr 惰性化
    //    不划算（判定本身也要它），直接提前解析。
    std::vector<std::vector<std::vector<int32_t>>> gmats_shared;
    std::vector<int> ck_shared;
    bool gmats_ok = false;
    if (world_upper <= coarse_cap) {
        try {
            gmats_shared.reserve(res_list.size());
            for (auto gitem : res_list) {
                py::array_t<int32_t, py::array::c_style | py::array::forcecast> arr(
                    py::reinterpret_borrow<py::object>(gitem));
                auto rr = arr.unchecked<2>();
                std::vector<std::vector<int32_t>> rows((size_t)rr.shape(0),
                    std::vector<int32_t>((size_t)rr.shape(1)));
                for (py::ssize_t k = 0; k < rr.shape(0); ++k)
                    for (py::ssize_t c = 0; c < rr.shape(1); ++c)
                        rows[(size_t)k][(size_t)c] = rr(k, c);
                gmats_shared.push_back(std::move(rows));
            }
            for (auto v : ck_list) ck_shared.push_back(v.cast<int>());
            gmats_ok = true;
        } catch (const std::exception&) {
            gmats_ok = false;
        }
    }

    if (gmats_ok) {
        // ---- 级 2：精确计数（论文 ‖S‖）----
        try {
            int64_t walked = 0;
            situations = count_situations_exact(
                gmats_shared, ck_shared, (int64_t)a, (int64_t)num10,
                (int64_t)clicks9.size(), DSTAR, &walked);
            situations_ok = (situations <= DSTAR);
            g_last_situations = situations;   // 诊断导出（py_decide 读取）
        } catch (const std::exception&) {
            // 计数失败 ⇒ 保守拒绝（不因异常放行昂贵层）
            situations_ok = false;
        }
    }

    // 进 OPTIMAL 的最终条件（论文 Algorithm 3 line 2 语义）：
    //   ‖S‖ ≤ criterion  —— situations_ok 即此判定
    // 内存保险 mem.allowed 仍保留（用户铁律：1.5GB 硬上限不可调），
    // 但它在 ‖S‖≤DSTAR 时必然为真（DSTAR×88B ≪ 1.5GB），故不再先行短路。
    if (situations_ok && mem.allowed) {
        // Materialize the world set (mine layouts over the full board), same
        // semantics as py_win_rate stage-1: enumerate group solution rows and
        // clicks9 combinations subject to remaining-mine feasibility.
        std::vector<Bits> worlds;
        bool optimal_ok = true;
        try {
            // gmats/ck 已在准入前解析（gmats_shared/ck_shared），直接复用引用，
            // 避免第二次全量拷贝（2026-11-09 性能修复）。
            const auto& gmats = gmats_shared;
            const auto& ck = ck_shared;

            const int nw_engine = (ncell + 63) / 64;
            // （旧 PRE-GUARD 已删除，2026-11-09 对齐论文时）
            // 它曾用 choose(n9, rem_max) 的**松上界**估算完整世界数，作为
            // world_upper 粗筛的补充。现在准入阶段已用 count_situations_exact()
            // 算出**精确 ‖S‖** 并与 DSTAR 比较（论文 Algorithm 3 语义），
            // 能进到这里的局面 ‖S‖ 已确认 ≤ DSTAR ⇒ 旧的松上界只会**误杀**
            // 本可精确穷举的最强层，故删除。
            //
            // 运行时内存保险仍在下方物化循环的 MID-LOOP GUARD：
            //   worlds.size() > DSTAR ⇒ throw（最多物化 DSTAR+1 个世界）。
            // 它比任何估算都精确，且代价 O(DSTAR)。
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
                    // 🔴 关键：OPTIMAL 层此前**不返回 prob**，UI 收到的 payload
                    // 里 prob 为空 ⇒ 末局进入精确穷举层后整盘热力图回落成
                    // “无数据”灰底（用户实测「最后几步热力图变全灰」）。
                    // 用同一个世界集合给所有未开格算逐格安全概率，与 win_rate /
                    // pbs 分支的 prob 语义一致（“该格不是雷”的概率）。
                    py::dict prob;
                    for (int c : cands) {
                        const int cx = eng.coord_x(c), cy = eng.coord_y(c);
                        prob[py::make_tuple(cx, cy)] =
                            py::float_(eng.safety(c, wk));
                    }
                    // 🟢 真实状态转移（用户需求 2026-11-08：帮助页要看前瞻）
                    // 此刻 OPTIMAL 层已经把**全部世界**物化在 eng.world_bits，
                    // 枚举成本已付过，直接复用不额外开销：
                    //   click_outcomes(cell) 按「点击该格后的可观测结果」把
                    //   一致世界分组，每组 weight = 组内世界数 / 安全世界数
                    //   —— 即「给定该格安全，出现这一观测的条件概率」。
                    // 这是真值，不是估的：世界集来自本轮精确穷举，
                    // 观测签名 Obs = (完整已开位集, 本步新开格及数字)。
                    py::list transitions;
                    std::vector<ExactEngine::Outcome> outs;
                    const double p_clicksafe =
                        eng.click_outcomes(best_cell, wk, eng.opened0, outs);
                    for (auto& o : outs) {
                        // 本分支新开的格子及其数字（Obs 已算好，但 Outcome
                        // 只带 opened 位集；数字从掘开的棋盘反推即可）
                        py::list cells_py;
                        Bits delta = b_andnot(o.opened, eng.opened0);
                        std::vector<std::pair<int, int>> new_cells;
                        for (int i = 0; i < delta.nw; ++i) {
                            uint64_t x = delta.w[i];
                            while (x) {
                                int bit = msw_ctzll(x);
                                x &= x - 1;
                                int c = (i << 6) + bit;
                                new_cells.push_back({eng.coord_x(c),
                                                     eng.coord_y(c)});
                            }
                        }
                        for (auto& p : new_cells)
                            cells_py.append(py::make_tuple(p.first, p.second));
                        py::dict branch;
                        branch["probability"] = py::float_(o.weight);
                        branch["new_cells"] = cells_py;
                        branch["label"] = py::str(
                            std::to_string(new_cells.size()) + " 格展开");
                        branch["board"] = transition_branch_board(
                            eng, cv, o.wk, o.opened,
                            eng.coord_x(best_cell), eng.coord_y(best_cell));
                        transitions.append(branch);
                    }
                    if (p_clicksafe > 0.0 && !transitions.empty()) {
                        py::dict cond;
                        cond["p_click_safe"] = py::float_(p_clicksafe);
                        cond["basis"] = py::str("exact_worlds");
                        cond["world_count"] = py::int_((int64_t)eng.world_bits.size());
                        // transitions 语义：条件概率（以「该格安全」为前提），
                        // 与点击前的安全概率相乘才是无条件联合概率。
                        return make_response(
                            eng.coord_x(best_cell), eng.coord_y(best_cell),
                            v_opt, 0, "optimal", mem.bytes, world_upper, prob,
                            py::list(),    // tied
                            py::list(),    // weighted
                            py::none(),    // unused（保留位，勿删）
                            transitions,
                            cond);
                    }
                    return make_response(eng.coord_x(best_cell), eng.coord_y(best_cell),
                                         v_opt, 0, "optimal", mem.bytes, world_upper,
                                         prob);
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

    // 前瞻层准入（2026-11-08 用户裁决：换约束方法，保留前瞻）：
    //   log2_total_est ≤ COST_CAP（真实枚举规模可承受）
    //   && mem.allowed（1.5GB 硬上限，用户铁律不可调）
    //   && world_upper > DSTAR（**必须**：OPTIMAL 够不到才算新增覆盖）
    // 旧条件 limitation <= wr_threshold 已废弃 —— 它与 OPTIMAL 层形成空集互斥
    // （实测 reason=win_rate 0 次触发），且与真实成本方向相反。
    // wr_threshold 参数保留仅为方便外部实验覆盖：设置极大值也不会强行放过
    // 代价不可承受的局面（COST_CAP 仍是硬门）。
    //
    // ⚠️ 性能回归修复（2026-11-09，实测总耗时 126ms → 22485ms / 178x）：
    // 首版只按 cost_log2 判定，导致 5×5 等小盘上「world_upper 很小、cost_log2
    // 也很小」的局面全部涌进 win_rate，而它们在旧版走廉价的 pbs。
    // 根因：漏掉「OPTIMAL 已覆盖则不必前瞻」这一互斥条件 —— 与旧版
    // limitation<=8 被 OPTIMAL 遮蔽是同一个坑的反面。
    // 加上 world_upper > DSTAR 后，前景层只处理 OPTIMAL 够不到的局面。
    // =================================================================
    // LAYER 2（精确前瞻）已于 2026-11-09 按用户裁决移除。
    //
    // 移除依据（实测，纯 C++ decide 循环口径）：
    //   · 真实可覆盖区间（world_upper > DSTAR 且 cost_log2 <= COST_CAP）
    //     仅占全部决策的 1.2%；
    //   · 该区间 100% 是 node_cap_fallback —— ExactEngine::after_click 是
    //     精确 V*，数千世界时单次 3~8 秒，60 万节点不足；
    //   · node_cap 提到 500 万：win_rate 成功 0→2 次/40 局，单次 3.7 秒，
    //     胜率仍 40.00%（与不提高完全相同）⇒ 无可测量收益。
    // ⇒ 当前算力下 LAYER 2 只吃延迟、不产收益，故全部决策统一走
    //   pbs + PSEQ（LAYER 3）。OPTIMAL 层（world_upper <= DSTAR 的精确
    //   穷举）**不受影响**，仍然是最强的一层。
    //
    // 保留 COST_CAP_LOG2 / log2_total_est / mem / inner_expand_log2_upper：
    //   cost_log2 仍是 decide() 的诊断字段（且估算器的方向性错误已修正），
    //   将来提高算力恢复前瞻时，只需把本开关改回原条件即可。
    // =================================================================
    // 决策出口：pbs（逐候选安全概率）+ PSEQ 破平局（论文 L3）。
    py::list oc = coords_to_pylist(ordered_clicks);
    py::list c9 = coords_to_pylist(clicks9);
    py::tuple pbs = py_pbs_compute(world_upper, num10, oc, c9, res_list,
                                   ck_list, a, no_progress, py::none());
    py::array_t<float, py::array::c_style | py::array::forcecast> res(pbs[0]);
    auto rr = res.unchecked<1>();
    // pbs 无可行候选时返回空数组；禁止继续读取 max/做分母计算。
    // 旧路径会把空数组带入后续决策，最终可能在原生侧触发除零。
    if (rr.shape(0) == 0)
        return make_response(-1, -1, 0.0, 1, "pbs_no_feasible_worlds",
                             mem.bytes, world_upper);
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
        // P0-2 修复（review2 两席独立同报）：原 `int counts[64][10]` 是固定
        // 栈数组，而 nTied = tied.size() 没有 64 上限；超过 64 个同分候选时
        // `counts[ti][mn]++` 会写穿栈帧（原生内存损坏）。改为按 nTied 动态
        // 分配，语义逐位不变，且不截断 tied（不改变算法候选集合）。
        std::vector<std::array<int, 10>> counts(
            nTied, std::array<int, 10>{0, 0, 0, 0, 0, 0, 0, 0, 0, 0});
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
            // 2026-11-10 确定性修复（真实盘面黄金测试抓出）：
            // 原实现对残余平局用 thread_local xorshift 随机数，状态跨调用
            // 累积 ⇒ 同一盘面重复 decide 返回不同坐标（实测 12/60 不稳定）。
            // 这与 py_decide 注释「tie-break is deterministic」矛盾，会让
            // 同 seed 复现 / A/B 对照 / 黄金基线全部失效。改用已有的
            // deterministic_tie_break（按 (x,y) 字典序最小值），纯函数。
            if (!X.empty()) {
                chosen = deterministic_tie_break(X);
                weighted_py = coords_to_pylist(X);
            } else {
                chosen = deterministic_tie_break(tied);
                weighted_py = coords_to_pylist(tied);
            }
        } else {
            chosen = deterministic_tie_break(tied);
            weighted_py = coords_to_pylist(tied);
        }
    }
    return make_response(chosen.first, chosen.second,
                         bestv, 1, mem.allowed ? "limitation" : "memory_gate",
                         mem.bytes, world_upper, prob,
                         coords_to_pylist(tied), weighted_py);

    // ---- 决策出口（论文三层实装的收敛点）----
    // 现在所有决策都在上方 `!use_win_rate` 块内返回：
    //   · OPTIMAL（论文 §6 popt 精确穷举，world_upper <= DSTAR）
    //   · pbs + PSEQ（论文 pbs + 破平局计数过滤）
    // LAYER 2（前瞻）已于 2026-11-09 按用户裁决移除，见上方依据注释。
}

// ===========================================================================
// 帮助模式精确状态转移（2026-11-08）
//
// 背景（端到端实测）：OPTIMAL 层已在决策内产出真实转移，但 expert 盘
// 30x16/99 的绝大多数决策走 certain_safe / win_rate / pbs / limitation 分支，
// 这些分支此前完全不产转移 ⇒ 帮助页浮层永远显示「暂无真实状态转移分布」。
// 用户实测指出「进入 win_rate 阶段之后肯定能看到状态转移」——win_rate 阶段
// 本身就枚举完整世界集，完全有能力算真实前瞻。
//
// 实现：在 py_decide 包装层（决策结果已定、decide_impl 的 12 个出口一行不改）
// 对帮助模式补一次精确世界枚举 + ExactEngine::click_outcomes。概率是真值：
//   weight = 该可观测分支的一致性世界数 / 点击该格安全的一致性世界数
// 即「给定点击该格不踩雷」的条件概率，绝不合成/估算。
//
// 硬上限：世界数超限时明确返回 reason，UI 显示准确原因（不再误导为「暂未
// 产出」）。自动模式（is_play=true）完全不进入本函数。
// ===========================================================================
static constexpr int64_t HELP_TRANSITION_WORLD_CAP = 200000;

// ===========================================================================
// 世界集缓存：一次 decide 构造一次，悬停任意格复用（用户裁决 2026-11-08）。
// 指纹 = 棋盘全部格值 + (a,w,h)；盘面一变立即失效，绝不复用过期世界集。
// thread_local：帮助线程与 GUI 线程各自独立，无数据竞争。
// ===========================================================================
struct TransitionEngineCache {
    uint64_t fp = 0;
    int a = 0, w = 0, h = 0;
    ExactEngine eng;
    // 渲染 7×7 局面时必须用真实盘面（已开数字/标记雷），不能靠一致世界重算。
    std::vector<int32_t> cv;
    bool valid = false;
    std::string reason;      // 构造失败原因（空=成功）
};
static thread_local TransitionEngineCache g_tr_cache;

static uint64_t board_fingerprint(const std::vector<int32_t>& cv) {
    uint64_t h = 0x9E3779B97F4A7C15ull;
    for (int32_t v : cv) {
        h ^= (uint64_t)(uint32_t)v + 0x165667B19E3779F9ull
             + (h << 6) + (h >> 2);
    }
    return h;
}

// 为一个「可观测分支」渲染以目标格为中心的 7×7 局部局面。
// 编码与 utils/vision.py + ui/preview_popup.py 契约一致：
//   0-8 已开数字 / 9 未开 / 10 确定雷 / 11 确定安全 / -1 棋盘外。
//
// 🔴 2026-11-08 修复（用户实测「状态转移跟实际状态不符、边上数字不显示」）：
//   ① **已开格必须直接沿用真实 cv 的数字**，不能用一致世界重算。
//      旧实现用 `eng.number(idx, first_world)` —— 一致世界只是「与已知数字相容」
//      的假设雷布局，不是真实雷位，算出来的数字必然与盘面冲突（实测整行被算成 0）。
//   ② 真实 cv 里的 10（右键标记雷）/11（推理安全）此前不算 opened，会被画成
//      未开 9 甚至被 inter 覆盖，导致「覆盖不全 / 边上数字消失」。
//   ③ 分支内「所有一致世界共有的雷」才标 10；真实已标记的 10 也保留为 10。
static py::list transition_branch_board(const ExactEngine& eng,
                                        const std::vector<int32_t>& cv,
                                        const ExactEngine::WKey& wk,
                                        const Bits& opened,
                                        int cx, int cy) {
    py::list board;
    const int span = 7, half = span / 2;
    // 该分支内的一致世界集合：所有世界共有的雷位 = 确定雷
    Bits u, inter;
    eng.union_inter(wk, u, inter);
    for (int dj = -half; dj <= half; ++dj) {
        py::list row;
        for (int di = -half; di <= half; ++di) {
            const int x = cx + di, y = cy + dj;
            if (x < 1 || x > eng.w || y < 1 || y > eng.h) {
                row.append(py::int_(-1));
                continue;
            }
            const int idx = eng.idx_of(x, y);
            const int32_t real_v = cv[(size_t)y * eng.W2 + x];
            if (real_v >= 0 && real_v <= 8) {
                // 已开数字：以真实盘面为准（事实层，不得重算）
                row.append(py::int_(real_v));
            } else if (real_v == 10) {
                // 用户已标记的雷：保留标记语义
                row.append(py::int_(10));
            } else if (real_v == 11) {
                // 推理确定安全：保留
                row.append(py::int_(11));
            } else if (opened.test(idx)) {
                // 该分支点击后新掘开的格：用该分支的世界子集算数字
                int first_world = -1;
                eng.for_each_world(wk, [&](int i) {
                    if (first_world < 0) first_world = i;
                });
                const Bits& mine = first_world >= 0
                    ? eng.world_bits[(size_t)first_world] : u;
                row.append(py::int_(eng.number(idx, mine)));
            } else if (inter.test(idx)) {
                row.append(py::int_(10));   // 分支内确定雷
            } else {
                row.append(py::int_(9));    // 未开
            }
        }
        board.append(row);
    }
    return board;
}

// ===========================================================================
// 帮助模式精确状态转移（用户裁决 2026-11-08：中局不计算状态转移）
//
// 只在能精确穷举一致世界时产出真实转移；world_upper 超过
// HELP_TRANSITION_WORLD_CAP 时直接返回明确原因，UI 显示「世界过多」，
// **不做任何采样或近似**（用户明确要求「中局不要计算状态转移」）。
// ===========================================================================

// 对已构造好的世界集，按「任意指定格 (x,y)」算真实状态转移。
// 两处复用：decide 出口（best_cell）与悬停接口（任意格）。
static py::dict compute_transitions_for_cell(ExactEngine& eng,
                                             const std::vector<int32_t>& cv,
                                             int cx, int cy) {
    py::dict out;
    out["transitions"] = py::none();
    out["transition_meta"] = py::none();
    out["reason"] = py::none();
    auto fail = [&](const char* why) {
        out["reason"] = py::str(why);
        py::dict meta;
        meta["basis"] = py::str("unavailable");
        meta["reason"] = py::str(why);
        out["transition_meta"] = meta;
        return out;
    };
    const int cell = cy * eng.W2 + cx;
    if (cx < 1 || cy < 1 || cell < 0 || cell >= eng.total_bits ||
        eng.opened0.test(cell))
        return fail("cell_not_closed");
    try {
        ExactEngine::WKey wk = eng.all_wkey();
        std::vector<ExactEngine::Outcome> outs;
        const double p_clicksafe =
            eng.click_outcomes(cell, wk, eng.opened0, outs);
        py::list transitions;
        for (auto& o : outs) {
            py::list cells_py;
            Bits delta = b_andnot(o.opened, eng.opened0);
            std::vector<std::pair<int, int>> new_cells;
            for (int i = 0; i < delta.nw; ++i) {
                uint64_t x = delta.w[i];
                while (x) {
                    int bit = msw_ctzll(x);
                    x &= x - 1;
                    int c = (i << 6) + bit;
                    new_cells.push_back({eng.coord_x(c), eng.coord_y(c)});
                }
            }
            for (auto& q : new_cells)
                cells_py.append(py::make_tuple(q.first, q.second));
            py::dict branch;
            branch["probability"] = py::float_(o.weight);
            branch["new_cells"] = cells_py;
            branch["label"] = py::str(
                std::to_string(new_cells.size()) + " 格展开");
            branch["board"] = transition_branch_board(
                eng, cv, o.wk, o.opened, cx, cy);
            transitions.append(branch);
        }
        if (p_clicksafe <= 0.0 || transitions.empty())
            return fail("no_safe_worlds");
        py::dict cond;
        cond["p_click_safe"] = py::float_(p_clicksafe);
        cond["basis"] = py::str("exact_worlds");
        cond["world_count"] = py::int_((int64_t)eng.world_bits.size());
        cond["cell"] = py::make_tuple(cx, cy);
        out["transitions"] = transitions;
        out["transition_meta"] = cond;
        return out;
    } catch (const std::exception&) {
        return fail("engine_error");
    }
}

static py::dict help_exact_transitions(
    py::array_t<int32_t, py::array::c_style | py::array::forcecast> cell_value,
    int a, int w, int h, int best_x, int best_y) {
    py::dict out;
    out["transitions"] = py::none();
    out["transition_meta"] = py::none();
    out["reason"] = py::none();
    auto fail = [&](const char* why) {
        out["reason"] = py::str(why);
        py::dict meta;
        meta["basis"] = py::str("unavailable");
        meta["reason"] = py::str(why);
        out["transition_meta"] = meta;
        return out;
    };
    if (best_x < 1 || best_y < 1) return fail("no_best_cell");
    const int W2 = w + 2;
    const size_t ncell = (size_t)(h + 2) * (w + 2);
    const int32_t* raw = static_cast<const int32_t*>(cell_value.request().ptr);
    std::vector<int32_t> cv(raw, raw + ncell);

    int num10 = 0;
    for (int y = 1; y <= h; ++y)
        for (int x = 1; x <= w; ++x)
            if (cv[(size_t)y * W2 + x] == 10) ++num10;

    py::tuple split = py_split_frontier(cell_value);
    py::list f_py = split[0].cast<py::list>();
    py::list i_py = split[1].cast<py::list>();
    std::vector<Coord> clicks, clicks9;
    for (auto item : f_py) {
        auto t = item.cast<py::tuple>();
        clicks.emplace_back(py::int_(t[0]), py::int_(t[1]));
    }
    for (auto item : i_py) {
        auto t = item.cast<py::tuple>();
        clicks9.emplace_back(py::int_(t[0]), py::int_(t[1]));
    }
    if (clicks.empty()) return fail("no_frontier");

    // 分组：与 decide_impl 完全同构（邻接约束数字 1..8，含 8）。
    // ⚠️ 修复（2026-11-08）：原判据为 0<v<8（排除 8），与 run_part_solve 的
    // 1<=v<=8 不一致，导致残局贴着数字 8 的 frontier 格独立成组且无解
    // （empty_group / 决策不可用）。详见 decide_impl 处同一修复的注释。
    std::vector<std::vector<Coord>> groups;
    std::vector<std::set<Coord>> group_sets;
    std::vector<std::set<Coord>> cell_sets(clicks.size());
    for (size_t k = 0; k < clicks.size(); ++k) {
        const auto& p = clicks[k];
        for (int u = p.first - 1; u <= p.first + 1; ++u)
            for (int v = p.second - 1; v <= p.second + 1; ++v) {
                int32_t val = cv[(size_t)v * W2 + u];
                if (val >= 1 && val <= 8) cell_sets[k].emplace(u, v);
            }
    }
    for (size_t k = 0; k < clicks.size(); ++k) {
        std::vector<size_t> hit;
        for (size_t g = 0; g < groups.size(); ++g)
            for (const auto& c : cell_sets[k])
                if (group_sets[g].count(c)) { hit.push_back(g); break; }
        if (hit.empty()) {
            groups.push_back({clicks[k]});
            group_sets.push_back(cell_sets[k]);
        } else {
            const size_t keep = hit.front();
            groups[keep].push_back(clicks[k]);
            group_sets[keep].insert(cell_sets[k].begin(), cell_sets[k].end());
            for (size_t hi = hit.size(); hi-- > 1;) {
                const size_t src = hit[hi];
                groups[keep].insert(groups[keep].end(),
                                    groups[src].begin(), groups[src].end());
                group_sets[keep].insert(group_sets[src].begin(),
                                        group_sets[src].end());
                groups.erase(groups.begin() + (std::ptrdiff_t)src);
                group_sets.erase(group_sets.begin() + (std::ptrdiff_t)src);
            }
        }
    }

    py::list res_list, ck_list;
    int64_t world_upper = 1;
    py::object no_progress = py::none();
    try {
        for (const auto& g : groups) {
            py::tuple rr = py_part_solve(coords_to_pylist(g), cell_value,
                                         a, w, h, no_progress);
            py::array_t<int32_t, py::array::c_style | py::array::forcecast> sol(rr[0]);
            const int64_t n = py::int_(rr[1]);
            if (n <= 0) return fail("empty_group");
            res_list.append(sol);
            ck_list.append(py::int_(n));
            if (world_upper > HELP_TRANSITION_WORLD_CAP / n) {
                out["world_upper"] = py::int_(HELP_TRANSITION_WORLD_CAP + 1);
                return fail("world_count_exceeded");
            }
            world_upper *= n;
        }
    } catch (const std::exception&) {
        return fail("part_solve_error");
    }
    if (world_upper > HELP_TRANSITION_WORLD_CAP) {
        out["world_upper"] = py::int_(world_upper);
        return fail("world_count_exceeded");
    }

    const int nw_engine = (ncell + 63) / 64;
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

    std::vector<Bits> worlds;
    bool too_many = false;
    try {
        Odometer od(std::move(ck));
        std::vector<int> index_list;
        while (od.next(index_list)) {
            int64_t mine_total = 0;
            Bits wbits; wbits.zero(nw_engine);
            for (size_t i = 0; i < index_list.size(); ++i) {
                const auto& row = gmats[i][(size_t)index_list[i]];
                const auto& group_cells = groups[i];
                for (size_t j = 0; j < row.size() && j < group_cells.size(); ++j)
                    if (row[j] == 1) {
                        const auto& c = group_cells[j];
                        wbits.set((size_t)c.second * W2 + c.first);
                        ++mine_total;
                    }
            }
            const int64_t rem = (int64_t)a - num10 - mine_total;
            if (rem < 0 ||
                (int64_t)a > num10 + mine_total + (int64_t)clicks9.size())
                continue;
            if (rem == 0) {
                worlds.push_back(wbits);
                if ((int64_t)worlds.size() > HELP_TRANSITION_WORLD_CAP) {
                    too_many = true; break;
                }
                continue;
            }
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
                    if ((int64_t)worlds.size() > HELP_TRANSITION_WORLD_CAP) {
                        too_many = true; break;
                    }
                }
                if (too_many) break;
            }
            if (too_many) break;
        }
    } catch (const std::exception&) {
        return fail("enumeration_error");
    }
    if (too_many) {
        out["world_upper"] = py::int_(world_upper);
        return fail("world_count_exceeded");
    }
    if (worlds.empty()) return fail("no_feasible_worlds");

    ExactEngine eng;
    eng.w = w; eng.h = h; eng.W2 = W2; eng.a = a;
    eng.node_cap = 0;
    eng.total_bits = (int)ncell;
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
    eng.nbr.assign(ncell, Bits());
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

    // 缓存本轮世界集：help_exact_transitions 在 decide 出口被调用一次，
    // 之后悬停任意格都复用这份引擎，不再重新枚举世界集。
    {
        TransitionEngineCache c;
        c.fp = board_fingerprint(cv);
        c.a = a; c.w = w; c.h = h;
        c.eng = eng;
        c.cv = cv;
        c.valid = true;
        c.reason = "";
        g_tr_cache = std::move(c);
    }
    return compute_transitions_for_cell(eng, cv, best_x, best_y);
}

// 按「鼠标悬停的任意格 (x,y)」算状态转移（Python 悬停时调用）。
// 优先复用 decide 出口缓存的世界集（指纹一致即命中，零重枚举）；
// 盘面变了或缓存失效时退回 help_exact_transitions 重建并刷新缓存。
static py::dict py_transitions_for(
    py::array_t<int32_t, py::array::c_style | py::array::forcecast> cell_value,
    int a, int w, int h, int x, int y) {
    const int32_t* raw = static_cast<const int32_t*>(cell_value.request().ptr);
    const size_t ncell = (size_t)(h + 2) * (w + 2);
    std::vector<int32_t> cv(raw, raw + ncell);
    const uint64_t fp = board_fingerprint(cv);
    if (g_tr_cache.valid && g_tr_cache.fp == fp &&
        g_tr_cache.a == a && g_tr_cache.w == w && g_tr_cache.h == h) {
        py::dict r = compute_transitions_for_cell(
            g_tr_cache.eng, g_tr_cache.cv, x, y);
        r["cached"] = py::bool_(true);
        return r;
    }
    py::dict r = help_exact_transitions(cell_value, a, w, h, x, y);
    r["cached"] = py::bool_(false);
    return r;
}

static py::dict py_decide(
    py::array_t<int32_t, py::array::c_style | py::array::forcecast> cell_value,
    int a, int w, int h, int64_t node_cap,
    int64_t mem_budget, uint64_t seed, bool is_play) {
    // P1/P0-4：mem_budget 形参仅为兼容既有 pybind 签名保留，决策层内部
    // 一律使用 DEFAULT_MEM_BUDGET（1.5GB 用户铁律硬上限），不再消费其值。
    (void)mem_budget;
    // decide() is a PURE function: output depends only on cell_value+config.
    // seed is accepted for harness symmetry but is intentionally unused;
    // tie-break is deterministic (option A, user-approved strategy change).
    (void)seed;
    // ⚠️ 2026-11-09：`wr_threshold` 参数已删除（用户裁决「winrate 分支都取消了，
    //    应该取消这个参数」）。它曾是 pbs 层的准入阈值
    //    `use_win_rate = limitation <= wr_threshold`，但该分支已随 LAYER 2 整体
    //    删除，参数在 decide_impl 函数体内**零消费**（只被转发）。
    //    保留它会让 Python 侧的「自适应调阈值」看起来在生效、实则无效。
    py::dict r = decide_impl(cell_value, a, w, h, node_cap,
                             mem_budget, is_play);
    // ---- 论文对齐诊断（2026-11-09）----
    // situations = 本次算出的精确 ‖S‖（论文 Algorithm 1 的一致世界总数）。
    //   -1 = 未计数（被 world_upper 粗筛挡掉 / 内存闸拒绝 / 计数异常）。
    //   用于与 Python 参考实现 count_worlds_exact 逐位对拍，并解释准入行为。
    // dstar = 本次生效的 DSTAR（自动 256 / 帮助 768；MSW_DSTAR 可覆盖）。
    r["situations"] = py::int_(g_last_situations);
    r["dstar"] = py::int_(g_last_dstar);
    // 把决策难度 limitation 作为诊断字段返回（与 decide_impl 内部同公式）：
    //   limitation = clicks9 数量 * 0.8 + log2(world_upper)
    // Python 侧用它 + 真实耗时驱动自适应 wr_threshold 升降档；此前该值只在
    // C++ 内部用于选层，从未外传 ⇒ 30s 预算无法驱动任何调整（用户实测）。
    try {
        py::tuple sp = py_split_frontier(cell_value);
        const double n9 = (double)py::len(sp[1].cast<py::list>());
        const double wu = (double)py::float_(r["world_upper"]);
        // limitation 保留为**诊断字段**（不参与任何决策，2026-11-08 起）：
        // 实测它与真实成本方向相反且其消费点全为死分支，仅用于日志/热力图展示。
        r["limitation"] = py::float_(n9 * 0.8 + std::log2(std::max(1.0, wu)));
        // 前瞻层真实成本量（新增诊断）：log2(world_upper × C(n9, rem_max))。
        // 这是判定「能否做精确前瞻」的正确量，也是动态深度的输入。
        // 与 decide_impl 内部同口径（见该函数 COST_CAP 处注释）。
        // ⚠️ py_split_frontier 返回 (frontier, interior, num9, num10)，
        //    用 sp[3] 取 num10；切勿假设 decide 返回值里有 num10 字段（它没有）。
        try {
            const int64_t num10v = (int64_t)py::int_(sp[3]);
            // ⚠️ 2026-11-09：此诊断字段必须与 decide_impl 的准入闸**同口径**，
            // 否则「日志说规模很小、实际却被拒」这类误判无法从日志看出
            // （实测就是如此：cost_log2=9.17 但 LAYER 2 撞 HARD_CAP 失败）。
            // 旧实现取 rem = min(a-num10, n9) 再算 C(n9, rem)，与准入闸
            // 同一个单峰函数方向性错误（rem 靠近 n9 时低估达指数级）。
            int64_t rem_cap = (int64_t)a - num10v;
            if (rem_cap < 0) rem_cap = 0;
            double lb = std::log2(std::max(1.0, wu));
            lb += inner_expand_log2_upper((int64_t)n9, rem_cap);
            r["cost_log2"] = py::float_(lb);
        } catch (const std::exception&) {
            r["cost_log2"] = py::float_(-1.0);
        }
    } catch (const std::exception&) {
        r["limitation"] = py::float_(-1.0);   // 诊断字段失败不影响决策
        r["cost_log2"] = py::float_(-1.0);
    }
    // 帮助模式专用：决策层（certain_safe / win_rate / pbs / limitation 等）此前
    // 不产出状态转移，导致帮助浮层恒显示「暂无」。这里在决策结果已定之后补一次
    // 精确世界前瞻（OPTIMAL 层自带转移，已有则不再重算）。
    // 自动模式（is_play=true）完全跳过 —— 用户明确要求 Auto 页只显示热力图。
    // MSW_NO_HELP_TRANSITIONS=1 可关闭前瞻（诊断/回退用）。
    static const bool no_help_tr = [] {
        const char* e = std::getenv("MSW_NO_HELP_TRANSITIONS");
        return e && std::string(e) == "1";
    }();
    if (!no_help_tr && !is_play && r.contains("x") && r.contains("y")) {
        py::object existing = r["transitions"];
        if (existing.is_none() || py::len(existing) == 0) {
            try {
                py::dict t = help_exact_transitions(
                    cell_value, a, w, h,
                    py::int_(r["x"]), py::int_(r["y"]));
                r["transitions"] = t["transitions"];
                r["transition_meta"] = t["transition_meta"];
                r["transition_reason"] = t["reason"];
            } catch (const std::exception& e) {
                // 前瞻失败绝不影响决策结果本身（x/y/prob/tied 已确定）。
                py::dict meta;
                meta["basis"] = py::str("unavailable");
                meta["reason"] = py::str(e.what());
                r["transition_meta"] = meta;
                r["transition_reason"] = py::str("lookahead_exception");
            }
        }
    }
    return r;
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
          py::arg("node_cap") = 2000000,
          py::arg("mem_budget") = decision_layer::DEFAULT_MEM_BUDGET,
          py::arg("seed") = 0, py::arg("is_play") = true,
          "Unified native decision. NOTE: mem_budget is accepted for ABI "
          "compatibility only; the decision layer always enforces the fixed "
          "1.5GB DEFAULT_MEM_BUDGET and ignores this value.");
    m.def("transitions_for", &decision_layer::py_transitions_for,
          py::arg("cell_value"), py::arg("a") = 99, py::arg("w") = 30,
          py::arg("h") = 16, py::arg("x"), py::arg("y"),
          "Real state transitions for clicking the hovered cell (x,y).\n"
          "Reuses the world set cached by the last decide() call; rebuilds\n"
          "only when the board changes. 7x7 outcome boards are centered on\n"
          "the hovered cell, not the best cell.");
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
    m.def("pbs_compute", &py_pbs_compute, py::arg("total"), py::arg("num10"), py::arg("clicks"),
          py::arg("clicks9"), py::arg("res_list"), py::arg("ck"), py::arg("a"), py::arg("progress"),
          py::arg("visible"),
          "process_bigger_situation 纯计算部分：返回 (res, mine_num, total)");
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
                                        node_cap > 0 ? node_cap : 2000000,
                                        decision_layer::DEFAULT_MEM_BUDGET,
                                        /*is_play*/ true);
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
        r = decision_layer::decide_impl(board, a, w, h,
                                        node_cap > 0 ? node_cap : 2000000,
                                        decision_layer::DEFAULT_MEM_BUDGET,
                                        /*is_play*/ true);
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
