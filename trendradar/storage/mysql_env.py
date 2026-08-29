# coding=utf-8
"""
MYSQL_* 环境变量 → MySQL 连接参数 的统一读取入口。

供 examples 下的 demo 脚本与一次性工具复用，避免在代码中硬编码
用户名/密码。优先级：MYSQL_* 环境变量 > 传入的默认值。

支持的环境变量：
    MYSQL_HOST / MYSQL_PORT / MYSQL_USERNAME / MYSQL_PASSWORD /
    MYSQL_DATABASE / MYSQL_CHARSET
（与 trendradar/core/loader.py 及 mcp_server 工具层使用的变量名一致）
"""

import os
from typing import Any, Dict


def conn_params_from_env(
    host: str = "localhost",
    port: int = 3306,
    username: str = "root",
    password: str = "12345678",
    database: str = "trendradar",
    charset: str = "utf8mb4",
) -> Dict[str, Any]:
    """构造 MySQL 连接参数字典；每个键都可被对应的 MYSQL_* 环境变量覆盖。

    Args:
        host/port/username/password/database/charset: 未设置环境变量时的回退默认值。

    Returns:
        含 host/port/username/password/database/charset 六个键的字典，
        可直接 ``**`` 解包传给 init_db_pool / init_mysql_pipeline / MySQLReader。
    """

    def _env_or(key: str, fallback: Any) -> str:
        value = os.getenv(key, "").strip()
        return value if value else str(fallback)

    return {
        "host": _env_or("MYSQL_HOST", host),
        "port": int(_env_or("MYSQL_PORT", port)),
        "username": _env_or("MYSQL_USERNAME", username),
        "password": _env_or("MYSQL_PASSWORD", password),
        "database": _env_or("MYSQL_DATABASE", database),
        "charset": _env_or("MYSQL_CHARSET", charset),
    }
