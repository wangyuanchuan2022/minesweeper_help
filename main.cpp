#include <iostream>
#include <vector>
#include <ctime>

unsigned long int C_num(const int a, const int b) {
    unsigned long int total = 1;
    for (int i = 0; i < b; ++i) {
        total *= (a - i);
        total /= (i + 1);
    }
    return total;
}

std::vector<std::vector<int>> C_list(const int a, const int b) {
    std::vector<std::vector<int>> result;
    std::vector<int> ck;
    std::vector<int> num;
    result.clear();
    ck.clear();
    num.clear();
    for (int i = 0; i < b; ++i) {
        num.push_back(i);
        ck.push_back((a - b + i));
    }
    unsigned long int limit = num.size();
    unsigned long int total = C_num(a, b);
    for (unsigned long int i = 0; i < total; ++i) {
        result.push_back(num);
        std::vector<int> new_num(num);
        new_num[limit - 1] += 1;
        for (unsigned long j = limit - 1; j > 0; --j) {
            if (new_num[j] > ck[j]){
                new_num[j - 1] += 1;
            }
        }
        for (unsigned long int j = 1; j < limit; ++j) {
            if (new_num[j] > ck[j]){
                new_num[j] = new_num[j - 1] + 1;
            }
        }

        num = new_num;
        std::vector<int>().swap(new_num);
    }
    std::vector<int>().swap(num);
    return result;
}

std::vector<std::vector<int>> get_list(int a, int num, const int list_num) {
    if (a < 1){
        a = 1;
    }
    if (num > list_num - 1){
        num = list_num - 1;
    }
    if (num < 1){
        num = 1;
    }
    if (num < a){
        a = num;
    }

    std::vector<std::vector<int>> result;
    result.clear();
    for (int i = a; i < num + 1; ++i) {
        std::vector<std::vector<int>> res = C_list(list_num, i);
        for (const auto & re : res) {
            result.push_back(re);
        }
        std::vector<std::vector<int>>().swap(res);
    }
    return result;
}

extern "C"{
[[maybe_unused]] void part_solve(const int * clicks, int len_clicks, const int * cell_value, int w, int h,
    int num10, int num9, const int * cs, int len_cs, int a, int * res_list, unsigned long int res_w){
        std::vector<std::vector<int>> list = get_list(a - num10 - num9, a - num10, len_clicks);
        const int len = (w + 2) * (h + 2);
        unsigned long int res_index = 0;
        int * value = new int[len];
        for (unsigned long int index = 0; index < list.size(); ++index) {
            for (int i = 0; i < (w + 2); ++i) {
                for (int j = 0; j < (h + 2); ++j) {
                    value[j * (w + 2) + i] = cell_value[j * (w + 2) + i];
                }
            }
            for (int loc : list[index]) {
                value[clicks[loc * 2 + 1] * (w + 2) + clicks[loc * 2]] = 10;
            }

            int flag = 0;
            for (int i = 0; i < len_cs; ++i) {
                int m = cs[i * 2];
                int n = cs[i * 2 + 1];
                int cnt10 = 0;
                for (int x = m - 1; x < m + 2; ++x) {
                    for (int y = n - 1; y < n + 2; ++y) {
                        if (value[y * (w + 2) + x] == 10){
                            cnt10 += 1;
                        }
                    }
                }
                if (value[n * (w + 2) + m] != cnt10){
                    flag = -1;
                    break;
                }
            }
            if (flag == 0){
                for (int i = 0; i < res_w; ++i) {
                    res_list[res_index * res_w + i] = 0;
                }
                for (int loc:list[index]) {
                    res_list[res_index * res_w + loc] = 1;
                }
                res_index += 1;
            }
        }
        std::vector<std::vector<int>>().swap(list);
        int flag = 0;
        for (int i = 0; i < (w + 2); ++i) {
            for (int j = 0; j < (h + 2); ++j) {
                value[j * (w + 2) + i] = cell_value[j * (w + 2) + i];
            }
        }
        for (int i = 0; i < len_cs; ++i) {
            int m = cs[i * 2];
            int n = cs[i * 2 + 1];
            int cnt10 = 0;
            for (int x = m - 1; x < m + 2; ++x) {
                for (int y = n - 1; y < n + 2; ++y) {
                    if (value[y * (w + 2) + x] == 10){
                        cnt10 += 1;
                    }
                }
            }
            if (value[n * (w + 2) + m] != cnt10){
                flag = -1;
                break;
            }
        }
        if (flag == 0){
            for (int i = 0; i < res_w; ++i) {
                res_list[res_index * res_w + i] = 0;
            }
        }
        delete[] value;
    }
}

int main() {
    clock_t start, end;
    start=clock();
    int * arr = new int[16]{0,0,0,0,0,0,9,0,0,0,0,0,0,0,0,0};
    int * cs = new int [2]{1, 1};
    int * clicks = new int [2]{2, 1};
    int * out = new int [3]{2, 2, 2};
    part_solve(clicks, 1, arr, 2, 2, 0, 0, cs, 1, 0, out, 1);
    for (int i = 0; i < 2; ++i) {
        std::cout << out[i] << '\n';
    }
    end=clock();
    std::cout<<"DBSCAN time: "<<(end-start)<<" ms"<<std::endl;         // time.h计时
    return 0;
}
