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
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import json_repair
from openai import OpenAI

from core.llm_utils.retry import except_handler
from core.logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
LLM_LOG_DIR = BASE_DIR / "output" / "llm_log"
CACHE_LOCK = threading.Lock()

# 本地无鉴权端点（llama.cpp / Ollama 等，空 key 可放行）
# 注意：主机名一律不含 URL 括号（"[::1]" 在 URL 中写作 http://[::1]:port）
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def _is_local_url(base_url: str) -> bool:
    """判断 base_url 是否指向本地服务（无需 API key 鉴权）。"""
    netloc = base_url.split("://", 1)[-1].split("/", 1)[0]
    if netloc.startswith("["):
        host = netloc.split("]", 1)[0][1:]  # 括号 IPv6：http://[::1]:8080 → ::1
    else:
        host = netloc.split(":", 1)[0]
    return host.lower() in _LOCAL_HOSTS


# ── 缓存读写 ──────────────────────────────────────────────────────


def _get_cache_key(
    model: str,
    temperature: float,
    prompt: str,
    system_prompt: str,
    max_tokens: int = 2048,
    resp_type: str | None = None,
    base_url: str = "",
) -> str:
    """生成缓存键 —— 任何影响 LLM 输出的参数都应参与计算。"""
    raw = f"{model}|{temperature}|{max_tokens}|{resp_type}|{base_url}|{prompt}|{system_prompt}"
    return hashlib.md5(raw.encode()).hexdigest()


# ── 缓存 TTL（秒） ──
CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 天

#: 网关默认输出预算。纠错/润色调用方会按内容长度显式给足（见 AICorrector）。
DEFAULT_MAX_TOKENS = 2048
#: 截断自愈重试的预算上限（不超过主流模型的输出上限）
TRUNCATION_ESCALATION_CEILING = 8192

# ── 进程内内存缓存（一级缓存，避免重复文件 I/O）──
# 值为 (response, cached_at)：此前只存 response，内存缓存既无 TTL 也无容量上限，
# 长时间运行（批量纠错成千上万行）会无界增长，且会永久返回过期响应 —— 文件缓存
# 的 7 天 TTL 被内存层完全绕过。使用 OrderedDict 以便 LRU 淘汰。
MEMORY_CACHE_MAX_ENTRIES = 512
_memory_cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
_memory_cache_lock = threading.Lock()


def _memory_cache_get(cache_key: str) -> Any:
    """读取内存缓存（遵守 TTL + LRU 续期）。未命中/已过期返回 None。"""
    now = time.time()
    with _memory_cache_lock:
        entry = _memory_cache.get(cache_key)
        if entry is None:
            return None
        value, cached_at = entry
        if (now - cached_at) > CACHE_TTL_SECONDS:
            del _memory_cache[cache_key]
            return None
        _memory_cache.move_to_end(cache_key)
        return value


def _memory_cache_put(cache_key: str, value: Any) -> None:
    """写入内存缓存并按 LRU 淘汰超出容量的条目。"""
    with _memory_cache_lock:
        _memory_cache[cache_key] = (value, time.time())
        _memory_cache.move_to_end(cache_key)
        while len(_memory_cache) > MEMORY_CACHE_MAX_ENTRIES:
            _memory_cache.popitem(last=False)


def clear_memory_cache() -> None:
    """清空内存缓存（测试与「重新加载配置」后强制重取时使用）。"""
    with _memory_cache_lock:
        _memory_cache.clear()


def _is_meta_response(resp) -> bool:
    """判断响应是否为 meta-response（LLM 没有执行任务，而是询问输入）。

    这类响应通常以"请提供"/"请问"/"好的，"开头且很短，是 prompt 缺少上下文时的典型表现。
    """
    if not isinstance(resp, str):
        return False
    text = resp.strip()
    if len(text) > 50:
        return False
    if "\n" in text:
        return False  # 多行内容不太可能是 meta-response
    meta_prefixes = ("好的，请提供", "请提供", "请问", "好的，请问", "我需要您提供", "请告诉我", "请您提供")
    return any(text.startswith(p) for p in meta_prefixes)


