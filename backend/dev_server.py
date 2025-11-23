#!/usr/bin/env python3
"""
开发服务器启动脚本
支持热重载功能，文件修改后自动重启
"""

import uvicorn
from app.core.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "main:app",  # 应用模块路径
        host=settings.HOST,
        port=settings.PORT,
        reload=True,  # 启用热重载
        reload_dirs=["app"],  # 监控的目录
        log_level="info",
    )

