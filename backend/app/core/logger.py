"""
统一日志配置模块
提供规范的日志记录功能，包含traceback信息
"""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from datetime import datetime


# 日志格式
LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | "
    "%(name)s:%(funcName)s:%(lineno)d | "
    "%(message)s"
)

# 日期格式
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logger(
    name: str = "vewealth",
    level: int = logging.INFO,
    log_dir: str = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
) -> logging.Logger:
    """
    设置并返回logger实例
    
    Args:
        name: logger名称
        level: 日志级别
        log_dir: 日志文件目录，如果为None则不写入文件
        max_bytes: 单个日志文件最大大小
        backup_count: 保留的日志文件数量
        
    Returns:
        配置好的logger实例
    """
    logger = logging.getLogger(name)
    
    # 避免重复添加handler
    if logger.handlers:
        return logger
    
    logger.setLevel(level)
    logger.propagate = False
    
    # 创建formatter
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
    
    # 控制台handler - 始终添加
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 文件handler - 可选
    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        
        # 按日期创建日志文件
        log_file = log_path / f"{name}.log"
        
        file_handler = RotatingFileHandler(
            filename=log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger


def get_logger(name: str = None) -> logging.Logger:
    """
    获取logger实例
    
    Args:
        name: logger名称，如果为None则使用调用者的模块名
        
    Returns:
        logger实例
    """
    if name is None:
        # 获取调用者的模块名
        import inspect
        frame = inspect.currentframe().f_back
        name = frame.f_globals.get("__name__", "vewealth")
    
    return logging.getLogger(name)


# 创建全局logger实例
# 后端根目录的logs文件夹
backend_dir = Path(__file__).parent.parent.parent
log_dir = backend_dir / "logs"

# 主应用logger
app_logger = setup_logger(
    name="vewealth",
    level=logging.INFO,
    log_dir=str(log_dir),
)

# 为不同模块创建子logger
def get_module_logger(module_name: str) -> logging.Logger:
    """
    获取模块级别的logger
    
    Args:
        module_name: 模块名称
        
    Returns:
        模块logger
    """
    return logging.getLogger(f"vewealth.{module_name}")

