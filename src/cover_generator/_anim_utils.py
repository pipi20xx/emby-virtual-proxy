"""动态封面(滚动列/轮播)共享工具。

代码移植自 sy1024 项目 app/services/cover_generator.py(同作者项目),负责:
配色提取、渐变/模糊背景、圆角阴影卡片、滚动列帧合成、动图编码(GIF/WebP/APNG)。
动态画布固定 320x180 (16:9),兼容 Emby 动态壁纸。

本模块不提供 create_ 入口函数,不会被样式调度选中,仅供各动态样式插件复用。
"""

import base64
import colorsys
import math
import random
from collections import Counter
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

import logging
logger = logging.getLogger(__name__)

# 动态画布尺寸(16:9),与 sy1024 保持一致以兼容 Emby 动态壁纸
ANIM_WIDTH = 320
ANIM_HEIGHT = 180
ANIM_FRAME_COUNT = 120    # 120 帧无缝循环
ANIM_FRAME_DURATION = 67  # 每帧毫秒数 (15 FPS)

# 多图布局参数(与静态多图样式 style_multi_1 同源)
POSTER_GEN_CONFIG = {
    "ROWS": 3,
    "COLS": 3,
    "MARGIN": 22,
    "CORNER_RADIUS": 46.1,
    "ROTATION_ANGLE": -15.8,
    "START_X": 835,
    "START_Y": -362,
    "COLUMN_SPACING": 100,
    "CELL_WIDTH": 410,
    "CELL_HEIGHT": 610,
    "CANVAS_WIDTH": 1920,
    "CANVAS_HEIGHT": 1080,
}

SUPPORTED_MATERIAL_FORMATS = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")


# ========== 素材收集 ==========

def collect_material_paths(library_dir, needed=None):
    """从素材目录收集图片路径,按文件名数字排序;不足 needed 时循环补齐。"""
    files = [
        p for p in Path(library_dir).iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_MATERIAL_FORMATS
    ]
    files.sort(key=lambda p: int(p.stem) if p.stem.isdigit() else float("inf"))
    paths = [str(p) for p in files]
    if needed:
        if not paths:
            return []
        fill = list(paths)
        while len(fill) < needed:
            fill.extend(fill)
        paths = fill[:needed]
    return paths


# ========== 配色提取 ==========

def is_not_black_white_gray_near(color, threshold=20):
    """判断颜色既不是黑、白、灰,也不是接近黑、白。"""
    r, g, b = color
    if (r < threshold and g < threshold and b < threshold) or \
       (r > 255 - threshold and g > 255 - threshold and b > 255 - threshold):
        return False
    gray_diff_threshold = 10
    if abs(r - g) < gray_diff_threshold and abs(g - b) < gray_diff_threshold and abs(r - b) < gray_diff_threshold:
        return False
    return True


def rgb_to_hsv(color):
    r, g, b = [x / 255.0 for x in color]
    return colorsys.rgb_to_hsv(r, g, b)


