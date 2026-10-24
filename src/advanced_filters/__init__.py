"""高级筛选器模块。

translator.py:把高级筛选规则翻译为 Emby 原生 API 参数 + 需代理端后筛选的规则,
被代理核心 (handler_items / handler_latest) 与管理后台共同使用,是纯函数模块。

规则本身存储在 config.json (AppConfig.advanced_filters),
规则的增删改由管理后台 /api/advanced-filters 提供。

旧导入路径 proxy_handlers._filter_translator 仍可用(内部转发到本模块)。
"""
from .translator import translate_rules, OPERATOR_MAP, FIELD_MAP  # noqa: F401
