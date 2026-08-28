# MySQL 数据库集成指南

## 概述

TrendRadar 现已支持将热点新闻数据和 AI 分析结果存储到 MySQL 数据库。本指南详细说明如何配置和使用 MySQL 存储功能。

## 目录

1. [快速开始](#快速开始)
2. [数据库初始化](#数据库初始化)
3. [配置参数](#配置参数)
4. [API 使用示例](#api-使用示例)
5. [集成到主流水线](#集成到主流水线)
6. [数据查询示例](#数据查询示例)
7. [故障排查](#故障排查)

---

## 快速开始

### 1. 安装依赖

确保已安装 SQLAlchemy 和 PyMySQL：

```bash
pip install SQLAlchemy==2.1.1 PyMySQL==1.1.1
```

或使用项目的 `pyproject.toml`：

```bash
pip install -e .
```

### 2. 初始化数据库

运行初始化脚本创建表结构：

```bash
# 使用默认参数（localhost:3306, root:12345678）
python -m trendradar.storage.mysql_init

# 或指定自定义参数
python -m trendradar.storage.mysql_init \
    --host 192.168.1.10 \
    --port 3306 \
    --user admin \
    --password mypassword \
    --database trendradar

# 仅验证表结构
python -m trendradar.storage.mysql_init verify
```

### 3. 快速测试

```python
from trendradar.storage import init_db_pool, MySQLStorageBackend

# 初始化连接池
db_pool = init_db_pool(
    host="localhost",
    port=3306,
    username="root",
    password="12345678",
    database="trendradar"
)

# 创建存储后端
backend = MySQLStorageBackend(db_pool)

# 测试连接
if backend.health_check():
    print("MySQL 连接成功！")
    print(f"表统计: {backend.get_table_stats()}")
```

---

## 数据库初始化

### 数据表结构

#### `raw_data_feed` - 原始数据表

存储从各数据源抓取的原始数据（新闻、RSS 等）。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INT | 主键（自增） |
| `source_type` | VARCHAR(50) | 数据源类型（如 "hotlist_news", "rss_feed") |
| `content` | TEXT | 原始内容（新闻标题、RSS 标题等），UTF-8MB4 |
| `url` | VARCHAR(1024) | 内容链接 |
| `source_id` | VARCHAR(100) | 来源 ID（如 "toutiao", "baidu", "hacker-news") |
| `source_name` | VARCHAR(200) | 来源名称 |
| `additional_data` | JSON | 额外数据（JSON 格式，如排名、时间线等） |
| `created_at` | DATETIME | 记录创建时间（UTC） |
| `updated_at` | DATETIME | 记录更新时间（UTC） |

**索引：**
- `idx_source_type_created` - (source_type, created_at)
- `idx_source_id_created` - (source_id, created_at)

#### `financial_sentiment` - 情感分析表

存储 LLM 解析后的结构化结果（股票情感分析）。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INT | 主键（自增） |
| `stock_name` | VARCHAR(200) | 股票名称（如 "Apple"） |
| `stock_code` | VARCHAR(50) | 股票代码（如 "AAPL"） |
| `sentiment_score` | FLOAT | 情感评分（-1.0 到 1.0） |
| `alert_level` | ENUM | 告警级别（'Low', 'Medium', 'High'） |
| `summary_event` | TEXT | 事件摘要，UTF-8MB4 |
| `raw_data_id` | INT FK | 外键，指向 raw_data_feed.id |
| `analysis_metadata` | JSON | 分析元数据（JSON 格式） |
| `created_at` | DATETIME | 记录创建时间（UTC） |
| `updated_at` | DATETIME | 记录更新时间（UTC） |

**索引：**
- `idx_stock_code_created` - (stock_code, created_at)
- `idx_alert_level_created` - (alert_level, created_at)
- `idx_raw_data_id` - (raw_data_id)
- `idx_sentiment_score` - (sentiment_score)

---

## 配置参数

### 连接池配置

```python
from trendradar.storage import init_db_pool

db_pool = init_db_pool(
    host="localhost",              # 数据库主机地址
    port=3306,                     # 端口
    username="root",               # 用户名
    password="12345678",           # 密码
    database="trendradar",         # 数据库名
    charset="utf8mb4",             # 字符集
    pool_size=10,                  # 连接池大小
    max_overflow=20,               # 超额连接数
    pool_recycle=3600,             # 连接回收时间（秒）
    echo_sql=False,                # 是否打印 SQL（调试）
)
```

**参数说明：**
- `pool_size`: 基础连接池大小。建议值：5-10
- `max_overflow`: 允许的超额连接数。建议值：20-50
- `pool_recycle`: MySQL 8小时超时，建议设置为 3600（1小时）

---

## API 使用示例

### 原始数据操作

#### 保存单条原始数据

```python
from trendradar.storage import MySQLStorageBackend, get_db_pool

backend = MySQLStorageBackend(get_db_pool())

# 保存新闻数据
data_id = backend.save_raw_data(
    source_type="hotlist_news",
    content="标题：AI 芯片新突破",
    url="https://example.com/news/123",
    source_id="toutiao",
    source_name="头条",
    additional_data={
        "rank": 1,
        "ranks": [5, 3, 1],
        "crawl_time": "14:30"
    }
)

if data_id:
    print(f"数据已保存，ID: {data_id}")
```

#### 批量保存原始数据

```python
# 批量保存爬虫数据
records = [
    {
        'source_type': 'hotlist_news',
        'content': '新闻 1',
        'url': 'https://example.com/1',
        'source_id': 'toutiao',
        'source_name': '头条',
        'additional_data': {'rank': 1}
    },
    {
        'source_type': 'hotlist_news',
        'content': '新闻 2',
        'url': 'https://example.com/2',
        'source_id': 'baidu',
        'source_name': '百度',
        'additional_data': {'rank': 2}
    },
]

count = backend.save_raw_data_batch(records)
print(f"已保存 {count} 条原始数据")
```

#### 查询原始数据

```python
from datetime import datetime, timedelta

# 按来源查询
results = backend.query_raw_data(
    source_id="toutiao",
    limit=50
)

# 按时间范围查询
end_date = datetime.utcnow()
start_date = end_date - timedelta(hours=1)

results = backend.query_raw_data(
    start_date=start_date,
    end_date=end_date,
    limit=100
)

for record in results:
    print(f"{record['source_id']}: {record['content']}")
```

### 情感分析操作

#### 保存单条分析结果

```python
# 保存股票情感分析结果
sentiment_id = backend.save_financial_sentiment(
    stock_name="Apple",
    stock_code="AAPL",
    sentiment_score=0.75,              # 范围: -1.0 到 1.0
    alert_level="High",                # 或 "Medium" / "Low"
    summary_event="AI芯片利好，股价上涨",
    raw_data_id=123,                   # 关联的原始数据ID（可选）
    analysis_metadata={
        "confidence": 0.95,
        "context": "在新闻中提及 Apple 推出新 AI 芯片"
    }
)

if sentiment_id:
    print(f"情感分析已保存，ID: {sentiment_id}")
```

#### 批量保存分析结果

```python
sentiment_records = [
    {
        'stock_name': 'Apple',
        'stock_code': 'AAPL',
        'sentiment_score': 0.75,
        'alert_level': 'High',
        'summary_event': '...',
        'raw_data_id': 123,
        'analysis_metadata': {...}
    },
    {
        'stock_name': 'Microsoft',
        'stock_code': 'MSFT',
        'sentiment_score': 0.50,
        'alert_level': 'Medium',
        'summary_event': '...',
        'raw_data_id': 124,
    },
]

count = backend.save_financial_sentiment_batch(sentiment_records)
print(f"已保存 {count} 条情感分析")
```

#### 查询情感分析

```python
# 查询高告警级别的记录
high_alerts = backend.query_financial_sentiment(
    alert_level="High",
    limit=50
)

# 查询特定股票
aapl_sentiments = backend.query_financial_sentiment(
    stock_code="AAPL",
    limit=100
)

# 查询情感分数范围
positive_sentiments = backend.query_financial_sentiment(
    min_sentiment=0.5,
    max_sentiment=1.0,
    limit=100
)

# 按时间范围查询
from datetime import datetime, timedelta
recent = backend.query_financial_sentiment(
    start_date=datetime.utcnow() - timedelta(hours=1),
    end_date=datetime.utcnow(),
    limit=100
)

for record in recent:
    print(f"{record['stock_code']}: {record['sentiment_score']} ({record['alert_level']})")
```

---

## 集成到主流水线

### 修改爬虫流程

在 `trendradar/crawler/fetcher.py` 中集成 MySQL 存储：

```python
from trendradar.storage.mysql_pipeline import init_mysql_pipeline, get_mysql_pipeline

# 在程序初始化时
pipeline = init_mysql_pipeline(
    host="localhost",
    port=3306,
    username="root",
    password="12345678",
    database="trendradar"
)

# 在数据获取后
def save_crawled_data(crawl_results):
    pipeline = get_mysql_pipeline()
    
    for source_id, news_list in crawl_results.items():
        pipeline.ingest_crawled_news(
            news_items=news_list,
            source_id=source_id,
            source_name="来源名称"
        )
```

### 修改 AI 分析流程

在 `trendradar/ai/analyzer.py` 中集成 MySQL 存储：

```python
from trendradar.storage.mysql_pipeline import get_mysql_pipeline

class AIAnalyzer:
    def analyze_and_save(self, news_content):
        # ... AI 分析代码 ...
        analysis_result = self.analyze(news_content)
        
        # 保存到 MySQL
        pipeline = get_mysql_pipeline()
        pipeline.process_ai_analysis(analysis_result)
        
        return analysis_result
```

### 完整的数据流示例

```python
from trendradar.storage.mysql_pipeline import init_mysql_pipeline
from trendradar.crawler import DataFetcher
from trendradar.ai import AIAnalyzer

# 初始化
pipeline = init_mysql_pipeline()
fetcher = DataFetcher()
analyzer = AIAnalyzer()

# 1. 爬取数据
crawl_results = fetcher.fetch_all()

# 2. 保存原始数据
for source_id, news_list in crawl_results.items():
    pipeline.ingest_crawled_news(
        news_items=news_list,
        source_id=source_id,
    )

# 3. AI 分析
ai_results = analyzer.analyze(crawl_results)

# 4. 保存分析结果
pipeline.process_ai_analysis(ai_results)

# 5. 查询统计
stats = pipeline.get_stats()
print(f"数据库统计: {stats}")
```

---

## 数据查询示例

### 实时告警查询

```python
from trendradar.storage import get_db_pool, MySQLStorageBackend

backend = MySQLStorageBackend(get_db_pool())

# 查询所有高告警记录
high_alerts = backend.query_financial_sentiment(alert_level="High")

for alert in high_alerts:
    print(f"[{alert['alert_level']}] {alert['stock_code']}: "
          f"{alert['sentiment_score']} - {alert['summary_event']}")
```

### 股票追踪

```python
# 追踪特定股票的情感趋势
stock_code = "AAPL"
sentiments = backend.query_financial_sentiment(stock_code=stock_code, limit=100)

# 计算平均情感分
avg_sentiment = sum(s['sentiment_score'] for s in sentiments) / len(sentiments)
print(f"{stock_code} 平均情感分: {avg_sentiment:.2f}")
```

### 数据统计

```python
# 获取表统计
stats = backend.get_table_stats()
print(f"原始数据: {stats['raw_data_feed']} 条")
print(f"情感分析: {stats['financial_sentiment']} 条")
```

---

## 故障排查

### 问题 1：连接失败

**症状：** `pymysql.err.OperationalError: (2003, "Can't connect...")`

**解决方案：**
1. 确保 MySQL 服务运行：`sudo service mysql status`
2. 检查连接参数（host, port, username, password）
3. 检查防火墙设置
4. 确认数据库存在：`mysql -u root -p -e "SHOW DATABASES LIKE 'trendradar'"`

### 问题 2：表创建失败

**症状：** `pymysql.err.ProgrammingError: (1064, ...)`

**解决方案：**
1. 检查字符集配置
2. 确保使用的 MySQL 版本 >= 5.7
3. 查看错误日志：运行 `python -m trendradar.storage.mysql_init --database test_db`

### 问题 3：中文乱码

**症状：** 查询到的中文显示为 `?` 或乱码

**解决方案：**
1. 确保使用 `utf8mb4` 字符集
2. 检查连接字符串中的 `charset` 参数
3. 在 MySQL 中验证：
   ```sql
   USE trendradar;
   SELECT COLLATION_NAME FROM INFORMATION_SCHEMA.COLUMNS 
   WHERE TABLE_NAME='raw_data_feed' AND COLUMN_NAME='content';
   ```

### 问题 4：性能问题

**症状：** 查询缓慢，响应时间长

**解决方案：**
1. 检查索引是否创建：`SHOW INDEX FROM raw_data_feed;`
2. 调整连接池大小（增加 `pool_size`）
3. 添加分区（对于大型表）：
   ```sql
   ALTER TABLE raw_data_feed PARTITION BY RANGE (YEAR(created_at)) (...);
   ```

### 问题 5：连接池泄漏

**症状：** 长时间运行后内存占用持续增长

**解决方案：**
1. 确保在上下文管理器中使用 `session_scope()`
2. 确保调用 `close_db_pool()` 在程序退出时
3. 设置合理的 `pool_recycle` 参数

---

## 高级配置

### 环境变量配置

在 `.env` 文件中定义：

```bash
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=12345678
MYSQL_DATABASE=trendradar
MYSQL_CHARSET=utf8mb4
```

在代码中读取：

```python
import os
from trendradar.storage import init_db_pool

db_pool = init_db_pool(
    host=os.getenv('MYSQL_HOST', 'localhost'),
    port=int(os.getenv('MYSQL_PORT', 3306)),
    username=os.getenv('MYSQL_USER', 'root'),
    password=os.getenv('MYSQL_PASSWORD', '12345678'),
    database=os.getenv('MYSQL_DATABASE', 'trendradar'),
)
```

### 主从复制配置

对于高可用架构，配置主从复制：

```python
# 主库
master_pool = init_db_pool(host="192.168.1.10", ...)

# 从库（读）
slave_pool = init_db_pool(host="192.168.1.11", ...)

# 写操作到主库，读操作到从库
backend = MySQLStorageBackend(master_pool)
query_result = backend.query_raw_data(...)  # 使用从库
```

---

## 性能优化建议

1. **批量操作**：使用 `save_raw_data_batch()` 而不是单条保存
2. **连接池调优**：根据并发数调整 `pool_size`
3. **索引优化**：定期检查慢查询日志
4. **分区策略**：按时间分区大型表
5. **定期清理**：删除过期数据以保持性能

---

## 参考资源

- [SQLAlchemy 文档](https://docs.sqlalchemy.org/)
- [PyMySQL 文档](https://pymysql.readthedocs.io/)
- [MySQL 字符集文档](https://dev.mysql.com/doc/refman/8.0/en/charset-unicode.html)

---

## 常见问题（FAQ）

**Q: 是否可以同时使用 SQLite 和 MySQL？**
A: 可以。可以通过 StorageManager 的 `backend_type` 参数灵活切换。

**Q: MySQL 中的数据是否可以导出为 CSV？**
A: 可以。使用 SQL：
```sql
SELECT * FROM raw_data_feed 
INTO OUTFILE '/tmp/export.csv' 
FIELDS TERMINATED BY ','
LINES TERMINATED BY '\\n';
```

**Q: 如何备份数据？**
A: 使用 MySQL 工具：
```bash
mysqldump -u root -p trendradar > backup.sql
```

---

*文档最后更新：2024年*
