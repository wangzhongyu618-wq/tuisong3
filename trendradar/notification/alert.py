# coding=utf-8
"""轻量级系统告警推送（与报表推送 dispatch_all 相互独立）

用途：Cookie 失效、数据断流等运维告警——只发一条纯文本消息，
不走报表模板/翻译/分批逻辑。已配置的渠道逐个尝试，未配置跳过；
任何渠道失败只记录日志并返回 False，绝不向调用方抛异常。

覆盖渠道：飞书 / 钉钉 / 企业微信 / Telegram / ntfy / Bark / Slack / 通用 Webhook。
邮件渠道未覆盖（SMTP+MIME 构造较重且告警时效性差），需要邮件告警的用户
可通过 GENERIC_WEBHOOK_URL 桥接。
"""

import logging
from typing import Dict, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)


def _proxies_from_config(config: Dict) -> Optional[Dict[str, str]]:
    """按主程序约定读取代理配置（USE_PROXY + DEFAULT_PROXY）。"""
    if config.get("USE_PROXY"):
        proxy_url = config.get("DEFAULT_PROXY", "")
        if proxy_url:
            return {"http": proxy_url, "https": proxy_url}
    return None


def send_alert(title: str, message: str, config: Dict, timeout: int = 30) -> Dict[str, bool]:
    """向所有已配置渠道发送一条纯文本告警。

    Args:
        title: 告警标题（会拼在正文首行，便于群消息一眼识别）
        message: 告警正文
        config: TrendRadar 扁平配置（与 NotificationDispatcher 同一约定，
                顶层直接读取 FEISHU_WEBHOOK_URL 等键）
        timeout: 单渠道请求超时（秒）

    Returns:
        Dict[str, bool]: 渠道名 → 是否发送成功；未配置任何渠道时返回空 dict。
    """
    results: Dict[str, bool] = {}
    text = f"{title}\n{message}" if title else message
    proxies = _proxies_from_config(config)

    def _post(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            logger.warning(f"[告警] {name} 发送失败: {detail}")
        results[name] = bool(ok)

    # 飞书（纯文本，msg_type: text）
    if config.get("FEISHU_WEBHOOK_URL"):
        try:
            resp = requests.post(
                config["FEISHU_WEBHOOK_URL"],
                json={"msg_type": "text", "content": {"text": text}},
                proxies=proxies, timeout=timeout,
            )
            data = resp.json()
            ok = resp.status_code == 200 and (
                data.get("code") == 0 or data.get("StatusCode") == 0
            )
            _post("feishu", ok, data.get("msg") or data.get("StatusMessage") or "")
        except Exception as exc:
            _post("feishu", False, str(exc))

    # 钉钉（纯文本）
    if config.get("DINGTALK_WEBHOOK_URL"):
        try:
            resp = requests.post(
                config["DINGTALK_WEBHOOK_URL"],
                json={"msgtype": "text", "text": {"content": text}},
                proxies=proxies, timeout=timeout,
            )
            data = resp.json()
            _post(
                "dingtalk",
                resp.status_code == 200 and data.get("errcode") == 0,
                data.get("errmsg", ""),
            )
        except Exception as exc:
            _post("dingtalk", False, str(exc))

    # 企业微信（纯文本）
    if config.get("WEWORK_WEBHOOK_URL"):
        try:
            resp = requests.post(
                config["WEWORK_WEBHOOK_URL"],
                json={"msgtype": "text", "text": {"content": text}},
                proxies=proxies, timeout=timeout,
            )
            data = resp.json()
            _post(
                "wework",
                resp.status_code == 200 and data.get("errcode") == 0,
                data.get("errmsg", ""),
            )
        except Exception as exc:
            _post("wework", False, str(exc))

    # Telegram（需要 bot token 与 chat_id 配对）
    if config.get("TELEGRAM_BOT_TOKEN") and config.get("TELEGRAM_CHAT_ID"):
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{config['TELEGRAM_BOT_TOKEN']}/sendMessage",
                json={
                    "chat_id": config["TELEGRAM_CHAT_ID"],
                    "text": text,
                    "disable_web_page_preview": True,
                },
                proxies=proxies, timeout=timeout,
            )
            data = resp.json()
            _post(
                "telegram",
                resp.status_code == 200 and data.get("ok"),
                data.get("description", ""),
            )
        except Exception as exc:
            _post("telegram", False, str(exc))

    # ntfy（纯文本 body，Title 头用 ASCII 避免 HTTP header 编码问题）
    if config.get("NTFY_SERVER_URL") and config.get("NTFY_TOPIC"):
        try:
            url = f"{config['NTFY_SERVER_URL'].rstrip('/')}/{config['NTFY_TOPIC']}"
            resp = requests.post(
                url,
                data=text.encode("utf-8"),
                headers={"Title": "TrendRadar Alert", "Priority": "high", "Tags": "warning"},
                proxies=proxies, timeout=timeout,
            )
            _post("ntfy", resp.status_code == 200, f"status={resp.status_code}")
        except Exception as exc:
            _post("ntfy", False, str(exc))

    # Bark（iOS 推送）
    if config.get("BARK_URL"):
        try:
            parsed = urlparse(config["BARK_URL"])
            device_key = parsed.path.strip("/").split("/")[0] if parsed.path else ""
            if device_key:
                resp = requests.post(
                    f"{parsed.scheme}://{parsed.netloc}/push",
                    json={
                        "title": title,
                        "body": message,
                        "device_key": device_key,
                        "sound": "default",
                        "group": "TrendRadar",
                    },
                    proxies=proxies, timeout=timeout,
                )
                data = resp.json()
                _post(
                    "bark",
                    resp.status_code == 200 and data.get("code") == 200,
                    data.get("message", ""),
                )
            else:
                _post("bark", False, f"无法从 URL 提取 device_key: {config['BARK_URL']}")
        except Exception as exc:
            _post("bark", False, str(exc))

    # Slack
    if config.get("SLACK_WEBHOOK_URL"):
        try:
            resp = requests.post(
                config["SLACK_WEBHOOK_URL"],
                json={"text": text},
                proxies=proxies, timeout=timeout,
            )
            _post("slack", resp.status_code == 200 and resp.text == "ok", resp.text)
        except Exception as exc:
            _post("slack", False, str(exc))

    # 通用 Webhook
    if config.get("GENERIC_WEBHOOK_URL"):
        try:
            resp = requests.post(
                config["GENERIC_WEBHOOK_URL"],
                headers={"Content-Type": "application/json"},
                json={"title": title, "content": message},
                proxies=proxies, timeout=timeout,
            )
            _post(
                "generic_webhook",
                200 <= resp.status_code < 300,
                f"status={resp.status_code}",
            )
        except Exception as exc:
            _post("generic_webhook", False, str(exc))

    return results
