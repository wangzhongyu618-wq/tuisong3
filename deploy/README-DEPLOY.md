# TrendRadar 部署手册（Windows 本机 / Docker）

> 前置状态：P0-② 雪球模块后续全部完成（cookie 失效告警、独立频控、驱动漂移报错），
> 真实链路已验证：headless Chrome 抓取 xueqiu.com -> MySQL 入库（261 项测试全绿）。

---

## 一、Windows 本机部署（推荐，当前机器已按此方式部署）

### 1. 填写本地密钥 `secrets.env`（项目根目录，已被 .gitignore 排除）

```
XUEQIU_COOKIES=cookiesu=...; device_id=...; xq_a_token=...; xqat=...
AI_API_KEY=你的模型Key
MYSQL_PASSWORD=数据库密码
```

Cookie 获取：浏览器登录雪球 -> F12 -> Network -> 任选一个请求 -> 复制请求头里
`Cookie:` 后面的整串。AI Key / MySQL 密码**不要**写进 config.yaml（该文件会推送 GitHub）。

### 2. 注册定时任务

```powershell
powershell -ExecutionPolicy Bypass -File deploy\install_schedule.ps1
# 默认每 30 分钟一轮；自定义间隔：
powershell -ExecutionPolicy Bypass -File deploy\install_schedule.ps1 -IntervalMinutes 60
```

### 3. 日常运维

| 操作 | 命令 |
|---|---|
| 手动跑一轮 | `schtasks /run /tn TrendRadar` |
| 查看状态 | `schtasks /query /tn TrendRadar /v` |
| 查看日志 | `output\trendradar_task.log`（UTF-8） |
| 卸载 | `powershell -ExecutionPolicy Bypass -File deploy\install_schedule.ps1 -Remove` |

### 4. 工作原理

```
计划任务(每30分钟) -> deploy/run_once.ps1
  -> 加载 secrets.env 为进程环境变量（不落盘、不进 git）
  -> .venv\Scripts\python.exe -m trendradar
     热榜+RSS 抓取 -> 雪球 Selenium 抓取（interval_minutes 频控，默认 120 分钟一次）
     -> AI 分析/实体情感提取 -> 推送渠道 -> MySQL 入库
```

- **频控**：cron 每 30 分钟触发 ≠ 雪球每 30 分钟抓一次；雪球由
  `config.yaml xueqiu.interval_minutes`（或 `XUEQIU_INTERVAL_MINUTES`）独立控制。
- **要求**：Chrome 需图形会话，机器保持登录（锁屏 OK）；任务错过触发点（关机/休眠）
  恢复后会自动补跑（StartWhenAvailable）。
- **告警**：Cookie 失效（login_wall）会推送告警到已配置的通知渠道；驱动版本漂移
  会输出分类的中文修复提示。

---

## 二、Docker 部署（服务器）

1. `cd docker && copy .env.example .env`，填写 `XUEQIU_COOKIES` / `AI_API_KEY` / `MYSQL_PASSWORD`
   （`.env` 已被 .gitignore 与 .dockerignore 排除，只留在服务器本地）
2. 启动：
   ```bash
   docker compose up -d                                # 官方镜像
   docker compose -f docker-compose-build.yml up -d --build   # 自构建
   ```
3. 镜像内已装 `chromium + chromium-driver`，并默认
   `XUEQIU_EXECUTABLE_PATH=/usr/bin/chromedriver`；代码会自动探测
   `/usr/bin/chromium` 作为浏览器 binary。
4. 挂载：`../config`（只读）、`../output`；cron 模式默认 `*/30 * * * *`。
5. 容器连宿主机 MySQL：`MYSQL_HOST=host.docker.internal`；首次启用先初始化表结构：
   `python -m trendradar.storage.mysql_init`

---

## 三、安全公告（务必处理）

历史上真实智谱 API Key 曾提交到公开仓库 `github.com/wangzhongyu618-wq/tuisong3`
（config/config.yaml，现已从工作区移除、改由 secrets.env/环境变量提供）。
**请到智谱开放平台控制台作废该 Key 并更换**。git 历史中的旧 Key 作废后即无风险，
无需重写历史。

## 四、常见问题

- **日志乱码**：日志文件为 UTF-8；用 `code`/VS Code 或 `Get-Content -Encoding UTF8` 查看，
  不要用默认 GBK 的记事本以外的工具误判为程序问题。
- **`--doctor` 在旧版本崩溃**：Windows GBK 重定向下 emoji 输出崩溃，
  已在 `__main__.py` 入口统一 reconfigure 为 UTF-8 修复。
- **雪球提示驱动版本不匹配**：升级 Chrome 与 chromedriver，或设置
  `XUEQIU_EXECUTABLE_PATH`（容器内已默认指定）。