def _load_cache_from_file(cache_key: str, log_title: str):
    """从缓存文件中读取匹配的响应（二级缓存）。"""
    with CACHE_LOCK:
        cache_file = LLM_LOG_DIR / f"{log_title}.json"
        if cache_file.exists():
            try:
                with open(cache_file, encoding="utf-8") as f:
                    entries = json.load(f)
                # P0-T7 修复：损坏缓存顶层非 list 时直接忽略，
                # 否则 entry.get 抛 AttributeError 被 except_handler 误当 API 失败重试 7 秒
                if not isinstance(entries, list):
                    logger.warning("缓存文件顶层非列表，忽略 [%s]: %s", log_title, type(entries).__name__)
                    return None
                now = time.time()
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue  # 防御混合损坏条目
                    if entry.get("cache_key") == cache_key:
                        resp = entry.get("response")
                        # 非字符串/空响应不可用（同时把 resp 收窄为 str 供后续切片）
                        if not isinstance(resp, str) or not resp.strip():
                            continue
                        if _is_meta_response(resp):
                            logger.debug("跳过 meta-response 缓存 [%s]: %s", log_title, resp[:40])
                            continue
                        cached_at = entry.get("cached_at", 0)
                        if cached_at and (now - cached_at) > CACHE_TTL_SECONDS:
                            logger.debug("缓存已过期 [%s]: %s", log_title, cache_key[:12])
                            continue
                        logger.debug("命中文件缓存 [%s]: %s", log_title, cache_key[:12])
                        return resp
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("读取缓存失败 [%s]: %s", log_title, e)
    return None


def _load_cache(cache_key: str, log_title: str):
    """读取缓存（一级内存缓存 → 二级文件缓存）。"""
    # 一级缓存：内存查找（~0.01ms，遵守 TTL）
    cached = _memory_cache_get(cache_key)
    if cached is not None:
        logger.debug("命中内存缓存 [%s]: %s", log_title, cache_key[:12])
        return cached
    # 二级缓存：文件查找（~5ms）
    result = _load_cache_from_file(cache_key, log_title)
    if result is not None:
        _memory_cache_put(cache_key, result)
    return result


