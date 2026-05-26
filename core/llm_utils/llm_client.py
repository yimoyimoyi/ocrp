"""统一 LLM API 调用网关。

模仿 VideoLingo 的 ask_gpt() 设计，作为所有 LLM 交互的唯一入口。
特性：
- openai 库客户端（替代原始 requests）
- 指数退避重试（装饰器）
- 响应缓存（prompt 级别去重）
- JSON 容错解析（json_repair）
- 响应结构校验（valid_def 回调）
- 流式输出支持
"""

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

import json_repair
from openai import OpenAI

from core.llm_utils.retry import except_handler
from core.logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
LLM_LOG_DIR = BASE_DIR / "output" / "llm_log"
CACHE_LOCK = threading.Lock()


# ── 缓存读写 ──────────────────────────────────────────────────────

def _get_cache_key(model: str, temperature: float, prompt: str, system_prompt: str) -> str:
    """生成缓存键 —— 任何影响 LLM 输出的参数都应参与计算。"""
    raw = f"{model}|{temperature}|{prompt}|{system_prompt}"
    return hashlib.md5(raw.encode()).hexdigest()


def _load_cache(cache_key: str, log_title: str):
    """从缓存文件中读取匹配的响应。"""
    with CACHE_LOCK:
        cache_file = LLM_LOG_DIR / f"{log_title}.json"
        if cache_file.exists():
            try:
                with open(cache_file, encoding="utf-8") as f:
                    entries = json.load(f)
                for entry in entries:
                    if entry.get("cache_key") == cache_key:
                        logger.debug("命中缓存 [%s]: %s", log_title, cache_key[:12])
                        return entry.get("response")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("读取缓存失败 [%s]: %s", log_title, e)
    return None


def _save_cache(cache_key: str, response, log_title: str):
    """将响应写入缓存文件（追加）。"""
    with CACHE_LOCK:
        LLM_LOG_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = LLM_LOG_DIR / f"{log_title}.json"
        entries = []
        if cache_file.exists():
            try:
                with open(cache_file, encoding="utf-8") as f:
                    entries = json.load(f)
            except (json.JSONDecodeError, OSError):
                entries = []
        entries.append({"cache_key": cache_key, "response": response})
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)


# ── 速率限制 ──────────────────────────────────────────────────────

class RateLimiter:
    """滑动窗口 RPM 速率限制器，线程安全。"""

    def __init__(self, max_rpm: int = 60):
        self._max_rpm = max_rpm
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def acquire(self):
        """请求许可，若达到速率上限则阻塞等待。"""
        if self._max_rpm <= 0:
            return
        with self._lock:
            now = time.time()
            cutoff = now - 60.0
            self._timestamps = [t for t in self._timestamps if t > cutoff]
            if len(self._timestamps) >= self._max_rpm:
                wait = self._timestamps[0] - cutoff + 0.1
                logger.debug("速率限制: 等待 %.1fs (%d/%d RPM)", wait, len(self._timestamps), self._max_rpm)
                time.sleep(wait)
                now = time.time()
                cutoff = now - 60.0
                self._timestamps = [t for t in self._timestamps if t > cutoff]
            self._timestamps.append(now)

    def set_max_rpm(self, max_rpm: int):
        self._max_rpm = max_rpm


# 全局速率限制器实例
_global_rate_limiter = RateLimiter(max_rpm=60)


# ── URL 修正 ──────────────────────────────────────────────────────

def _normalize_base_url(base_url: str) -> str:
    """标准化 base_url —— 确保以 /v1 结尾（火山引擎特殊处理）。"""
    url = base_url.rstrip("/")
    if "ark" in url:
        return "https://ark.cn-beijing.volces.com/api/v3"
    if not url.endswith("/v1"):
        return url + "/v1"
    return url


# ── 连接测试 ──────────────────────────────────────────────────────

def test_connection(api_key: str, base_url: str, model: str, timeout: int = 10) -> tuple[bool, str]:
    """测试 API 连接是否正常。

    Args:
        api_key: API 密钥
        base_url: API 端点 URL
        model: 模型名称
        timeout: 超时秒数

    Returns:
        (成功标志, 状态消息)
    """
    url = _normalize_base_url(base_url)
    try:
        client = OpenAI(api_key=api_key, base_url=url)
        client.models.list(timeout=timeout)
        return True, "连接正常"
    except Exception as e:
        msg = str(e)
        if "timeout" in msg.lower() or "timed out" in msg.lower():
            return False, f"连接超时 ({timeout}s)"
        if "401" in msg or "unauthorized" in msg.lower():
            return False, "API Key 无效 (401)"
        if "403" in msg:
            return False, "无权限访问 (403)"
        if "404" in msg:
            return False, "端点不存在 (404)"
        if "connection" in msg.lower() or "refused" in msg.lower():
            return False, "无法连接到服务器"
        return False, msg[:80]


