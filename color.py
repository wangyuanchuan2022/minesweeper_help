import math
import colorsys
from typing import List, Tuple


class OKLCHGradient:
    """
    OKLCH颜色空间渐变生成器
    OKLCH: Lightness(亮度), Chroma(色度), Hue(色调)
    """

    # D65标准白点
    D65 = [0.95047, 1.0, 1.08883]

    @staticmethod
    def rgb_to_oklch(rgb: Tuple[float, float, float]) -> Tuple[float, float, float]:
        """
        将RGB颜色转换为OKLCH颜色空间
        RGB范围: [0, 1]
        """

        # 首先转换到线性RGB
        def linearize(c: float) -> float:
            if c <= 0.04045:
                return c / 12.92
            else:
                return ((c + 0.055) / 1.055) ** 2.4

        r_lin = linearize(rgb[0])
        g_lin = linearize(rgb[1])
        b_lin = linearize(rgb[2])

        # 转换到XYZ颜色空间
        x = r_lin * 0.4124564 + g_lin * 0.3575761 + b_lin * 0.1804375
        y = r_lin * 0.2126729 + g_lin * 0.7151522 + b_lin * 0.0721750
        z = r_lin * 0.0193339 + g_lin * 0.1191920 + b_lin * 0.9503041

        # 转换到LMS颜色空间
        l = 0.8189330101 * x + 0.3618667424 * y - 0.1288597137 * z
        m = 0.0329845436 * x + 0.9293118715 * y + 0.0361456387 * z
        s = 0.0482003018 * x + 0.2643662691 * y + 0.6338517070 * z

        # 非线性压缩
        l_ = l ** (1 / 3) if l > 0 else -((-l) ** (1 / 3))
        m_ = m ** (1 / 3) if m > 0 else -((-m) ** (1 / 3))
        s_ = s ** (1 / 3) if s > 0 else -((-s) ** (1 / 3))

        # 转换到OKLab
        L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
        a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
        b = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_

        # 转换到OKLCH
        C = math.sqrt(a * a + b * b)
        H = math.atan2(b, a)
        H = math.degrees(H) % 360

        return (L, C, H)

    @staticmethod
    def oklch_to_rgb(oklch: Tuple[float, float, float]) -> Tuple[float, float, float]:
        """
        将OKLCH颜色转换回RGB颜色空间
        """
        L, C, H = oklch

        # 转换回OKLab
        H_rad = math.radians(H)
        a = C * math.cos(H_rad)
        b = C * math.sin(H_rad)

        # 转换到LMS
        l_ = L + 0.3963377774 * a + 0.2158037573 * b
        m_ = L - 0.1055613458 * a - 0.0638541728 * b
        s_ = L - 0.0894841775 * a - 1.2914855480 * b

        # 非线性逆变换
        l = l_ ** 3 if l_ > 0 else -((-l_) ** 3)
        m = m_ ** 3 if m_ > 0 else -((-m_) ** 3)
        s = s_ ** 3 if s_ > 0 else -((-s_) ** 3)

        # 转换到XYZ
        x = 1.2270138511 * l - 0.5577999807 * m + 0.2812561490 * s
        y = -0.0405801784 * l + 1.1122568696 * m - 0.0716766787 * s
        z = -0.0763812845 * l - 0.4214819784 * m + 1.5861632204 * s

        # 转换到线性RGB
        r_lin = 3.2404542 * x - 1.5371385 * y - 0.4985314 * z
        g_lin = -0.9692660 * x + 1.8760108 * y + 0.0415560 * z
        b_lin = 0.0556434 * x - 0.2040259 * y + 1.0572252 * z

        # 伽马校正
        def delinearize(c: float) -> float:
            if c <= 0.0031308:
                return 12.92 * c
            else:
                return 1.055 * (c ** (1 / 2.4)) - 0.055

        r = delinearize(r_lin)
        g = delinearize(g_lin)
        b = delinearize(b_lin)

        # 限制在[0,1]范围内
        r = max(0, min(1, r))
        g = max(0, min(1, g))
        b = max(0, min(1, b))

        r = int(r * 255)
        g = int(g * 255)
        b = int(b * 255)

        return r, g, b

    @staticmethod
    def create_gradient(colors_rgb: List[Tuple[float, float, float]],
                        steps: int = 100) -> List[Tuple[float, float, float]]:
        """
        创建OKLCH渐变

        参数:
        colors_rgb: RGB颜色列表，每个颜色为(r, g, b)，范围[0, 1]
        steps: 渐变步数

        返回:
        RGB渐变颜色列表
        """
        if len(colors_rgb) < 2:
            raise ValueError("至少需要2个颜色来创建渐变")

        # 转换为OKLCH
        colors_oklch = [OKLCHGradient.rgb_to_oklch(rgb) for rgb in colors_rgb]

        gradient = []

        for i in range(steps):
            t = i / (steps - 1)  # 0到1的插值参数

            # 找到当前t在哪个颜色段
            segment_length = 1.0 / (len(colors_oklch) - 1)
            segment_index = min(int(t / segment_length), len(colors_oklch) - 2)
            segment_t = (t - segment_index * segment_length) / segment_length

            # 获取当前段的两个颜色
            start_oklch = colors_oklch[segment_index]
            end_oklch = colors_oklch[segment_index + 1]

            # 处理色调的循环插值
            start_L, start_C, start_H = start_oklch
            end_L, end_C, end_H = end_oklch

            # 计算最短的色调路径（考虑360°循环）
            hue_diff = end_H - start_H
            if abs(hue_diff) > 180:
                if hue_diff > 0:
                    start_H += 360
                else:
                    end_H += 360

            # 线性插值
            L = start_L + (end_L - start_L) * segment_t
            C = start_C + (end_C - start_C) * segment_t
            H = start_H + (end_H - start_H) * segment_t
            H = H % 360  # 确保色调在0-360范围内

            # 转换回RGB
            rgb = OKLCHGradient.oklch_to_rgb((L, C, H))
            gradient.append(rgb)

        return gradient

    @staticmethod
    def rgb_to_hex(rgb: Tuple[float, float, float]) -> str:
        """将RGB颜色转换为十六进制字符串"""
        r, g, b = [int(round(c * 255)) for c in rgb]
        return f"#{r:02x}{g:02x}{b:02x}"

    @staticmethod
    def hex_to_rgb(hex_color: str) -> Tuple[float, float, float]:
        """将十六进制颜色转换为RGB"""
        hex_color = hex_color.lstrip('#')
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b = int(hex_color[4:6], 16) / 255.0
        return (r, g, b)


