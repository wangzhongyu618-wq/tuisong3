# ═══════════════════════════════════════════════════════════════
#          TrendRadar QMT 消费端数据规格文档（P1-④）
# ═══════════════════════════════════════════════════════════════
#
# 本文档面向以 QMT/xtquant 为代表的下游量化消费端，描述 TrendRadar
# 产出的"实体+情感分"结构化数据的完整读取契约。
# 文档与代码由 tests/test_qmt_consumer_spec.py 防漂移测试约束：
# 表字段、索引/唯一键、MCP 工具名单变更而文档未同步时测试会失败。
#
# ═══════════════════════════════════════════════════════════════

# QMT 消费端数据规格（P1-④）

> 版本基准：P1-⑤（含事件级去重） · 适用对象：QMT / xtquant 等下游策略端与 Agent
> 本文档描述**只读**数据契约；写入侧由 TrendRadar 主程序独占，消费端**严禁写库**。

## 1. 概述

TrendRadar 每个调度轮次抓取多平台热榜/RSS 财经新闻，经 LLM 做实体提取与情感标注后，
将"金融实体 + 情感得分 + 告警级别 + 事件摘要"结构化沉淀到 MySQL
`financial_sentiment` 表（原始新闻沉淀在 `raw_data_feed` 表），供下游查询与订阅。

前提开关（`config/config.yaml`）：
- `storage.mysql.enabled: true`（启用 MySQL 存储）
- `ai_analysis.enable_sentiment_extraction: true`（启用实体提取，逐实体入库）
- `schedule.enabled: true`（调度轮次驱动数据产出，如 `morning_evening` 预设）

三种读取通道（按推荐顺序）：

| 通道 | 适用场景 | 说明 |
|------|----------|------|
| ① 直连 MySQL（SQL） | QMT 策略端定时轮询（推荐） | 建独立只读账号，见 §5.1 |
| ② MCP 只读工具 | Agent/LLM 自然语言查询 | `mysql_*` 系列工具，见 §5.2 |
| ③ MySQLReader Python API | 同机 Python 进程内集成 | `trendradar.storage.mysql_reader`，见 §5.3 |

## 2. 数据流水线总览

```
多平台热榜/RSS 抓取
        │  内容去重：uq_raw_dedup (source_type, source_id, content_hash)
        ▼
raw_data_feed（原始新闻，含 rank 等附加 JSON）
        │  每轮次一次轻量 LLM 调用（config/ai_sentiment_prompt.txt）
        ▼
实体提取 JSON：entities[{name, code, sentiment_score, alert_level,
                          event_summary, context}]（最多 10 条/轮）
        │  P0-③ sector_mapping.yaml 防幻觉闸门：空 code 按名称补全 ETF 代码，
        │  非法 code 强制清除
        │  事件去重（P1-⑤）：批内 seen 集合 + 库内 uq_sentiment_event(event_hash)
        ▼
financial_sentiment（实体+情感分结构化沉淀）──►  QMT 消费端读取
```

## 3. 数据契约

### 3.1 `raw_data_feed`（原始新闻表）

| 列 | 类型 | 空 | 说明 |
|----|------|----|------|
| id | INT 自增主键 | 否 | 单调递增，可作增量拉取水位 |
| source_type | VARCHAR(50) | 否 | 数据源类型：`hotlist_news` / `rss_feed` / `xueqiu_v_dynamic` 等 |
| content | TEXT | 否 | 原始内容（标题/摘要） |
| url | VARCHAR(1024) | 是 | 内容链接 |
| source_id | VARCHAR(100) | 否 | 来源 ID（如 `cls-hot`、`wallstreetcn-hot`） |
| source_name | VARCHAR(200) | 是 | 来源名称 |
| related_tickers | JSON (TEXT) | 是 | 关联股票代码列表（JSON 数组） |
| additional_data | JSON (TEXT) | 是 | 附加数据（`rank`/`ranks`/`crawl_time` 等） |
| content_hash | CHAR(64) | 否 | 内容 sha256，参与来源级去重 |
| created_at | DATETIME | 否 | 创建时间（**UTC**） |
| updated_at | DATETIME | 是 | 更新时间（**UTC**） |

