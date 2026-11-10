// C++ 覆盖率 driver（gcov / MinGW，零外部依赖）。
//
// 用 #include 把 mscore.cpp 纳入同一翻译单元，使 static 纯算法函数可见；
// 只调用不依赖 Python C-API 的纯函数，喂真实盘面形状的数据。
// 构建：tools/cpp_cov_build.py（g++ --coverage），产出 .gcno/.gcda 由 gcov 解析。
//
// 覆盖目标（全是 cpp/mscore.cpp 内 L3180 之前的真实生产函数）：
//   count_situations_exact / estimate_mem_impl / combination_ratio /
//   C_num / get_list_sizes / make_choose_table / inner_expand_log2_upper /
//   b_and / b_andnot / board_fingerprint
// 不覆盖：split_frontier 等 abnative_bridge 桥接函数（仅在
//   AB_NATIVE_MERGED 下存在，且与 py 对象耦合，见 cpp_coverage.cmd 说明）、
//   py_decide / decide_impl / PYBIND11_MODULE 等 pybind 绑定层。
//
// 不覆盖：py_decide / decide_impl / PYBIND11_MODULE 等 pybind 绑定层
// （需要真实 Python 解释器 + MSVC ABI，属另一口径，报告中单列）。
#define MSW_COV_DRIVER 1
#include "cpp_cov/mscore_pure.inc"

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <string>
#include <vector>

// 真实 cfg：30x16 / 99 雷 / cell_width=24（来自 cfg.json）
static const int W = 30, H = 16, A = 99;
static const int W2 = W + 2, H2 = H + 2;

static std::vector<int32_t> make_board(int mode) {
    std::vector<int32_t> cv((size_t)H2 * W2, 9);  // 全未开
    // 边界 padding 置 0（与生产 cell_value 一致）
    for (int x = 0; x < W2; ++x) { cv[x] = 0; cv[(size_t)(H2 - 1) * W2 + x] = 0; }
    for (int y = 0; y < H2; ++y) { cv[(size_t)y * W2] = 0; cv[(size_t)y * W2 + W2 - 1] = 0; }
    switch (mode) {
    case 0:  // 全未开（开局）
        break;
    case 1:  // 角落首点开一片（真实 Arbiter 延迟布雷开局形态）
        cv[1 * W2 + 1] = 0;
        for (int dy = 0; dy <= 2; ++dy)
            for (int dx = 0; dx <= 2; ++dx)
                cv[(size_t)(1 + dy) * W2 + (1 + dx)] = (dy + dx) % 3;
        break;
    case 2:  // 中局：数字 + 未开 + 标记雷混合
        for (int y = 1; y <= H; ++y)
            for (int x = 1; x <= W; ++x) {
                int v = ((x * 7 + y * 13) % 11);
                cv[(size_t)y * W2 + x] = (v < 9) ? v : (v == 9 ? 9 : 10);
            }
        cv[1 * W2 + 1] = 0;  // 保证至少一个已开格
        break;
    case 3:  // 残局：大量已开 + 少量未开/旗
        for (int y = 1; y <= H; ++y)
            for (int x = 1; x <= W; ++x)
                cv[(size_t)y * W2 + x] = ((x + y) % 7 == 0) ? 9
                                       : ((x + y) % 11 == 0 ? 10 : (x + y) % 8);
        break;
    }
    return cv;
}

int main() {
    int calls = 0;
    // 1) 组合数学纯函数（真实棋盘规模）
    for (int n = 1; n <= 60; ++n) {
        volatile double v = C_num(n, n / 2);
        (void)v;
        ++calls;
    }
    for (int n = 2; n <= 30; ++n) {
        volatile double v = combination_ratio(1, 2, n);
        (void)v;
        ++calls;
    }
    for (int a = 1; a <= 40; ++a) {
        auto sizes = get_list_sizes(a, a / 2, 8);
        ++calls;
        (void)sizes;
    }

    // 3) count_situations_exact：构造真实分组矩阵
    {
        std::vector<std::vector<std::vector<int32_t>>> gmats;
        std::vector<int> ck;
        // 两组，每组 2 个解，每解 2 个格子（0/1 雷分布）
        for (int g = 0; g < 2; ++g) {
            std::vector<std::vector<int32_t>> rows;
            for (int r = 0; r < 2; ++r) {
                std::vector<int32_t> row = {0, 1};
                if (r == 1) row = {1, 0};
                rows.push_back(row);
            }
            gmats.push_back(rows);
            ck.push_back(2);
        }
        int64_t walked = 0;
        int64_t s = decision_layer::count_situations_exact(
            gmats, ck, /*a=*/10, /*num10=*/0, /*n9=*/4,
            /*cap=*/256, &walked);
        ++calls;
        std::printf("count_situations_exact=%lld walked=%lld\n",
                    (long long)s, (long long)walked);
        // 再跑一次 cap 很小的路径（触发哨兵早退分支）
        s = decision_layer::count_situations_exact(
            gmats, ck, 10, 0, 4, /*cap=*/2, &walked);
        ++calls;
        std::printf("count_situations_exact(cap=2)=%lld\n", (long long)s);
    }

    // 4) estimate_mem_impl / inner_expand_log2_upper / make_choose_table
    {
        auto e = decision_layer::estimate_mem_impl(1000, 50, 1536LL * 1024 * 1024);
        ++calls;
        std::printf("estimate_mem allowed=%d bytes=%lld\n",
                    (int)e.allowed, (long long)e.bytes);
        auto e2 = decision_layer::estimate_mem_impl(100000000, 400, 1536LL * 1024 * 1024);
        ++calls;
        std::printf("estimate_mem(big) allowed=%d\n", (int)e2.allowed);
        volatile double u = decision_layer::inner_expand_log2_upper(100, 50);
        (void)u; ++calls;
        bool sat = false;
        auto tab = decision_layer::make_choose_table(50, 25, &sat);
        ++calls;
        std::printf("choose_table n=%zu sat=%d\n", tab.size(), (int)sat);
    }

    // 5) Bits 位运算（纯段内）
    {
        Bits a, b;
        for (int i = 0; i < 10; ++i) { a.w[i] = 0xF0F0F0F0ULL; b.w[i] = 0x0F0F0F0FULL; }
        Bits c = b_and(a, b);
        Bits d = b_andnot(a, b);
        ++calls;
        std::printf("bits and=%llu andnot=%llu\n",
                    (unsigned long long)c.w[0], (unsigned long long)d.w[0]);
    }

    std::printf("CPP_COV_CALLS=%d\n", calls);
    return 0;
}
