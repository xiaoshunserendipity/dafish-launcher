"""从输出里识别本地服务地址，并在默认浏览器里打开。

安全前提：子进程输出是**不可信**的。批处理如果被篡改，里面可以打印任意网址，
所以这里只放行本机与局域网地址，其余一律拒绝。
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

URL_RE = re.compile(r"https?://[^\s\"'<>()\[\]{}]+", re.IGNORECASE)

# 行内出现这些词，说明这条输出大概率是"入口地址"
ENTRY_HINTS = (
    "listening",
    "running on",
    "started on",
    "server started",
    "webui",
    "web ui",
    "open in browser",
)

_TRAILING = ".,;:!?、。，；）)]}'\">"


def extract_urls(text: str) -> list[str]:
    """把一段文本里的 http/https 地址按出现顺序去重取出。"""
    found: list[str] = []
    for match in URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(_TRAILING)
        if url and url not in found:
            found.append(url)
    return found


def is_local_url(url: str) -> bool:
    """只放行本机与局域网地址（localhost / 127.x / 10.x / 172.16-31.x / 192.168.x / ::1）。"""
    try:
        parts = urlsplit(url)
    except Exception:
        return False
    if (parts.scheme or "").lower() not in ("http", "https"):
        return False
    host = (parts.hostname or "").strip().lower()
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local)


def normalize_url(raw: str) -> str:
    """补全协议头；空字符串原样返回。"""
    text = (raw or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    return text


def pick_entry_url(text: str) -> str:
    """从一行输出里挑出第一个本地地址；没有就返回空。"""
    for url in extract_urls(text):
        if is_local_url(url):
            return url
    return ""


def has_entry_hint(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in ENTRY_HINTS)


def open_in_browser(url: str) -> bool:
    """用系统默认浏览器打开；失败返回 False。"""
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        return bool(QDesktopServices.openUrl(QUrl(url)))
    except Exception:
        return False
