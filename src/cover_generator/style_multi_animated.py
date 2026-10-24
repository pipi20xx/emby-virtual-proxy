"""样式:多图九宫格(动态) —— style_multi_animated

三列斜置海报循环滚动,叠加渐变/模糊背景与标题文字,输出 320x180 动图。
MATERIAL_MODE = "multi":接收 library_dir,取 9 张素材(不足自动循环补齐)。
"""

from PIL import Image, ImageDraw

from cover_generator._anim_utils import (
    ANIM_FRAME_COUNT,
    ANIM_FRAME_DURATION,
    ANIM_HEIGHT,
    ANIM_WIDTH,
    POSTER_GEN_CONFIG,
    collect_material_paths,
    create_blur_background,
    create_extended_column,
    create_gradient_background,
    darken_color,
    draw_multiline_text,
    draw_text,
    find_dominant_vibrant_colors,
    generate_animation_frame,
    get_poster_primary_color,
    save_animation,
)

import logging
logger = logging.getLogger(__name__)

MATERIAL_MODE = "multi"


def create_style_multi_animated(library_dir, title, font_path, animation_format="gif",
                                font_size=(1, 1), blur_size=50, color_ratio=0.8, is_blur=False):
    try:
        zh_font_path, en_font_path = font_path
        title_zh, title_en = title
        zh_font_size_ratio, en_font_size_ratio = font_size

        if float(zh_font_size_ratio) <= 0:
            zh_font_size_ratio = 1
        if float(en_font_size_ratio) <= 0:
            en_font_size_ratio = 1

        cfg = POSTER_GEN_CONFIG
        scale_factor = ANIM_WIDTH / cfg["CANVAS_WIDTH"]

        image_paths = collect_material_paths(library_dir)
        if not image_paths:
            logger.error("动态九宫格样式:素材目录中未找到可用图片。")
            return False

        first_img_path = image_paths[0]
        with Image.open(first_img_path) as first_img:
            vibrant = find_dominant_vibrant_colors(first_img.convert("RGB"))
        blur_color = vibrant[0] if vibrant else (237, 159, 77)

        if is_blur:
            bg_img = create_blur_background(first_img_path, ANIM_WIDTH, ANIM_HEIGHT, blur_color,
                                            int(blur_size * scale_factor), color_ratio)
        else:
            grad_color = get_poster_primary_color(first_img_path)
            bg_img = create_gradient_background(ANIM_WIDTH, ANIM_HEIGHT, grad_color)

        # 行主序转列主序,使素材按列分组
        target_count = cfg["ROWS"] * cfg["COLS"]
        poster_files = collect_material_paths(library_dir, needed=target_count)
        reordered = []
        for col in range(cfg["COLS"]):
            for row in range(cfg["ROWS"]):
                idx = row * cfg["COLS"] + col
                if idx < len(poster_files):
                    reordered.append(poster_files[idx])
        grouped = [reordered[i:i + cfg["ROWS"]] for i in range(0, len(reordered), cfg["ROWS"])]

        cell_width = int(cfg["CELL_WIDTH"] * scale_factor)
        cell_height = int(cfg["CELL_HEIGHT"] * scale_factor)
        margin = int(cfg["MARGIN"] * scale_factor)
        corner_radius = int(cfg["CORNER_RADIUS"] * scale_factor)

        extended_columns = []
        column_heights = []
        for col_posters in grouped:
            ext_col, h = create_extended_column(col_posters, cell_width, cell_height, margin, corner_radius)
            extended_columns.append(ext_col)
            column_heights.append(h)

        # 预先计算色块颜色,避免逐帧闪烁
        rand_color = (78, 78, 78, 255)
        shadow_c = darken_color(blur_color, 0.8)

        frames = []
        for i in range(ANIM_FRAME_COUNT):
            frame = generate_animation_frame(bg_img, extended_columns, column_heights, i, ANIM_FRAME_COUNT, cfg, scale_factor)

            frame = draw_text(
                frame, title_zh,
                (int(73 * scale_factor), int(427 * scale_factor)),
                zh_font_path, int(163 * float(zh_font_size_ratio) * scale_factor),
                shadow=True, shadow_color=shadow_c, shadow_offset=int(10 * scale_factor),
            )

            if title_en:
                base_size = 50 * float(en_font_size_ratio) * scale_factor
                line_spacing = base_size * 0.1
                wc = len(title_en.split())
                mc = max([len(w) for w in title_en.split()])
                f_size = base_size
                if mc > 10 or wc > 3:
                    f_size = max(base_size * (10 / max(mc, wc * 3)) ** 0.8, 30 * scale_factor)

                frame, line_count = draw_multiline_text(
                    frame, title_en,
                    (int(125 * scale_factor), int(625 * scale_factor)),
                    en_font_path, int(f_size),
                    line_spacing, shadow=True, shadow_color=shadow_c, shadow_offset=int(4 * scale_factor),
                )

                block_h = base_size + line_spacing + (line_count - 1) * (int(f_size) + line_spacing)
                draw = ImageDraw.Draw(frame)
                bx, by = int(84 * scale_factor), int(620 * scale_factor)
                bw = int(21 * scale_factor)
                draw.rectangle([(bx, by), (bx + bw, by + block_h)], fill=rand_color)

            frames.append(frame)

        result = save_animation(frames, animation_format, ANIM_FRAME_DURATION)

        # 显式释放所有帧和中间图片
        for f in frames:
            f.close()
        for ec in extended_columns:
            ec.close()
        bg_img.close()

        return result
    except Exception as e:
        logger.error(f"创建动态九宫格封面时出错: {e}", exc_info=True)
        return False
