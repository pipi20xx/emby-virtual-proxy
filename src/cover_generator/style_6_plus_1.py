"""样式:背景轮播(动态) —— style_6_plus_1

首张素材作为背景,6 张圆角海报在底部横向循环滚动,叠加标题文字。
输出 320x180 动图。
MATERIAL_MODE = "multi":接收 library_dir(第 1 张作背景,其余作海报)。
"""

from PIL import Image, ImageOps

from cover_generator._anim_utils import (
    ANIM_FRAME_COUNT,
    ANIM_FRAME_DURATION,
    ANIM_HEIGHT,
    ANIM_WIDTH,
    add_rounded_corners,
    add_shadow_simple,
    collect_material_paths,
    draw_text,
    prepare_background,
    save_animation,
)

import logging
logger = logging.getLogger(__name__)

MATERIAL_MODE = "multi"


def create_style_6_plus_1(library_dir, title, font_path, animation_format="gif",
                          font_size=(1, 1), blur_size=50, color_ratio=0.8, is_blur=False):
    try:
        zh_font_path, en_font_path = font_path
        title_zh, title_en = title
        zh_font_size_ratio, en_font_size_ratio = font_size

        if float(zh_font_size_ratio) <= 0:
            zh_font_size_ratio = 1
        if float(en_font_size_ratio) <= 0:
            en_font_size_ratio = 1

        scale = ANIM_WIDTH / 1920

        image_paths = collect_material_paths(library_dir)
        if len(image_paths) < 2:
            logger.error("背景轮播样式:至少需要 1 张背景与 1 张海报素材。")
            return False
        bg_path, poster_paths = image_paths[0], image_paths[1:]
        while len(poster_paths) < 6:
            poster_paths.extend(poster_paths)
        poster_paths = poster_paths[:6]

        # 背景清晰但轻度压暗;is_blur 时按比例高斯模糊
        bg_img = prepare_background(bg_path, (ANIM_WIDTH, ANIM_HEIGHT), int(blur_size * scale) if is_blur else 0)

        # 底部约显示 5.5 张卡片
        p_w, p_h = int(310 * scale), int(465 * scale)
        margin = int(40 * scale)
        posters = []
        for p_path in poster_paths:
            poster = None
            try:
                poster = Image.open(p_path)
                p = ImageOps.fit(poster.convert("RGBA"), (p_w, p_h), Image.LANCZOS)
                p = add_rounded_corners(p, int(25 * scale))
                p = add_shadow_simple(p, (int(8 * scale), int(8 * scale)), (0, 0, 0, 150), int(12 * scale))
                posters.append(p)
            finally:
                if poster is not None:
                    poster.close()

        frames = []
        strip_w = len(posters) * (p_w + margin)

        for i in range(ANIM_FRAME_COUNT):
            frame = bg_img.copy()
            offset = (i / ANIM_FRAME_COUNT) * strip_w

            # 首尾各多画一轮,保证任意偏移下画面连续
            for j in range(-1, 2):
                for idx, p in enumerate(posters):
                    x = int(j * strip_w + idx * (p_w + margin) - offset + ANIM_WIDTH // 2 - p_w // 2)
                    y = int(ANIM_HEIGHT - p_h - 80 * scale)
                    if -p_w < x < ANIM_WIDTH:
                        frame.paste(p, (x, y), p)

            if title_zh:
                text_x, text_y = int(80 * scale), int(220 * scale)
                zh_size = int(140 * float(zh_font_size_ratio) * scale)
                frame = draw_text(frame, title_zh, (text_x, text_y), zh_font_path, zh_size,
                                  shadow=True, shadow_alpha=120)

                if title_en:
                    en_size = int(60 * float(en_font_size_ratio) * scale)
                    frame = draw_text(frame, title_en.upper(), (text_x + int(5 * scale), text_y + zh_size + int(15 * scale)),
                                      en_font_path, en_size, shadow=True, shadow_alpha=100)

            frames.append(frame)

        result = save_animation(frames, animation_format, ANIM_FRAME_DURATION)

        for f in frames:
            f.close()
        bg_img.close()

        return result
    except Exception as e:
        logger.error(f"创建背景轮播封面时出错: {e}", exc_info=True)
        return False
