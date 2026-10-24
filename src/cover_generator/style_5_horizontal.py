"""样式:横向轮播(动态) —— style_5_horizontal

纯黑背景,4 张海报(上 2/3)带倒影(下 1/3)横向循环滚动,无文字。
输出 320x180 动图。
MATERIAL_MODE = "multi":接收 library_dir,取 4 张素材。
"""

from PIL import Image, ImageFilter, ImageOps

from cover_generator._anim_utils import (
    ANIM_FRAME_COUNT,
    ANIM_FRAME_DURATION,
    ANIM_HEIGHT,
    ANIM_WIDTH,
    collect_material_paths,
    save_animation,
)

import logging
logger = logging.getLogger(__name__)

MATERIAL_MODE = "multi"


def create_style_5_horizontal(library_dir, title, font_path, animation_format="gif",
                              font_size=(1, 1), blur_size=50, color_ratio=0.8, is_blur=False):
    try:
        scale = ANIM_WIDTH / 1920

        poster_paths = collect_material_paths(library_dir, needed=4)
        if not poster_paths:
            logger.error("横向轮播样式:素材目录中未找到可用图片。")
            return False

        # 纯黑背景,无提亮无模糊
        bg_img = Image.new("RGBA", (ANIM_WIDTH, ANIM_HEIGHT), (0, 0, 0, 255))

        # 布局:上 2/3 海报 + 下 1/3 倒影
        main_h = int(ANIM_HEIGHT * 2 / 3)
        reflection_h = ANIM_HEIGHT - main_h
        p_w = int(main_h * 2 / 3)
        margin = int(25 * scale)

        posters = []
        for p_path in poster_paths:
            poster = None
            try:
                poster = Image.open(p_path)
                # 主海报(上 2/3)
                p_main = ImageOps.fit(poster.convert("RGBA"), (p_w, main_h), Image.LANCZOS)

                # 倒影(下 1/3)
                reflection = p_main.crop((0, main_h - reflection_h, p_w, main_h)).transpose(Image.FLIP_TOP_BOTTOM)
                reflection = reflection.filter(ImageFilter.GaussianBlur(radius=0.5 * scale))

                # 倒影渐变遮罩:高可见度、缓慢淡出
                mask = Image.new("L", (p_w, reflection_h), 0)
                for y in range(reflection_h):
                    ratio = 1 - (y / reflection_h)
                    alpha = int(200 * (ratio ** 0.7))
                    for x in range(p_w):
                        mask.putpixel((x, y), alpha)

                full_p = Image.new("RGBA", (p_w, ANIM_HEIGHT), (0, 0, 0, 0))
                full_p.paste(p_main, (0, 0))
                full_p.paste(reflection, (0, main_h), mask)
                posters.append(full_p)
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
                    y = 0
                    if -p_w < x < ANIM_WIDTH:
                        frame.paste(p, (x, y), p)
            frames.append(frame)

        result = save_animation(frames, animation_format, ANIM_FRAME_DURATION)

        for f in frames:
            f.close()
        bg_img.close()

        return result
    except Exception as e:
        logger.error(f"创建横向轮播封面时出错: {e}", exc_info=True)
        return False
