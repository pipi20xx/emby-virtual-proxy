# src/advanced_filters/translator.py —— 高级筛选规则 -> Emby 原生参数 翻译器
"""将高级筛选规则翻译成 Emby API 原生查询参数。

能直接映射到 Emby 参数的规则会即时返回给调用方拼进请求;
无法映射的规则原样塞进 post_filter_rules,由代理端拉取数据后自行过滤。
本模块是纯函数,不发起任何网络请求。
"""

import logging
from typing import List, Dict, Any, Tuple
from datetime import datetime, timedelta

from models import AdvancedFilterRule

logger = logging.getLogger(__name__)

# 映射我们的操作符到 Emby API 的过滤器名称后缀
# 例如： 'equals' -> 'Is', 'contains' -> 'Contains' (虽然不常用), 'greater_than' -> 'GreaterThan'
# 注意：Emby 对参数名的大小写敏感。
OPERATOR_MAP = {
    "equals": "Is",
    "not_equals": "IsNot",
    "contains": "Contains",
    "greater_than": "GreaterThan",
    "less_than": "LessThan",
}

# 映射我们的字段到 Emby API 的查询参数
# Key 是我们在前端定义的 field, Value 是对应的 Emby API 参数 (或元组)
# 以下映射均经 Emby 4.10.2 实测验证;实测被忽略的参数不在此映射,一律走代理端后筛选:
#   - MaxCommunityRating / MinCriticRating / MaxCriticRating 被忽略
#   - IsMovie / IsSeries(参数与 Filters 形式均被忽略)
FIELD_MAP = {
    "CommunityRating": ("MinCommunityRating", None),  # 仅 greater_than 可原生(见下方分支)
    "OfficialRating": "OfficialRatings",
    "ProductionYear": ("MinPremiereDate", "MaxPremiereDate"),  # 将年份转换为日期
    "PremiereDate": ("MinPremiereDate", "MaxPremiereDate"),    # 首播日期
    "DateCreated": ("MinDateCreated", "MaxDateCreated"),       # 添加日期
    "Genres": "Genres",
    "Tags": "Tags",
    "Studios": "Studios",
    # VideoRange(画质)不映射:Emby 查询参数 VideoTypes 实为介质类型(videofile/dvd/bluray/iso),
    # 与 HDR/SDR 画质无关,原生翻译会得到错误结果,该字段一律走代理端后筛选
    "Container": "Containers",
    "NameStartsWith": "NameStartsWith",
    "SeriesStatus": "SeriesStatus",
    # 播放状态:实测 IsPlayed 参数有效;IsUnplayed 参数与 Filters 形式均被忽略,走后筛选
    "IsPlayed": "IsPlayed",
    "HasSubtitles": "HasSubtitles",
    "HasOfficialRating": "HasOfficialRating",
    # 存在性检查(实测 true/false 均有效)
    "ProviderIds.Tmdb": "HasTmdbId",
    "ProviderIds.Imdb": "HasImdbId",
}


def translate_rules(rules: List[AdvancedFilterRule]) -> Tuple[Dict[str, Any], List[AdvancedFilterRule]]:
    """
    将高级筛选规则列表翻译成 Emby 原生 API 参数和需要后筛选的规则。

    Args:
        rules: 从前端传来的高级筛选规则列表。

    Returns:
        一个元组，包含:
        - emby_native_params: 一个可以直接用于 aiohttp 请求的字典。
        - post_filter_rules: 一个无法被翻译，需要代理端进行后筛选的规则列表。
    """
    emby_native_params = {}
    post_filter_rules = []

    for rule in rules:
        field = rule.field
        operator = rule.operator
        value = rule.value
        translated = False

        # 处理相对日期(例如"最近30天内"):执行时动态换算成绝对日期
        if rule.relative_days and field in ["PremiereDate", "DateCreated"]:
            target_date = datetime.utcnow() - timedelta(days=rule.relative_days)
            value = target_date.strftime('%Y-%m-%d')
            operator = "greater_than"

        if operator == "is_not_empty":
            if field in FIELD_MAP and isinstance(FIELD_MAP[field], str) and FIELD_MAP[field].startswith("Has"):
                emby_native_params[FIELD_MAP[field]] = "true"
                translated = True

        elif operator == "is_empty":
            if field in FIELD_MAP and isinstance(FIELD_MAP[field], str) and FIELD_MAP[field].startswith("Has"):
                emby_native_params[FIELD_MAP[field]] = "false"
                translated = True

        elif field in FIELD_MAP:
            param_template = FIELD_MAP[field]

            # 处理范围查询 (Min/Max);均经 Emby 4.10.2 实测验证
            if isinstance(param_template, tuple):
                min_param, max_param = param_template

                def _range_value(op: str) -> str:
                    if field == "ProductionYear":
                        return f"{value}-01-01T00:00:00.000Z" if op == "greater_than" else f"{value}-12-31T23:59:59.999Z"
                    if field in ("PremiereDate", "DateCreated"):
                        return f"{value}T00:00:00.000Z" if op == "greater_than" else f"{value}T23:59:59.999Z"
                    return str(value)

                if field == "CommunityRating":
                    # 实测 Emby 4.10 只认 MinCommunityRating,MaxCommunityRating 被忽略:
                    # 仅「大于」可原生,等于/小于一律走后筛选
                    if operator == "greater_than":
                        emby_native_params[min_param] = value
                        translated = True
                elif operator == "greater_than":
                    emby_native_params[min_param] = _range_value("greater_than")
                    translated = True
                elif operator == "less_than":
                    emby_native_params[max_param] = _range_value("less_than")
                    translated = True
                elif operator == "equals" and field in ("ProductionYear", "PremiereDate", "DateCreated"):
                    # 年份/日期的精确匹配:Min/Max 日期范围
                    emby_native_params[min_param] = _range_value("greater_than")
                    emby_native_params[max_param] = _range_value("less_than")
                    translated = True
                # 其余情况不翻译,走后筛选

            # 处理直接映射的字段
            # 仅 equals 翻译为原生参数;not_equals/contains/not_contains 等语义
            # 与原生参数(等值匹配)不一致,一律降级后筛选,避免排除类条件被反向翻译。
            elif isinstance(param_template, str):
                if operator == "equals":
                    emby_native_params[param_template] = value
                    translated = True

        if not translated:
            logger.info(f"高级筛选规则无法翻译，将进行后筛选: {rule.field} {rule.operator} {rule.value}")
            post_filter_rules.append(rule)
        else:
            logger.info(f"高级筛选规则已成功翻译为原生参数: {rule.field} -> {emby_native_params}")

    return emby_native_params, post_filter_rules
