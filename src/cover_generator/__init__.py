"""封面样式插件包。

每个 style_*.py 是一个独立的封面样式插件,契约:
  - 提供 create_<模块文件名>(title: (中文, 英文), font_path: (中文字体, 英文字体), ...) -> base64 编码图片
  - 可选声明 MATERIAL_MODE = "multi" | "single":
      "multi"  额外接收 library_dir=<素材目录>,自行取多图拼接
      "single" 额外接收 image_path=<单张主图路径>
    未声明时由 services/cover_service.py 的内置注册表按旧规则兼容
    (style_multi_1 -> multi, style_single_1/2 -> single)。

新增样式无需改动任何现有代码:在本目录放置新文件后,
管理后台 /api/generate-cover 传对应的 style_name 即可调用。
"""