索引：`idx_source_type_created`、`idx_source_id_created`、`idx_created_at`；
唯一键：`uq_raw_dedup (source_type, source_id, content_hash)`——同一来源下相同内容
跨轮次只保留一条（最早一条）。

### 3.2 `financial_sentiment`（实体+情感分表，核心消费表）

| 列 | 类型 | 空 | 说明 |
|----|------|----|------|
| id | INT 自增主键 | 否 | 单调递增，**推荐作增量拉取水位** |
| stock_name | VARCHAR(200) | 否 | 实体名称（个股名/ETF 名/板块主题名，如 `英伟达`、`存储芯片`） |
| stock_code | VARCHAR(50) | 否 | 证券代码，格式规范见 §3.5；无确定代码时可能为 `UNKNOWN`（兜底值） |
| sentiment_score | FLOAT | 否 | 情感评分，范围 **[-1.0, 1.0]**：-1 极度利空 ~ 1 极度利好，0 中性 |
| alert_level | ENUM | 否 | 告警级别：`Low` / `Medium` / `High`（High=重大事件或多条新闻共振） |
| summary_event | TEXT | 是 | 事件摘要（一句话概括驱动事件，≤60 字，基于新闻原文） |
| event_hash | CHAR(64) | 是 | 事件哈希（P1-⑤），去重语义见 §4；无有效事件键时为 NULL |
| raw_data_id | INT FK→raw_data_feed.id | 是 | 溯源：该实体提取自哪条原始新闻（原始数据删除时置 NULL，`ondelete=SET NULL`） |
| analysis_metadata | JSON (TEXT) | 是 | 分析元数据，契约见 §3.3 |
| created_at | DATETIME | 否 | 创建时间（**UTC**） |
| updated_at | DATETIME | 是 | 更新时间（**UTC**） |

索引：`idx_stock_code_created`、`idx_alert_level_created`、`idx_raw_data_id`、
`idx_sentiment_score`、`idx_created_at`；唯一键：`uq_sentiment_event (event_hash)`。

查询维度（与 MCP `mysql_describe_schema` 自描述一致）：
`stock_code` / `stock_name`(模糊) / `alert_level` / 评分范围 / 时间范围。

### 3.3 `analysis_metadata` JSON 契约

写入侧由 `pipeline.process_ai_analysis()` 生成，键集合固定如下：

| 键 | 类型 | 说明 |
|----|------|------|
| context | string | 原文关键句摘录（≤100 字，直接摘录或最小化改写）——**事件判重锚点** |
| source_text | string | 预留字段；当前提示词不输出，恒为 `''`（读取时勿依赖） |
| confidence | float/null | 提取置信度；当前提示词不输出，通常为 `null`（读取时勿依赖） |

> 兼容性提示：`context` 为稳定输出；`source_text`/`confidence` 为预留位，
> 消费端**必须容忍缺失或 null**。该 JSON 以 TEXT 存储，读取方需自行 `JSON_PARSE`。

### 3.4 枚举与取值范围

| 字段 | 取值 | 说明 |
|------|------|------|
| alert_level | `Low` / `Medium` / `High` | 一般提及 / 明确事件驱动 / 重大事件或多条共振 |
| sentiment_score | [-1.0, 1.0] 连续值 | 越界值由写入侧裁剪；建议消费端按阈值分桶（如 ≤-0.6 强利空、≥0.6 强利好） |
| stock_code | 见 §3.5 | A股/ETF/港股为纯数字，美股为字母 ticker，兜底 `UNKNOWN` |

> 枚举防御建议：消费端对 `alert_level` 用白名单匹配（未来可能扩展级别），
> 对未知值按 `Low` 处理并告警。

### 3.5 证券代码格式规范（重要）

`stock_code` 的格式由提取提示词与防幻觉闸门共同保证：

| 市场 | 格式 | 示例 |
|------|------|------|
| A股个股/ETF | **6 位数字，无交易所后缀** | `600000`、`300308`、`688981`、`512480` |
| 港股 | 5 位数字 | `00700` |
| 美股 | 字母 ticker | `AAPL`、`NVDA` |
| 板块/主题（无确定代码） | 空串 → 入库时兜底为 `UNKNOWN` | `UNKNOWN`（配合 `stock_name` 使用） |

