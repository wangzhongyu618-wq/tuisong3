# coding=utf-8
"""雪球大V动态抓取器

说明：
- 继承现有抓取器约定，负责抓取特定雪球主页的最新动态
- 使用 Selenium + Headless Chrome 获取页面真实渲染结果
- 解析最新发帖文本与时间戳
- 将增量数据转换为标准新闻记录后入库并触发后续流水线
"""

import json
import logging
import os
import random
import re
import shutil
import tempfile
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

try:
    from selenium import webdriver
    from selenium.webdriver import ChromeOptions
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
except ImportError:  # pragma: no cover
    webdriver = None
    ChromeOptions = None
    By = None
    WebDriverWait = None
    EC = None


class XueqiuSeleniumFetcher:
    """雪球大V动态抓取器

    兼容现有趋势抓取方案，使用 Selenium 无头浏览器获取目标主页的最新动态。
    """

    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Upgrade-Insecure-Requests": "1",
    }

    def __init__(
        self,
        target_url: Optional[str] = None,
        headless: bool = True,
        page_load_timeout: int = 25,
        wait_min: int = 2,
        wait_max: int = 5,
        executable_path: Optional[str] = None,
        cookies: Optional[str] = None,
    ):
        self.target_url = target_url
        self.headless = headless
        self.page_load_timeout = page_load_timeout
        self.wait_min = wait_min
        self.wait_max = wait_max
        self.executable_path = executable_path
        # 登录态 Cookie：显式参数优先；未传/留空时回读环境变量 XUEQIU_COOKIES。
        # 支持格式：浏览器 Cookie 请求头串（"k1=v1; k2=v2"）或 JSON（{"k1":"v1"}）。
        self.cookies = cookies if cookies else os.environ.get("XUEQIU_COOKIES", "")
        # 最近一次 fetch_latest_posts 的登录态判定（监控/告警用）：
        # "ok"=页面正常 | "login_wall"=被重定向登录页(cookie 失效) |
        # "no_cookies"=未配置 cookie | "error"=抓取异常 | "unknown"=尚未运行
        self.last_login_state = "unknown"

    def _random_wait(self) -> None:
        """随机等待 2-5 秒，避免反爬"""
        time.sleep(random.uniform(self.wait_min, self.wait_max))

    def _create_driver(self):
        if webdriver is None or ChromeOptions is None:
            raise RuntimeError("未安装 selenium，无法创建无头浏览器驱动")

        options = ChromeOptions()
        options.add_argument("--headless=new") if self.headless else None
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1440,2200")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-extensions")
        options.add_argument(f"--user-agent={self.DEFAULT_HEADERS['User-Agent']}")
        options.add_argument("--lang=zh-CN,zh")
        # 独立临时 profile：避免与本机正在运行的 Chrome 实例冲突
        # （Windows 下复用默认 profile 会导致 headless 启动即崩溃：
        #   session not created: DevToolsActivePort file doesn't exist）
        self._user_data_dir = tempfile.mkdtemp(prefix="xq_chrome_")
        options.add_argument(f"--user-data-dir={self._user_data_dir}")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        # Linux/容器环境兜底：Debian 等发行版的浏览器叫 chromium 且不在 selenium
        # manager 的默认探测路径，不显式指定 binary 会在启动时报 cannot find chrome
        # binary。按常见命名顺序定位；Windows 上 chrome 不在 PATH，保持原行为
        # （由 selenium manager 处理）。
        if not getattr(options, "binary_location", ""):
            for _name in (
                "chrome",
                "google-chrome",
                "google-chrome-stable",
                "chromium",
                "chromium-browser",
            ):
                _found = shutil.which(_name)
                if _found:
                    options.binary_location = _found
                    break

        if self.executable_path:
            # selenium 4.10+ 已移除 executable_path 参数，改用 Service 指定驱动路径
            from selenium.webdriver.chrome.service import Service

            try:
                return webdriver.Chrome(options=options, service=Service(self.executable_path))
            except Exception as exc:
                raise RuntimeError(self._driver_error_hint(exc)) from exc
        # 未指定驱动路径时交给 selenium manager 自动匹配
        try:
            return webdriver.Chrome(options=options)
        except Exception as exc:
            raise RuntimeError(self._driver_error_hint(exc)) from exc

    @staticmethod
    def _driver_error_hint(exc: Exception) -> str:
        """把浏览器驱动启动异常翻译成带修复建议的提示。

        chromedriver 与 Chrome 版本漂移、驱动缺失、Chrome 未安装、
        selenium manager 网络受限是最常见的四类问题，报错原文是英文
        且看不出修复路径，这里按关键词归类给出可操作建议（保留原文）。
        """
        raw = str(exc) or exc.__class__.__name__
        low = raw.lower()
        original = f"原始错误: {raw}"
        if "session not created" in low or "only supports chrome version" in low:
            return (
                "chromedriver 与本机 Chrome 版本不匹配（session not created）。"
                "请升级 chromedriver 与 Chrome 至匹配版本，或在 config xueqiu.executable_path"
                f"（或环境变量 XUEQIU_EXECUTABLE_PATH）显式指定可用驱动路径。{original}"
            )
        if "cannot find chrome binary" in low or "chrome failed to start" in low:
            return (
                "未找到本机 Chrome 浏览器或浏览器启动失败。"
                f"请安装 Chrome/Chromium（Linux 服务器请确认浏览器路径）。{original}"
            )
        if "chromedriver" in low and (
            "executable needs to be in path" in low or "no such file" in low
        ):
            return (
                "未找到 chromedriver 可执行文件。请安装与 Chrome 版本匹配的 chromedriver，"
                "并通过 config xueqiu.executable_path"
                f"（或环境变量 XUEQIU_EXECUTABLE_PATH）指定路径。{original}"
            )
        if "selenium manager" in low or "error managing" in low or "cannot be downloaded" in low:
            return (
                "selenium manager 自动获取 chromedriver 失败（常见于网络受限环境）。"
                "请手动下载与 Chrome 匹配的 chromedriver，并在 config xueqiu.executable_path"
                f"（或环境变量 XUEQIU_EXECUTABLE_PATH）指定路径。{original}"
            )
        return f"浏览器驱动启动失败。{original}"

    @staticmethod
    def _parse_cookies(raw: str) -> List[Tuple[str, str]]:
        """解析 Cookie 配置字符串。

        支持两种格式：
        - 浏览器 Cookie 请求头串："xq_a_token=abc; xqat=def"
        - JSON 对象：'{"xq_a_token": "abc", "xqat": "def"}'

        无效片段（无 name/value）自动忽略，返回 (name, value) 列表。
        """
        raw = (raw or "").strip()
        if not raw:
            return []

        # 格式 2：JSON 对象 / 数组
        if raw.startswith("{") or raw.startswith("["):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("[雪球] cookies 配置以 { 或 [ 开头但不是合法 JSON，将按普通 Cookie 串解析")
            else:
                pairs: List[Tuple[str, str]] = []
                if isinstance(data, dict):
                    for name, value in data.items():
                        if str(name).strip() and str(value).strip():
                            pairs.append((str(name).strip(), str(value).strip()))
                elif isinstance(data, list):
                    for item in data:
                        if not isinstance(item, dict):
                            continue
                        name = str(item.get("name", "")).strip()
                        value = str(item.get("value", "")).strip()
                        if name and value:
                            pairs.append((name, value))
                return pairs

        # 格式 1：浏览器 Cookie 请求头串
        pairs = []
        for segment in raw.split(";"):
            if "=" not in segment:
                continue
            name, value = segment.split("=", 1)
            name = name.strip()
            value = value.strip()
            if name and value:
                pairs.append((name, value))
        return pairs

    def _wait_page_ready(self, driver) -> None:
        """等待页面 document.readyState 加载完成。"""
        WebDriverWait(driver, self.page_load_timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

    def _inject_cookies(self, driver) -> int:
        """向驱动注入登录态 Cookie。

        Selenium 要求先在目标域下打开页面才能 add_cookie，
        因此先访问雪球首页完成 Cookie 预置，再由调用方导航到目标页。

        Returns:
            实际注入成功的 cookie 条数。
        """
        cookie_pairs = self._parse_cookies(self.cookies)
        if not cookie_pairs:
            return 0

        # 先落雪球域首页，否则 add_cookie 会抛 InvalidCookieDomainException
        driver.get("https://xueqiu.com/")
        self._wait_page_ready(driver)

        injected = 0
        for name, value in cookie_pairs:
            try:
                driver.add_cookie({"name": name, "value": value, "domain": ".xueqiu.com"})
                injected += 1
            except Exception as exc:  # pragma: no cover - 单条 cookie 异常不应中断整轮
                logger.warning(f"[雪球] cookie 注入失败（已跳过）: {name}: {exc}")
        self._random_wait()

        # 登录态校验：雪球会话依赖 xq_a_token / xqat 等关键 cookie
        try:
            present = {str(c.get("name", "")) for c in driver.get_cookies()}
            key_cookies = {"xq_a_token", "xqat", "xq_r_token", "xq_id_token", "xq_token_id"}
            hits = sorted(key_cookies & present)
            if hits:
                logger.info(f"[雪球] 登录态 cookie 注入成功: 共 {injected} 条，命中关键会话 {hits}")
            else:
                logger.warning(
                    f"[雪球] 已注入 {injected} 条 cookie，但未检测到关键会话 cookie"
                    f"（xq_a_token/xqat 等），登录态可能无效"
                )
        except Exception:  # pragma: no cover - 校验失败不影响主流程
            pass
        return injected

    def _warn_if_login_wall(self, driver) -> bool:
        """目标页加载后检测是否被重定向到登录页（cookie 失效/未生效）。

        Returns:
            True 表示命中登录墙（登录态已失效）；False 表示页面正常或无法判定。
        """
        try:
            current = (driver.current_url or "").lower()
            if "signin" in current or "login" in current:
                logger.warning("[雪球] 页面被重定向至登录页，登录态 cookie 可能已失效，请更新 XUEQIU_COOKIES")
                return True
        except Exception:  # pragma: no cover
            pass
        return False

    def _extract_source_identity(self, target_url: str) -> Dict[str, str]:
        parsed = urlparse(target_url)
        path_parts = [p for p in parsed.path.split("/") if p]
        source_id = "xueqiu_user"
        source_name = "雪球用户"
        if path_parts:
            source_id = path_parts[0] if path_parts[0] not in {"u", "user"} else (path_parts[1] if len(path_parts) > 1 else source_id)
            source_name = source_id
        return {"source_id": source_id, "source_name": source_name}

    def _extract_post_candidates(self, driver) -> List[Dict[str, Any]]:
        # 注意 r""" 原始字符串：JS 里的 \s 等正则转义不能被 Python 再解释，
        # 否则触发 DeprecationWarning: invalid escape sequence
        script = r"""
        (() => {
            const results = [];
            const seen = new Set();
            const addCandidate = (text, timestamp) => {
                const normalized = (text || '').replace(/\s+/g, ' ').trim();
                if (!normalized) return;
                const key = normalized.slice(0, 120) + '|' + (timestamp || '');
                if (seen.has(key)) return;
                seen.add(key);
                results.push({ text: normalized, timestamp: timestamp || '' });
            };

            const fromDom = () => {
                const selectors = [
                    '[data-status-id]',
                    '[class*="timeline__item__content"]',
                    '.status-item',
                    '.status__item',
                    '.status-content',
                    '.status-content__text',
                    '.status-main',
                ];

                for (const selector of selectors) {
                    const nodes = document.querySelectorAll(selector);
                    for (const node of nodes) {
                        const textNode = node.innerText || node.textContent || '';
                        const text = String(textNode).replace(/\s+/g, ' ').trim();
                        if (!text || text.length < 8) continue;
                        let timestamp = '';
                        const timeNode = node.querySelector('time, .time, .status-time, [datetime]');
                        if (timeNode) {
                            timestamp = timeNode.getAttribute('datetime') || timeNode.textContent || timeNode.innerText || '';
                        }
                        if (!timestamp) {
                            // 精确匹配帖子容器（注意：[class*="timeline__item"] 会命中 content 节点自身）
                            const parent = node.closest('.timeline__item') || node.parentElement || document.body;
                            const candidate = parent.querySelector('time, .time, .status-time, [datetime], .date-and-source');
                            if (candidate) {
                                timestamp = (candidate.getAttribute('datetime') || candidate.textContent || candidate.innerText || '')
                                    .replace(/来自雪球/g, '')
                                    .replace(/[·•]/g, ' ')
                                    .replace(/\s+/g, ' ')
                                    .trim();
                            }
                        }
                        addCandidate(text, timestamp);
                    }
                }

                // 宽泛兜底：无具体 selector 命中时才尝试 article（避免与具体节点重复计入）
                if (!results.length) {
                    for (const node of document.querySelectorAll('article')) {
                        const textNode = node.innerText || node.textContent || '';
                        const text = String(textNode).replace(/\s+/g, ' ').trim();
                        if (!text || text.length < 8) continue;
                        let timestamp = '';
                        const timeNode = node.querySelector('time, .time, .status-time, [datetime]');
                        if (timeNode) {
                            timestamp = timeNode.getAttribute('datetime') || timeNode.textContent || timeNode.innerText || '';
                        }
                        addCandidate(text, timestamp);
                    }
                }
            };

            const fromState = () => {
                const state = window.__INITIAL_STATE__ || window.__NEXT_DATA__ || {};
                const stack = [state];
                while (stack.length) {
                    const current = stack.pop();
                    if (!current || typeof current !== 'object') continue;
                    if (Array.isArray(current)) {
                        for (const item of current) stack.push(item);
                        continue;
                    }
                    const keys = Object.keys(current);
                    for (const key of keys) {
                        const value = current[key];
                        if (typeof value === 'string' && value.length > 8 && value.length < 500 && !key.toLowerCase().includes('image')) {
                            if (/\d{4}-\d{2}-\d{2}|\d{4}\/\d{2}\/\d{2}|\d{2}:\d{2}/.test(value)) continue;
                            addCandidate(value, '');
                        }
                        if (value && typeof value === 'object') stack.push(value);
                    }
                }
            };

            fromDom();
            fromState();
            return results.slice(0, 15);
        })()
        """
        try:
            # 首页/时间线为异步渲染：无结果时轮询重试（页面可能仍在加载帖子流）
            # 注意：script 是裸 IIFE，必须用括号包裹后 return，否则 execute_script 恒返回 None
            data = []
            for attempt in range(5):
                data = driver.execute_script("return (" + script + ")") or []
                if data:
                    break
                time.sleep(2)
        except Exception as exc:  # pragma: no cover
            logger.warning(f"[雪球] 提取动态失败: {exc}")
            return []

        results: List[Dict[str, Any]] = []
        for item in data or []:
            text = self._clean_text(item.get("text", ""))
            if not text:
                continue
            timestamp = self._clean_text(item.get("timestamp", "")) or self._extract_timestamp_from_text(text)
            results.append({
                "content": text,
                # 相对时间在抓取时刻锚定为绝对时间；无法识别时保留原文
                "published_at": self._parse_relative_time(timestamp) if timestamp else "",
            })
        return results

    @staticmethod
    def _clean_text(value: Any) -> str:
        if value is None:
            return ""
        return re.sub(r"\s+", " ", str(value)).strip()

    @staticmethod
    def _extract_timestamp_from_text(text: str) -> str:
        match = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}[\s]+\d{1,2}:\d{2}:?\d{0,2}|\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}:\d{2})", text)
        return match.group(0) if match else ""

    @staticmethod
    def _parse_relative_time(text: str, now: Optional[datetime] = None) -> str:
        """把雪球站内时间文本解析为带本地时区的绝对时间（ISO 格式）。

        页面上的时间是相对/简化形式（"3小时前"、"昨天 14:30"、"09-07"），
        存库后无法再换算，必须在抓取时刻锚定为绝对时间。输出带时区偏移
        （如 2026-09-07T10:30:00+08:00），避免下游 format_iso_time_friendly
        把 naive 时间误当作 UTC 造成 8 小时错位。

        支持：刚刚 / N分钟前 / N小时前 / (今天|昨天) HH:MM / MM-DD( HH:MM)?
        / yyyy-MM-dd( HH:MM(:SS)?)?（兼容 / 分隔）/ HH:MM；
        自动剥离"编辑于"前缀与"来自xx"后缀。
        无法识别时原样返回，宁可保留原文也不丢信息。
        """
        raw = (text or "").strip()
        if not raw:
            return ""
        cleaned = re.sub(r"^(?:编辑|修改)于\s*", "", raw)
        cleaned = re.sub(r"\s*来自\S*$", "", cleaned).strip()
        anchor = now or datetime.now().astimezone()

        def _fmt(dt: datetime) -> str:
            return dt.replace(microsecond=0).isoformat()

        try:
            if cleaned == "刚刚":
                return _fmt(anchor)
            m = re.fullmatch(r"(\d+)\s*分钟前", cleaned)
            if m:
                return _fmt(anchor - timedelta(minutes=int(m.group(1))))
            m = re.fullmatch(r"(\d+)\s*小时前", cleaned)
            if m:
                return _fmt(anchor - timedelta(hours=int(m.group(1))))
            m = re.fullmatch(r"(今天|昨天)\s*(\d{1,2}):(\d{2})", cleaned)
            if m:
                day = anchor.date() - timedelta(days=1 if m.group(1) == "昨天" else 0)
                return _fmt(datetime(day.year, day.month, day.day,
                                     int(m.group(2)), int(m.group(3)), tzinfo=anchor.tzinfo))
            m = re.fullmatch(r"(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", cleaned)
            if m:
                return _fmt(datetime(anchor.year, int(m.group(1)), int(m.group(2)),
                                     int(m.group(3) or 0), int(m.group(4) or 0), tzinfo=anchor.tzinfo))
            m = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", cleaned)
            if m:
                return _fmt(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                                     int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0),
                                     tzinfo=anchor.tzinfo))
            m = re.fullmatch(r"(\d{1,2}):(\d{2})", cleaned)
            if m:
                return _fmt(datetime(anchor.year, anchor.month, anchor.day,
                                     int(m.group(1)), int(m.group(2)), tzinfo=anchor.tzinfo))
        except (ValueError, OverflowError):
            return raw
        return raw

    def fetch_latest_posts(
        self,
        target_url: Optional[str] = None,
        max_posts: int = 10,
    ) -> List[Dict[str, Any]]:
        """抓取目标主页最新发帖文本和时间戳"""
        url = target_url or self.target_url
        if not url:
            raise ValueError("请提供目标主页 URL")

        driver = self._create_driver()
        try:
            if self.cookies:
                # 登录态注入：失败时降级为未登录态继续，不中断抓取
                try:
                    self._inject_cookies(driver)
                except Exception as exc:
                    logger.warning(f"[雪球] cookie 注入流程异常，将以未登录态继续: {exc}")
            else:
                self.last_login_state = "no_cookies"

            driver.get(url)
            self._random_wait()
            self._wait_page_ready(driver)
            self._random_wait()
            if self.cookies:
                # 登录墙判定：cookie 失效会被重定向到登录页，静默降级必须显性化
                self.last_login_state = (
                    "login_wall" if self._warn_if_login_wall(driver) else "ok"
                )
            posts = self._extract_post_candidates(driver)
            return posts[:max_posts]
        except Exception as exc:  # pragma: no cover
            self.last_login_state = "error"
            logger.error(f"[雪球] 抓取失败: {exc}", exc_info=True)
            return []
        finally:
            try:
                driver.quit()
            except Exception:  # pragma: no cover
                pass
            # 清理本次会话使用的临时 profile 目录
            shutil.rmtree(getattr(self, "_user_data_dir", ""), ignore_errors=True)

    def fetch_and_store_latest_posts(
        self,
        target_url: Optional[str] = None,
        max_posts: int = 10,
        source_name: Optional[str] = None,
        source_id: Optional[str] = None,
        mysql_pipeline: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """抓取并写入 MySQL raw_data_feed，并触发后续管道。

        返回格式：
        {
            "stored_count": int,
            "posts": [...],
            "source_id": str,
            "source_name": str,
        }
        """
        url = target_url or self.target_url
        if not url:
            raise ValueError("请提供目标主页 URL")

        posts = self.fetch_latest_posts(url, max_posts=max_posts)
        if not posts:
            return {
                "stored_count": 0,
                "posts": [],
                "source_id": source_id or "xueqiu_user",
                "source_name": source_name or "雪球用户",
                "login_state": self.last_login_state,
            }

        identity = self._extract_source_identity(url)
        final_source_id = source_id or identity["source_id"]
        final_source_name = source_name or identity["source_name"]

        normalized_posts = []
        crawl_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        for index, post in enumerate(posts, 1):
            published_at = post.get("published_at", "")
            content = post.get("content", "")
            normalized_posts.append({
                "title": content,
                "url": url,
                "rank": index,
                "ranks": [index],
                # crawl_time 语义是"抓取时刻"，不承担发布时间（published_at 单独记录）
                "crawl_time": crawl_now,
                "source_type": "xueqiu_v_dynamic",
                "author": final_source_name,
                "published_at": published_at,
            })

        if mysql_pipeline is None:
            try:
                from trendradar.storage.mysql_pipeline import get_mysql_pipeline, init_mysql_pipeline
                try:
                    mysql_pipeline = get_mysql_pipeline()
                except RuntimeError:
                    mysql_pipeline = init_mysql_pipeline()
            except Exception as exc:  # pragma: no cover
                logger.warning(f"[雪球] MySQL 管道初始化失败: {exc}")
                mysql_pipeline = None

        if mysql_pipeline is not None:
            stored_count = mysql_pipeline.ingest_crawled_news(
                normalized_posts,
                source_id=final_source_id,
                source_name=final_source_name,
            )
        else:
            stored_count = 0
            logger.warning("[雪球] 未初始化 MySQL 数据管道，已跳过 raw_data_feed 写入")

        return {
            "stored_count": stored_count,
            "posts": [
                {
                    "content": post.get("content", ""),
                    "published_at": post.get("published_at", ""),
                    "source_type": "xueqiu_v_dynamic",
                    "url": url,
                }
                for post in posts
            ],
            "source_id": final_source_id,
            "source_name": final_source_name,
            "login_state": self.last_login_state,
        }
