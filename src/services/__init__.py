"""管理后台业务服务层。

每个服务模块封装一块独立业务,admin_server.py 的路由只做薄封装调用:
  - emby_client.py     Emby API 统一访问(管理侧)
  - cover_service.py   封面生成(素材抓取 + 样式插件调度)
  - rss_service.py     RSS 虚拟库刷新与定时调度
  - library_service.py 虚拟库 CRUD 与显示顺序
  - system_service.py  系统级操作(Docker 容器重启)
"""
