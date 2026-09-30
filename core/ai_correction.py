"""AI 纠错模块 —— 通过引擎配置进行二次纠错。

若引擎为 API 类型（openai_vision / ollama_vision / llamacpp），
则调用对应 API 对文本进行校对；

若引擎为 local 类型（paddleocr），
则直接调用引擎的 recognize() 对原图 ROI 重新识别。
"""

import json
import os
import re
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np

from core.config_manager import load_json_with_comments
from core.llm_utils import ask_llm
from core.logger import get_logger

# 默认提示词单一来源（P23 去重）：此前三处副本已漂移，polish_prompt 因域对象
# 默认值被截断而完全丢失 {待校对文本} 占位符，导致润色把"无正文提示词"发给模型。
from core.prompts import (
    DEFAULT_CORRECTION_PROMPT,
    DEFAULT_CORRECTION_SYSTEM_PROMPT,
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_POLISH_PROMPT,
    DEFAULT_POLISH_SYSTEM_PROMPT,
    DEFAULT_SUMMARY_PROMPT,
    DEFAULT_TRANSLATE_SYSTEM_PROMPT,
    POLISH_TEXT_PLACEHOLDER,
    is_placeholder_free_polish_prompt,
)

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = BASE_DIR / "config"

logger = get_logger(__name__)

# [DEBUG] 临时调试日志 —— LLM 输入输出（仅 ORCP_DEBUG_SEG=1 时启用）
import datetime as _adt

_ALOG = BASE_DIR / "logs" / "debug_seg.log"