def _cleanup_cache_files(max_files: int = 100):
    """清理过多的缓存文件，保留最新的 max_files 个。"""
    try:
        if not LLM_LOG_DIR.exists():
            return
        cache_files = sorted(LLM_LOG_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        for old_file in cache_files[max_files:]:
            old_file.unlink(missing_ok=True)
            logger.debug("清理旧缓存文件: %s", old_file.name)
    except Exception as e:
        logger.warning("缓存文件清理失败: %s", e)


def _save_cache(cache_key: str, response, log_title: str):
    """将响应写入缓存（文件 + 内存）。"""
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
            # P0-T7 修复：写入前同样校验顶层结构，损坏文件重置为空列表
            if not isinstance(entries, list):
                entries = []
        entries.append(
            {
                "cache_key": cache_key,
                "response": response,
                "cached_at": time.time(),
            }
        )
        if len(entries) > 200:
            entries = entries[-200:]
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
        # 每次保存时检查全局文件数量
        _cleanup_cache_files()
    # 同步写入内存缓存
    _memory_cache_put(cache_key, response)


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
        while True:
            with self._lock:
                now = time.time()
                cutoff = now - 60.0
                self._timestamps = [t for t in self._timestamps if t > cutoff]
                if len(self._timestamps) < self._max_rpm:
                    self._timestamps.append(now)
                    return
                wait = self._timestamps[0] - cutoff + 0.1
            # 在锁外休眠，不阻塞其他线程的检查
            logger.debug("速率限制: 等待 %.1fs", wait)
            time.sleep(max(wait, 0.1))

    def set_max_rpm(self, max_rpm: int):
        self._max_rpm = max_rpm


# 全局速率限制器实例
_global_rate_limiter = RateLimiter(max_rpm=30)


def set_global_rpm(max_rpm: int):
    """设置全局速率限制器的 RPM 上限。"""
    _global_rate_limiter.set_max_rpm(max_rpm)


# ── URL 修正 ──────────────────────────────────────────────────────


def _normalize_base_url(base_url: str) -> str:
    """标准化 base_url —— 确保路径后缀正确（保留用户自定义域名/端口/路径前缀）。"""
    url = base_url.rstrip("/")
    if "ark.cn-beijing.volces.com" in url:
        # 火山引擎 API 路径为 /api/v3，仅确保路径正确，不覆盖用户域名
        if not url.endswith("/api/v3"):
            return url + "/api/v3"
        return url
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


@except_handler("LLM API request failed", retry=3, delay=1.0, default_return=None)
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
    max_tokens: int = DEFAULT_MAX_TOKENS,
    image: Any = None,
    no_cache: bool = False,
    use_rate_limiter: bool = True,
    cancel_event: threading.Event | None = None,
    escalate_on_truncation: bool = True,
    truncated_out: list[bool] | None = None,
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
        max_tokens: 最大输出 token 数（默认 2048）
        image: 可选 numpy 图像数组，非 None 时构造 vision 格式消息
        no_cache: 跳过缓存读取/写入
        use_rate_limiter: 是否经过全局速率限制器。
                          OCR 视觉引擎调用应设为 False（高频帧处理），
                          纠错调用保持默认 True（批量操作需限速）。
        cancel_event: 协作式取消信号。已置位时立即返回 None（不发起请求）。
                      流式模式下每个 chunk 检查一次，可**真正中断**在飞请求；
                      非流式模式只能在发起前检查（HTTP 请求本身无法中途打断），
                      因此取消生效最迟在本次请求返回后。
        escalate_on_truncation: 输出因 max_tokens 截断时，是否把预算翻倍重试一次
                      （上限 ``TRUNCATION_ESCALATION_CEILING``）。
        truncated_out: 可选出参。追加一个 bool 表示**最终**结果是否被截断 —— 调用方
                      据此回退，避免把半截内容当作正常结果（如润色把 480 字变成 43 字）。

    Returns:
        非流式：str（resp_type=None）或 dict（resp_type="json"）
        流式：str（拼接后的完整内容）
        API 失败且所有重试耗尽：None
        已取消：None
    """
    if cancel_event is not None and cancel_event.is_set():
        logger.info("LLM 调用已取消（发起前检查）[%s]", log_title)
        return None

    # 空 key 处理（P0-T7 修复）：本地服务（llama.cpp/Ollama）无鉴权，放行；
    # 云端服务空 key 保持明确报错（与 vision 引擎 _check_v1_availability 先例一致）
    if not api_key:
        if _is_local_url(base_url):
            api_key = "not-needed"
        else:
            logger.error("API key 未设置")
            return None
    if not model:
        if _is_local_url(base_url):
            model = "default"  # llama.cpp server 忽略 model 字段
        else:
            logger.error("模型名称未设置")
            return None

    # ── 缓存检查（流式模式、vision 模式、no_cache 不缓存） ──
    cacheable = not stream and image is None and not no_cache

    def _key_for(budget: int) -> str:
        return _get_cache_key(model, temperature, prompt, system_prompt, budget, resp_type, base_url)

    if cacheable:
        cache_key = _key_for(max_tokens)
        cached = _load_cache(cache_key, log_title)
        if cached is not None:
            return cached
    else:
        cache_key = ""

    # ── 速率限制（OCR 等高帧率场景不限制，纠错等批量操作限制）──
    if use_rate_limiter:
        _global_rate_limiter.acquire()

    # ── 构造请求 ──
    url = _normalize_base_url(base_url)
    client = OpenAI(api_key=api_key, base_url=url)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    if image is not None:
        import base64

        import cv2

        _, buf = cv2.imencode(".jpg", image)
        b64 = base64.b64encode(buf).decode("utf-8")
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        )
    else:
        messages.append({"role": "user", "content": prompt})

    params: dict = dict(
        model=model,
        messages=messages,
        temperature=temperature,
        timeout=timeout,
        max_tokens=max_tokens,
    )
    # DeepSeek 默认启用 thinking mode，消耗 max_tokens 预算导致空返回
    if "deepseek" in model.lower():
        params["extra_body"] = {"thinking": {"type": "disabled"}}

    # ── JSON 模式：不使用 response_format（DeepSeek 已知 bug：JSON 模式下概率空返回） ──
    # 依赖 prompt 中的 "JSON" 字样 + json_repair 解析即可

    # ── 调用（截断时以更大预算自愈式重试一次） ──
    # 截断（finish_reason == "length"）此前只记日志：调用方拿到半截内容并当作
    # 正常结果 —— 润色会把 480 字输入变成 43 字输出，批量纠错会把半句写进表格行。
    # 这里先按内容长度给足预算（见 AICorrector），若仍被截断再把预算翻倍重试一次；
    # 到顶后由 truncated_out 告知调用方，由其回退（绝不静默采用半截内容）。
    budgets = [max_tokens]
    if escalate_on_truncation and max_tokens < TRUNCATION_ESCALATION_CEILING:
        budgets.append(min(TRUNCATION_ESCALATION_CEILING, max_tokens * 2))

    result: str | dict | None = None
    for idx, budget in enumerate(budgets):
        params["max_tokens"] = budget
        flag: list[bool] = []
        if cacheable:
            cache_key = _key_for(budget)
        if stream:
            result = _call_stream(client, params, stream_callback, log_title, logger, cancel_event, flag)
        else:
            result = _call_normal(
                client, params, resp_type, valid_def, cache_key, log_title, logger, cancel_event, flag
            )
        truncated = bool(flag and flag[0])
        if not truncated or idx + 1 == len(budgets):
            break
        logger.warning(
            "输出被 max_tokens(%d) 截断 [%s]，以 %d 预算重试",
            budget,
            log_title,
            budgets[idx + 1],
        )

    if truncated_out is not None:
        truncated_out.append(truncated)
    return result


def _call_stream(client, params, stream_callback, log_title, log, cancel_event=None, truncated_out=None):
    """流式调用 LLM —— 拼接所有 chunk 并返回完整文本。

    注意：连接中途中断时捕获异常并返回已接收的部分内容，
    避免 @except_handler 触发重试（重试会丢失已接收内容）。

    ``cancel_event`` 已置位时跳出 chunk 循环并返回已接收内容 —— 这是唯一能
    **真正中断在飞请求**的路径（非流式调用只能在发起前检查）。
    """
    params["stream"] = True
    full_content = ""
    chunk_count = 0
    truncated = False
    cancelled = False
    try:
        stream_resp = client.chat.completions.create(**params)
        for chunk in stream_resp:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            if getattr(choices[0], "finish_reason", None) == "length":
                truncated = True
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue
            content = getattr(delta, "content", None) or ""
            if content:
                full_content += content
                chunk_count += 1
                if stream_callback:
                    stream_callback(content)
    except Exception as e:
        log.warning("流式传输中断 [%s]: %s，返回已接收部分 (%d chars)", log_title, e, len(full_content))
    else:
        if cancelled:
            log.info("流式调用已取消 [%s]: 已接收 %d chunks, %d chars", log_title, chunk_count, len(full_content))
        elif truncated:
            log.warning(
                "流式输出被 max_tokens 截断 [%s]: %d chunks, %d chars", log_title, chunk_count, len(full_content)
            )
        else:
            log.info("流式接收完成 [%s]: %d chunks, %d chars", log_title, chunk_count, len(full_content))
    if truncated_out is not None:
        truncated_out.append(truncated)
    return full_content.strip()


def _call_normal(
    client, params, resp_type, valid_def, cache_key, log_title, log, cancel_event=None, truncated_out=None
):
    """非流式调用 LLM —— 解析响应并按需缓存。

    截断响应（finish_reason == "length"）**不写入缓存**：否则一次 max_tokens
    不足的结果会在缓存中存活 7 天，之后每次请求都拿到同一份残缺内容。

    ``cancel_event`` 已置位时在发起请求前返回 None —— 非流式 HTTP 请求无法
    中途打断，因此取消最迟在本次请求返回后生效（流式路径见 ``_call_stream``）。
    """
    if cancel_event is not None and cancel_event.is_set():
        log.info("LLM 调用已取消（请求发起前）[%s]", log_title)
        return None
    resp_raw = client.chat.completions.create(**params)
    choice = resp_raw.choices[0]
    content = choice.message.content or ""
    finish_reason = getattr(choice, "finish_reason", None)

    # ── 空响应直接返回 None（避免缓存和后续解析问题） ──
    if not content.strip():
        log.warning("LLM 返回空内容 [%s]", log_title)
        return None

    if finish_reason == "length":
        # 输出被 max_tokens 截断：内容不完整，除不缓存外还必须告知调用方
        # （由 ask_llm 决定放大预算重试，或由消费方回退到原文）。
        log.warning("LLM 输出被 max_tokens 截断 [%s]，本次结果不写入缓存", log_title)
        if truncated_out is not None:
            truncated_out.append(True)

    # ── meta-response 拦截 ──
    # 这类响应说明模型没有执行任务而是要求补充输入。此前只在**缓存读取**时过滤，
    # 线上响应本身照常返回 —— 会被当作纠错/润色结果写回字幕行（静默污染）。
    if _is_meta_response(content):
        log.warning("LLM 返回 meta-response（未执行任务）[%s]: %s", log_title, content.strip()[:60])
        return None

    # ── JSON 容错解析 ──
    if resp_type == "json":
        parsed: Any
        try:
            parsed = json_repair.loads(content)
        except (json.JSONDecodeError, ValueError) as e:
            log.warning("JSON 解析失败 [%s]，按纯文本返回: %s", log_title, e)
            return content.strip()
        # json_repair 对非 JSON 输入**不抛异常**，而是静默返回异类值：
        #   "这不是 JSON {{{"            → []
        #   "[ID:0] a\n[ID:1] b"         → ['ID:1']
        #   纯文本散文                    → ''
        # 把它们当合法 JSON 结果会让调用方把 "[]" 当作纠错文本写回，或误判为
        # 解析成功而放弃 [ID:n] 文本回退。非 dict 一律按纯文本返回，交给调用方
        # 已有的文本回退链（与流式模式行为一致）。
        if not isinstance(parsed, dict):
            log.warning("JSON 响应非对象 [%s]（得到 %s），按纯文本返回", log_title, type(parsed).__name__)
            return content.strip()
    else:
        parsed = content

    # ── 响应校验 ──
    if valid_def:
        result = valid_def(parsed)
        if result.get("status") != "success":
            raise ValueError(f"响应校验失败 [{log_title}]: {result.get('message', 'unknown')}")

    # ── 缓存（截断结果不入缓存） ──
    if cache_key and finish_reason != "length":
        _save_cache(cache_key, parsed, log_title)

    return parsed