**QMT/xtquant 后缀映射建议**（消费端职责，本系统不产出带后缀代码；
映射后请以 xtquant 官方代码规范核对）：

| 前缀规则 | 建议后缀 | 示例 |
|----------|----------|------|
| `6` 开头（沪 A/科创） | `.SH` | `600000` → `600000.SH` |
| `0` / `3` 开头（深 A/创业） | `.SZ` | `300308` → `300308.SZ` |
| `4` / `8` 开头（北交所） | `.BJ` | `830799` → `830799.BJ` |
| `5` 开头（沪 ETF） | `.SH` | `512480` → `512480.SH` |
| `1` 开头（深 ETF） | `.SZ` | `159995` → `159995.SZ` |
| 美股 ticker | 以 xtquant 规范为准 | 保持原样或按券商配置映射 |
| `UNKNOWN` | 无法映射 | 仅可按 `stock_name` 人工/词表映射（可参考 `config/sector_mapping.yaml`） |

### 3.6 时间与时区

- 所有 `created_at` / `updated_at` 均为 **UTC**（Python `datetime.utcnow` 落库）；
- 北京时间 = UTC + 8；消费端展示或对齐交易时段时自行换算；
- 增量拉取做时间比较时，注意 MySQL 连接会话时区不影响 DATETIME 字面值
  （存的是 UTC 裸时间），SQL 中用 `UTC_TIMESTAMP()` 或传 UTC 参数比较。

## 4. 去重语义（P1-⑤）

消费端需要理解的两组去重键：

### 4.1 原始层 `uq_raw_dedup`
`(source_type, source_id, content_hash)` 唯一 —— 同一来源下相同标题/摘要
跨轮次只保留最早一条。**影响**：`raw_data_feed` 中不会看到"同来源同内容"的
重复新闻，轮次间可安全全量扫描。

### 4.2 事件层 `uq_sentiment_event`
`(event_hash)` 唯一 —— 同一 **(实体, 事件文本)** 组合跨采集轮次只保留
最早一条记录。计算规则（单一事实源：`FinancialSentiment.compute_event_hash`）：

```
事件键 = sha256( 实体标识 + \x1f + 归一化事件文本 )
实体标识   = stock_code（去空白后大写）优先，为空回退 stock_name（大写）
归一化文本 = 连续空白（含换行/制表）压缩为单个空格
事件文本   = analysis_metadata.context 优先（原文关键句摘录，跨轮稳定），
             缺失时回退 summary_event
```

**消费端影响**：
1. 同一实体对同一事件的"重复提及"不会产生重复行——轮次间增量拉取
   （§5.1 水位法）天然安全，无需再按 (code, 事件) 二次判重；
2. 同一实体在**不同事件**下会各保留一条（键含事件文本）；
3. `event_hash IS NULL` 表示实体或事件文本缺失（无法判重），**不参与唯一键**，
   可能为语义重复，消费端如需强一致可自行按 (stock_code, summary_event) 辅助判重；
4. 写入侧批内与库内（1062）双层去重均**保留最早记录**，与 `raw_data_feed` 行为一致。

## 5. 读取接口

### 5.1 通道一：直连 MySQL（QMT 策略端推荐）

**只读账号**（消费端仅授 SELECT）：

```sql
CREATE USER 'qmt_reader'@'%' IDENTIFIED BY '强密码';
GRANT SELECT ON trendradar.* TO 'qmt_reader'@'%';
FLUSH PRIVILEGES;
```

**增量拉取（水位法，幂等且 O(新行)）**——消费端持久化上次最大 `id`：

```sql
-- 每次轮询：取水位之后的全部新事件（id 单调递增，走主键顺序扫描）
SELECT id, stock_name, stock_code, sentiment_score, alert_level,
       summary_event, event_hash, raw_data_id, analysis_metadata, created_at
FROM financial_sentiment
WHERE id > :last_watermark_id
ORDER BY id ASC
LIMIT 500;
```

**高告警订阅**（走 `idx_alert_level_created`）：

