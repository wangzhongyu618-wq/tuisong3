# MySQL 集成快速开始指南

## 概览

本指南说明如何快速设置 TrendRadar 的 MySQL 存储功能。

## 前置条件

1. **MySQL 服务器**（版本 5.7+ 或 8.0+）
   - 运行中并可访问
   - 有 root 或其他管理员账户

2. **Python 环境**（3.12+）
   - 已安装 TrendRadar 项目依赖

## 快速步骤

### 步骤 1: 安装依赖

```bash
# 如果还未安装项目依赖
pip install -e .

# 或单独安装 MySQL 相关包
pip install SQLAlchemy==2.1.1 PyMySQL==1.1.1
```

### 步骤 2: 初始化数据库

```bash
# 使用默认参数初始化（localhost:3306, root:12345678）
python -m trendradar.storage.mysql_init

# 输出应该显示：
# [初始化] 开始初始化 MySQL 数据库
# [初始化] 管理员引擎已创建...
# [初始化] 数据库创建成功: trendradar
# ...
# [初始化] MySQL 数据库初始化完成！
```

如果你的 MySQL 配置不同，使用参数：

```bash
python -m trendradar.storage.mysql_init \
    --host 192.168.1.10 \
    --port 3306 \
    --user admin \
    --password your_password \
    --database trendradar
```

### 步骤 3: 验证安装

```bash
# 验证表结构
python -m trendradar.storage.mysql_init verify
```

### 步骤 4: 运行示例

```bash
# 运行 MySQL 集成示例
python examples/mysql_integration_example.py

# 输出应该显示各种操作的结果：
# ✓ MySQL 连接成功
# ✓ 已保存 3 条新闻数据
# ✓ 已保存 2 条 RSS 数据
# ...
```

## 基本使用

### 最简单的示例

```python
from trendradar.storage import init_db_pool, MySQLStorageBackend

# 初始化连接池
db_pool = init_db_pool()

# 创建存储后端
backend = MySQLStorageBackend(db_pool)

# 保存原始数据
data_id = backend.save_raw_data(
    source_type="hotlist_news",
    content="新闻标题",
    url="https://example.com",
    source_id="toutiao",
)

# 保存情感分析结果
sentiment_id = backend.save_financial_sentiment(
    stock_name="Apple",
    stock_code="AAPL",
    sentiment_score=0.75,
    alert_level="High",
    summary_event="利好事件"
)

# 查询数据
sentiments = backend.query_financial_sentiment(alert_level="High")
print(f"高告警记录: {len(sentiments)}")
```

### 使用数据管道

```python
from trendradar.storage.mysql_pipeline import init_mysql_pipeline

# 初始化管道
pipeline = init_mysql_pipeline()

# 存储爬虫数据
pipeline.ingest_crawled_news(
    news_items=[
        {"title": "新闻1", "url": "...", "rank": 1},
    ],
    source_id="toutiao"
)

# 存储 AI 分析结果
pipeline.process_ai_analysis({
    "entities": [{
        "type": "STOCK",
        "name": "Apple",
        "code": "AAPL",
        "sentiment_score": 0.75,
        "alert_level": "High",
        "event_summary": "..."
    }]
})

# 获取统计
stats = pipeline.get_stats()
```

## 常见问题

### Q: 如何修改默认密码？

A: 在初始化前修改 MySQL 密码：

```bash
# 使用 MySQL CLI
mysql -u root -p
ALTER USER 'root'@'localhost' IDENTIFIED BY 'new_password';
FLUSH PRIVILEGES;

# 然后初始化时指定新密码
python -m trendradar.storage.mysql_init --password new_password
```

### Q: 如何连接远程 MySQL 服务器？

A: 指定 host 参数：

```bash
python -m trendradar.storage.mysql_init --host 192.168.1.10 --user admin
```

### Q: 初始化失败，如何查看详细错误？

A: 检查 MySQL 服务是否运行，并查看错误日志：

```bash
# Linux/Mac
sudo service mysql status
tail -f /var/log/mysql/error.log

# Windows
Get-Service MySQL80
```

### Q: 如何清空数据库重新开始？

A: **警告：此操作会删除所有数据！**

```bash
# MySQL CLI
mysql -u root -p
DROP DATABASE trendradar;
CREATE DATABASE trendradar CHARACTER SET utf8mb4;
EXIT;

# 然后重新创建表
python -m trendradar.storage.mysql_init
```

### Q: 中文数据显示乱码怎么办？

A: 确保使用 `utf8mb4` 字符集。检查：

```sql
-- 检查表的字符集
SHOW CREATE TABLE raw_data_feed;

-- 应该看到 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
```

如果不对，可以修复：

```sql
ALTER TABLE raw_data_feed CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
ALTER TABLE financial_sentiment CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

## 配置文件方案

如果希望通过配置文件管理数据库参数，可以在 `config/config.yaml` 中添加：

```yaml
# config/config.yaml

# ... 其他配置 ...

mysql:
  enabled: true
  host: localhost
  port: 3306
  username: root
  password: 12345678
  database: trendradar
  charset: utf8mb4
  pool_size: 10
  max_overflow: 20
  echo_sql: false
```

然后在代码中读取：

```python
from trendradar.core import load_config
from trendradar.storage import init_db_pool

config = load_config()
mysql_config = config.get('mysql', {})

if mysql_config.get('enabled'):
    db_pool = init_db_pool(**mysql_config)
```

## 生产环境配置

### 1. 使用强密码

```bash
python -m trendradar.storage.mysql_init \
    --user admin \
    --password "超过12个字符的强密码"
```

### 2. 创建专用数据库用户

```sql
-- 使用 root 登录 MySQL
CREATE USER 'trendradar'@'localhost' IDENTIFIED BY 'secure_password';
GRANT ALL PRIVILEGES ON trendradar.* TO 'trendradar'@'localhost';
FLUSH PRIVILEGES;
```

然后使用：

```bash
python -m trendradar.storage.mysql_init \
    --user trendradar \
    --password secure_password
```

### 3. 配置备份策略

```bash
# 定期备份脚本 (backup.sh)
#!/bin/bash
BACKUP_DIR="/path/to/backups"
DATE=$(date +%Y%m%d_%H%M%S)
mysqldump -u trendradar -p -h localhost trendradar | gzip > $BACKUP_DIR/trendradar_$DATE.sql.gz
```

### 4. 监控和日志

启用慢查询日志：

```sql
SET GLOBAL slow_query_log = 'ON';
SET GLOBAL long_query_time = 1;
```

## 性能优化

### 调整连接池大小

根据并发量调整：

```python
db_pool = init_db_pool(
    pool_size=20,           # 增加基础连接数
    max_overflow=50,        # 增加超额连接数
    pool_recycle=3600,      # 1小时回收一次
)
```

### 批量操作

优先使用批量方法：

```python
# ✓ 快速 - 一次数据库往返
backend.save_raw_data_batch(records)

# ✗ 慢速 - 多次数据库往返
for record in records:
    backend.save_raw_data(**record)
```

## 下一步

- 查看详细文档：[MYSQL_INTEGRATION_GUIDE.md](MYSQL_INTEGRATION_GUIDE.md)
- 阅读 API 参考：见代码注释
- 运行完整示例：`python examples/mysql_integration_example.py`

## 获取帮助

遇到问题？

1. 检查日志输出
2. 查看故障排查部分：[MYSQL_INTEGRATION_GUIDE.md#故障排查](MYSQL_INTEGRATION_GUIDE.md#故障排查)
3. 提交问题报告

---

**提示**：保存此指南链接以备后续参考！