def _alog(msg: str):
    """写入分句/纠错调试日志。仅在环境变量 ORCP_DEBUG_SEG=1 时启用。"""
    if os.environ.get("ORCP_DEBUG_SEG", "") != "1":
        return
    try:
        _ALOG.parent.mkdir(parents=True, exist_ok=True)
        if _ALOG.exists() and _ALOG.stat().st_size > 5 * 1024 * 1024:
            data = _ALOG.read_bytes()
            _ALOG.write_bytes(data[len(data) // 2 :])
    except OSError:
        pass
    with open(_ALOG, "a", encoding="utf-8") as f:
        f.write(f"{_adt.datetime.now().strftime('%H:%M:%S.%f')[:-3]} [AI] {msg}\n")


# ── 批量纠错 ID 前缀常量 ──
ID_PREFIX = "[ID:"
# 兼容 AI 输出变体：[ID:0]、[ID：0]、[ID 0]、ID:0、[id:0]
ID_PATTERN = re.compile(
    r"\[\s*ID\s*[:：]\s*(\d+)\s*\](.*?)(?=\n\[\s*ID\s*[:：]\s*\d+\s*\]|\Z)",
    re.DOTALL | re.IGNORECASE,
)
# 匹配时间标记 [hh:mm:ss.ms -> hh:mm:ss.ms] 或 [hh:mm:ss] 或 (hh:mm:ss)
TIME_MARKER = re.compile(
    r"\s*[(\[]\s*\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?\s*(?:->|→|-{1,2}>|,)\s*\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?\s*[)\]]\s*"
    r"|\s*\[\s*\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?\s*\]\s*",
)
ID_TAG = re.compile(r"\[\s*ID\s*[:：]?\s*\d+\s*\]\s*", re.IGNORECASE)
# AI 可能输出的 markdown 代码块包裹
_MD_FENCE = re.compile(r"^```(?:json|text)?\s*\n?|\n?```\s*$", re.MULTILINE)


def _clean_content(text: str) -> str:
    """去除 AI 可能附带的时间标记、[ID:n] 标记和 markdown 代码块。"""
    text = _MD_FENCE.sub("", text)
    text = TIME_MARKER.sub("", text)
    text = ID_TAG.sub("", text)
    return text.strip()


def _clean_summary_prompt(value) -> str:
    """清洗 summary_prompt：历史版本可能写入字面量 "None"（旧 P2 bug 污染）。"""
    if not value or str(value).strip().lower() in ("none", "null"):
        return DEFAULT_SUMMARY_PROMPT
    return str(value)


def _clean_polish_prompt(value) -> str:
    """清洗 polish_prompt：无正文占位符的提示词无法注入待润色文本。

    历史域对象/建文件模板写入的是被截断的 "……进行润色..."，没有任何占位符，
    会把"不含正文的提示词"发给模型。此类值在内存中回落内置默认（不写盘，
    用户仍可在设置里看到并修改原值）。
    """
    if is_placeholder_free_polish_prompt(value):
        if str(value or "").strip():
            logger.warning("polish_prompt 缺少正文占位符，本次运行使用内置默认提示词")
        return DEFAULT_POLISH_PROMPT
    return str(value)


def load_correction_config() -> dict:
    """加载 ai_correction.json 配置。"""
    path = CONFIG_DIR / "ai_correction.json"
    if path.exists():
        try:
            cfg = load_json_with_comments(path)
            from core.config_schema import validate_config
            from core.config_schemas import AI_CORRECTION_SCHEMA

            ok, errors = validate_config(cfg, AI_CORRECTION_SCHEMA, "ai_correction.json")
            if not ok:
                logger.warning("纠错配置校验失败: %s", "; ".join(errors[:3]))
            return cfg
        except Exception as e:
            logger.warning("加载纠错配置失败: %s", e)
    return {"enabled": False, "engine": "openai_vision", "retry_on_failure": 2}


def _resolve_api_config(config: dict, preset_name: str = "") -> dict:
    """解析 API 连接配置：优先使用预设，回退到 config 中的直接配置。"""
    if preset_name:
        from core.api_preset_manager import APIPresetManager

        preset = APIPresetManager().get_preset(preset_name)
        if preset:
            return dict(preset)
        logger.error("API 预设 [%s] 不存在，请检查 api_presets.json", preset_name)
        return {"api_key": "", "base_url": "", "model": "", "timeout": 30}
    return {
        "api_key": config.get("api_key", "").strip(),
        "base_url": config.get("base_url", "http://127.0.0.1:8080"),
        "model": config.get("model", ""),
        "timeout": config.get("timeout", 30),
    }


class AICorrector:
    """AI 纠错器 —— 独立 API 配置（不依赖 OCR 引擎）。

    使用独立的 API Key / Base URL / Model 进行文本纠错。
    """

    def __init__(self, config: dict | None = None, engine_manager=None, preset_name: str = ""):
        self._config = config or load_correction_config()
        self._enabled = self._config.get("enabled", False)
        self._retry = self._config.get("retry_on_failure", 2)
        self._prompt_template = self._config.get("correction_prompt", DEFAULT_CORRECTION_PROMPT)
        self._engine_name = self._config.get("engine", "llamacpp")
        self._preset_name = preset_name
        api_cfg = _resolve_api_config(self._config, preset_name)
        self._api_key = api_cfg.get("api_key", "").strip()
        self._base_url = api_cfg.get("base_url", "http://127.0.0.1:8080").strip()
        self._model = api_cfg.get("model", "").strip()
        self._timeout = api_cfg.get("timeout", 30)
        self._engine_manager = engine_manager
        self._env_context: str = ""
        self._extract_env: bool = self._config.get("extract_environment", False)
        #: 最近一次 _call_llm 的最终结果是否被 max_tokens 截断（消费方据此回退）
        self._last_truncated: bool = False
        # 翻译模式（设置同步 R11：由配置读取，_open_settings 重建实例后保持 UI 勾选，
        # 此前 __init__ 硬编码 False 导致对话框 accept 后 translate 永久失效）
        self._translate_mode: bool = bool(self._config.get("translate_mode", False))
        # 从配置读取流式/JSON 模式（设置同步 P9 修复：重建实例后保持 UI 勾选状态）
        self._stream_mode: bool = bool(self._config.get("stream_mode", False))  # 流式输出模式
        self._json_mode: bool = bool(self._config.get("json_mode", False))  # JSON 输出模式
        # ── 自定义提示词字段 ──
        self._summary_prompt = _clean_summary_prompt(self._config.get("summary_prompt", DEFAULT_SUMMARY_PROMPT))
        self._correction_system_prompt = self._config.get("correction_system_prompt", DEFAULT_CORRECTION_SYSTEM_PROMPT)
        self._output_format = self._config.get("output_format", DEFAULT_OUTPUT_FORMAT)
        self._seg_time_gap: float = self._config.get("seg_time_gap", 3.0)
        # ── 润色模式字段 ──
        self._polish_enabled: bool = self._config.get("enable_polish", False)
        self._polish_prompt = _clean_polish_prompt(self._config.get("polish_prompt", ""))

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, val: bool):
        self._enabled = val

    def reload_config(self):
        """重新加载 ai_correction.json 配置（保留运行时模式标志）。"""
        # 保存运行时状态（UI setter 设置的值，不应被配置文件覆盖）
        _translate = self._translate_mode
        _stream = self._stream_mode
        _json = self._json_mode
        _env_ctx = self._env_context
        _extract = self._extract_env
        _seg_tg = self._seg_time_gap

        self._config = load_correction_config()
        self._enabled = self._config.get("enabled", False)
        self._retry = self._config.get("retry_on_failure", 2)
        self._prompt_template = self._config.get("correction_prompt", DEFAULT_CORRECTION_PROMPT)
        self._engine_name = self._config.get("engine", "llamacpp")
        api_cfg = _resolve_api_config(self._config, self._preset_name)
        self._api_key = api_cfg.get("api_key", "").strip()
        self._base_url = api_cfg.get("base_url", "http://127.0.0.1:8080")
        self._model = api_cfg.get("model", "")
        self._timeout = api_cfg.get("timeout", 30)
        self._summary_prompt = _clean_summary_prompt(self._config.get("summary_prompt", DEFAULT_SUMMARY_PROMPT))
        self._correction_system_prompt = self._config.get("correction_system_prompt", DEFAULT_CORRECTION_SYSTEM_PROMPT)
        self._output_format = self._config.get("output_format", DEFAULT_OUTPUT_FORMAT)
        self._seg_time_gap = self._config.get("seg_time_gap", 3.0)
        self._polish_enabled = self._config.get("enable_polish", False)
        self._polish_prompt = _clean_polish_prompt(self._config.get("polish_prompt", ""))

        # 恢复运行时状态
        self._translate_mode = _translate
        self._stream_mode = _stream
        self._json_mode = _json
        self._env_context = _env_ctx
        self._extract_env = _extract
        self._seg_time_gap = _seg_tg

    @property
    def engine_name(self) -> str:
        return self._engine_name

    @engine_name.setter
    def engine_name(self, val: str):
        self._engine_name = val

    @property
    def translate_mode(self) -> bool:
        return self._translate_mode

    @translate_mode.setter
    def translate_mode(self, val: bool):
        self._translate_mode = val

    @property
    def stream_mode(self) -> bool:
        return self._stream_mode

    @stream_mode.setter
    def stream_mode(self, val: bool):
        self._stream_mode = val

    @property
    def json_mode(self) -> bool:
        return self._json_mode

    @json_mode.setter
    def json_mode(self, val: bool):
        self._json_mode = val

    @property
    def polish_enabled(self) -> bool:
        return self._polish_enabled

    @polish_enabled.setter
    def polish_enabled(self, val: bool):
        self._polish_enabled = val

    @property
    def env_context(self) -> str:
        """已提取的环境上下文（供流程层展示/判断，公开 API）。"""
        return self._env_context

    def should_skip_env_extraction(self) -> bool:
        """公开 API：是否跳过环境提取（见 ``_should_skip_env_extraction``）。"""
        return self._should_skip_env_extraction()

    def apply_preset(self, preset_name: str):
        """切换 API 预设。"""
        self._preset_name = preset_name
        api_cfg = _resolve_api_config(self._config, preset_name)
        self._api_key = api_cfg.get("api_key", "").strip()
        self._base_url = api_cfg.get("base_url", "http://127.0.0.1:8080")
        self._model = api_cfg.get("model", "")
        self._timeout = api_cfg.get("timeout", 30)
        logger.info("已切换 API 预设: %s", preset_name or "默认")

    def is_default_correction_prompt(self) -> bool:
        """判断 correction_prompt 是否仍为内置默认值。

        取代此前 ``"文本校对" not in user_hint[:20]`` 的脆弱子串启发式：该写法
        只看前 20 个字符，任何以"文本校对"开头的用户自定义提示词都会被误判为
        默认值而丢弃；同时默认提示词一旦改写就会失效。
        """
        return str(self._prompt_template or "").strip() == DEFAULT_CORRECTION_PROMPT

    @property
    def extract_env(self) -> bool:
        return self._extract_env

    @extract_env.setter
    def extract_env(self, val: bool):
        self._extract_env = val

    def clear_env_context(self):
        """清除环境上下文。每次新视频/新内容处理前调用，防止上下文过期。"""
        self._env_context = ""
        logger.debug("环境上下文已清除")

    def _should_skip_env_extraction(self) -> bool:
        """判断是否跳过环境提取 API 调用。

        跳过条件（满足任一）：
        1. extract_env 开关已打开（用户手动管理环境上下文）
        2. _env_context 已存在（已提取过）
        3. 纠错/翻译 prompt 中已包含环境**占位符**（明确指示环境信息将由用户/其他来源提供）

        注意：默认 summary_prompt 的格式描述含"领域/氛围"等词，不能作为
        "已手动提供环境"的判据——否则自动提取永远被跳过（实测修复）。
        """
        if self._extract_env:
            return True
        if self._env_context:
            return True
        env_keywords = ("{环境信息}", "{环境上下文}", "{环境描述}")
        combined_prompt = self._prompt_template + self._summary_prompt + self._polish_prompt
        return any(kw in combined_prompt for kw in env_keywords)

    # ── API 调用辅助方法 ───────────────────────────────────────────

    def _get_engine_config(self) -> dict:
        """返回纠错 API 的独立配置。"""
        return {
            "api_key": self._api_key,
            "base_url": self._base_url,
            "model": self._model,
            "timeout": self._timeout,
        }

    @staticmethod
    def _resolve_placeholders(
        template: str,
        raw_text: str = "",
        context: str = "",
        env_context: str = "",
        timestamp: str = "",
        region: str = "",
        engine: str = "",
        language: str = "",
    ) -> str:
        """替换提示词模板中的占位符。

        支持的占位符:
            {原始结果} / [原始文本] → 当前 OCR 原始文本
            {上下文}               → 前后文文本
            {环境信息} / {环境上下文} / {环境描述} → 全文环境提取结果
            {时间戳}               → 当前条目的时间戳
            {区域}                 → 区域名称（字幕/语音等）
            {引擎}                 → OCR 引擎名称
            {语言}                 → 检测/设置的语言

        ``{环境上下文}``/``{环境描述}`` 是 ``{环境信息}`` 的同义写法：
        ``_should_skip_env_extraction`` 一直把它们当作"用户自管环境"的判据，
        但此前这里不替换它们，导致提示词里留下字面占位符且环境信息永久缺失。
        """
        result = template
        result = result.replace("{原始结果}", raw_text)
        result = result.replace("[原始文本]", raw_text)
        result = result.replace("{上下文}", context)
        result = result.replace("{环境信息}", env_context)
        result = result.replace("{环境上下文}", env_context)
        result = result.replace("{环境描述}", env_context)
        result = result.replace("{时间戳}", timestamp)
        result = result.replace("{区域}", region)
        result = result.replace("{引擎}", engine)
        result = result.replace("{语言}", language)
        return result

    def _build_system_prompt(self, env_context: str = "") -> str:
        """构建 system prompt（翻译/校对 + 环境上下文 + JSON 格式指令）。"""
        if self._translate_mode:
            system_msg = DEFAULT_TRANSLATE_SYSTEM_PROMPT
        else:
            system_msg = self._correction_system_prompt

        if env_context:
            system_msg += f"\n\n当前文本的环境上下文信息（供参考）：\n{env_context}"

        if self._json_mode:
            system_msg += (
                "\n\n你必须以 JSON 格式输出结果。"
                '对于批量纠错，输出格式为：{"results": [{"id": 行号, "text": "纠正后文本"}, ...]}。'
                "确保 JSON 格式严格有效，不要包含任何额外说明文字。"
            )

        return system_msg

    @staticmethod
    def _estimate_max_tokens(output_source: str) -> int:
        """按"需要生成多少字"估算输出预算。

        此前纠错/润色调用**完全不传 max_tokens**，网关默认 2048 —— 输入较长时
        输出会被硬切（润色把 480 字输入变成 43 字输出、批量纠错把半句写进表格行）。
        中文大致 1 字 ≈ 1 token，这里留 1.6× 余量再加固定开销。

        下限**不得低于网关默认值**：那样反而会把短行的预算从 2048 降到 1024，
        比修复前更糟。上限用主流模型的输出上限，避免超限报错。
        """
        from core.llm_utils.llm_client import DEFAULT_MAX_TOKENS, TRUNCATION_ESCALATION_CEILING

        needed = int(len(output_source or "") * 1.6) + 256
        return max(DEFAULT_MAX_TOKENS, min(TRUNCATION_ESCALATION_CEILING, needed))

    def _call_llm(
        self,
        prompt: str,
        system_prompt: str = "",
        stream_callback: Callable[[str], None] | None = None,
        resp_type: str | None = None,
        log_title: str = "default",
        _tag: str = "",
        no_cache: bool = False,
        cancel_event: threading.Event | None = None,
        output_token_source: str = "",
    ) -> str | dict | None:
        """调用统一 LLM 网关（封装 _get_engine_config → ask_llm）。

        这是 _call_api() 的替代方法，所有 LLM 调用统一走此入口。

        ``cancel_event`` 透传给网关，使 worker 的 stop() 能中断在飞请求
        （流式立即中断，非流式在请求返回后立即放弃重试）。

        ``output_token_source`` 为"期望被完整复述/改写"的文本（批量输入或待润色
        正文），用于按长度给足输出预算。留空则用 prompt 长度估算。

        调用后 ``self._last_truncated`` 表示**最终**结果是否仍被 max_tokens 截断。
        """
        ec = self._get_engine_config()
        api_key = ec.get("api_key", "").strip()
        base_url = ec.get("base_url", "https://api.openai.com/v1")
        model = ec.get("model", "gpt-4o")
        timeout = ec.get("timeout", 30)

        use_stream = self._stream_mode or stream_callback is not None
        max_tokens = self._estimate_max_tokens(output_token_source or prompt)

        _alog(f"=== API REQUEST [{_tag}] prompt_len={len(prompt)} ===")
        _alog(f"  PROMPT: {prompt}")

        logger.info(
            "AI 纠错 API 请求 | model=%s | stream=%s | json=%s | translate=%s | max_tokens=%d",
            model,
            use_stream,
            self._json_mode,
            self._translate_mode,
            max_tokens,
        )
        logger.debug("Prompt(%d chars): %s", len(prompt), prompt[:200])

        truncated: list[bool] = []
        result = ask_llm(
            prompt=prompt,
            system_prompt=system_prompt,
            resp_type=resp_type,
            log_title=log_title,
            temperature=0.1,
            stream=use_stream,
            stream_callback=stream_callback,
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            max_tokens=max_tokens,
            no_cache=no_cache,
            cancel_event=cancel_event,
            truncated_out=truncated,
        )
        self._last_truncated = bool(truncated and truncated[0])

        if result is None:
            _alog(f"  RESPONSE [{_tag}]: None (all retries exhausted)")
            return None

        content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        _alog(f"  RESPONSE [{_tag}] len={len(content)}: {content[:500]}")
        return result

    # ── 环境提取 ───────────────────────────────────────────────

    def extract_environment(self, all_texts: list) -> str:
        """从全文摘要提取环境上下文（领域、氛围、主要内容），作为 system prompt 的补充。

        Args:
            all_texts: 全部 OCR 结果文本列表

        Returns:
            环境描述字符串，失败返回空字符串
        """
        if not all_texts:
            return ""
        combined = "\n".join(str(t) for t in all_texts[:100])
        if len(combined) < 20:
            return ""
        prompt = self._summary_prompt + "\n\nOCR文本：\n" + combined[:4000]
        try:
            result = self._call_llm(
                prompt=prompt,
                system_prompt="你是一个文本分析助手，擅长总结和归纳。",
                resp_type=None,
                log_title="env_extract",
                no_cache=True,
            )
            if result is None:
                return ""
            self._env_context = str(result).strip()
            if self._env_context:
                logger.info("环境提取完成: %d chars", len(self._env_context))
            return self._env_context
        except Exception as e:
            logger.error("环境提取失败: %s", e)
            return ""

    # ── 单条纠错 ───────────────────────────────────────────────

    def correct(
        self,
        raw_text: str,
        context_texts: list | None = None,
        image: np.ndarray | None = None,
        stream_callback: Callable[[str], None] | None = None,
        prompt_override: str = "",
        cancel_event: threading.Event | None = None,
    ) -> str | None:
        """对一段原始 OCR 文本进行纠错（或重新识别）。

        Args:
            raw_text: OCR 识别的原始文本
            context_texts: 前后文文本列表（仅 API 模式使用）
            image: 原始帧 ROI 图像（仅本地引擎模式使用）
            stream_callback: 流式回调，接收每次 chunk 的文本片段

        Returns:
            纠正后的文本，若失败返回 None
        """
        if not raw_text.strip():
            return raw_text

        # ── 本地引擎模式：直接调用引擎的 recognize() 重新识别 ──
        if self._is_local_engine():
            if image is None:
                logger.error("本地引擎模式需要提供 image 参数")
                return None
            return self._correct_local(image)

        # ── API 引擎模式 ──
        context_str = ""
        if context_texts:
            context_str = "\n".join(f"[{i + 1}] {t}" for i, t in enumerate(context_texts[-5:]))

        # ── 模板模式 vs 自定义模式 ──
        if prompt_override:
            prompt = self._resolve_placeholders(
                prompt_override,
                raw_text=raw_text,
                context=context_str,
                env_context=self._env_context,
            )
        elif self._translate_mode:
            user_hint = self._prompt_template.strip()
            custom = ""
            # 仅在用户真正自定义过提示词时注入风格参考（默认为校对提示词，
            # 作为翻译风格参考毫无意义）
            if user_hint and not self.is_default_correction_prompt():
                custom = f"\n风格参考：{user_hint}"
            prompt = (
                f"{context_str}\n\n"
                f"请将以下 OCR 文本翻译为中文。{custom}\n"
                f"要求：译文自然流畅、符合中文字幕习惯；专有名词保留原文；"
                f"中英混排时保留英文仅翻译中文。\n\n"
                f"原文：\n{raw_text}"
            )
        else:
            prompt = self._resolve_placeholders(
                self._prompt_template,
                raw_text=raw_text,
                context=context_str,
                env_context=self._env_context,
            )

        system_prompt = self._build_system_prompt(env_context=self._env_context)
        resp_type = "json" if self._json_mode else None

        result = self._call_llm(
            prompt=prompt,
            system_prompt=system_prompt,
            stream_callback=stream_callback,
            resp_type=resp_type,
            log_title="correction",
            _tag="row",
            cancel_event=cancel_event,
            output_token_source=raw_text,
        )

        if result is None:
            return None

        # 输出被 max_tokens 截断：单行纠错拿到的就是残缺文本，绝不能写回字幕行。
        # 返回 None 让调用方保留原文（fail-safe）。
        if self._last_truncated:
            logger.warning("单行纠错输出被截断，放弃本次结果（保留原文）: %s", raw_text[:40])
            return None

        content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

        # ── JSON 模式：解析 JSON 提取实际文本 ──
        if self._json_mode and isinstance(result, dict):
            data = result
            # 支持更多可能的 key 名称（AI 可能使用不同的字段名）
            items = (
                data.get("results")
                or data.get("items")
                or data.get("data")
                or data.get("corrections")
                or data.get("output")
                or []
            )
            if isinstance(items, list) and items:
                first = items[0]
                if isinstance(first, dict):
                    content = first.get("text") or first.get("content") or first.get("corrected") or content
            elif isinstance(items, dict):
                # 单个结果 dict（非 list）
                content = items.get("text") or items.get("content") or items.get("corrected") or content
            elif isinstance(data, dict):
                content = (
                    data.get("text")
                    or data.get("content")
                    or data.get("corrected")
                    or data.get("answer")
                    or data.get("output")
                    or content
                )

        # 用正则剔除输出格式标记外壳（仅从头尾移除，避免误伤内容中的字符）
        fmt = self._output_format.strip()
        if fmt:
            import re

            escaped = re.escape(fmt)
            content = re.sub(f"^{escaped}[\\s\\n]*", "", str(content))
            content = re.sub(f"[\\s\\n]*{escaped}$", "", content)
            content = content.strip()
        if content and content != raw_text:
            return str(content)
        return raw_text

    # ── 批量纠错 ───────────────────────────────────────────────

    def correct_batch(
        self,
        texts: list[tuple[int, str]],
        max_retries: int | None = None,
        stream_callback: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[int, str]:
        """批量对多条文本进行 AI 纠错/翻译。

        Args:
            texts: [(row_idx, text), ...]
            max_retries: 解析失败时的最大重试次数
            stream_callback: 流式回调
            cancel_event: 协作式取消信号，置位后立即停止重试并返回原文填充结果
        """
        if not texts:
            return {}

        id_map, batch_text, original_map = self._prepare_batch_input(texts)

        prompt = self._build_correction_prompt(batch_text)
        system_prompt = self._build_system_prompt(env_context=self._env_context)
        resp_type = "json" if self._json_mode else None

        if max_retries is None:
            max_retries = self._retry  # 默认从配置 retry_on_failure 读取

        last_error = ""
        for attempt in range(max_retries + 1):
            # 协作式取消：worker.stop() 置位后立即停止重试（不必等重试全部走完），
            # 直接返回原文填充结果 —— 停止操作对用户即刻可见。
            if cancel_event is not None and cancel_event.is_set():
                logger.info("批量纠错已取消，用原文填充（attempt=%d）", attempt)
                cancelled: dict[int, str] = {}
                self._fill_missing_with_original(cancelled, id_map, original_map)
                return cancelled
            result = self._call_llm(
                prompt=prompt,
                system_prompt=system_prompt,
                stream_callback=stream_callback,
                resp_type=resp_type,
                log_title="correction_batch",
                _tag="correct_batch",
                cancel_event=cancel_event,
                output_token_source=batch_text,
            )
            if result is None:
                last_error = "API 返回空"
                logger.warning("批量纠错 API 返回空，ask_llm 内部重试已耗尽，跳出外层循环")
                # ❌ 不继续外层重试：ask_llm 内部 @except_handler 已重试 4 次
                break

            # 输出被截断：最后一条 [ID:n] 可能只写了一半（例如 "[ID:3] 你好世"）。
            # 若原样采纳就会把半句写进字幕行 —— 这里丢弃**最后一条**解析结果，
            # 缺失行由 _fill_missing_with_original 用原文补齐，其余行照常生效。
            truncated = self._last_truncated
            if truncated:
                logger.warning("批量纠错输出被截断，丢弃可能不完整的最后一条解析结果")

            content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

            # ── JSON 模式：直接传 dict，避免 json.dumps → json.loads 往返 ──
            if self._json_mode:
                corrected = self._try_parse_json_batch(result, id_map, original_map)
                if corrected is not None:
                    _alog(f"  CORRECT PARSED corrected_map_len={len(corrected)}")
                    if not corrected:
                        self._fill_missing_with_original(corrected, id_map, original_map)
                    return corrected if corrected else {}
                if attempt >= max_retries:
                    logger.error("批量 JSON 解析最终失败，用原文填充")
                    fallback: dict[int, str] = {}
                    self._fill_missing_with_original(fallback, id_map, original_map)
                    return fallback
                logger.warning("JSON 解析失败 (第%d次), 重试...", attempt + 1)
                continue

            # ── 文本模式 ──
            parsed = self._parse_batch_result(content)
            if truncated and parsed:
                parsed.pop(max(parsed), None)
            if not parsed and attempt < max_retries:
                logger.warning("解析全空 (第%d次)，重试...", attempt + 1)
                continue
            if not parsed:
                logger.warning("批量解析最终空（已达最大重试次数），用原文填充")
                fallback: dict[int, str] = {}
                self._fill_missing_with_original(fallback, id_map, original_map)
                return fallback

            corrected_map = self._build_result_map(parsed, id_map, original_map)
            self._reconcile_batch_result(parsed, id_map, "批量纠错(文本)")
            if not corrected_map and attempt < max_retries:
                logger.warning("所有行解析为空 (第%d次)，重试...", attempt + 1)
                continue

            return corrected_map

        logger.error("批量纠错最终失败: %s，用原文填充", last_error)
        fallback_final: dict[int, str] = {}
        self._fill_missing_with_original(fallback_final, id_map, original_map)
        return fallback_final

    # ── correct_batch 辅助方法 ──

    def _prepare_batch_input(self, texts):
        """预处理批量输入：建立 ID 映射、构建标记行、原文映射。"""
        lines, id_map, original_map = [], {}, {}
        for idx, item in enumerate(texts):
            row_idx = item[0]
            safe_text = item[1].replace("\n", " ").strip()
            lines.append(f"{ID_PREFIX}{idx}] {safe_text}")
            id_map[idx] = row_idx
            original_map[row_idx] = item[1]
        return id_map, "\n".join(lines), original_map

    def _build_correction_prompt(self, batch_text, is_1based: bool = False):
        """构建纠错/翻译 prompt。

        ``is_1based`` 保留仅为向后兼容（历史调用方传入）；ID 前缀一律由
        ``_format_batch`` 生成，本函数不依赖行号基数，故不使用该参数。
        """
        user_hint = self._prompt_template.strip()
        custom_hint = ""
        # 仅注入真正自定义过的提示词（默认提示词已由 system prompt 承载；
        # 此前用前 20 字符子串启发式判断，会误伤以相同字样开头的自定义提示词）
        if user_hint and not self.is_default_correction_prompt():
            custom_hint = f"用户额外参考（按需采纳）：{user_hint}\n"

        if self._translate_mode:
            task = (
                "请将上述文本翻译为中文。\n"
                "要求：\n"
                "- 逐行翻译，输出行数必须与输入行数一致\n"
                "- 如果上下文显示某行与相邻行是同一句话的碎片，将完整语义合并到该行翻译中，使每行译文语义完整\n"
                "- 翻译自然流畅，符合中文字幕表达习惯\n"
                "- 专有名词（人名、地名、作品名）保留原文或采用通用译名\n"
                "- 中英混排时保留英文原文，仅翻译中文部分\n"
                "- 单行不超过20个汉字，超出请适当精简"
            )
        else:
            task = (
                "请校对文本中的错误。\n"
                "要求：\n"
                "- 逐行校对，输出行数必须与输入行数一致\n"
                "- 只修正明显错误，不要改写原意\n"
                "- 如果上下文显示某行是不完整的碎片（与相邻行属于同一句话），"
                "将完整语义合并到该行中，使每行语义完整"
            )
        if self._json_mode:
            prompt = (
                f"以下是需要处理的内容：\n{batch_text}\n\n"
                f"{custom_hint}{task}\n\n"
                f"输出格式（严格遵守 JSON）：\n"
                f'{{"results": [{{"id": 0, "text": "处理后的第一行"}}, {{"id": 1, "text": "处理后的第二行"}}, ...]}}'
            )
        else:
            prompt = (
                f"以下是需要处理的内容：\n{batch_text}\n\n"
                f"{custom_hint}{task}\n\n"
                f"输出格式（严格遵守，每行一个 [ID:行号]）：\n"
                f"[ID:0] 处理后的第一行\n[ID:1] 处理后的第二行\n..."
            )
        return prompt

    @staticmethod
    def _reconcile_batch_result(parsed: dict, id_map: dict, log_title: str) -> None:
        """核对模型返回的批次行覆盖情况（诊断，不改变返回值）。

        批量纠错依赖模型回传**每一行**的 ``[ID:n]``。缺失行会被
        ``_fill_missing_with_original`` 用原文补齐 —— 这是安全兜底，但它**静默**
        掩盖了"模型只处理了前几行"这类系统性问题（典型原因：max_tokens 截断、
        模型提前收尾），让人误以为整批纠错已生效。此处显式记录缺失/多余行号。
        """
        try:
            seen = {int(k) for k in parsed}
        except (TypeError, ValueError):
            return
        missing = sorted(set(id_map) - seen)
        extra = sorted(seen - set(id_map))
        if missing:
            logger.warning(
                "%s：模型仅返回 %d/%d 行，缺失 %d 行将由原文补齐（批次下标示例: %s）",
                log_title,
                len(set(id_map) & seen),
                len(id_map),
                len(missing),
                missing[:10],
            )
        if extra:
            logger.warning("%s：模型返回了 %d 个不存在的行号，已忽略（示例: %s）", log_title, len(extra), extra[:10])

    def _try_parse_json_batch(self, result, id_map, original_map):
        """尝试从 JSON 响应解析批量结果。成功返回 dict，失败返回 None，全空返回 {}。

        JSON 解析失败时回退到 [ID:n] 文本格式解析（流式模式常见）。
        """
        try:
            data = json.loads(result) if isinstance(result, str) else result
        except (json.JSONDecodeError, TypeError):
            # 回退：尝试 [ID:n] 文本格式（流式模式 LLM 常返回此格式）
            if isinstance(result, str):
                parsed = self._parse_batch_result(result)
                if parsed:
                    self._reconcile_batch_result(parsed, id_map, "批量纠错(文本回退)")
                    return self._build_result_map(parsed, id_map, original_map)
            return None
        items = None
        if isinstance(data, dict):
            items = data.get("results") or data.get("items")
        if not isinstance(items, list):
            return None
        corrected_map = {}
        seen: dict[int, str] = {}
        for entry in items:
            if not isinstance(entry, dict):
                continue
            eid = entry.get("id") if "id" in entry else entry.get("index")
            etext = entry.get("text") or entry.get("content") or ""
            if eid is None:
                continue
            try:
                idx_in_batch = int(eid)
            except (ValueError, TypeError):
                continue
            seen[idx_in_batch] = etext
            if idx_in_batch in id_map:
                row_idx = id_map[idx_in_batch]
                clean = _clean_content(etext)
                if clean and clean != original_map.get(row_idx, "").strip():
                    corrected_map[row_idx] = clean
        self._reconcile_batch_result(seen, id_map, "批量纠错(JSON)")
        return corrected_map

    def _build_result_map(self, parsed, id_map, original_map):
        """从解析结果构建纠错映射（排除与原文相同的项）。"""
        corrected_map = {}
        for idx_in_batch, content in parsed.items():
            if idx_in_batch in id_map:
                row_idx = id_map[idx_in_batch]
                clean = _clean_content(content)
                if clean and clean != original_map.get(row_idx, "").strip():
                    corrected_map[row_idx] = clean
        return corrected_map

    @staticmethod
    def _fill_missing_with_original(corrected_map, id_map, original_map):
        """对 AI 未返回的行，用原文自动填充。"""
        for idx_in_batch, row_idx in id_map.items():
            if row_idx not in corrected_map:
                corrected_map[row_idx] = original_map.get(row_idx, "")

    @staticmethod
    def _parse_batch_result(text: str) -> dict[int, str]:
        """从 AI 返回文本中解析 [ID:idx] 标记的内容。

        容错处理：
        - 去除 markdown 代码块包裹
        - 兼容全角/半角冒号、大小写变体
        - 当标准正则无匹配时，回退到宽松模式

        Returns:
            {idx: content} 字典
        """
        cleaned = _MD_FENCE.sub("", text).strip()

        result = {}
        for match in ID_PATTERN.finditer(cleaned):
            try:
                idx = int(match.group(1))
                content = match.group(2).strip()
                if content:
                    result[idx] = content
            except (ValueError, IndexError):
                continue

        # 回退1：[0] text 格式（LLM 经常省略 ID:）
        if not result:
            _BRACKET = re.compile(r"^\[(\d+)\]\s*(.+)", re.MULTILINE)
            for match in _BRACKET.finditer(cleaned):
                try:
                    idx = int(match.group(1))
                    content = match.group(2).strip()
                    if content:
                        result[idx] = content
                except (ValueError, IndexError):
                    continue

        # 回退2：尝试从 JSON 中提取 {"results": [{"id": n, "text": "..."}]}
        if not result:
            try:
                import json as _json

                _data = _json.loads(cleaned)
                _items = _data.get("results") or _data.get("items") or _data.get("data") or []
                for _entry in _items:
                    if isinstance(_entry, dict):
                        _eid = _entry.get("id") if "id" in _entry else _entry.get("index")
                        _etext = _entry.get("text") or _entry.get("content") or ""
                        if _eid is not None and _etext:
                            result[int(_eid)] = _etext.strip()
            except Exception:
                pass

        # 回退3：宽松模式匹配 "数字. 文本" 或 "数字) 文本" 格式
        if not result:
            _LOOSE = re.compile(r"^(\d+)\s*[.)\:：]\s*(.+)", re.MULTILINE)
            for match in _LOOSE.finditer(cleaned):
                try:
                    idx = int(match.group(1))
                    content = match.group(2).strip()
                    if content:
                        result[idx] = content
                except (ValueError, IndexError):
                    continue

        return result

    def _is_local_engine(self) -> bool:
        """若引擎配置 type 为 local，则为本地引擎模式。"""
        if not self._engine_manager:
            return False
        eng_cfg = self._engine_manager.get_engine_config(self._engine_name)
        return eng_cfg.get("type") == "local"

    def _correct_local(self, image: np.ndarray) -> str | None:
        """调用本地引擎对图像重新识别。"""
        if self._engine_manager is None:
            logger.error("引擎管理器未初始化")
            return None
        try:
            eng = self._engine_manager.get_engine(self._engine_name)
            if eng is None:
                logger.error("引擎 [%s] 不可用", self._engine_name)
                return None
            result = eng.recognize(image)
            return result.strip() if result else None
        except Exception as e:
            logger.error("本地引擎识别失败: %s", e)
            return None

    # ── 润色模式 ─────────────────────────────────────────────

    def polish(
        self,
        original_text: str,
        corrected_text: str,
        cancel_event: threading.Event | None = None,
    ) -> str | None:
        """对纠错/翻译后的文本进行润色。

        Args:
            original_text: OCR 原始文本
            corrected_text: 已纠错或翻译后的文本
            cancel_event: 协作式取消信号，置位后放弃润色并返回原纠错文本

        Returns:
            润色后的文本，失败返回 None
        """
        if not self._polish_enabled:
            return corrected_text
        if not original_text.strip() or not corrected_text.strip():
            return corrected_text
        if cancel_event is not None and cancel_event.is_set():
            return corrected_text

        prompt = self._resolve_placeholders(
            self._polish_prompt,
            raw_text=original_text,
            context="",
            env_context=self._env_context,
        )
        # 额外替换 {待校对文本}
        prompt = prompt.replace(POLISH_TEXT_PLACEHOLDER, corrected_text)
        # 不变量：提示词必须真的包含待润色正文。用户/历史配置的 polish_prompt 可能
        # 完全不含占位符（域对象旧默认值即是被截断的无占位符句子），此时上面两步都是
        # 空操作，会把"没有正文的提示词"发给模型，返回内容与输入无关 —— 静默污染结果。
        if corrected_text not in prompt:
            logger.warning(
                "润色提示词缺少 %s 占位符（提示词=%r），已自动追加待润色正文",
                POLISH_TEXT_PLACEHOLDER,
                prompt[:60],
            )
            prompt = f"{prompt}\n\n原始文本：\n{original_text}\n待润色文本：\n{corrected_text}"
        # 环境上下文：判断"是否已注入"要看**环境正文本身是否出现**，而不是
        # 提示词里有没有"环境"两个字 —— 提示词正文（如"符合中文字幕习惯"段落）
        # 或用户自定义文案里出现该词就会让环境信息被永久跳过。
        if self._env_context and self._env_context not in prompt:
            prompt += f"\n\n【环境上下文（参考）】\n{self._env_context}"

        result = self._call_llm(
            prompt=prompt,
            system_prompt=DEFAULT_POLISH_SYSTEM_PROMPT,
            resp_type=None,
            log_title="polish",
            _tag="polish",
            cancel_event=cancel_event,
            output_token_source=corrected_text,
        )
        if result is None:
            logger.warning("润色 API 失败 (row)，使用原文")
            return corrected_text
        content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        # 输出被 max_tokens 截断：润色结果是"改写后的全文"，被切掉尾部就是内容丢失
        # （实测 480 字输入只回来 43 字）。此时宁可保留未润色的纠错文本。
        if self._last_truncated:
            logger.warning(
                "润色输出被截断（输入 %d 字，返回 %d 字），保留未润色文本", len(corrected_text), len(content)
            )
            return corrected_text
        return content.strip() or corrected_text