# 使用示例
def example_usage():
    """使用示例"""
    gradient_gen = OKLCHGradient()

    # 示例1: 创建从蓝色到红色的渐变
    blue = (0, 0, 1)  # RGB蓝色
    red = (1, 0, 0)  # RGB红色

    print("蓝色到红色的OKLCH渐变:")
    gradient = gradient_gen.create_gradient([blue, red], steps=5)

    for i, color in enumerate(gradient):
        hex_color = gradient_gen.rgb_to_hex(color)
        print(f"步骤 {i}: RGB{tuple(round(c, 3) for c in color)} -> {hex_color}")

    print("\n" + "=" * 50)

    # 示例2: 多颜色渐变
    colors_hex = ["#ff0000", "#00ff00", "#0000ff", "#ffff00"]
    colors_rgb = [gradient_gen.hex_to_rgb(hex_color) for hex_color in colors_hex]

    print("红->绿->蓝->黄的多色渐变:")
    multi_gradient = gradient_gen.create_gradient(colors_rgb, steps=8)

    for i, color in enumerate(multi_gradient):
        hex_color = gradient_gen.rgb_to_hex(color)
        print(f"步骤 {i}: {hex_color}")

    print("\n" + "=" * 50)

    # 示例3: 与HSL渐变的比较
    def hsl_gradient(colors_rgb, steps):
        """传统的HSL渐变作为对比"""
        colors_hsl = [colorsys.rgb_to_hls(*rgb) for rgb in colors_rgb]
        gradient = []

        for i in range(steps):
            t = i / (steps - 1)
            segment_index = min(int(t * (len(colors_hsl) - 1)), len(colors_hsl) - 2)
            segment_t = (t - segment_index / (len(colors_hsl) - 1)) * (len(colors_hsl) - 1)

            start_h, start_l, start_s = colors_hsl[segment_index]
            end_h, end_l, end_s = colors_hsl[segment_index + 1]

            # 处理色调插值
            hue_diff = end_h - start_h
            if abs(hue_diff) > 0.5:
                if hue_diff > 0:
                    start_h += 1.0
                else:
                    end_h += 1.0

            h = (start_h + (end_h - start_h) * segment_t) % 1.0
            l = start_l + (end_l - start_l) * segment_t
            s = start_s + (end_s - start_s) * segment_t

            rgb = colorsys.hls_to_rgb(h, l, s)
            gradient.append(rgb)

        return gradient

    print("OKLCH vs HSL 渐变比较:")
    oklch_grad = gradient_gen.create_gradient([blue, red])
    hsl_grad = hsl_gradient([blue, red], steps=5)

    print("OKLCH渐变:")
    for color in oklch_grad:
        print(f"  RGB{tuple(round(c, 3) for c in color)}")

    print("HSL渐变:")
    for color in hsl_grad:
        print(f"  RGB{tuple(round(c, 3) for c in color)}")


if __name__ == "__main__":
    example_usage()