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
#include <climits>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

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

using Board = std::vector<int32_t>;  // 行主序 (h+2)×(w+2)

// 对应 deduction.py cell_around(i, j)：统计 (j±1, i±1) 邻域内 9 / 10 的数量。
// 边界条件与 Python 相同（0 <= m <= w+1 且 0 <= n <= h+1）。
inline void cell_around(const Board& b, int w, int h, int W2, int i, int j,
                        int& cnt9, int& cnt10) {
    int c9 = 0, c10 = 0;
    for (int n = j - 1; n <= j + 1; ++n) {
        for (int m = i - 1; m <= i + 1; ++m) {
            if (0 <= m && m <= w + 1 && 0 <= n && n <= h + 1) {
                int32_t v = b[(size_t)n * W2 + m];
                if (v == 9) ++c9;
                if (v == 10) ++c10;
            }
        }
    }
    cnt9 = c9;
    cnt10 = c10;
}

// 进度回调：仅整数值变化时触发（Python 侧 _throttled_pv_signal_emit 自带 100ms 节流）
struct Progress {
    py::object cb;  // callable(int) 或 None
    int last = -1;
    explicit Progress(py::object c) : cb(std::move(c)) {}
    void emit(int pct) {
        if (cb.is_none()) return;
        if (pct == last) return;
        last = pct;
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

// combination_ratio(x, x_min, n)：对应 combinatorics.py（含 assert 前置校验）
double combination_ratio(int x, int x_min, int n) {
    if (!(0 <= x && x <= x_min && x_min <= n)) {
        PyErr_SetString(PyExc_AssertionError, "assert 0 <= x <= x_min <= n");
        throw py::error_already_set();
    }
    double res = 1.0;
    for (int i = 0; i < x_min - x; ++i) {
        res *= (double)(x + 1 + i);
        res /= (double)(n - x - i);
    }
    return res;
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

    PartSolveCtx(std::vector<Coord> clicks_, int w_, int h_, py::object cb, int a_)
        : w(w_), h(h_), W2(w_ + 2), a(a_),
          clicks(std::move(clicks_)), state(this->clicks.size(), 0), progress(std::move(cb)) {}

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
    // 复制板面并统计初始雷数
    ctx.board.assign(cell_value.data(), cell_value.data() + (size_t)(h + 2) * (w + 2));
    for (int32_t v : ctx.board)
        if (v == 10) ++ctx.mine_count;

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

    ctx.recurse(0, 1);

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
    std::unordered_map<std::string, double> memory;

    WinRateCtx(int w_, int h_, int a_, bool is_play_, py::object cb)
        : w(w_), h(h_), W2(w_ + 2), a(a_), is_play(is_play_), progress(std::move(cb)) {}

    // 对应 hash_cell_value：np.where(l > 9, 9, l).tobytes() 的 MD5
    std::string hash_board(const Board& b) const {
        std::string norm(b.size() * 4, '\0');
        for (size_t k = 0; k < b.size(); ++k) {
            int32_t v = (b[k] > 9) ? 9 : b[k];
            std::memcpy(&norm[k * 4], &v, 4);  // 小端（x86 原生，与 tobytes 一致）
        }
        MD5 md5;
        md5.update(reinterpret_cast<const unsigned char*>(norm.data()), norm.size());
        return md5.final();
    }
};

// f()：递归求胜率（与 Python 闭包 f 完全对照）
double wr_f(WinRateCtx& ctx, std::vector<Coord> clicks, std::vector<Board> boards, int depth,
            double depth_limit) {
    {
        auto it = ctx.memory.find(ctx.hash_board(boards[0]));
        if (it != ctx.memory.end()) return it->second;
    }
    if (boards.size() == 1) return 1.0;
    if ((double)depth > depth_limit) return 1.0;

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
        std::vector<std::vector<Board>> groups;
        std::unordered_map<int, size_t> val_slot;
        for (const auto& b : boards) {
            if (b[(size_t)v * ctx.W2 + u] != 10) {
                Board nb = b;
                int c9, c10;
                cell_around(nb, ctx.w, ctx.h, ctx.W2, u, v, c9, c10);
                nb[(size_t)v * ctx.W2 + u] = c10;
                auto it = val_slot.find(c10);
                if (it == val_slot.end()) {
                    val_slot.emplace(c10, groups.size());
                    groups.emplace_back();
                    groups.back().push_back(std::move(nb));
                } else {
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
                wr_f(ctx, std::move(new_clicks), std::move(groups[g]), depth + 1, depth_limit);
            win_p += trans_prob * win_r;
        }
        res_list.push_back(win_p);
        running_max = has_max ? std::max(running_max, win_p) : win_p;
        has_max = true;
    }
    double mx = *std::max_element(res_list.begin(), res_list.end());
    ctx.memory[ctx.hash_board(boards[0])] = mx;
    return mx;
}

}  // namespace

py::tuple py_win_rate(py::sequence clicks_seq, py::sequence clicks9_seq, py::list res_list,
                      py::array_t<int32_t, py::array::c_style | py::array::forcecast> cell_value,
                      py::sequence ck_seq, int64_t num10, int a, int w, int h, bool is_play,
                      py::object progress_cb) {
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

    WinRateCtx ctx(w, h, a, is_play, std::move(progress_cb));

    // ---- 阶段 1：枚举所有完整局面 ----
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

    const size_t total = cell_value_list.size();
    // Python：depth_limit = 200 / len(clicks)（clicks 为空 → ZeroDivisionError）
    if (clicks.empty()) {
        PyErr_SetString(PyExc_ZeroDivisionError, "division by zero");
        throw py::error_already_set();
    }
    double depth_limit = 200.0 / (double)clicks.size();
    if (total == 0) {
        // Python：clicks2p 计算中 /total → ZeroDivisionError
        PyErr_SetString(PyExc_ZeroDivisionError, "division by zero");
        throw py::error_already_set();
    }

    // ---- 阶段 2：外层（clicks += clicks9，稳定排序后逐格评估）----
    std::vector<Coord> all_clicks = clicks;
    all_clicks.insert(all_clicks.end(), clicks9.begin(), clicks9.end());

    std::vector<double> cp(all_clicks.size());
    for (size_t k = 0; k < all_clicks.size(); ++k) {
        int u = all_clicks[k].first, v = all_clicks[k].second;
        size_t cnt10 = 0;
        for (const auto& b : cell_value_list)
            if (b[(size_t)v * W2 + u] == 10) ++cnt10;
        cp[k] = 1.0 - (double)cnt10 / (double)total;
    }
    // clicks2p 按原始（排序前）插入序构建 —— 与 Python 在 sorted 之前建 dict 一致
    py::dict clicks2p;
    for (size_t k = 0; k < all_clicks.size(); ++k)
        clicks2p[py::make_tuple(all_clicks[k].first, all_clicks[k].second)] = py::float_(cp[k]);
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
    std::vector<double> res;
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
            double win_r = wr_f(ctx, std::move(new_clicks), std::move(gb_all[g]), 1, depth_limit);
            win_p += trans_prob * win_r;
        }
        ctx.progress.emit((int)(((double)(i + 1) / (double)all_clicks.size()) * 100.0));
        res.push_back(win_p);
        running_max = has_max ? std::max(running_max, win_p) : win_p;
        has_max = true;
    }

