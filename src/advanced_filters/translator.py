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

# 依赖用户 UserData 的字段:共享元数据(管理员身份解析)无法评估,
# 本地全量模式下须推迟到拿到该用户自己的播放状态后再判定
USER_STATE_FIELDS = {"IsPlayed", "IsUnplayed"}

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
    "ProviderIds.Tvdb": "HasTvdbId",
    "Overview": "HasOverview",
}

# 等值取反的专用排除参数(实测有效):not_equals 用原生排除参数,而不是降级后筛选
EXCLUDE_PARAM_MAP = {
    "Tags": "ExcludeTags",
}


def _resolve_rule_value(rule: AdvancedFilterRule) -> Tuple[str, str]:
    """相对日期规则(如"最近30天内")在执行时动态换算成绝对日期 + 大于。

    每次执行都重新计算,而非保存时转换一次——保证"最近 30 天"每天都是真正的最近 30 天。
    """
    if rule.relative_days and rule.field in ["PremiereDate", "DateCreated"]:
        target_date = datetime.utcnow() - timedelta(days=rule.relative_days)
        return target_date.strftime('%Y-%m-%d'), "greater_than"
    return rule.value, rule.operator


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
        value, operator = _resolve_rule_value(rule)

        if operator == "is_not_empty":
            if field in FIELD_MAP and isinstance(FIELD_MAP[field], str) and FIELD_MAP[field].startswith("Has"):
                emby_native_params[FIELD_MAP[field]] = "true"
                translated = True

        elif operator == "is_empty":
            if field in FIELD_MAP and isinstance(FIELD_MAP[field], str) and FIELD_MAP[field].startswith("Has"):
                emby_native_params[FIELD_MAP[field]] = "false"
                translated = True

        elif field in EXCLUDE_PARAM_MAP and operator == "not_equals":
            # 有专用排除参数的字段:不等于也原生执行(实测 ExcludeTags=值 与 Tags 互补)
            emby_native_params[EXCLUDE_PARAM_MAP[field]] = value
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


def find_vlib_filter(config, vlib):
    """查找虚拟库绑定的高级筛选器;未绑定或 ID 失效返回 None。"""
    if not getattr(vlib, "advanced_filter_id", None):
        return None
    return next((f for f in config.advanced_filters if f.id == vlib.advanced_filter_id), None)


def is_local_mode(adv_filter) -> bool:
    return getattr(adv_filter, "execution_mode", "native") == "local"


def materialize_local_rules(rules: List[AdvancedFilterRule]) -> List[AdvancedFilterRule]:
    """本地全量模式:规则全部由代理端评估,相对日期在此刻物化为绝对日期。"""
    out: List[AdvancedFilterRule] = []
    for rule in rules:
        value, operator = _resolve_rule_value(rule)
        if value == rule.value and operator == rule.operator:
            out.append(rule)
        else:
            out.append(AdvancedFilterRule(field=rule.field, operator=operator, value=value, relative_days=rule.relative_days))
    return out


def resolve_for_serving(config, vlib) -> Tuple[Dict[str, Any], List[AdvancedFilterRule], List[AdvancedFilterRule], bool]:
    """预热/直出场景的模式分流,返回 (拉取参数, 元数据规则, 用户态规则, 匹配方式)。

    native:与历史行为一致——可翻译规则进拉取参数,其余全部作为后筛选规则(含用户态),一律 AND。
    local:裸拉全量(不带任何筛选参数),元数据规则在共享层全量评估;
          用户态规则(IsPlayed/IsUnplayed)依赖各用户自己的 UserData,
          由调用方在叠加用户状态后评估,不烙进共享元数据。
          匹配方式透传筛选器的 match_all(本地全量模式下 AND/OR 均可执行)。
    """
    adv_filter = find_vlib_filter(config, vlib)
    if adv_filter is None:
        return {}, [], [], True
    if is_local_mode(adv_filter):
        metadata_rules = [r for r in adv_filter.rules if r.field not in USER_STATE_FIELDS]
        user_rules = [r for r in adv_filter.rules if r.field in USER_STATE_FIELDS]
        return {}, materialize_local_rules(metadata_rules), materialize_local_rules(user_rules), adv_filter.match_all
    native_params, post_rules = translate_rules(adv_filter.rules)
    return native_params, post_rules, [], True


def resolve_for_user_query(config, vlib) -> Tuple[Dict[str, Any], List[AdvancedFilterRule], bool]:
    """以用户令牌实时查询的场景(latest / 首页分区 / 实时回退)的模式分流。

    响应自带该用户的 UserData,本地全量模式下全部规则(含用户态)都可本地评估。
    返回 (拉取参数, 本地评估规则, 匹配方式)。
    """
    adv_filter = find_vlib_filter(config, vlib)
    if adv_filter is None:
        return {}, [], True
    if is_local_mode(adv_filter):
        return {}, materialize_local_rules(adv_filter.rules), adv_filter.match_all
    native_params, post_rules = translate_rules(adv_filter.rules)
    return native_params, post_rules, True
