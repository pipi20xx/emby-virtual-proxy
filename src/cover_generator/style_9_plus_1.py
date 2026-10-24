"""样式:背景斜播(动态) —— style_9_plus_1

首张素材作为背景,9 张海报组成三列斜置滚动(布局同九宫格,起点右移),
无文字,输出 320x180 动图。
MATERIAL_MODE = "multi":接收 library_dir(第 1 张作背景,其余作海报)。
"""

from cover_generator._anim_utils import (
    ANIM_FRAME_COUNT,
    ANIM_FRAME_DURATION,
    ANIM_HEIGHT,
    ANIM_WIDTH,
    POSTER_GEN_CONFIG,
    collect_material_paths,
    create_extended_column,
    generate_animation_frame,
    prepare_background,
    save_animation,
)

import logging
logger = logging.getLogger(__name__)

MATERIAL_MODE = "multi"


def create_style_9_plus_1(library_dir, title, font_path, animation_format="gif",
                          font_size=(1, 1), blur_size=50, color_ratio=0.8, is_blur=False):
    try:
        cfg = POSTER_GEN_CONFIG.copy()
        cfg.update({
            "START_X": 1205,
            "START_Y": -397,
        })

        scale = ANIM_WIDTH / cfg["CANVAS_WIDTH"]

        image_paths = collect_material_paths(library_dir)
        if len(image_paths) < 2:
            logger.error("背景斜播样式:至少需要 1 张背景与 1 张海报素材。")
            return False
        bg_path, poster_paths = image_paths[0], image_paths[1:]
        while len(poster_paths) < 9:
            poster_paths.extend(poster_paths)
        poster_paths = poster_paths[:9]

        bg_img = prepare_background(bg_path, (ANIM_WIDTH, ANIM_HEIGHT), int(blur_size * scale) if is_blur else 0)

        # 行主序转列主序,使素材按列分组
        reordered = []
        for col in range(cfg["COLS"]):
            for row in range(cfg["ROWS"]):
                reordered.append(poster_paths[(row * cfg["COLS"] + col) % len(poster_paths)])

        grouped = [reordered[i:i + cfg["ROWS"]] for i in range(0, len(reordered), cfg["ROWS"])]
        cell_w, cell_h = int(cfg["CELL_WIDTH"] * scale), int(cfg["CELL_HEIGHT"] * scale)
        margin, corner = int(cfg["MARGIN"] * scale), int(cfg["CORNER_RADIUS"] * scale)

        extended_columns, column_heights = [], []
        for col_posters in grouped:
            ext_col, h = create_extended_column(col_posters, cell_w, cell_h, margin, corner)
            extended_columns.append(ext_col)
            column_heights.append(h)

        frames = []
        for i in range(ANIM_FRAME_COUNT):
            frame = generate_animation_frame(bg_img, extended_columns, column_heights, i, ANIM_FRAME_COUNT, cfg, scale)
            frames.append(frame)

        result = save_animation(frames, animation_format, ANIM_FRAME_DURATION)

        for f in frames:
            f.close()
        for ec in extended_columns:
            ec.close()
        bg_img.close()

        return result
    except Exception as e:
        logger.error(f"创建背景斜播封面时出错: {e}", exc_info=True)
        return False