    // 返回：res（float 列表）、排序后 clicks、total、clicks2p（dict，原始插入序）
    py::list res_py;
    for (double v : res) res_py.append(py::float_(v));
    py::list clicks_py;
    for (const auto& c : all_clicks) clicks_py.append(py::make_tuple(c.first, c.second));

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
    auto visible = [&visible_cb](bool v) {
        if (!visible_cb.is_none()) visible_cb(py::bool_(v));
    };

    if (total > 10000) {
        // ---- 近似分支（total>10000）----
        visible(false);
        double mine_num = 0.0;

        // |set(clicks) ∪ set(clicks9)|
        int64_t union_size;
        {
            std::vector<uint64_t> seen;
            seen.reserve(clicks.size() + clicks9.size());
            for (const auto& c : clicks)
                seen.push_back(((uint64_t)(uint32_t)c.first << 32) | (uint32_t)c.second);
            for (const auto& c : clicks9)
                seen.push_back(((uint64_t)(uint32_t)c.first << 32) | (uint32_t)c.second);
            std::sort(seen.begin(), seen.end());
            seen.erase(std::unique(seen.begin(), seen.end()), seen.end());
            union_size = (int64_t)seen.size();
        }

        std::vector<double> res_out;
        for (const auto& res_l : groups) {
            if (res_l.empty()) {
                PyErr_SetString(PyExc_ValueError, "min() arg is an empty sequence");
                throw py::error_already_set();
            }
            double estimated_mine_num = 0.0;
            int min_mine_cnt = INT_MAX;
            for (const auto& row : res_l) {
                int s = 0;
                for (int32_t v : row) s += v;
                min_mine_cnt = std::min(min_mine_cnt, s);
            }
            int _all = (int)(union_size - (int64_t)res_l[0].size());
            int x_min = a - min_mine_cnt - (int)num10;
            double _total = 0.0;
            // 逐行顺序累加（对应 np.array(_res_s).sum(axis=0) 的行序累加语义）
            std::vector<double> col_sum(res_l[0].size(), 0.0);
            for (const auto& row : res_l) {
                int _mine_num = 0;
                for (int32_t v : row) _mine_num += v;
                double p = combination_ratio(a - _mine_num - (int)num10, x_min, _all);
                for (size_t c = 0; c < row.size(); ++c)
                    col_sum[c] += (double)row[c] * p;  // int 数组 * Python float → float64
                estimated_mine_num += p * (double)_mine_num;
                _total += p;
            }
            for (size_t c = 0; c < col_sum.size(); ++c) {
                col_sum[c] /= _total;
                col_sum[c] = 1.0 - col_sum[c];
            }
            mine_num += estimated_mine_num / _total;
            res_out.insert(res_out.end(), col_sum.begin(), col_sum.end());
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
    if (total == 0) {
        // Python 在首个迭代的 int((num / total) * 100) 处抛 ZeroDivisionError
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
        // Python：min(mine_num) → ValueError
        PyErr_SetString(PyExc_ValueError, "min() arg is an empty sequence");
        throw py::error_already_set();
    }
    int min_mine_cnt = *std::min_element(mine_num.begin(), mine_num.end());
    int x_min = a - min_mine_cnt - (int)num10;

    double estimated_mine_num = 0.0;
    double total_w = 0.0;
    std::vector<float> acc(res_rows[0].size(), 0.0f);
    for (size_t i = 0; i < mine_num.size(); ++i) {
        double p = combination_ratio(a - mine_num[i] - (int)num10, x_min, (int)clicks9.size());
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
    double mine_num_out = estimated_mine_num / total_w;

    py::array_t<float> res_arr((py::ssize_t)acc.size());
    std::copy(acc.begin(), acc.end(), res_arr.mutable_data());
    return py::make_tuple(res_arr, py::float_(mine_num_out), py::float_(total_w));
}

// ---------------------------------------------------------------------------
// 模块定义
// ---------------------------------------------------------------------------
PYBIND11_MODULE(mscore, m) {
    m.doc() = "minesweeper_help native core (C++): part_solve / part_solve_single / "
              "win_rate / pbs_compute — 与 Python 实现语义完全一致的加速实现";
    m.def("part_solve", &py_part_solve, py::arg("clicks"), py::arg("cell_value"), py::arg("a"),
          py::arg("w"), py::arg("h"), py::arg("progress"),
          "part_solve 内核（_try=False）：返回 (解矩阵[num×n], num_solve)");
    m.def("part_solve_single", &py_part_solve_single, py::arg("clicks"), py::arg("cell_value"),
          py::arg("cs"), py::arg("num10"), py::arg("num9"), py::arg("a"), py::arg("w"),
          py::arg("h"), py::arg("progress"),
          "part_solve_single 内核（_try=False）：返回 (解矩阵[num×n], num)");
    m.def("win_rate", &py_win_rate, py::arg("clicks"), py::arg("clicks9"), py::arg("res_list"),
          py::arg("cell_value"), py::arg("ck"), py::arg("num10"), py::arg("a"), py::arg("w"),
          py::arg("h"), py::arg("is_play"), py::arg("progress"),
          "win_rate 完整移植：返回 (res, sorted_clicks, total, clicks2p)");
    m.def("pbs_compute", &py_pbs_compute, py::arg("total"), py::arg("num10"), py::arg("clicks"),
          py::arg("clicks9"), py::arg("res_list"), py::arg("ck"), py::arg("a"), py::arg("progress"),
          py::arg("visible"),
          "process_bigger_situation 纯计算部分：返回 (res, mine_num, total)");
    m.def("md5",
          [](py::bytes data) {
              std::string s = data;
              MD5 md5;
              md5.update(reinterpret_cast<const unsigned char*>(s.data()), s.size());
              return py::bytes(md5.final());
          },
          py::arg("data"),
          "MD5 摘要（16 字节二进制），与 hashlib.md5 一致（诊断/测试用）");
}