```sql
SELECT fs.id, fs.stock_code, fs.stock_name, fs.sentiment_score,
       fs.summary_event, fs.created_at,
       JSON_UNQUOTE(JSON_EXTRACT(fs.analysis_metadata, '$.context')) AS context,
       rd.content AS source_content, rd.url
FROM financial_sentiment fs
LEFT JOIN raw_data_feed rd ON rd.id = fs.raw_data_id
WHERE fs.alert_level = 'High'
  AND fs.created_at >= UTC_TIMESTAMP() - INTERVAL 1 DAY
ORDER BY fs.created_at DESC;
```

**实体情绪时间线**（走 `idx_stock_code_created`）：

```sql
SELECT stock_code, stock_name, sentiment_score, summary_event, created_at
FROM financial_sentiment
WHERE stock_code = '300308'
ORDER BY created_at DESC
LIMIT 50;
```

**轮询节奏建议**：与 `schedule.enabled` 的调度轮次对齐（如 `morning_evening`
预设对应早/晚两个波次），轮次后延迟 1–2 分钟轮询；水位法保证重复轮询无副作用。

### 5.2 通道二：MCP 只读工具（Agent/LLM 场景）

`mcp_server/server.py` 暴露的 `mysql_*` 只读工具（返回 JSON，`limit` 收敛到 [1, 200]）：

| 工具名 | 关键参数 | 说明 |
|--------|----------|------|
| `mysql_describe_schema` | — | 表结构/字段/查询维度自描述（Agent 查询起点） |
| `mysql_search_raw_data` | source_type, source_id, keyword, start_date, end_date, limit | 检索原始新闻（keyword 对 content 模糊匹配） |
| `mysql_recent_news` | source_type, limit | 最近抓取新闻（created_at 倒序） |
| `mysql_search_sentiments` | stock_code, stock_name, alert_level, min_sentiment, max_sentiment, start_date, end_date, limit | 多条件检索情感记录 |
| `mysql_top_stocks` | limit, horizon_days | 近 N 天平均情感分最正面股票聚合 TOP |
| `mysql_get_sentiment_by_id` | sentiment_id | 单条情感记录详情 |

日期参数支持 `2026-08-01`、`2026-08-01T12:00:00` 等 ISO 格式；
返回统一为 `{"success": true, "total": n, "items": [...]}`（或 `found`/`item`）。

### 5.3 通道三：MySQLReader Python API（同机集成）

```python
from trendradar.storage.mysql_reader import MySQLReader

reader = MySQLReader(host="localhost", port=3306,
                     username="qmt_reader", password="***",
                     database="trendradar")
rows = reader.search_sentiments(stock_code="300308", limit=20)
top  = reader.top_stocks(limit=5, horizon_days=7)
reader.close()  # 释放连接池
```

连接参数优先级：`MYSQL_*` 环境变量 > `config/config.yaml` 的 `storage.mysql` > 默认值。

## 6. 消费端实现建议

1. **水位持久化**：`last_watermark_id` 落盘（如 JSON 文件），进程重启续传；
2. **只读隔离**：仅授 SELECT 的独立账号，杜绝消费端误写；
3. **容错**：连接失败/超时按指数退避重试，不阻塞策略主流程；
4. **解析防御**：`analysis_metadata` 为 TEXT 存 JSON，解析失败按 NULL 处理；
   `stock_code='UNKNOWN'` 的记录先经 `stock_name` 词表映射再下单；
5. **风控复核**：LLM 提取存在非确定性（幻觉/漏提可能），情感分仅作参考信号，
   下单前建议叠加价格/成交量等行情侧确认；本数据**不构成投资建议**。

## 7. 兼容性承诺

- 列只增不改：已有列的名称/语义/类型不变；新增列以 nullable 或带默认值加入；
- 枚举可能扩展：`alert_level` 未来或新增级别，消费端用白名单兜底；
- `source_type` 会随接入数据源增加（新值不断追加），按前缀归类而非穷举；
- 去重键语义（§4）为稳定承诺：`event_hash` 计算规则变更视为破坏性变更，
  将升版并在本文件标注。

---
*维护约定：修改 `trendradar/storage/mysql_models.py` 表结构、
`mcp_server/server.py` 的 `mysql_*` 工具清单或 `config/ai_sentiment_prompt.txt`
输出字段时，必须同步更新本文档——`tests/test_qmt_consumer_spec.py`
会校验文档与代码一致（防漂移）。*