def hsv_to_rgb(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def adjust_to_macaron(h, s, v, target_saturation_range=(0.2, 0.7), target_value_range=(0.55, 0.85)):
    """将颜色的饱和度和亮度调整到接近马卡龙色系的范围,同时避免颜色过亮。"""
    adjusted_s = min(max(s, target_saturation_range[0]), target_saturation_range[1])
    adjusted_v = min(max(v, target_value_range[0]), target_value_range[1])
    return adjusted_s, adjusted_v


def find_dominant_vibrant_colors(image, num_colors=5):
    """提取图片中出现频率较高的前 N 种非黑非白非灰颜色,并调整到马卡龙色系。"""
    img = image.copy()
    img.thumbnail((100, 100))
    img = img.convert("RGB")
    pixels = list(img.getdata())
    filtered_pixels = [p for p in pixels if is_not_black_white_gray_near(p)]
    if not filtered_pixels:
        return []
    color_counter = Counter(filtered_pixels)
    dominant_colors = color_counter.most_common(num_colors * 3)

    macaron_colors = []
    seen_hues = set()
    for color, count in dominant_colors:
        h, s, v = rgb_to_hsv(color)
        adjusted_s, adjusted_v = adjust_to_macaron(h, s, v)
        adjusted_rgb = hsv_to_rgb(h, adjusted_s, adjusted_v)

        hue_degree = int(h * 360)
        is_similar_hue = any(abs(hue_degree - seen) < 15 for seen in seen_hues)
        if not is_similar_hue and adjusted_rgb not in macaron_colors:
            macaron_colors.append(adjusted_rgb)
            seen_hues.add(hue_degree)
            if len(macaron_colors) >= num_colors:
                break
    return macaron_colors


def darken_color(color, factor=0.7):
    r, g, b = color
    return (int(r * factor), int(g * factor), int(b * factor))


def get_poster_primary_color(image_path):
    """分析图片并返回出现最多的主色调 (RGBA)。"""
    try:
        img = Image.open(image_path).resize((100, 150), Image.LANCZOS).convert("RGBA")
        pixels = [p for p in list(img.getdata()) if p[3] > 200 and 30 < (p[0] + p[1] + p[2]) / 3 < 220]
        if not pixels:
            return (150, 100, 50, 255)
        return Counter(pixels).most_common(1)[0][0]
    except Exception:
        return (150, 100, 50, 255)


# ========== 背景合成 ==========

def add_film_grain(image, intensity=0.05):
    """为图像添加胶片颗粒效果。"""
    mode = image.mode
    img_array = np.array(image, dtype=np.float32)

    if mode == "RGBA":
        channels = img_array.shape[2]
        for i in range(min(3, channels)):
            channel = img_array[:, :, i]
            noise = np.random.normal(0, 255 * intensity, channel.shape)
            img_array[:, :, i] = np.clip(channel + noise, 0, 255)
    else:
        noise = np.random.normal(0, 255 * intensity, img_array.shape)
        img_array = np.clip(img_array + noise, 0, 255)

    return Image.fromarray(img_array.astype(np.uint8), mode)


def create_gradient_background(width, height, color):
    """基于主题色创建从左(深)到右(浅)的渐变背景。"""
    if not color:
        color = (random.randint(50, 200), random.randint(50, 200), random.randint(50, 200), 255)
    elif len(color) == 3:
        color = color + (255,)

    r, g, b = max(0, int(color[0] * 0.65)), max(0, int(color[1] * 0.65)), max(0, int(color[2] * 0.65))
    left_color = (r, g, b, color[3])

    r2, g2, b2 = (min(255, max(r + 80, min(230, int(color[0] * 1.9)))),
                  min(255, max(g + 80, min(230, int(color[1] * 1.9)))),
                  min(255, max(b + 80, min(230, int(color[2] * 1.9)))))
    right_color = (r2, g2, b2, color[3])

    left_img = Image.new("RGBA", (width, height), left_color)
    right_img = Image.new("RGBA", (width, height), right_color)

    mask = Image.new("L", (width, height), 0)
    mask_data = [int(255.0 * (x / width) ** 0.7) for y in range(height) for x in range(width)]
    mask.putdata(mask_data)

    return Image.composite(right_img, left_img, mask)


def create_blur_background(image_path, width, height, color, blur_size, color_ratio):
    """首图模糊化并与主题色混合,叠加左深右浅的提亮渐变与胶片颗粒。"""
    original_img = Image.open(image_path).convert("RGBA")
    bg_img = ImageOps.fit(original_img, (width, height), method=Image.LANCZOS)
    bg_img = bg_img.filter(ImageFilter.GaussianBlur(radius=int(blur_size)))

    actual_color = darken_color(color, 0.85)
    bg_color_array = np.array([[actual_color[:3]]], dtype=float)
    bg_img_array = np.array(bg_img, dtype=float)[:, :, :3]

    blended_bg = bg_img_array * (1 - float(color_ratio)) + bg_color_array * float(color_ratio)
    blended_bg = np.clip(blended_bg, 0, 255).astype(np.uint8)

    blended_bg_img = Image.fromarray(blended_bg, "RGB").convert("RGBA")

    gradient_mask = Image.new("L", (width, height), 0)
    draw_mask = ImageDraw.Draw(gradient_mask)
    for x in range(width):
        alpha = int((x / width) * 255 * 0.6)
        draw_mask.line([(x, 0), (x, height)], fill=alpha)

    lighten_layer = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    lighten_layer.putalpha(gradient_mask)

    return add_film_grain(Image.alpha_composite(blended_bg_img, lighten_layer), 0.03)


def prepare_background(bg_path, size, blur_radius=0):
    """准备背景图(6+1/9+1 样式):等比铺满 + 轻度压暗,保证海报可见性。"""
    bg = Image.open(bg_path).convert("RGB")
    bg = ImageOps.fit(bg, size, method=Image.LANCZOS, centering=(0.5, 0.5))
    if blur_radius > 0:
        bg = bg.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    overlay = Image.new("RGBA", size, (0, 0, 0, 60))
    bg_rgba = bg.convert("RGBA")
    return Image.alpha_composite(bg_rgba, overlay)


# ========== 卡片效果 ==========

def add_rounded_corners(img, radius=30):
    """给图片添加圆角,通过超采样消除锯齿。"""
    factor = 2
    width, height = img.size
    enlarged_img = img.resize((width * factor, height * factor), Image.Resampling.LANCZOS).convert("RGBA")
    mask = Image.new("L", (width * factor, height * factor), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (width * factor, height * factor)], radius=radius * factor, fill=255)
    background = Image.new("RGBA", (width * factor, height * factor), (255, 255, 255, 0))
    high_res_result = Image.composite(enlarged_img, background, mask)
    return high_res_result.resize((width, height), Image.Resampling.LANCZOS)


