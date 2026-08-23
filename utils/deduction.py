# -*- coding: utf-8 -*-
"""基础规则推理：单格规则（number0）、1-1/1-2 式子集推理（number_3_1）及邻域查询。

自 utils/util.py 拆分而来。``DeductionMixin`` 不单独使用，
由 ``utils.solver.Solver`` 混入，依赖实例属性：
``w``/``h``/``is_play``/``bx``/``by``/``cell_width``/``num``/
``pos_dict_list``/``appended_pos``/``cell_value``，
以及 ``rescan_after_click``（见 ``utils.vision``）。
"""
import pyautogui


class DeductionMixin:
    def cell_around(self, i, j, cell_value):
        cnt9 = 0
        cnt10 = 0
        for n in range(j - 1, j + 2):
            for m in range(i - 1, i + 2):
                if 0 <= m <= self.w + 1 and 0 <= n <= self.h + 1:
                    if cell_value[n, m] == 9:
                        cnt9 += 1
                    if cell_value[n, m] == 10:
                        cnt10 += 1
        return cnt9, cnt10

    def mine_clear1(self, cell_value, clicks=None):
        if clicks:
            for i, j in clicks:
                cell_value = self.number0(i, j, cell_value)
            return cell_value

        for j in range(1, self.h + 1):
            if (
                    (9 in cell_value[j - 1])
                    or (9 in cell_value[j])
                    or (9 in cell_value[j + 1])
            ):
                for i in range(1, self.w + 1):
                    if 0 < cell_value[j, i] < 8:
                        cell_value = self.number0(i, j, cell_value)

        return cell_value

    def number0(self, i, j, cell_value):
        c = False
        cnt9, cnt10 = self.cell_around(i, j, cell_value)
        if not cnt10 <= cell_value[j, i] <= cnt9 + cnt10:
            raise ValueError("识别错误")
        if (cnt9 + cnt10) == cell_value[j, i] and cnt9 != 0:
            for n in range(j - 1, j + 2):
                for m in range(i - 1, i + 2):
                    if cell_value[n, m] == 9:
                        cell_value[n, m] = 10
                        if not self.is_play:
                            if (
                                    tuple((m, n)) not in self.appended_pos
                                    and self.cell_value[n, m] != 10
                            ):
                                c = True
                                self.pos_dict_list.append(
                                    {
                                        "pos": (m, n),
                                        "confidence": 0,
                                        "num": self.num,
                                        "is_mine": True,
                                        "is_best": False,
                                        "exp": f"由({i}, {j})得出",
                                        "is_recommend": False,
                                    }
                                )
                                self.appended_pos.add(tuple((m, n)))

        elif cnt10 == cell_value[j, i] and cnt9 >= 1:
            for n in range(j - 1, j + 2):
                for m in range(i - 1, i + 2):
                    if cell_value[n, m] == 9:
                        if self.is_play:
                            c = True
                            pyautogui.click(
                                self.bx + m * self.cell_width,
                                self.by + n * self.cell_width,
                            )
                        else:
                            if tuple((m, n)) not in self.appended_pos:
                                c = True
                                cell_value[n, m] = 11
                                self.pos_dict_list.append(
                                    {
                                        "pos": (m, n),
                                        "confidence": 1,
                                        "num": self.num,
                                        "is_mine": False,
                                        "is_best": True,
                                        "exp": f"由({i}, {j})得出",
                                        "is_recommend": False,
                                    }
                                )
                                self.appended_pos.add(tuple((m, n)))
        if c:
            if not self.is_play:
                self.num += 1
            else:
                cell_value = self.rescan_after_click(cell_value)

        return cell_value

    def number_3_1(self, i, j, cell_value):
        x1 = cell_value[j, i]
        a, cnt10 = self.get_set(i, j, cell_value)
        x1 -= cnt10
        if x1 <= 0:
            return cell_value
        for x in range(i - 2, i + 3):
            for y in range(j - 2, j + 3):
                x2 = cell_value[y, x]
                if y > self.h or y < 1 or x > self.w or x < 1 or (x == i and y == j):
                    continue
                elif 0 < x2 < 8:
                    c = False
                    b, cnt10_x2 = self.get_set(x, y, cell_value)
                    x2 -= cnt10_x2
                    if x2 < 0:
                        continue
                    bj = a | b
                    x_set = bj - b
                    z_set = bj - a
                    y_set = a & b
                    if x1 - x2 == len(x_set):
                        for u, v in x_set:
                            cell_value[v, u] = 10
                            if not self.is_play:
                                if (
                                        tuple((u, v)) not in self.appended_pos
                                        and self.cell_value[v, u] != 10
                                ):
                                    c = True
                                    self.pos_dict_list.append(
                                        {
                                            "pos": (u, v),
                                            "confidence": 0,
                                            "num": self.num,
                                            "is_mine": True,
                                            "is_best": False,
                                            "exp": f"由({i}, {j}), ({x}, {y})得出\n"
                                                   f"{list(y_set) if len(y_set) > 0 else str('公共区域')}"
                                                   f"中至多有{x2}个雷，{list(x_set | y_set)}中"
                                                   f"有{x1}个雷，所以{list(x_set)}是雷。",
                                            "is_recommend": False,
                                        }
                                    )
                                    self.appended_pos.add(tuple((u, v)))

                        for u, v in z_set:
                            if cell_value[v, u] == 9:
                                if self.is_play:
                                    pyautogui.click(
                                        self.bx + u * self.cell_width,
                                        self.by + v * self.cell_width,
                                    )
                                    c = True
                                else:
                                    cell_value[v, u] = 11
                                    if tuple((u, v)) not in self.appended_pos:
                                        c = True
                                        self.pos_dict_list.append(
                                            {
                                                "pos": (u, v),
                                                "confidence": 1,
                                                "num": self.num,
                                                "is_mine": False,
                                                "is_best": True,
                                                "exp": f"由({i}, {j}), ({x}, {y})得出\n"
                                                       f"{str(f'已经可以判断出{list(x_set)}是雷。') if len(x_set) != 0 else str('')}"
                                                       f"现在{list(y_set) if len(y_set) > 0 else str('公共区域')}"
                                                       f"中有{x2}个雷，{list(z_set | y_set)}中有"
                                                       f"{x2}个雷，所以{list(z_set)}不是雷。",
                                                "is_recommend": False,
                                            }
                                        )
                                        self.appended_pos.add(tuple((u, v)))

                    if x2 - x1 == len(z_set):
                        for u, v in z_set:
                            cell_value[v, u] = 10
                            if not self.is_play:
                                if (
                                        tuple((u, v)) not in self.appended_pos
                                        and self.cell_value[v, u] != 10
                                ):
                                    c = True
                                    self.pos_dict_list.append(
                                        {
                                            "pos": (u, v),
                                            "confidence": 0,
                                            "is_mine": True,
                                            "num": self.num,
                                            "is_best": False,
                                            "exp": f"由({i}, {j}), ({x}, {y})得出\n"
                                                   f"{list(y_set) if len(y_set) > 0 else str('公共区域')}"
                                                   f"中至多有{x1}个雷，{list(z_set | y_set)}中"
                                                   f"有{x2}个雷，所以{list(z_set)}是雷。",
                                            "is_recommend": False,
                                        }
                                    )
                                    self.appended_pos.add(tuple((u, v)))
                        for u, v in x_set:
                            if cell_value[v, u] == 9:
                                if self.is_play:
                                    pyautogui.click(
                                        self.bx + u * self.cell_width,
                                        self.by + v * self.cell_width,
                                    )
                                    c = True
                                else:
                                    cell_value[v, u] = 11
                                    if tuple((u, v)) not in self.appended_pos:
                                        c = True
                                        self.pos_dict_list.append(
                                            {
                                                "pos": (u, v),
                                                "confidence": 1,
                                                "is_mine": False,
                                                "num": self.num,
                                                "is_best": True,
                                                "exp": f"由({i}, {j}), ({x}, {y})得出\n"
                                                       f"{str(f'已经可以判断出{list(z_set)}是雷。') if len(z_set) != 0 else str('')}"
                                                       f"现在{list(y_set) if len(y_set) > 0 else str('公共区域')}"
                                                       f"中有{x1}个雷，{list(x_set | y_set)}中有"
                                                       f"{x1}个雷，所以{list(x_set)}不是雷。",
                                                "is_recommend": False,
                                            }
                                        )
                                        self.appended_pos.add(tuple((u, v)))

                    if c:
                        if not self.is_play:
                            self.num += 1
                        else:
                            cell_value = self.rescan_after_click(cell_value)

        return cell_value

    def mine_clear3_1(self, cell_value, clicks=None):
        if clicks:
            for i, j in clicks:
                cell_value = self.number_3_1(i, j, cell_value)
            return cell_value
        for i in range(2, self.w):
            for j in range(2, self.h):
                if 0 < cell_value[j, i] < 8:
                    if self.cell_around(i, j, cell_value)[0] > 0:
                        cell_value = self.number_3_1(i, j, cell_value)
        return cell_value

    def get_set_1(self, i, j, cell_value):
        result = set()
        for n in range(j - 1, j + 2):
            for m in range(i - 1, i + 2):
                if 0 <= m <= self.w + 1 and 0 <= n <= self.h + 1:
                    if 0 < cell_value[n, m] < 8:
                        result.add((m, n))
        return result

    def get_set(self, i, j, cell_value):
        result = set()
        cnt10 = 0
        for n in range(j - 1, j + 2):
            for m in range(i - 1, i + 2):
                if 0 <= m <= self.w + 1 and 0 <= n <= self.h + 1:
                    if cell_value[n, m] == 9:
                        result.add((m, n))
                    elif cell_value[n, m] == 10:
                        cnt10 += 1
        return result, cnt10
