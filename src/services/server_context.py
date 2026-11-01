# src/services/server_context.py —— 多服聚合:属主解析、复合 ID 与入口请求路由(阶段 2/3)
"""多 Emby 聚合的两个核心机制(设计见 docs/multi-emby-aggregation.md):

1. 复合 ID 与属主解析:从服条目下发为 `{server_id}~{item_id}`(主服条目不加前缀);
   任何携带复合 ID 的客户端请求都能反查出属主服务器。
2. 入口请求路由:reverse_proxy 顶部调用 resolve_routed_request,把路径/查询参数/
   认证头整体改写为属主服务器的真实形态(真实 item id、属主服 user id、属主服
   后端 token),后续 handler 链完全无感。未命中复合 ID → 原样放行(主服,零改动)。

多服激活条件:注册表含从服(非主服条目)且至少一个虚拟库引用了从服 ——
否则所有函数均为无操作,行为与单服完全一致。
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode

import aiohttp
from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from emby_net import emby_proxy_from_config
from models import AppConfig, EmbyServerConfig
from services import credential_service

logger = logging.getLogger(__name__)

_COMPOSITE_SEP = "~"
_USER_RESOLVE_TTL = 1800

# 复合 ID 解析(用于生成 Token="..." 替换)
_AUTH_TOKEN_RE = re.compile(r'(Token=")([^"]*)(")')
_TOKEN_PARAM_KEYS = {"x-emby-token", "api_key"}
# 可能携带条目引用的查询参数(值里出现从服复合 ID 时,请求应路由到该服)。
# 典型如详情页"收录于合集"部件:GET /Users/{uid}/Items?IncludeItemTypes=BoxSet&ListItemIds=s2~1540
_QUERY_ID_PARAM_KEYS = {"listitemids", "ids", "parentid", "mediasourceid",
                        "seriesid", "seasonid", "albumid", "itemids"}

# {服务器 base url: (user_id, resolved_at)}
_user_cache: Dict[str, Tuple[str, float]] = {}


class RoutingError(Exception):
    """多服路由失败(属主服不可达 / 凭证或用户解析失败)。"""


# ---------- 注册表视图 ----------

def _registry(config) -> List[EmbyServerConfig]:
    return list(getattr(config, "emby_servers", None) or [])


def primary_server(config) -> Optional[EmbyServerConfig]:
    for server in _registry(config):
        if server.is_primary and server.id:
            return server
    return None


def secondary_servers(config) -> Dict[str, EmbyServerConfig]:
    """已注册的从服 {id: 条目};主服条目与空 id 不参与复合 ID。"""
    return {s.id: s for s in _registry(config) if (not s.is_primary) and s.id}


def server_like_config(server: EmbyServerConfig, config) -> AppConfig:
    """把注册表条目转成临时 AppConfig —— credential_service 等字段驱动模块直接可用。"""
    proxy = (server.proxy or "").strip() or (getattr(config, "emby_proxy", "") or "")
    return AppConfig(
        emby_url=server.url or "",
        emby_api_key=server.api_key or "",
        emby_username=server.username or "",
        emby_password=server.password or "",
        emby_proxy=proxy,
    )


def iter_server_configs(config):
    """产出 (server_id|None, AppConfig-like),主服在前 —— 聚合管线依赖该顺序实现「主服优先合并」。

    注册表为空时仅产出 (None, 原config),即旧字段形态的主服。
    """
    registry = _registry(config)
    if not registry:
        yield None, config
        return
    ordered = [s for s in registry if s.is_primary] + [s for s in registry if not s.is_primary]
    for server in ordered:
        yield server.id, server_like_config(server, config)


def multi_server_active(config) -> bool:
    """聚合管线是否激活:注册表有从服,且至少一个虚拟库引用了从服。"""
    if not secondary_servers(config):
        return False
    for vlib in getattr(config, "virtual_libraries", None) or []:
        if vlib_uses_secondary(vlib, config):
            return True
    return False


def vlib_uses_secondary(vlib, config) -> bool:
    secondaries = secondary_servers(config)
    return any(sid in secondaries for sid in (getattr(vlib, "server_ids", None) or []))


def vlib_allowed_server_ids(vlib, config) -> Optional[set]:
    """该库允许的来源服务器 id 集合;None = 不限制(未引用从服,行为与单服一致)。"""
    if not vlib_uses_secondary(vlib, config):
        return None
    secondaries = secondary_servers(config)
    allowed = {sid for sid in (getattr(vlib, "server_ids", None) or []) if sid in secondaries}
    primary = primary_server(config)
    if primary and primary.id:
        allowed.add(primary.id)
    return allowed


def iter_vlib_server_targets(vlib, config):
    """probe/按服操作用:该库涉及的 (server_id, AppConfig-like),主服在前。"""
    primary = primary_server(config)
    if primary and primary.id:
        yield primary.id, server_like_config(primary, config)
    else:
        yield "primary", config
    for sid in (getattr(vlib, "server_ids", None) or []):
        server = secondary_servers(config).get(sid)
        if server:
            yield sid, server_like_config(server, config)


# ---------- 从服真实库枚举(首页「我的媒体」注入 + 布局页展示) ----------

_secondary_libs_cache: Dict[str, Tuple[List[Dict[str, Any]], float]] = {}
_SECONDARY_LIBS_TTL = 1800


async def get_secondary_libraries(config, session) -> List[Dict[str, Any]]:
    """枚举各从服的真实媒体库,供首页「我的媒体」与布局页使用。

    返回条目:{server_id, composite_id, name, collection_type, image_tag, has_image},
    composite_id 为 `{server_id}~{library_id}` —— 客户端点磁贴发起的浏览请求
    (ParentId=复合 ID)由现有参数营救路由到属主从服,原生处理排序/筛选/子视图。
    进程内缓存 30 分钟;单台失败警告跳过,不阻塞其它服务器。
    """
    secondaries = secondary_servers(config)
    if not secondaries:
        return []

    cache_key = "|".join(sorted(f"{sid}:{s.url}" for sid, s in secondaries.items()))
    now = time.time()
    cached = _secondary_libs_cache.get(cache_key)
    if cached and now - cached[1] < _SECONDARY_LIBS_TTL:
        return cached[0]

    libs: List[Dict[str, Any]] = []
    for sid, server in secondaries.items():
        server_cfg = server_like_config(server, config)
        try:
            server_headers = await credential_service.get_backend_headers(server_cfg, session=session)
            # 用 MediaFolders 而非 /Views:/Views 在部分 Emby 版本上不带 CollectionType,
            # 缺类型的库会被客户端按"通用文件夹"渲染;MediaFolders 必定携带库类型
            libs_url = f"{server_cfg.emby_url.rstrip('/')}/emby/Library/MediaFolders"
            async with session.get(
                libs_url, headers=server_headers,
                proxy=emby_proxy_from_config(server_cfg),
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"枚举从服 {sid} 媒体库失败: Status={resp.status}")
                    continue
                items = (await resp.json(content_type=None)).get("Items", [])
        except Exception as e:
            logger.warning(f"枚举从服 {sid} 媒体库出错: {e}")
            continue

        for lib in items:
            lib_id = lib.get("Id")
            if not lib_id:
                continue
            tags = lib.get("ImageTags") or {}
            libs.append({
                "server_id": sid,
                "composite_id": f"{sid}{_COMPOSITE_SEP}{lib_id}",
                "name": f"{server.name or sid}·{lib.get('Name') or lib_id}",
                "collection_type": lib.get("CollectionType"),
                "image_tag": tags.get("Primary"),
                "has_image": bool(tags.get("Primary")),
            })
        logger.info(f"枚举从服 {sid} 媒体库完成,共 {len(items)} 个库。")

    _secondary_libs_cache[cache_key] = (libs, now)
    return libs


# ---------- 复合 ID ----------

def encode_item_ref(server_id: str, item_id) -> str:
    return f"{server_id}{_COMPOSITE_SEP}{item_id}"


def split_item_ref_server(ref, config) -> Optional[str]:
    """复合 ID → 从服 id;非复合形态或前缀未注册 → None。"""
    s = str(ref or "")
    if _COMPOSITE_SEP not in s:
        return None
    prefix = s.split(_COMPOSITE_SEP, 1)[0]
    return prefix if prefix in secondary_servers(config) else None


def split_item_ref(ref, config) -> Optional[Tuple[str, str, EmbyServerConfig]]:
    """复合 ID → (server_id, 真实 item id, 属主条目);不合法返回 None。"""
    s = str(ref or "")
    if _COMPOSITE_SEP not in s:
        return None
    prefix, _, real_id = s.partition(_COMPOSITE_SEP)
    server = secondary_servers(config).get(prefix)
    if not server or not real_id:
        return None
    return prefix, real_id, server


def item_server_id(item, config) -> str:
    """条目归属服务器 id:带已注册前缀 → 从服;否则 → 主服(注册表空时为占位 'primary')。"""
    server_id = split_item_ref_server(item.get("Id") if isinstance(item, dict) else item, config)
    if server_id:
        return server_id
    primary = primary_server(config)
    return primary.id if primary and primary.id else "primary"


# ---------- 属主服用户解析(带缓存) ----------

async def resolve_server_user_id(server_cfg, session) -> str:
    """解析某台服务器的内部操作用户:user_pass 用登录账号;api_key 模式取用户列表第一个。"""
    base_url = (server_cfg.emby_url or "").rstrip("/")
    now = time.time()
    cached = _user_cache.get(base_url)
    if cached and now - cached[1] < _USER_RESOLVE_TTL:
        return cached[0]

    if credential_service.auth_mode(server_cfg) == "user_pass":
        auth = await credential_service.get_backend_auth(server_cfg, session=session)
        user_id = str(auth.get("user_id") or "")
        if not user_id:
            raise RoutingError(f"登录账号缺少用户 Id ({base_url})")
    else:
        headers = await credential_service.get_backend_headers(server_cfg, session=session)
        try:
            async with session.get(
                f"{base_url}/emby/Users", headers=headers,
                proxy=emby_proxy_from_config(server_cfg),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    raise RoutingError(f"解析服务器用户失败 ({base_url}) Status={resp.status}")
                users = await resp.json(content_type=None)
        except aiohttp.ClientError as e:
            raise RoutingError(f"连接服务器失败 ({base_url}): {e}") from e
        user_id = next(
            (str(u.get("Id")) for u in (users if isinstance(users, list) else [])
             if isinstance(u, dict) and u.get("Id")),
            "",
        )
        if not user_id:
            raise RoutingError(f"服务器用户列表无有效 Id ({base_url})")

    _user_cache[base_url] = (user_id, now)
    return user_id


# ---------- 入口请求路由 ----------

async def resolve_routed_request(request: Request, full_path: str, config, session):
    """路径携带从服复合 ID 时,把整个请求改写为属主服务器的真实形态。

    返回 (new_request, new_full_path, EmbyServerConfig, server_id);未命中返回 None。
    属主服凭证/用户解析失败抛 RoutingError(调用方转 502)。
    """
    hit: Optional[Tuple[str, str, EmbyServerConfig]] = None
    raw_segments = full_path.split("/")
    for seg in raw_segments:
        ref = split_item_ref(seg, config)
        if ref:
            hit = ref
            break
    if hit is None:
        # 裸 ID 营救:部分客户端会把复合 ID 规范化成裸 ID 再发起后续请求。
        # 只救两类:播放流(videos/audio 之后的段)与图片(Items/{id}/Images/...,
        # 限定 ID 段后必须紧跟 Images)—— 详情/浏览不参与,避免两服内部 ID
        # 撞号时点错条目;图片顶多张冠李戴,可接受。
        for i, seg in enumerate(raw_segments):
            prev_lower = raw_segments[i - 1].lower() if i > 0 else ""
            next_lower = raw_segments[i + 1].lower() if i + 1 < len(raw_segments) else ""
            is_stream_id = prev_lower in _RESCUE_PREV_SEGMENTS
            is_image_id = prev_lower == "items" and next_lower.startswith("images")
            if not (is_stream_id or is_image_id):
                continue
            mapped = _served_real_id_map.get(seg)
            if not mapped or time.time() - mapped[1] > _SERVED_MAP_TTL:
                continue
            server = secondary_servers(config).get(mapped[0])
            if server is not None:
                logger.info(f"多服路由:裸 ID '{seg}' 命中映射,路由至 {mapped[0]}。")
                hit = (mapped[0], seg, server)
                break
    if hit is None:
        # 参数营救:部分请求把条目引用放在查询参数里(ListItemIds/Ids/ParentId 等),
        # 路径上无从判断属主 —— ID 类参数的值里出现从服复合 ID 即路由到该服,
        # 后续的参数改写会把复合值剥回真实形态再转发。
        query_string_raw = request.scope.get("query_string", b"") or b""
        for key, value in parse_qsl(query_string_raw.decode("latin-1"), keep_blank_values=True):
            if key.lower() not in _QUERY_ID_PARAM_KEYS:
                continue
            for part in value.split(","):
                ref = split_item_ref(part.strip(), config)
                if ref:
                    logger.info(f"多服路由:参数 {key} 携带 {ref[0]} 复合 ID,路由至 {ref[0]}。")
                    hit = ref
                    break
            if hit:
                break
    if hit is None:
        return None

    server_id, _real_id, server = hit
    server_cfg = server_like_config(server, config)
    auth = await credential_service.get_backend_auth(server_cfg, session=session)
    token = auth["token"]
    server_user_id = await resolve_server_user_id(server_cfg, session)
    composite_marker = f"{server_id}{_COMPOSITE_SEP}"

    # 记录原始客户端 uid(路径/查询改写前的形态):播放状态类写操作失效叠加层时,
    # 必须用客户端身份的键,而不是被改写后的从服后台账号
    client_uid = None
    for i in range(1, len(raw_segments)):
        if raw_segments[i - 1] == "Users" and raw_segments[i]:
            client_uid = raw_segments[i]
            break
    if not client_uid:
        for key, value in parse_qsl((request.scope.get("query_string", b"") or b"").decode("latin-1"), keep_blank_values=True):
            if key.lower() == "userid":
                client_uid = value
                break

    # 1) 路径改写:复合 ID → 真实 id;/Users/{客户端uid}/ → /Users/{属主服uid}/;
    #    段内嵌复合值(如字幕路径的 mediasource_s2~1576)剥前缀保留真实形态。
    raw_segments = full_path.split("/")
    new_segments: List[str] = []
    for seg in raw_segments:
        ref = split_item_ref(seg, config)
        if ref and ref[0] == server_id:
            seg = ref[1]
        elif composite_marker in seg:
            seg = seg.replace(composite_marker, "")
        new_segments.append(seg)
    for i in range(1, len(new_segments)):
        if new_segments[i - 1] == "Users" and new_segments[i]:
            new_segments[i] = server_user_id
    new_full_path = "/".join(new_segments)

    # 2) 查询参数改写:UserId 换属主服用户;客户端 token 参数换成属主服 token;
    #    其余参数值里若带了本服下发的复合值(如 MediaSourceId=mediasource_s2~1576),
    #    剥回真实形态再转发(值本身是属主服签发的,只是混入了我们的前缀)。
    query_string = request.scope.get("query_string", b"") or b""
    pairs = parse_qsl(query_string.decode("latin-1"), keep_blank_values=True)
    new_pairs: List[Tuple[str, str]] = []
    for key, value in pairs:
        lowered = key.lower()
        if lowered == "userid":
            value = server_user_id
        elif lowered in _TOKEN_PARAM_KEYS:
            value = token
        elif composite_marker in value:
            value = value.replace(composite_marker, "")
        new_pairs.append((key, value))

    # 3) 请求头改写:X-Emby-Token 与 X-Emby-Authorization 内嵌 Token 替换为属主服 token
    new_headers: List[Tuple[bytes, bytes]] = []
    for name, value in request.scope["headers"]:
        lowered = name.decode("latin-1").lower()
        if lowered == "x-emby-token":
            value = token.encode("latin-1")
        elif lowered == "x-emby-authorization":
            text = _AUTH_TOKEN_RE.sub(lambda m: f'{m.group(1)}{token}{m.group(3)}', value.decode("latin-1"))
            value = text.encode("latin-1")
        new_headers.append((name, value))

    # 4) 以改写后的 scope 重建 Request(scope 共享 → state 互通)
    scope = dict(request.scope)
    scope["query_string"] = urlencode(new_pairs).encode("latin-1")
    scope["headers"] = new_headers
    new_request = Request(scope, request.receive)
    new_request.state.evp_server_id = server_id
    if client_uid:
        new_request.state.evp_client_uid = client_uid
    logger.info(f"多服路由:请求携带 {server_id} 复合 ID,已改写指向 {server.name or server_id}。")
    return new_request, new_full_path, server, server_id


_primary_server_id_cache: Dict[str, Tuple[str, float]] = {}


async def get_primary_server_id(config, session) -> str:
    """客户端唯一认识的服务器 ID(主服 System/Info 的 Id)。

    聚合下发的所有条目必须统一用这个 ServerId —— 客户端渲染卡片时执行
    `connectionManager.getApiClient(item.ServerId)`,ServerId 是它没登记过的
    从服 ID 时会拿到 null,随后 `null.getImageUrl` 直接让 Emby Web 渲染崩溃。
    """
    cache_key = (config.emby_url or "").rstrip("/")
    now = time.time()
    cached = _primary_server_id_cache.get(cache_key)
    if cached and now - cached[1] < 24 * 3600:
        return cached[0]

    server_id = (getattr(config, "emby_server_id", "") or "").strip()
    if not server_id:
        try:
            headers = await credential_service.get_backend_headers(config, session=session)
            async with session.get(
                f"{cache_key}/emby/System/Info", headers=headers,
                proxy=emby_proxy_from_config(config),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    server_id = str((await resp.json(content_type=None)).get("Id") or "")
        except Exception as e:
            logger.warning(f"解析主服 ServerId 失败: {e}")
    if not server_id:
        server_id = "emby"
    _primary_server_id_cache[cache_key] = (server_id, now)
    return server_id


# ---------- 出站改写:属主服响应的条目 ID 加回复合前缀 ----------

# 需要加前缀的 ID 字段(客户端会拿它们发起后续请求:详情/图片/播放/进度/导航)
_OUTBOUND_ID_FIELDS = {"Id", "SeriesId", "SeasonId", "EpisodeId", "AlbumId", "ParentId", "MediaSourceId"}
# 这些集合里是"人名/演员"等非条目导航,客户端按名称或主服查询,加前缀反而打不通
_OUTBOUND_SKIP_KEYS = {"People", "ArtistItems", "AlbumArtists"}

# 裸 ID 营救映射:出站改写时记录 {真实ID: (属主服, 时间)}。
# 部分客户端会把复合 ID 规范化成裸 ID 再发起播放请求(如 /videos/1512/original.mp4),
# 无前缀无从判断属主,只能靠这份映射救回。仅用于播放流路径,浏览路径不参与,
# 避免两台服务器内部 ID 撞号时误伤浏览。
_SERVED_MAP_TTL = 24 * 3600
_served_real_id_map: Dict[str, Tuple[str, float]] = {}
_RESCUE_PREV_SEGMENTS = {"videos", "audio"}


def _record_served_id(real_id: str, server_id: str) -> None:
    _served_real_id_map[str(real_id)] = (server_id, time.time())
    if len(_served_real_id_map) > 20000:
        cutoff = time.time() - _SERVED_MAP_TTL
        stale = [k for k, v in _served_real_id_map.items() if v[1] < cutoff][:2000]
        for k in stale:
            _served_real_id_map.pop(k, None)


def _prefix_ids_outbound(obj, server_id: str, marker: str, primary_server_id: str):
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key in _OUTBOUND_SKIP_KEYS:
                out[key] = value
            elif key == "ServerId":
                # 统一成客户端认识的主服 ServerId(从服自带的 ID 客户端解析不到 apiClient)
                out[key] = primary_server_id
            elif key in _OUTBOUND_ID_FIELDS and isinstance(value, (str, int)) and value:
                s = str(value)
                if s.startswith(marker):
                    out[key] = s
                else:
                    _record_served_id(s, server_id)
                    out[key] = f"{server_id}{_COMPOSITE_SEP}{s}"
            else:
                out[key] = _prefix_ids_outbound(value, server_id, marker, primary_server_id)
        return out
    if isinstance(obj, list):
        return [_prefix_ids_outbound(item, server_id, marker, primary_server_id) for item in obj]
    return obj


async def prefix_item_ids(items: List[dict], server_id: str, config, session) -> List[Dict[str, Any]]:
    """出站改写的条目级封装:Id 加复合前缀 + ServerId 归一为客户端认识的主服 ID。

    供代理自己构造的从服响应(如从服真实库的首页「最新」分区)复用,
    与路由透传响应的出站改写保持同一套改写规则。
    """
    primary_server_id = await get_primary_server_id(config, session)
    marker = f"{server_id}{_COMPOSITE_SEP}"
    return [
        _prefix_ids_outbound(item, server_id, marker, primary_server_id)
        for item in items if isinstance(item, dict)
    ]


async def route_playstate_body(request: Request, full_path: str, config, session):
    """播放状态上报(POST /Sessions/Playing/*)的请求体路由。

    ItemId 在请求体里、路径上无从判断属主:体 ItemId 带从服复合前缀或命中
    营救映射时,把上报转发给属主服(凭证替换、体 ItemId 剥前缀)——
    从服条目的播放进度因此可续。未命中返回 None(原样主服,零变化)。
    """
    if request.method != "POST":
        return None
    if "/sessions/playing/" not in f"/{full_path.lower()}/":
        return None

    body = await request.body()
    try:
        payload = json.loads(body or b"{}")
        item_id = str(payload.get("ItemId") or "")
    except Exception:
        return None
    if not item_id:
        return None

    ref = split_item_ref(item_id, config)
    if ref is None:
        mapped = _served_real_id_map.get(item_id)
        if mapped and time.time() - mapped[1] <= _SERVED_MAP_TTL:
            server = secondary_servers(config).get(mapped[0])
            if server is not None:
                ref = (mapped[0], item_id, server)
    if ref is None:
        return None

    server_id, real_item_id, server = ref
    server_cfg = server_like_config(server, config)
    auth = await credential_service.get_backend_auth(server_cfg, session=session)

    payload["ItemId"] = real_item_id
    url = f"{server_cfg.emby_url.rstrip('/')}/emby/{full_path}"
    send_headers = {"Content-Type": "application/json", "X-Emby-Token": auth["token"]}
    async with session.post(url, json=payload, headers=send_headers,
                            proxy=emby_proxy_from_config(server_cfg)) as resp:
        status = resp.status
        resp_body = await resp.read()
    logger.info(f"多服路由:播放状态上报(ItemId={item_id})已路由至 {server_id}(Status={status})。")
    return Response(content=resp_body if resp_body else None, status_code=status,
                    media_type="application/json")


async def rewrite_routed_response_ids(response, server_id: str, config, session):
    """属主服响应出站前,把 JSON 里的条目 ID 字段加回复合前缀,ServerId 统一为主服。

    没有这一步,客户端拿到属主服的裸 ID 后发起的播放/图片/进度请求不带前缀,
    路由无法识别属主,会被打到主服(404/500);ServerId 不归一则客户端
    connectionManager 解析不到 apiClient,渲染直接崩溃。仅处理 JSON。
    已带前缀的值幂等跳过(响应可能来自缓存)。
    """
    content_type = (response.headers.get("content-type") or "").lower()
    if "application/json" not in content_type:
        return response

    status = getattr(response, "status_code", 200)
    headers = dict(response.headers)
    if isinstance(response, StreamingResponse):
        body = b"".join([chunk async for chunk in response.body_iterator])
    elif hasattr(response, "body"):
        body = response.body
    else:
        body = await response.body()

    try:
        primary_server_id = await get_primary_server_id(config, session)
        data = json.loads(body)
        marker = f"{server_id}{_COMPOSITE_SEP}"
        data = _prefix_ids_outbound(data, server_id, marker, primary_server_id)
        new_body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    except Exception as e:
        logger.warning(f"多服出站改写失败(原样返回): {e}")
        new_body = body

    headers.pop("content-length", None)
    headers.pop("content-encoding", None)
    return Response(content=new_body, status_code=status, headers=headers, media_type="application/json")