def add_shadow_simple(img, offset=(5, 5), shadow_color=(0, 0, 0, 100), blur_radius=3):
    """给图片添加右侧和底部阴影。"""
    shadow_width = img.width + offset[0] + blur_radius * 2
    shadow_height = img.height + offset[1] + blur_radius * 2
    shadow = Image.new("RGBA", (shadow_width, shadow_height), (0, 0, 0, 0))
    shadow_layer = Image.new("RGBA", img.size, shadow_color)
    shadow.paste(shadow_layer, (blur_radius + offset[0], blur_radius + offset[1]))
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur_radius))
    result = Image.new("RGBA", shadow.size, (0, 0, 0, 0))
    result.paste(img, (blur_radius, blur_radius), img if img.mode == "RGBA" else None)
    return Image.alpha_composite(shadow, result)


# ========== 文字绘制 ==========

def draw_text(image, text, position, font_path, font_size,
              fill_color=(255, 255, 255, 255), shadow=False,
              shadow_color=None, shadow_offset=10, shadow_alpha=75):
    """在图像上绘制单行文字,可选多层偏移 + 高斯模糊的柔和阴影。"""
    if not text:
        return image
    img_copy = image.convert("RGBA")
    text_layer = Image.new("RGBA", img_copy.size, (255, 255, 255, 0))
    shadow_layer = Image.new("RGBA", img_copy.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(text_layer)
    shadow_draw = ImageDraw.Draw(shadow_layer)
    font = ImageFont.truetype(font_path, font_size)

    if shadow:
        if shadow_color is None:
            shadow_color = (50, 50, 50, shadow_alpha)
        else:
            shadow_color = shadow_color[:3] + (shadow_alpha,)
        for offset in range(3, shadow_offset + 1, 2):
            shadow_draw.text((position[0] + offset, position[1] + offset), text, font=font, fill=shadow_color)

    draw.text(position, text, font=font, fill=fill_color)
    blurred_shadow = shadow_layer.filter(ImageFilter.GaussianBlur(radius=shadow_offset))
    combined = Image.alpha_composite(img_copy, blurred_shadow)
    return Image.alpha_composite(combined, text_layer)


def draw_multiline_text(image, text, position, font_path, font_size, line_spacing=10,
                        fill_color=(255, 255, 255, 255), shadow=False,
                        shadow_color=None, shadow_offset=4, shadow_alpha=100):
    """在图像上按空格分行绘制多行文字,返回 (图像, 行数)。"""
    if not text:
        return image, 0
    img_copy = image.convert("RGBA")
    text_layer = Image.new("RGBA", img_copy.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(text_layer)
    font = ImageFont.truetype(font_path, font_size)
    lines = text.split(" ")

    if shadow:
        if shadow_color is None:
            shadow_color = (50, 50, 50, shadow_alpha)
        else:
            shadow_color = shadow_color[:3] + (shadow_alpha,)

    x, y = position
    for i, line in enumerate(lines):
        current_y = y + i * (font_size + line_spacing)
        if shadow:
            for offset in range(3, shadow_offset + 1, 2):
                draw.text((x + offset, current_y + offset), line, font=font, fill=shadow_color)
        draw.text((x, current_y), line, font=font, fill=fill_color)

    return Image.alpha_composite(img_copy, text_layer), len(lines)


# ========== 滚动列动画 ==========

def create_extended_column(column_posters, cell_width, cell_height, margin, corner_radius):
    """构建单列海报条:内容纵向重复两份,供帧合成时做无缝循环滚动。

    返回 (扩展列图像, 单份高度)。
    """
    rows = len(column_posters)
    single_column_height = rows * cell_height + (rows - 1) * margin

    shadow_extra_width = 20 + 20 * 2
    shadow_extra_height = 20 + 20 * 2

    extended_height = single_column_height * 2 + margin
    column_image = Image.new(
        "RGBA",
        (cell_width + shadow_extra_width, extended_height + shadow_extra_height),
        (0, 0, 0, 0),
    )

    for copy_index in range(2):
        base_y = copy_index * (single_column_height + margin)
        for row_index, poster_path in enumerate(column_posters):
            poster = None
            try:
                poster = Image.open(poster_path)
                resized_poster = ImageOps.fit(poster, (cell_width, cell_height), Image.LANCZOS)
                if corner_radius > 0:
                    resized_poster = add_rounded_corners(resized_poster, corner_radius).convert("RGBA")

                resized_poster_with_shadow = add_shadow_simple(resized_poster, (20, 20), (0, 0, 0, 255), 20)
                y_position = base_y + row_index * (cell_height + margin)
                column_image.paste(resized_poster_with_shadow, (0, y_position), resized_poster_with_shadow)
            except Exception as e:
                logger.warning(f"处理素材 {poster_path} 时出错: {e}")
                continue
            finally:
                if poster is not None:
                    poster.close()
    return column_image, single_column_height


def generate_animation_frame(gradient_bg, extended_columns, column_heights, frame_index, total_frames, cfg, scale_factor=1.0):
    """渲染单帧:各列按进度上下交错滚动,中列向下、两侧向上,形成循环动画。"""
    result = gradient_bg.copy()
    margin = int(cfg["MARGIN"] * scale_factor)
    cell_width = int(cfg["CELL_WIDTH"] * scale_factor)
    rotation_angle = cfg["ROTATION_ANGLE"]

    move_distance = column_heights[0] + margin
    progress = frame_index / total_frames
    base_offset = int(progress * move_distance)

    for col_index, (extended_column, single_height) in enumerate(zip(extended_columns, column_heights)):
        if col_index >= cfg["COLS"]:
            break

        offset = base_offset if col_index == 1 else -base_offset
        shadow_extra = 20 + 20 * 2

        crop_y_start = (single_height // 2 + offset) % (single_height + margin)
        cropped_column = extended_column.crop((0, crop_y_start, extended_column.width, crop_y_start + single_height + shadow_extra))

        diag = int(math.sqrt(cropped_column.width ** 2 + cropped_column.height ** 2) * 1.5)
        rot_canvas = Image.new("RGBA", (diag, diag), (0, 0, 0, 0))
        rot_canvas.paste(cropped_column, ((diag - cropped_column.width) // 2, (diag - cropped_column.height) // 2), cropped_column)
        rotated_column = rot_canvas.rotate(rotation_angle, Image.BICUBIC, expand=True)

        col_x = int(cfg["START_X"] * scale_factor) + col_index * int(cfg["COLUMN_SPACING"] * scale_factor)
        col_center_y = int(cfg["START_Y"] * scale_factor) + single_height // 2
        col_center_x = col_x

        if col_index == 1:
            col_center_x += int(380 * scale_factor)
        elif col_index == 2:
            col_center_y -= int(155 * scale_factor)
            col_center_x += int(760 * scale_factor)

        fx = col_center_x - rotated_column.width // 2 + cell_width // 2
        fy = col_center_y - rotated_column.height // 2
        result.paste(rotated_column, (int(fx), int(fy)), rotated_column)

    return result


# ========== 动图编码 ==========

def save_animation(frames, animation_format, duration):
    """将帧序列编码为 GIF/WebP/APNG 并返回 base64 字符串。

    GIF 使用共享调色板 + 抖动以控制体积;WebP 转 RGB 提升压缩效率。
    """
    buffer = BytesIO()
    animation_format = animation_format.lower()
    if animation_format == "webp":
        rgb_frames = [f.convert("RGB") for f in frames]
        rgb_frames[0].save(buffer, format="WEBP", save_all=True, append_images=rgb_frames[1:],
                           duration=duration, loop=0, quality=80, method=4)
        for f in rgb_frames:
            f.close()
    elif animation_format == "png":
        frames[0].save(buffer, format="PNG", save_all=True, append_images=frames[1:], duration=duration, loop=0)
    else:
        gif_frames = []
        palette = frames[0].convert("RGB").quantize(colors=128, method=Image.Quantize.MEDIANCUT)
        for f in frames:
            gif_frames.append(f.convert("RGB").quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG))
        gif_frames[0].save(buffer, format="GIF", save_all=True, append_images=gif_frames[1:],
                           duration=duration, loop=0, optimize=True)
        for f in gif_frames:
            f.close()
        palette.close()
    return base64.b64encode(buffer.getvalue()).decode("utf-8")
