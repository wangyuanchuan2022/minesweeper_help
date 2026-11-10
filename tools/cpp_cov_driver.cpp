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

    // 5) Bits 位运算全方法（纯段内）
    {
        Bits a, b;
        for (int i = 0; i < 10; ++i) { a.w[i] = 0xF0F0F0F0ULL; b.w[i] = 0x0F0F0F0FULL; }
        a.nw = b.nw = 10;
        Bits c = b_and(a, b);
        Bits d = b_andnot(a, b);
        ++calls;
        std::printf("bits and=%llu andnot=%llu\n",
                    (unsigned long long)c.w[0], (unsigned long long)d.w[0]);
        // 全方法覆盖：zero / ones / test / set / empty / count / lowest / ==
        Bits e;
        e.zero(10);
        ++calls;
        bool e_empty = e.empty();
        int e_cnt = e.count();
        int e_low = e.lowest();
        e.set(7);
        e.set(63);
        e.set(64);
        bool e_test = e.test(7);
        bool e_empty2 = e.empty();
        int e_cnt2 = e.count();
        int e_low2 = e.lowest();
        bool eq = (e == e);
        Bits f;
        f.ones(10, 480);
        int f_cnt = f.count();
        int f_low = f.lowest();
        bool eq2 = (e == f);
        ++calls;
        std::printf("bits empty=%d/%d cnt=%d/%d low=%d/%d test=%d eq=%d/%d "
                    "ones_cnt=%d ones_low=%d\n",
                    (int)e_empty, (int)e_empty2, e_cnt, e_cnt2, e_low, e_low2,
                    (int)e_test, (int)eq, (int)eq2, f_cnt, f_low);
    }

    // 6) Board：4-bit 打包/解包、operator[] 读写、pair 构造、容量越界
    {
        std::vector<int32_t> vals;
        for (int i = 0; i < 512; ++i) vals.push_back((i % 4 == 0) ? 9
                                                     : (i % 7 == 0 ? 10 : i % 9));
        Board bd(vals.data(), vals.data() + vals.size());
        ++calls;
        int32_t g0 = bd.get(0);
        int32_t g1 = bd.get(1);
        bd.set(0, 5);
        int32_t g2 = bd.get(0);
        int32_t via_ref = bd[2];          // const operator[]
        bd[3] = 7;                        // Ref::operator=
        int32_t via_ref2 = bd[3];
        size_t bsz = bd.size();
        ++calls;
        // Ref 拷贝赋值分支
        bd[4] = bd[5];
        // Odometer：真实分组组合迭代（含空 limits 与单组）
        std::vector<int> ckb = {2, 3, 2};
        Odometer od(ckb);
        std::vector<int> idx;
        int od_n = 0;
        while (od.next(idx) && od_n < 200) ++od_n;
        ++calls;
        std::vector<int> ck0;
        Odometer od0(ck0);
        std::vector<int> idx0;
        int od0_n = 0;
        while (od0.next(idx0) && od0_n < 5) ++od0_n;
        ++calls;
        std::printf("board size=%zu g0=%d g1=%d g2=%d ref=%d ref2=%d "
                    "od_n=%d od0_n=%d\n",
                    bsz, g0, g1, g2, via_ref, via_ref2, od_n, od0_n);
        // cell_around：真实 30x16 盘面，含边界内外
        auto cvb = make_board(2);
        Board bcell(cvb.data(), cvb.data() + cvb.size());
        int c9 = 0, c10 = 0;
        cell_around(bcell, W, H, W2, 1, 1, c9, c10);       // 角落（边界外邻域）
        ++calls;
        cell_around(bcell, W, H, W2, 15, 8, c9, c10);      // 中间
        ++calls;
        cell_around(bcell, W, H, W2, W, H, c9, c10);       // 最右下
        ++calls;
        std::printf("cell_around c9=%d c10=%d\n", c9, c10);
        // init_size 越界：必须抛 runtime_error（fail-closed）
        bool threw = false;
        try {
            std::vector<int32_t> huge((size_t)PACK_MAX_CELLS + 8, 0);
            Board bad(huge.data(), huge.data() + huge.size());
            (void)bad;
        } catch (const std::runtime_error&) {
            threw = true;
        }
        ++calls;
        std::printf("board capacity overflow threw=%d\n", (int)threw);
    }

    // 7) inner_expand_log2_upper 全分支（N<=0 / 钳位 / 非有限）
    {
        double u1 = decision_layer::inner_expand_log2_upper(0, 10);      // N<=0
        double u2 = decision_layer::inner_expand_log2_upper(-5, 10);     // N<0
        double u3 = decision_layer::inner_expand_log2_upper(1, 100);     // s_hi>N 钳位
        double u4 = decision_layer::inner_expand_log2_upper(100, -3);    // s_hi<0 钳位
        double u5 = decision_layer::inner_expand_log2_upper(100, 0);     // rem=0
        double u6 = decision_layer::inner_expand_log2_upper(479, 99);    // 真实盘面
        double u7 = decision_layer::inner_expand_log2_upper(60, 30);
        ++calls;
        std::printf("log2_upper %.3f %.3f %.3f %.3f %.3f %.3f %.3f\n",
                    u1, u2, u3, u4, u5, u6, u7);
        // MemEstimate 结构字段
        auto em = decision_layer::estimate_mem_impl(0, 0, 1);
        auto em2 = decision_layer::estimate_mem_impl(1, 1, 0);
        ++calls;
        std::printf("mem_est0 worlds=%lld allowed=%d | mem_est1 allowed=%d\n",
                    (long long)em.worlds, (int)em.allowed, (int)em2.allowed);
        // make_choose_table 饱和与不饱和两条路径
        bool sat1 = false, sat2 = false;
        auto t1 = decision_layer::make_choose_table(200, 100, &sat1);
        auto t2 = decision_layer::make_choose_table(3, 1, &sat2);
        ++calls;
        std::printf("choose_table sat=%d n=%zu | sat2=%d n2=%zu\n",
                    (int)sat1, t1.size(), (int)sat2, t2.size());
    }

    // 8) ComboIter：组合迭代器全分支（正常 / b==0 / b 越界）
    {
        // 正常：C(6,3)=20 组
        ComboIter ci1(6, 3);
        std::vector<int> out;
        int n1 = 0;
        while (ci1.next(out)) ++n1;
        // b==0：只 yield 一次
        ComboIter ci0(5, 0);
        int n0 = 0;
        while (ci0.next(out)) ++n0;
        // b 越界（b<0 / b>a）：done 立即为 true，零次
        ComboIter cibad1(5, -1);
        int nb1 = 0;
        while (cibad1.next(out)) ++nb1;
        ComboIter cibad2(3, 9);
        int nb2 = 0;
        while (cibad2.next(out)) ++nb2;
        ++calls;
        std::printf("comboiter C(6,3)=%d b0=%d bneg=%d bover=%d\n",
                    n1, n0, nb1, nb2);
        // 真实棋盘规模：C(30,2)=435 / C(20,5)=15504
        ComboIter ci2(30, 2);
        int n2 = 0;
        while (ci2.next(out) && n2 < 5000) ++n2;
        ComboIter ci3(20, 5);
        int n3 = 0;
        while (ci3.next(out) && n3 < 20000) ++n3;
        ++calls;
        std::printf("comboiter C(30,2)=%d C(20,5)=%d\n", n2, n3);
    }

    // 9) ExactEngine 内部纯 C++ 结构：Key/KeyHash、Obs/ObsHash、Bits 容器
    {
        using EE = ExactEngine;
        // Key 相等与哈希（memo 键）
        EE::Key k1{1, 2, 3, 4}, k2{1, 2, 3, 4}, k3{9, 2, 3, 4};
        EE::KeyHash kh;
        bool keq = (k1 == k2);
        bool kne = (k1 == k3);
        size_t h1 = kh(k1), h3 = kh(k3);
        ++calls;
        // Obs 相等与哈希（世界观测签名）
        EE::Obs o1, o2, o3;
        o1.opened.zero(10); o2.opened.zero(10); o3.opened.zero(10);
        o1.opened.set(5); o2.opened.set(5); o3.opened.set(6);
        o1.pairs = {{3, 1}, {4, 0}};
        o2.pairs = {{3, 1}, {4, 0}};
        o3.pairs = {{3, 1}};
        // 三条分支：opened 不等 / pairs 长度不等 / pairs 元素不等
        bool o12 = (o1 == o2);      // true（全同）
        bool o13 = (o1 == o3);      // false（opened + pairs 长度都不同）
        EE::Obs o4 = o1;
        o4.pairs[0] = {7, 2};       // 同长度但元素不同
        bool o14 = (o1 == o4);      // false（pairs 元素不等分支）
        EE::ObsHash oh;
        size_t ho1 = oh(o1), ho4 = oh(o4);
        // Bits 相同长度不同内容 → operator== false 分支
        Bits b1, b2;
        b1.zero(10); b2.zero(10);
        b1.set(1); b2.set(2);
        bool beq = (b1 == b2);
        Bits b3;
        b3.zero(5);                 // nw 不同 → 首行 false 分支
        bool beq2 = (b1 == b3);
        ++calls;
        std::printf("engine keys eq=%d ne=%d h=%zu/%zu | obs %d/%d/%d "
                    "oh=%zu/%zu | bits %d/%d\n",
                    (int)keq, (int)kne, h1, h3,
                    (int)o12, (int)o13, (int)o14, ho1, ho4,
                    (int)beq, (int)beq2);
    }

    // 10) 小函数边界：get_list_sizes / combination_ratio_checked /
    //     msw_ctzll / msw_popcountll / Board const operator[]
    {
        // get_list_sizes 四条钳制分支：a<1 / num>listnum / num<1 / num<a
        auto s1 = get_list_sizes(0, 5, 8);      // a<1 -> a=1
        auto s2 = get_list_sizes(3, 99, 8);     // num>listnum -> num=8
        auto s3 = get_list_sizes(3, 0, 8);      // num<1 -> num=1, 再 num<a -> a=1
        auto s4 = get_list_sizes(6, 3, 8);      // num<a -> a=3
        auto s5 = get_list_sizes(4, 6, 8);      // 正常
        ++calls;
        std::printf("get_list_sizes %zu/%zu/%zu/%zu/%zu first=%d/%d/%d/%d/%d\n",
                    s1.size(), s2.size(), s3.size(), s4.size(), s5.size(),
                    s1.empty() ? -1 : s1[0], s2.empty() ? -1 : s2[0],
                    s3.empty() ? -1 : s3[0], s4.empty() ? -1 : s4[0],
                    s5.empty() ? -1 : s5[0]);
        // combination_ratio_checked：合法 + 三条非法（x<0 / x>x_min / x_min>n）
        double out = 0.0;
        bool ok1 = combination_ratio_checked(2, 3, 10, &out);
        bool ok2 = combination_ratio_checked(-1, 3, 10, &out);
        bool ok3 = combination_ratio_checked(4, 3, 10, &out);
        bool ok4 = combination_ratio_checked(2, 12, 10, &out);
        bool ok5 = combination_ratio_checked(0, 0, 0, &out);
        ++calls;
        std::printf("comb_ratio_checked ok=%d/%d/%d/%d/%d out=%.6f\n",
                    (int)ok1, (int)ok2, (int)ok3, (int)ok4, (int)ok5, out);
        // msw_ctzll(x==0) 走 GCC 分支的 "无置位" 约定；x!=0 正常
        int c0 = msw_ctzll(0ull);
        int c1 = msw_ctzll(1ull << 40);
        int pc0 = msw_popcountll(0ull);
        int pc1 = msw_popcountll(0xF0F0F0F0ull);
        ++calls;
        std::printf("ctzll(0)=%d ctzll(2^40)=%d popcnt=%d/%d\n",
                    c0, c1, pc0, pc1);
        // Board const operator[]（与可变 operator[] 不同分支）
        std::vector<int32_t> bv(480, 9);
        bv[0] = 1; bv[479] = 10;
        const Board cbd(bv.data(), bv.data() + bv.size());
        int32_t v0 = cbd[0];
        int32_t v479 = cbd[479];
        ++calls;
        std::printf("board const[] v0=%d v479=%d size=%zu\n",
                    v0, v479, cbd.size());
    }

    std::printf("CPP_COV_CALLS=%d\n", calls);
    return 0;
}
