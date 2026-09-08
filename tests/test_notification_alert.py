# coding=utf-8
"""trendradar/notification/alert.py 轻量告警推送单元测试

覆盖：
- 渠道选择：未配置渠道跳过（不发请求）；Telegram 配对校验（缺一不发）
- 载荷：飞书/钉钉/企微纯文本结构、Telegram URL、通用 Webhook 字段
- 容错：渠道异常被吞掉返回 False，绝不向调用方抛异常
- 判定：业务码非 0 / 非 2xx 标记失败
"""

import trendradar.notification.alert as alert_module
from trendradar.notification.alert import send_alert


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


def test_send_alert_skips_unconfigured_channels(monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("未配置渠道不应发起任何请求")

    monkeypatch.setattr(alert_module.requests, "post", _fail)

    assert send_alert("标题", "内容", {}) == {}
    # 只有 token 没有 chat_id：Telegram 不发送（配对校验）
    assert send_alert("标题", "内容", {"TELEGRAM_BOT_TOKEN": "tok"}) == {}


def test_send_alert_feishu_text_payload(monkeypatch):
    calls = []

    def fake_post(url, json=None, **kwargs):
        calls.append({"url": url, "payload": json})
        return _FakeResponse(200, {"code": 0})

    monkeypatch.setattr(alert_module.requests, "post", fake_post)
    results = send_alert("标题", "正文行", {"FEISHU_WEBHOOK_URL": "https://feishu/hook"})

    assert results == {"feishu": True}
    assert calls[0]["url"] == "https://feishu/hook"
    payload = calls[0]["payload"]
    assert payload["msg_type"] == "text"
    assert "标题" in payload["content"]["text"]
    assert "正文行" in payload["content"]["text"]


def test_send_alert_dingtalk_and_wework_text_mode(monkeypatch):
    calls = []

    def fake_post(url, json=None, **kwargs):
        calls.append({"url": url, "payload": json})
        return _FakeResponse(200, {"errcode": 0, "errmsg": "ok"})

    monkeypatch.setattr(alert_module.requests, "post", fake_post)
    config = {
        "DINGTALK_WEBHOOK_URL": "https://ding",
        "WEWORK_WEBHOOK_URL": "https://wework",
    }
    results = send_alert("标题", "内容", config)

    assert results == {"dingtalk": True, "wework": True}
    assert len(calls) == 2
    for call in calls:
        assert call["payload"]["msgtype"] == "text"
        assert "标题" in call["payload"]["text"]["content"]


def test_send_alert_telegram_url_and_payload(monkeypatch):
    calls = []

    def fake_post(url, json=None, **kwargs):
        calls.append({"url": url, "payload": json})
        return _FakeResponse(200, {"ok": True})

    monkeypatch.setattr(alert_module.requests, "post", fake_post)
    config = {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "chat1"}
    results = send_alert("标题", "内容", config)

    assert results == {"telegram": True}
    assert calls[0]["url"] == "https://api.telegram.org/bottok/sendMessage"
    assert calls[0]["payload"]["chat_id"] == "chat1"


def test_send_alert_generic_webhook_payload(monkeypatch):
    calls = []

    def fake_post(url, json=None, **kwargs):
        calls.append({"url": url, "payload": json})
        return _FakeResponse(200)

    monkeypatch.setattr(alert_module.requests, "post", fake_post)
    results = send_alert("标题", "内容", {"GENERIC_WEBHOOK_URL": "https://gw"})

    assert results == {"generic_webhook": True}
    assert calls[0]["payload"] == {"title": "标题", "content": "内容"}


def test_send_alert_channel_failure_never_raises(monkeypatch):
    """渠道网络异常必须被吞掉（返回 False），不能影响其它渠道和调用方。"""

    def fake_post(url, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(alert_module.requests, "post", fake_post)
    config = {"FEISHU_WEBHOOK_URL": "u1", "DINGTALK_WEBHOOK_URL": "u2"}
    results = send_alert("标题", "内容", config)

    assert results == {"feishu": False, "dingtalk": False}


def test_send_alert_business_code_failure(monkeypatch):
    """HTTP 200 但业务码非 0 时判定为失败。"""
    monkeypatch.setattr(
        alert_module.requests, "post",
        lambda url, **kw: _FakeResponse(200, {"code": 19021, "msg": "sign error"}),
    )
    results = send_alert("标题", "内容", {"FEISHU_WEBHOOK_URL": "u"})
    assert results == {"feishu": False}