# ── 核心调用 ──────────────────────────────────────────────────────

@except_handler("LLM API request failed", retry=5, delay=1.0, default_return=None)
def ask_llm(
    prompt: str,
    *,
    system_prompt: str = "",
    resp_type: str | None = None,
    valid_def: Callable[[str | dict], dict] | None = None,
    log_title: str = "default",
    temperature: float = 0.1,
    stream: bool = False,
    stream_callback: Callable[[str], None] | None = None,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
    timeout: int = 120,
) -> str | dict | None:
    """通过 OpenAI 兼容 API 调用 LLM。

    **纯函数设计**：所有 API 配置由调用者通过参数传入，不依赖全局状态或配置文件。
    相同的参数保证相同的输出（缓存机制）。

    Args:
        prompt: 用户提示词
        system_prompt: 系统提示词（ORCP 特有，VideoLingo 无此参数）
        resp_type: None=原始文本, "json"=JSON 容错解析
        valid_def: 响应校验回调，接收解析后的响应，返回：
                   {"status": "success"} 或 {"status": "error", "message": "..."}
                   校验失败时抛 ValueError 触发重试
        log_title: 缓存文件名（output/llm_log/{log_title}.json）
        temperature: 采样温度
        stream: 是否启用流式输出
        stream_callback: 流式回调，每收到一个 chunk 调用一次

            **流式模式重试行为**：若连接在流式传输中途中断（如第 N 个 chunk 后），
            except_handler 将触发重试并**从第一个 chunk 重新开始**，
            之前接收的部分内容全部丢失。OpenAI Chat Completions API 不支持流式断点续传。
        api_key: API 密钥
        base_url: API 端点 URL
        model: 模型名称
        timeout: 请求超时秒数（默认 120s）

    Returns:
        非流式：str（resp_type=None）或 dict（resp_type="json"）
        流式：str（拼接后的完整内容）
        API 失败且所有重试耗尽：None
    """
    if not api_key:
        logger.error("API key 未设置")
        return None
    if not model:
        logger.error("模型名称未设置")
        return None

    # ── 缓存检查（流式模式不缓存） ──
    if not stream:
        cache_key = _get_cache_key(model, temperature, prompt, system_prompt)
        cached = _load_cache(cache_key, log_title)
        if cached is not None:
            return cached
    else:
        cache_key = ""

    # ── 速率限制 ──
    _global_rate_limiter.acquire()

    # ── 构造请求 ──
    url = _normalize_base_url(base_url)
    client = OpenAI(api_key=api_key, base_url=url)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    params: dict = dict(
        model=model,
        messages=messages,
        temperature=temperature,
        timeout=timeout,
    )

    if resp_type == "json":
        params["response_format"] = {"type": "json_object"}

    # ── 流式调用 ──
    if stream:
        return _call_stream(client, params, stream_callback, log_title, logger)

    # ── 非流式调用 ──
    return _call_normal(client, params, resp_type, valid_def, cache_key, log_title, logger)


def _call_stream(client, params, stream_callback, log_title, log):
    """流式调用 LLM —— 拼接所有 chunk 并返回完整文本。"""
    params["stream"] = True
    stream_resp = client.chat.completions.create(**params)
    full_content = ""
    chunk_count = 0
    for chunk in stream_resp:
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        if delta is None:
            continue
        content = getattr(delta, "content", None) or ""
        if content:
            full_content += content
            chunk_count += 1
            if stream_callback:
                stream_callback(content)
    log.info("流式接收完成 [%s]: %d chunks, %d chars", log_title, chunk_count, len(full_content))
    return full_content.strip()


def _call_normal(client, params, resp_type, valid_def, cache_key, log_title, log):
    """非流式调用 LLM —— 解析响应并缓存。"""
    resp_raw = client.chat.completions.create(**params)
    content = resp_raw.choices[0].message.content or ""

    # ── JSON 容错解析 ──
    if resp_type == "json":
        try:
            parsed = json_repair.loads(content)
        except (json.JSONDecodeError, ValueError) as e:
            log.warning("JSON 解析失败 [%s]，返回原始文本: %s", log_title, e)
            return {"raw": content} if content.strip() else None
    else:
        parsed = content

    # ── 响应校验 ──
    if valid_def:
        result = valid_def(parsed)
        if result.get("status") != "success":
            raise ValueError(f"响应校验失败 [{log_title}]: {result.get('message', 'unknown')}")

    # ── 缓存 ──
    if cache_key:
        _save_cache(cache_key, parsed, log_title)

    return parsed
