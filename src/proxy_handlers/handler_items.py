# src/proxy_handlers/handler_items.py (高性能重构版)

import logging
import json
from fastapi import Request, Response
from aiohttp import ClientSession
from models import AppConfig, AdvancedFilter
from typing import List, Any, Dict

from . import handler_merger, handler_views
# 导入我们新的翻译器和旧的后筛选逻辑
from ._filter_translator import translate_rules
from .handler_rss import RssHandler
from proxy_cache import vlib_items_cache
import db_manager
logger = logging.getLogger(__name__)

# --- 后筛选逻辑 (保留用于处理无法翻译的规则) ---
def _get_nested_value(item: Dict[str, Any], field_path: str) -> Any:
    keys = field_path.split('.')
    value = item
    for key in keys:
        if isinstance(value, dict): value = value.get(key)
        else: return None
    return value

def _check_condition(item_value: Any, operator: str, rule_value: str) -> bool:
    if operator not in ["is_empty", "is_not_empty"]:
        if item_value is None: return False
        if isinstance(item_value, list):
            if operator == "contains": return rule_value in item_value
            if operator == "not_contains": return rule_value not in item_value
            return False
        if operator in ["greater_than", "less_than"]:
            try:
                if operator == "greater_than": return float(item_value) > float(rule_value)
                if operator == "less_than": return float(item_value) < float(rule_value)
            except (ValueError, TypeError): return False
        item_value_str = str(item_value).lower()
        rule_value_str = str(rule_value).lower()
        if operator == "equals": return item_value_str == rule_value_str
        if operator == "not_equals": return item_value_str != rule_value_str
        if operator == "contains": return rule_value_str in item_value_str
        if operator == "not_contains": return rule_value_str not in item_value_str
    if operator == "is_empty": return item_value is None or item_value == '' or item_value == []
    if operator == "is_not_empty": return item_value is not None and item_value != '' and item_value != []
    return False

def _apply_post_filter(items: List[Dict[str, Any]], post_filter_rules: List[Dict]) -> List[Dict[str, Any]]:
    if not post_filter_rules: return items
    logger.info(f"在 {len(items)} 个项目上应用 {len(post_filter_rules)} 条后筛选规则。")
    filtered_items = []
    for item in items:
        if all(_check_condition(_get_nested_value(item, rule.field), rule.operator, rule.value) for rule in post_filter_rules):
            filtered_items.append(item)
    return filtered_items


