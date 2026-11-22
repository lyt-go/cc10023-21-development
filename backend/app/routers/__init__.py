"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import model_release, resource

# 专用路由需放在通用资源路由之前，避免 /api/{module} 动态路径抢先匹配。
ROUTERS = [model_release, resource]
