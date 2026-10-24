# src/proxy_handlers/_filter_translator.py (兼容 shim)
# 实现已模块化迁移至 advanced_filters/translator.py。
# 保留本文件是为了让既有导入路径 (handler_items / handler_latest) 无需改动。
from advanced_filters.translator import translate_rules  # noqa: F401
