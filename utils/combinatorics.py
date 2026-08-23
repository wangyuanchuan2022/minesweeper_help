# -*- coding: utf-8 -*-
"""组合数学工具：组合枚举、组合数计算与比值。

自 utils/util.py 拆分而来，纯函数、无项目内依赖（仅依赖 numpy）。
"""
import numpy as np


# 0：已开方格
# 1~8 ： 1~8
# 9：未开方格
# 10：雷
def C(a, b, start_from=None):
    """
    从a中选出b个数
    :param start_from: array
    :param a: must be larger than
    :param b:
    :return: list

    Examples
    --------
    >>> for i in C(4, 2):
    ...     print(i)
    [0, 1]
    [0, 2]
    [0, 3]
    [1, 2]
    [1, 3]
    [2, 3]
    """
    if b == 0:
        yield []
        return
    if b < 0 or b > a:
        return
    ck = range(a - b, a)
    num = list(range(b)) if start_from is None else list(start_from)
    while True:
        yield num.copy()
        num[-1] += 1
        for i in range(b):
            i *= -1
            if num[i] > ck[i]:
                num[i - 1] += 1
        if num[0] > ck[0]:
            break

        for i in range(b):
            if num[i] > ck[i]:
                num[i] = num[i - 1] + 1


def C_num(a, b):
    result = 1
    for i in range(b):
        result *= a - i
        result /= i + 1
    return result


def get_list(a, num, listnum, start=0, stop=-1):
    """
    :param stop:
    :param start:
    :param a: 小于num的正整数
    :param num: 小于listnum的正整数
    :param listnum: 列表的长度
    :return: 索引组成的列表

    Examples
    --------
    >>> for i in get_list(1, 4, 6):
    ...     print(i)
    [0]
    [1]
    [2]
    [3]
    [4]
    [5]
    [0, 1]
    [0, 2]
    [0, 3]
    [0, 4]
    [0, 5]
    [1, 2]
    [1, 3]
    [1, 4]
    [1, 5]
    [2, 3]
    [2, 4]
    [2, 5]
    [3, 4]
    [3, 5]
    [4, 5]
    [0, 1, 2]
    [0, 1, 3]
    [0, 1, 4]
    [0, 1, 5]
    [0, 2, 3]
    [0, 2, 4]
    [0, 2, 5]
    [0, 3, 4]
    [0, 3, 5]
    [0, 4, 5]
    [1, 2, 3]
    [1, 2, 4]
    [1, 2, 5]
    [1, 3, 4]
    [1, 3, 5]
    [1, 4, 5]
    [2, 3, 4]
    [2, 3, 5]
    [2, 4, 5]
    [3, 4, 5]
    [0, 1, 2, 3]
    [0, 1, 2, 4]
    [0, 1, 2, 5]
    [0, 1, 3, 4]
    [0, 1, 3, 5]
    [0, 1, 4, 5]
    [0, 2, 3, 4]
    [0, 2, 3, 5]
    [0, 2, 4, 5]
    [0, 3, 4, 5]
    [1, 2, 3, 4]
    [1, 2, 3, 5]
    [1, 2, 4, 5]
    [1, 3, 4, 5]
    [2, 3, 4, 5]
    """
    a = int(a)
    num = int(num)
    listnum = int(listnum)
    if a < 1:
        a = 1

    if num > listnum - 1:
        num = listnum - 1

    if num < 1:
        num = 1

    if num < a:
        a = num

    total = [0]
    for i in range(a, num + 1):
        total.append(total[-1] + C_num(listnum, i))
    yield total[-1]
    if stop == -1:
        stop = total[-1]
    start_index = a + get_index_from_list(start, total) - 1
    returned = 0
    left_num = start - total[start_index - a]
    counter = 0
    for c in C(listnum, start_index):
        if counter >= left_num:
            yield c.copy()
            returned += 1
        counter += 1
        if returned >= stop - start:
            break

    for i in range(start_index + 1, num + 1):
        for c in C(listnum, i):
            if returned >= stop - start:
                break
            yield c.copy()
            returned += 1


def get_index_from_list(num, _list):
    for i in range(len(_list)):
        if num < _list[i]:
            return i
    return -1


def A(ck: list):
    """
    全排列
    :param ck: 列表中每一项长度
    :return: 索引组成的列表

    Examples
    --------
    >>> for i in A([2,1,3]):
    ...     print(i)
    [0 0 0]
    [0 0 1]
    [0 0 2]
    [1 0 0]
    [1 0 1]
    [1 0 2]
    """
    num = np.zeros(len(ck), dtype=np.int32)
    while True:
        yield num.copy()
        if len(ck) == 0:
            break
        num[-1] += 1
        for i in range(len(ck)):
            i *= -1
            if num[i] >= ck[i]:
                num[i - 1] += 1
        if num[0] >= ck[0]:
            break

        for i in range(len(ck)):
            if num[i] >= ck[i]:
                num[i] = 0


def p_of_c(x: int, n: int):
    x = int(x)
    n = int(n)
    if n == 0:
        return 1
    assert n >= x >= 0
    k = n // 2
    if x >= k:
        x = n - x
    res = 1
    for i in range(k - x):
        res *= x + 1 + i
        res /= n - x - i
    return res


def combination_ratio(x: int, x_min: int, n: int):
    """计算组合数比值 C(n, x) / C(n, x_min)，其中 0 <= x <= x_min <= n。

    直接逐项乘除，避免分别计算两个组合数再相除引入的浮点误差::

        C(n, x) / C(n, x_min) = ∏_{i=0}^{x_min-x-1} (x + 1 + i) / (n - x - i)
    """
    x = int(x)
    x_min = int(x_min)
    n = int(n)
    assert 0 <= x <= x_min <= n
    res = 1.0
    for i in range(x_min - x):
        res *= x + 1 + i
        res /= n - x - i
    return res
