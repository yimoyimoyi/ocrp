"""AI 纠错模块 —— 通过引擎配置进行二次纠错。

若引擎为 API 类型（openai_vision / ollama_vision / llamacpp），
则调用对应 API 对文本进行校对；

若引擎为 local 类型（paddleocr），
则直接调用引擎的 recognize() 对原图 ROI 重新识别。
"""

import json
import os
import re
from collections.abc import Callable
from pathlib import Path

import numpy as np

from core.config_manager import _load_json_with_comments
from core.llm_utils import ask_llm
from core.logger import get_logger

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
            _ALOG.write_bytes(data[len(data) // 2:])
    except OSError:
        pass
    with open(_ALOG, "a", encoding="utf-8") as f:
        f.write(f"{_adt.datetime.now().strftime('%H:%M:%S.%f')[:-3]} [AI] {msg}\n")

# ── 批量纠错 ID 前缀常量 ──
ID_PREFIX = "[ID:"
# 兼容 AI 输出变体：[ID:0]、[ID：0]、[ID 0]、ID:0、[id:0]
ID_PATTERN = re.compile(
    r'\[\s*ID\s*[:：]\s*(\d+)\s*\](.*?)(?=\n\[\s*ID\s*[:：]\s*\d+\s*\]|\Z)',
    re.DOTALL | re.IGNORECASE,
)
# 匹配时间标记 [hh:mm:ss.ms -> hh:mm:ss.ms] 或 [hh:mm:ss] 或 (hh:mm:ss)
TIME_MARKER = re.compile(
    r'\s*[(\[]\s*\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?\s*(?:->|→|-{1,2}>|,)\s*\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?\s*[)\]]\s*'
    r'|\s*\[\s*\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?\s*\]\s*',
)
ID_TAG = re.compile(r'\[\s*ID\s*[:：]?\s*\d+\s*\]\s*', re.IGNORECASE)
# AI 可能输出的 markdown 代码块包裹
_MD_FENCE = re.compile(r'^```(?:json|text)?\s*\n?|\n?```\s*$', re.MULTILINE)


def _clean_content(text: str) -> str:
    """去除 AI 可能附带的时间标记、[ID:n] 标记和 markdown 代码块。"""
    text = _MD_FENCE.sub('', text)
    text = TIME_MARKER.sub('', text)
    text = ID_TAG.sub('', text)
    return text.strip()


def load_correction_config() -> dict:
    """加载 ai_correction.json 配置。"""
    path = CONFIG_DIR / "ai_correction.json"
    if path.exists():
        try:
            cfg = _load_json_with_comments(path)
            from core.config_schema import validate_config
            from core.config_schemas import AI_CORRECTION_SCHEMA
            validate_config(cfg, AI_CORRECTION_SCHEMA, "ai_correction.json")
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

    def __init__(self, config: dict | None = None,
                 engine_manager=None, preset_name: str = ""):
        self._config = config or load_correction_config()
        self._enabled = self._config.get("enabled", False)
        self._retry = self._config.get("retry_on_failure", 2)
        self._prompt_template = self._config.get("correction_prompt",
            "你是一个文本校对专家。请根据上下文纠正OCR识别结果中的明显错误，保留原格式。")
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
        self._translate_mode: bool = False  # 翻译模式，由外部 setter 设置
        self._stream_mode: bool = False     # 流式输出模式
        self._json_mode: bool = False       # JSON 输出模式
        # ── 自定义提示词字段 ──
        self._summary_prompt = self._config.get("summary_prompt",
            "请根据以下OCR识别文本，总结出这段内容的：\n"
            "1. 领域/类型（如：小说、新闻、游戏对话、学术论文等）\n"
            "2. 整体氛围/语气（如：严肃、欢快、悲伤、紧张等）\n"
            "3. 主要内容/主题（一句话概括）\n\n"
            "请用简洁的中文回答，格式：\n"
            "领域：xxx\n氛围：xxx\n内容：xxx")
        self._correction_system_prompt = self._config.get("correction_system_prompt",
            "你是一个专业的字幕校对助手。你接收带有时间轴的OCR识别文本列表，"
            "逐行校对，保持行数不变，只修正明显错误，不要合并或拆分条目。"
            "用户自定义提示词作为额外参考。只返回修正后的结果。")
        self._output_format = self._config.get("output_format", "[纠正后文本]")
        self._seg_time_gap: float = self._config.get("seg_time_gap", 3.0)
        # ── 润色模式字段 ──
        self._use_template: bool = self._config.get("use_template", False)
        self._template_content: str = ""  # 由 ConfigPanel 或 main_window 设置
        self._polish_enabled: bool = self._config.get("enable_polish", False)
        self._polish_prompt = self._config.get("polish_prompt",
            "你是一个专业的字幕润色专家。请对以下已翻译/纠错后的字幕文本进行润色：\n"
            "1. 调整语序使表达更自然流畅\n2. 统一术语和风格\n3. 精简冗余表达\n"
            "4. 确保符合中文字幕习惯\n\n"
            "原始文本：{原始结果}\n"
            "待润色文本：{待校对文本}\n\n"
            "请直接输出润色后的文本，不要附加说明。")

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
        _use_tpl = self._use_template
        _tpl_content = self._template_content
        _env_ctx = self._env_context
        _extract = self._extract_env
        _seg_tg = self._seg_time_gap

        self._config = load_correction_config()
        self._enabled = self._config.get("enabled", False)
        self._retry = self._config.get("retry_on_failure", 2)
        self._prompt_template = self._config.get("correction_prompt",
            "你是一个文本校对专家。请根据上下文纠正OCR识别结果中的明显错误，保留原格式。")
        self._engine_name = self._config.get("engine", "llamacpp")
        api_cfg = _resolve_api_config(self._config, self._preset_name)
        self._api_key = api_cfg.get("api_key", "").strip()
        self._base_url = api_cfg.get("base_url", "http://127.0.0.1:8080")
        self._model = api_cfg.get("model", "")
        self._timeout = api_cfg.get("timeout", 30)
        self._summary_prompt = self._config.get("summary_prompt",
            "请根据以下OCR识别文本，总结出这段内容的：\n"
            "1. 领域/类型（如：小说、新闻、游戏对话、学术论文等）\n"
            "2. 整体氛围/语气（如：严肃、欢快、悲伤、紧张等）\n"
            "3. 主要内容/主题（一句话概括）\n\n"
            "请用简洁的中文回答，格式：\n"
            "领域：xxx\n氛围：xxx\n内容：xxx")
        self._correction_system_prompt = self._config.get("correction_system_prompt",
            "你是一个专业的字幕校对助手。你接收带有时间轴的OCR识别文本列表，"
            "逐行校对，保持行数不变，只修正明显错误，不要合并或拆分条目。"
            "用户自定义提示词作为额外参考。只返回修正后的结果。")
        self._output_format = self._config.get("output_format", "[纠正后文本]")
        self._seg_time_gap = self._config.get("seg_time_gap", 3.0)
        self._polish_enabled = self._config.get("enable_polish", False)
        self._polish_prompt = self._config.get("polish_prompt",
            "你是一个专业的字幕润色专家。请对以下已翻译/纠错后的字幕文本进行润色：\n"
            "1. 调整语序使表达更自然流畅\n2. 统一术语和风格\n3. 精简冗余表达\n"
            "4. 确保符合中文字幕习惯\n\n"
            "原始文本：{原始结果}\n"
            "待润色文本：{待校对文本}\n\n"
            "请直接输出润色后的文本，不要附加说明。")

        # 恢复运行时状态
        self._translate_mode = _translate
        self._stream_mode = _stream
        self._json_mode = _json
        self._use_template = _use_tpl
        self._template_content = _tpl_content
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
    def use_template(self) -> bool:
        return self._use_template

    @use_template.setter
    def use_template(self, val: bool):
        self._use_template = val

    def set_template_content(self, content: str):
        """设置当前选中的模板内容（由 ConfigPanel 或 main_window 注入）。"""
        self._template_content = content

    def apply_preset(self, preset_name: str):
        """切换 API 预设。"""
        self._preset_name = preset_name
        api_cfg = _resolve_api_config(self._config, preset_name)
        self._api_key = api_cfg.get("api_key", "").strip()
        self._base_url = api_cfg.get("base_url", "http://127.0.0.1:8080")
        self._model = api_cfg.get("model", "")
        self._timeout = api_cfg.get("timeout", 30)
        logger.info("已切换 API 预设: %s", preset_name or '默认')

    @property
    def extract_env(self) -> bool:
        return self._extract_env

    @extract_env.setter
    def extract_env(self, val: bool):
        self._extract_env = val

    def _should_skip_env_extraction(self) -> bool:
        """判断是否跳过环境提取 API 调用。

        跳过条件（满足任一）：
        1. extract_env 开关已打开（用户手动管理环境上下文）
        2. _env_context 已存在（已提取过）
        3. 纠错/翻译 prompt 中已包含环境相关占位符或关键词
        """
        if self._extract_env:
            return True
        if self._env_context:
            return True
        env_keywords = ("{环境信息}", "环境上下文", "领域", "氛围", "环境描述")
        combined_prompt = (
            self._prompt_template + self._template_content +
            self._summary_prompt + self._polish_prompt
        )
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
    def _resolve_placeholders(template: str, raw_text: str = "", context: str = "",
                               env_context: str = "", timestamp: str = "",
                               region: str = "", engine: str = "",
                               language: str = "") -> str:
        """替换提示词模板中的占位符。

        支持的占位符:
            {原始结果} / [原始文本] → 当前 OCR 原始文本
            {上下文}               → 前后文文本
            {环境信息}             → 全文环境提取结果
            {时间戳}               → 当前条目的时间戳
            {区域}                 → 区域名称（字幕/语音等）
            {引擎}                 → OCR 引擎名称
            {语言}                 → 检测/设置的语言
        """
        result = template
        result = result.replace("{原始结果}", raw_text)
        result = result.replace("[原始文本]", raw_text)
        result = result.replace("{上下文}", context)
        result = result.replace("{环境信息}", env_context)
        result = result.replace("{时间戳}", timestamp)
        result = result.replace("{区域}", region)
        result = result.replace("{引擎}", engine)
        result = result.replace("{语言}", language)
        return result

    def _build_system_prompt(self, env_context: str = "") -> str:
        """构建 system prompt（翻译/校对 + 环境上下文 + JSON 格式指令）。"""
        if self._translate_mode:
            system_msg = (
                "你是一个专业的字幕翻译助手。你接收带有时间轴（起止时间）的OCR识别文本列表，"
                "将每行 OCR 文本翻译为中文。保持原始行号前缀 [ID:行号]，逐行翻译。"
                "如果上下文显示某行是不完整的碎片（与上一行或下一行属于同一句话），"
                "将完整语义合并到该行的翻译中，使每行译文语义完整自然。"
                "保持输出行数与输入一致，每个 [ID:行号] 对应一行。"
                "用户自定义提示词仅作为翻译风格参考。只返回翻译后的结果。"
            )
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

    def _call_llm(self, prompt: str, system_prompt: str = "",
                  stream_callback: Callable[[str], None] | None = None,
                  resp_type: str | None = None,
                  log_title: str = "default",
                  _tag: str = "",
                  no_cache: bool = False) -> str | dict | None:
        """调用统一 LLM 网关（封装 _get_engine_config → ask_llm）。

        这是 _call_api() 的替代方法，所有 LLM 调用统一走此入口。
        """
        ec = self._get_engine_config()
        api_key = ec.get("api_key", "").strip()
        base_url = ec.get("base_url", "https://api.openai.com/v1")
        model = ec.get("model", "gpt-4o")
        timeout = ec.get("timeout", 30)

        use_stream = self._stream_mode or stream_callback is not None

        _alog(f"=== API REQUEST [{_tag}] prompt_len={len(prompt)} ===")
        _alog(f"  PROMPT: {prompt}")

        logger.info("AI 纠错 API 请求 | model=%s | stream=%s | json=%s | translate=%s",
                     model, use_stream, self._json_mode, self._translate_mode)
        logger.debug("Prompt(%d chars): %s", len(prompt), prompt[:200])

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
            no_cache=no_cache,
        )

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

    def correct(self, raw_text: str, context_texts: list | None = None,
                image: np.ndarray | None = None,
                stream_callback: Callable[[str], None] | None = None,
                prompt_override: str = "") -> str | None:
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
            context_str = "\n".join(
                f"[{i+1}] {t}" for i, t in enumerate(context_texts[-5:])
            )

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
            if user_hint and "文本校对" not in user_hint[:20]:
                custom = f"\n风格参考：{user_hint}"
            prompt = (
                f"{context_str}\n\n"
                f"请将以下 OCR 文本翻译为中文。{custom}\n"
                f"要求：译文自然流畅、符合中文字幕习惯；专有名词保留原文；"
                f"中英混排时保留英文仅翻译中文。\n\n"
                f"原文：\n{raw_text}"
            )
        elif self._use_template and self._template_content:
            # 模板模式：模板内容直接作为 prompt
            prompt = self._resolve_placeholders(
                self._template_content,
                raw_text=raw_text,
                context=context_str,
                env_context=self._env_context,
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
        )

        if result is None:
            return None

        content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

        # ── JSON 模式：解析 JSON 提取实际文本 ──
        if self._json_mode and isinstance(result, dict):
            data = result
            items = data.get("results") or data.get("items") or data.get("data") or []
            if isinstance(items, list) and items:
                first = items[0]
                if isinstance(first, dict):
                    content = first.get("text") or first.get("content") or content
            elif isinstance(data, dict):
                content = data.get("text") or data.get("content") or data.get("corrected") or content

        # 用自定义输出格式标记剔除格式外壳
        fmt = self._output_format.strip()
        if fmt:
            content = str(content).replace(fmt, "").strip()
        if content and content != raw_text:
            return str(content)
        return raw_text

    # ── 批量纠错 ───────────────────────────────────────────────

    def _fmt_time(self, sec: float) -> str:
        """格式化秒数为 SRT 时间串 H:MM:SS.mmm。"""
        if sec is None:
            return ""
        sec = sec or 0.0
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = int((sec - int(sec)) * 1000)
        return f"{h}:{m:02d}:{s:02d}.{ms:03d}"

    def correct_batch(self, texts: list[tuple[int, str]],
                      context_window: int = 3,
                      max_retries: int = 1,
                      stream_callback: Callable[[str], None] | None = None) -> dict[int, str]:
        """批量对多条文本进行 AI 纠错/翻译。

        Args:
            texts: [(row_idx, text), ...]
        """
        if not texts:
            return {}

        id_map, batch_text, original_map = self._prepare_batch_input(texts)

        context_block = self._build_context_block(texts, context_window)
        prompt = self._build_correction_prompt(batch_text, context_block)
        system_prompt = self._build_system_prompt(env_context=self._env_context)
        resp_type = "json" if self._json_mode else None

        last_error = ""
        for attempt in range(max_retries + 1):
            result = self._call_llm(
                prompt=prompt,
                system_prompt=system_prompt,
                stream_callback=stream_callback,
                resp_type=resp_type,
                log_title="correction_batch",
                _tag="correct_batch",
            )
            if result is None:
                last_error = "API 返回空"
                continue

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
            if not parsed and attempt < max_retries:
                logger.warning("解析全空 (第%d次)，重试...", attempt + 1)
                continue
            if not parsed:
                logger.warning("批量解析最终空（已达最大重试次数），用原文填充")
                fallback: dict[int, str] = {}
                self._fill_missing_with_original(fallback, id_map, original_map)
                return fallback

            corrected_map = self._build_result_map(parsed, id_map, original_map)
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

    def _build_context_block(self, texts, context_window):
        """为批次构建简洁的上下文（前后各 context_window 条）。"""
        if len(texts) <= 1:
            return ""
        # 取批次自身的前后文（批次内部的条目已经在 batch_text 中）
        # 这里只提供批次外的上下文，由调用方传入 full_texts 时才有意义
        # 简化：不构建冗余上下文，批次内条目本身已足够
        return ""

    def _build_correction_prompt(self, batch_text, context_block, is_1based: bool = False):
        """构建纠错/翻译 prompt。"""
        user_hint = self._prompt_template.strip()
        custom_hint = ""
        if user_hint and "文本校对" not in user_hint[:20] and "翻译以下" not in user_hint[:20]:
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
                f"{context_block}以下是需要处理的内容：\n{batch_text}\n\n"
                f"{custom_hint}{task}\n\n"
                f'输出格式（严格遵守 JSON）：\n'
                f'{{"results": [{{"id": 0, "text": "处理后的第一行"}}, {{"id": 1, "text": "处理后的第二行"}}, ...]}}'
            )
        else:
            prompt = (
                f"{context_block}以下是需要处理的内容：\n{batch_text}\n\n"
                f"{custom_hint}{task}\n\n"
                f"输出格式（严格遵守，每行一个 [ID:行号]）：\n"
                f"[ID:0] 处理后的第一行\n[ID:1] 处理后的第二行\n..."
            )
        return prompt

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
                    return self._build_result_map(parsed, id_map, original_map)
            return None
        items = None
        if isinstance(data, dict):
            items = data.get("results") or data.get("items")
        if not isinstance(items, list):
            return None
        corrected_map = {}
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
            if idx_in_batch in id_map:
                row_idx = id_map[idx_in_batch]
                clean = _clean_content(etext)
                if clean and clean != original_map.get(row_idx, "").strip():
                    corrected_map[row_idx] = clean
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
        cleaned = _MD_FENCE.sub('', text).strip()

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
            _BRACKET = re.compile(r'^\[(\d+)\]\s*(.+)', re.MULTILINE)
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
            _LOOSE = re.compile(r'^(\d+)\s*[.)\:：]\s*(.+)', re.MULTILINE)
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

    def polish(self, original_text: str, corrected_text: str) -> str | None:
        """对纠错/翻译后的文本进行润色。

        Args:
            original_text: OCR 原始文本
            corrected_text: 已纠错或翻译后的文本

        Returns:
            润色后的文本，失败返回 None
        """
        if not self._polish_enabled:
            return corrected_text
        if not original_text.strip() or not corrected_text.strip():
            return corrected_text

        prompt = self._resolve_placeholders(
            self._polish_prompt,
            raw_text=original_text,
            context="",
            env_context=self._env_context,
        )
        # 额外替换 {待校对文本}
        prompt = prompt.replace("{待校对文本}", corrected_text)
        # 若 prompt 中不含环境信息但 _env_context 已设置，强制附加
        if self._env_context and "环境" not in prompt:
            prompt += f"\n\n【环境上下文（参考）】\n{self._env_context}"

        result = self._call_llm(
            prompt=prompt,
            system_prompt="你是一个专业的字幕润色专家。只输出最终的润色结果文本。",
            resp_type=None,
            log_title="polish",
            _tag="polish",
        )
        if result is None:
            return corrected_text
        content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        return content.strip() or corrected_text
