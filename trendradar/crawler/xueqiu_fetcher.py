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
import random
import re
import time
from typing import Any, Dict, List, Optional
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
    ):
        self.target_url = target_url
        self.headless = headless
        self.page_load_timeout = page_load_timeout
        self.wait_min = wait_min
        self.wait_max = wait_max
        self.executable_path = executable_path

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
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        try:
            return webdriver.Chrome(options=options, executable_path=self.executable_path)
        except Exception:
            return webdriver.Chrome(options=options)

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
        script = """
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
                    '.status-item',
                    '.status__item',
                    '.status-content',
                    '.status-content__text',
                    '.status-main',
                    '[data-status-id]',
                    'article',
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
                            const parent = node.parentElement || document.body;
                            const candidate = parent.querySelector('time, .time, .status-time, [datetime]');
                            if (candidate) {
                                timestamp = candidate.getAttribute('datetime') || candidate.textContent || candidate.innerText || '';
                            }
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
        })();
        """
        try:
            data = driver.execute_script(script)
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
                "published_at": timestamp,
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
            driver.get(url)
            self._random_wait()
            WebDriverWait(driver, self.page_load_timeout).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            self._random_wait()
            posts = self._extract_post_candidates(driver)
            return posts[:max_posts]
        except Exception as exc:  # pragma: no cover
            logger.error(f"[雪球] 抓取失败: {exc}", exc_info=True)
            return []
        finally:
            try:
                driver.quit()
            except Exception:  # pragma: no cover
                pass

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
            return {"stored_count": 0, "posts": [], "source_id": source_id or "xueqiu_user", "source_name": source_name or "雪球用户"}

        identity = self._extract_source_identity(url)
        final_source_id = source_id or identity["source_id"]
        final_source_name = source_name or identity["source_name"]

        normalized_posts = []
        for index, post in enumerate(posts, 1):
            published_at = post.get("published_at", "")
            content = post.get("content", "")
            normalized_posts.append({
                "title": content,
                "url": url,
                "rank": index,
                "ranks": [index],
                "crawl_time": published_at or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
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
        }