async def handle_virtual_library_items(
    request: Request,
    full_path: str,
    method: str,
    real_emby_url: str,
    session: ClientSession,
    config: AppConfig
) -> Response | None:
    if "/Items/Prefixes" in full_path or "/Items/Counts" in full_path or "/Items/Latest" in full_path:
        return None

    params = request.query_params
    found_vlib = None
    
    parent_id_from_param = params.get("ParentId")
    if parent_id_from_param:
        found_vlib = next((vlib for vlib in config.virtual_libraries if vlib.id == parent_id_from_param), None)

    if not found_vlib and method == "GET" and 'Items' in full_path:
        path_parts = full_path.split('/');
        try:
            items_index = path_parts.index('Items')
            if items_index + 1 < len(path_parts):
                potential_path_vlib_id = path_parts[items_index + 1]
                found_vlib = next((vlib for vlib in config.virtual_libraries if vlib.id == potential_path_vlib_id), None)
        except ValueError: pass
    
    if not found_vlib:
        # 兼容Go版本的后备方案：处理非标准客户端（如网易爆米花）的请求
        # 这些客户端通过 /Users/xxx/Items 获取根视图，且请求参数中不含任何 'Id'
        has_id_param = any(key.lower().endswith('id') for key in params.keys())
        
        if not has_id_param:
            logger.info("检测到非标准客户端请求 (无 'Id' 后缀参数)，作为后备方案返回媒体库视图。")
            # 调用 handler_views 中的逻辑来返回一个伪造的根视图
            return await handler_views.handle_view_injection(request, full_path, method, real_emby_url, session, config)
        
        # 如果有 'Id' 参数但不是虚拟库，则为正常请求，放行
        return None

    logger.info(f"拦截到虚拟库 '{found_vlib.name}'，开始高性能筛选流程。")
    
    user_id = params.get("UserId")
    if not user_id:
        # (保持原有的UserId查找逻辑)
        path_parts_for_user = full_path.split('/')
        if 'Users' in path_parts_for_user:
            try:
                user_id_index = path_parts_for_user.index('Users') + 1
                if user_id_index < len(path_parts_for_user): user_id = path_parts_for_user[user_id_index]
            except (ValueError, IndexError): pass
    if not user_id: return Response(content="UserId not found", status_code=400)

    # --- 开始构建请求 ---
    new_params = {}
    
    # 【【【核心优化点 1】】】: 默认继承客户端的所有安全参数，包括分页和排序！
    client_start_index = params.get("StartIndex", "0")
    client_limit = params.get("Limit", "50")
    safe_params_to_inherit = [
        "SortBy", "SortOrder", "Fields", "EnableImageTypes", "ImageTypeLimit", 
        "EnableTotalRecordCount", "X-Emby-Token", "StartIndex", "Limit"
    ]
    for key in safe_params_to_inherit:
        if key in params: new_params[key] = params[key]

    required_fields = ["ProviderIds", "Genres", "Tags", "Studios", "People", "OfficialRatings", "CommunityRating", "ProductionYear", "VideoRange", "Container"]
    if "Fields" in new_params:
        existing_fields = set(new_params["Fields"].split(','))
        missing_fields = [f for f in required_fields if f not in existing_fields]
        if missing_fields: new_params["Fields"] += "," + ",".join(missing_fields)
    else: new_params["Fields"] = ",".join(required_fields)

    new_params["Recursive"] = "true"
    new_params["IncludeItemTypes"] = "Movie,Series,Video"

    resource_map = {"collection": "CollectionIds", "tag": "TagIds", "person": "PersonIds", "genre": "GenreIds", "studio": "StudioIds"}
    if found_vlib.resource_type in resource_map:
        new_params[resource_map[found_vlib.resource_type]] = found_vlib.resource_id
    # --- 【【【 新增：借鉴“缺失剧集”逻辑，重构 RSS 库的统一处理方案 】】】 ---
    elif found_vlib.resource_type == "rsshub":
        # 终极修复：将所有 RSS 逻辑委托给 RssHandler，并传入必要的上下文
        rss_handler = RssHandler()
        # 关键：传入 request.query_params 以便 RssHandler 可以继承 Fields 等参数
        # 关键：传入 user_id 和 session 以便 RssHandler 可以自己发起请求
        response_data = await rss_handler.handle(
            request_path=full_path, 
            vlib_id=found_vlib.id,
            request_params=request.query_params,
            user_id=user_id,
            session=session,
            real_emby_url=real_emby_url,
            request_headers=request.headers
        )
        
        # 手动分页
        start_idx = int(params.get("StartIndex", 0))
        limit_str = params.get("Limit")
        final_items = response_data.get("Items", [])
        
        if limit_str:
            try:
                limit = int(limit_str)
                paginated_items = final_items[start_idx : start_idx + limit]
            except (ValueError, TypeError):
                paginated_items = final_items[start_idx:]
        else:
            paginated_items = final_items[start_idx:]
            
        final_response = {"Items": paginated_items, "TotalRecordCount": len(final_items)}
        return Response(content=json.dumps(final_response).encode('utf-8'), media_type="application/json")
    # --- 【【【 RSS 逻辑结束 】】】 ---

    # 【【【核心优化点 2】】】: 应用高级筛选器翻译
    post_filter_rules = []
    if found_vlib.advanced_filter_id:
        adv_filter = next((f for f in config.advanced_filters if f.id == found_vlib.advanced_filter_id), None)
        if adv_filter:
            logger.info(f"正在为高级筛选器 '{adv_filter.name}' 翻译规则...")
            emby_native_params, post_filter_rules = translate_rules(adv_filter.rules)
            new_params.update(emby_native_params)
            if post_filter_rules: logger.info(f"有 {len(post_filter_rules)} 条规则需要在代理端后筛选。")
        else:
            logger.warning(f"虚拟库配置了高级筛选器ID '{found_vlib.advanced_filter_id}'，但未找到。")

    # 修复：如果 IsMovie 为 true，则强制 IncludeItemTypes 为 Movie
    if new_params.get("IsMovie") == "true":
        new_params["IncludeItemTypes"] = "Movie"
        logger.info("检测到 IsMovie: 'true'，强制设置 IncludeItemTypes 为 'Movie'。")
    elif new_params.get("IsSeries") == "true":
        new_params["IncludeItemTypes"] = "Series"
        logger.info("检测到 IsSeries: 'true'，强制设置 IncludeItemTypes 为 'Series'。")

    # 【【【核心优化点 3】】】: 处理合并的特殊情况
    # 如果启用了TMDB合并，我们需要获取一个更大的数据集来进行有效的合并，然后再在代理端进行分页。
    # 这是一种混合模式，仍然远比获取所有项目要高效。
    is_tmdb_merge_enabled = found_vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id

    target_emby_api_path = f"Users/{user_id}/Items"
    search_url = f"{real_emby_url}/emby/{target_emby_api_path}"
    
    headers_to_forward = {
        k: v for k, v in request.headers.items() 
        if k.lower() in [
            'accept', 'accept-language', 'user-agent',
            'x-emby-authorization', 'x-emby-client', 'x-emby-device-name',
            'x-emby-device-id', 'x-emby-client-version', 'x-emby-language',
            'x-emby-token'
        ]
    }
    
    logger.debug(f"向真实 Emby 发起优化后的最终请求: URL={search_url}, Params={new_params}")

    # 如果不启用TMDB合并，或者有无法翻译的后筛选规则，则走常规分页逻辑
    if not is_tmdb_merge_enabled or post_filter_rules:
        if is_tmdb_merge_enabled and post_filter_rules:
            logger.warning("TMDB合并已启用，但存在无法翻译的后筛选规则，合并将在当前页进行，可能不完整。")

        async with session.request(method, search_url, params=new_params, headers=headers_to_forward) as resp:
            if resp.status != 200:
                content = await resp.read()
                return Response(content=content, status_code=resp.status, headers=resp.headers)
            
            content = await resp.read()
            response_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ('transfer-encoding', 'connection', 'content-encoding', 'content-length')}
            
            if "application/json" in resp.headers.get("Content-Type", ""):
                try:
                    data = json.loads(content)
                    items_list = data.get("Items", [])
                    
                    if post_filter_rules:
                        items_list = _apply_post_filter(items_list, post_filter_rules)
                    
                    if is_tmdb_merge_enabled:
                        logger.info("正在对当前页的数据集执行TMDB合并...")
                        items_list = await handler_merger.merge_items_by_tmdb(items_list)
                    
                    data["Items"] = items_list
                    logger.info(f"原生筛选/合并完成。Emby返回总数: {data.get('TotalRecordCount')}, 当前页项目数: {len(items_list)}")
                    
                    final_items_to_return = data.get("Items", [])
                    if final_items_to_return:
                        vlib_items_cache[found_vlib.id] = final_items_to_return
                        db_manager.save_vlib_items_cache(found_vlib.id, final_items_to_return)
                        logger.info(f"✅ 已为虚拟库 '{found_vlib.name}' 缓存 {len(final_items_to_return)} 个项目以供封面生成使用。")
                    
                    content = json.dumps(data).encode('utf-8')
                except (json.JSONDecodeError, Exception) as e:
                    logger.error(f"处理响应时发生错误: {e}")

            return Response(content=content, status_code=resp.status, headers=response_headers)

    # --- TMDB合并的全量获取逻辑 ---
    else:
        logger.info("TMDB合并已启用，开始获取全量数据...")
        all_items = []
        start_index = 0
        limit = 200  # 每次请求200个
        
        # 移除客户端的分页参数，因为我们要自己控制
        new_params.pop("StartIndex", None)
        new_params.pop("Limit", None)
        
        while True:
            fetch_params = new_params.copy()
            fetch_params["StartIndex"] = str(start_index)
            fetch_params["Limit"] = str(limit)
            
            logger.debug(f"正在获取批次: StartIndex={start_index}, Limit={limit}")
            async with session.request(method, search_url, params=fetch_params, headers=headers_to_forward) as resp:
                if resp.status != 200:
                    logger.error(f"获取批次失败，状态码: {resp.status}")
                    # 返回错误或一个空的成功响应
                    return Response(content=json.dumps({"Items": [], "TotalRecordCount": 0}), status_code=200, media_type="application/json")

                batch_data = await resp.json()
                batch_items = batch_data.get("Items", [])
                
                if not batch_items:
                    logger.info("已获取所有数据。")
                    break
                
                all_items.extend(batch_items)
                start_index += len(batch_items)
                
                # 如果返回的项目数小于请求的limit，说明是最后一页
                if len(batch_items) < limit:
                    logger.info("已到达最后一页。")
                    break
        
        logger.info(f"全量数据获取完成，总共 {len(all_items)} 个项目。")

        # 1. 应用TMDB合并
        logger.info("正在对获取到的全量数据集执行TMDB合并...")
        merged_items = await handler_merger.merge_items_by_tmdb(all_items)
        
        # 2. 对合并后的结果进行手动分页
        total_record_count = len(merged_items)
        start_idx = int(client_start_index)
        limit_count = int(client_limit)
        paginated_items = merged_items[start_idx : start_idx + limit_count]
        
        # 3. 构建最终的响应
        final_data = {
            "Items": paginated_items,
            "TotalRecordCount": total_record_count,
            "StartIndex": start_idx
        }
        logger.info(f"合并后手动分页完成。总数: {total_record_count}, 返回页面项目数: {len(paginated_items)}")

        if paginated_items:
            vlib_items_cache[found_vlib.id] = paginated_items
            db_manager.save_vlib_items_cache(found_vlib.id, paginated_items)
            logger.info(f"✅ 已为虚拟库 '{found_vlib.name}' 缓存 {len(paginated_items)} 个项目以供封面生成使用。")

        content = json.dumps(final_data).encode('utf-8')
        # 伪造一个成功的响应头
        response_headers = {
            'Content-Type': 'application/json; charset=utf-8',
            'Content-Length': str(len(content))
        }
        return Response(content=content, status_code=200, headers=response_headers)

    return None
