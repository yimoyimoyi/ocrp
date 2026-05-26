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
from core.logger import get_logger
from core.llm_utils import ask_llm

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = BASE_DIR / "config"

logger = get_logger(__name__)

# [DEBUG] 临时调试日志 —— LLM 输入输出
import datetime as _adt
_ALOG = BASE_DIR / "logs" / "debug_seg.log"
_ALOG.parent.mkdir(parents=True, exist_ok=True)


def _alog(msg: str):
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
            "你是一个专业的字幕校对助手。你接收带有时间轴（起止时间）的OCR识别文本列表，"
            "输出时保留原始行号前缀，可根据语义合并或拆分条目，并为每条重新设定合理的起止时间。"
            "用户自定义提示词作为额外参考，不要被其限死输出格式。只返回修正后的结果。")
        self._output_format = self._config.get("output_format", "[纠正后文本]")
        # ── 分句模式字段 ──
        self._sentence_segmentation_enabled = self._config.get("enable_sentence_segmentation", False)
        self._segmentation_prompt = self._config.get("sentence_segmentation_prompt",
            "你是一个字幕分句专家。请根据语义将以下碎片化的OCR识别文本合并为完整的字幕条目。")
        self._segmentation_system_prompt = self._config.get("sentence_segmentation_system_prompt",
            "你是一个字幕分句助手。输入是连续的时间轴文本片段。你的任务是决定哪些连续行应该合并为一句话，并逐字拼接（仅可加标点）。禁止添加、删除或改写任何文字。只输出JSON，不要加任何说明。")
        # ── 校对模式字段 ──
        self._use_template: bool = self._config.get("use_template", False)
        self._template_content: str = ""  # 由 ConfigPanel 或 main_window 设置
        self._proofread_enabled: bool = self._config.get("enable_proofread", False)
        self._proofread_prompt = self._config.get("proofread_prompt",
            "你是一个专业的字幕校对审核员。请检查以下已翻译/纠错后的字幕文本，找出并修正：\n"
            "1. 语法错误或不自然的表达\n2. 术语翻译不一致\n3. 遗漏或多余的信息\n"
            "4. 不符合上下文语境的用词\n\n"
            "原始文本：{原始结果}\n"
            "待校对文本：{待校对文本}\n\n"
            "请直接输出校对后的文本，不要附加说明。")

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, val: bool):
        self._enabled = val

    def reload_config(self):
        """重新加载 ai_correction.json 配置。"""
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
            "1. 领域/类型\n2. 整体氛围/语气\n3. 主要内容/主题")
        self._correction_system_prompt = self._config.get("correction_system_prompt",
            "你是一个专业的字幕校对助手。你接收带有时间轴（起止时间）的OCR识别文本列表，"
            "输出时保留原始行号前缀，可根据语义合并或拆分条目，并为每条重新设定合理的起止时间。"
            "用户自定义提示词作为额外参考，不要被其限死输出格式。只返回修正后的结果。")
        self._output_format = self._config.get("output_format", "[纠正后文本]")
        self._sentence_segmentation_enabled = self._config.get("enable_sentence_segmentation", False)
        self._segmentation_prompt = self._config.get("sentence_segmentation_prompt",
            "你是一个字幕分句专家。请根据语义将以下碎片化的OCR识别文本合并为完整的字幕条目。")
        self._segmentation_system_prompt = self._config.get("sentence_segmentation_system_prompt",
            "你是一个专业的字幕分句助手。你接收带有时间轴的OCR识别文本碎片列表，根据语义将碎片合并为完整字幕句子。只输出JSON结果，不要添加任何说明文字。")
        self._use_template = self._config.get("use_template", False)
        self._proofread_enabled = self._config.get("enable_proofread", False)
        self._proofread_prompt = self._config.get("proofread_prompt",
            "你是一个专业的字幕校对审核员...")

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
    def sentence_segmentation_enabled(self) -> bool:
        return self._sentence_segmentation_enabled

    @sentence_segmentation_enabled.setter
    def sentence_segmentation_enabled(self, val: bool):
        self._sentence_segmentation_enabled = val

    @property
    def proofread_enabled(self) -> bool:
        return self._proofread_enabled

    @proofread_enabled.setter
    def proofread_enabled(self, val: bool):
        self._proofread_enabled = val

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
                "将每行 OCR 文本翻译为中文。保持原始行号前缀 [ID:行号]，可根据语义合并或拆分条目，"
                "并为每条重新设定合理的起止时间。用户自定义提示词仅作为翻译风格参考，不要被其限死输出格式。"
                "只返回翻译后的结果。"
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
                  _tag: str = "") -> str | dict | None:
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
                stream_callback: Callable[[str], None] | None = None) -> str | None:
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
        if self._translate_mode:
            user_hint = self._prompt_template.strip()
            custom = ""
            if user_hint and "文本校对" not in user_hint[:20]:
                custom = f"（风格参考：{user_hint}）"
            prompt = f"{context_str}\n\n请将以下 OCR 文本翻译为中文{custom}：\n{raw_text}"
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
            _tag=f"row",
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
                      max_retries: int = 3,
                      stream_callback: Callable[[str], None] | None = None,
                      raw_reference: list[str] | None = None) -> dict[int, str]:
        """批量对多条文本进行 AI 纠错。

        Args:
            texts: [(row_idx, raw_text), ...]
            raw_reference: 原始结果全文（用于分句后纠错的原文参考）
        """
        if not texts:
            return {}

        id_map, batch_text, original_map = self._prepare_batch_input(texts, raw_reference)
        n = len(texts)

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
                    return corrected if corrected else {}
                if attempt >= max_retries:
                    logger.error("批量 JSON 解析最终失败")
                    return {}
                prompt = self._append_retry_hint(prompt, "JSON 解析失败，请重试", attempt)
                continue

            # ── 文本模式 ──
            parsed = self._parse_batch_result(content)
            if not parsed and attempt < max_retries:
                logger.warning("解析全空 (第%d次)，重试...", attempt + 1)
                prompt = self._append_retry_hint(prompt, "未返回任何 [ID:行号]", attempt)
                continue
            if not parsed:
                logger.warning("批量解析最终空（已达最大重试次数）")
                return {}

            corrected_map = self._build_result_map(parsed, id_map, original_map)
            if not corrected_map and attempt < max_retries:
                logger.warning("所有行解析为空 (第%d次)，重试...", attempt + 1)
                prompt = self._append_retry_hint(prompt, "返回内容全部为空", attempt)
                continue

            # 缺失行用原文填充
            self._fill_missing_with_original(corrected_map, id_map, original_map, n)
            return corrected_map

        logger.error("批量纠错最终失败: %s", last_error)
        return {}

    # ── correct_batch 辅助方法 ──

    def _prepare_batch_input(self, texts, raw_reference: list[str] | None = None):
        """预处理批量输入：建立 ID 映射、构建标记行、原文映射。

        当 raw_reference 提供时，构建双段格式（原始结果 + 分句后的结果），
        texts 作为分句结果使用 1-based 序号。
        """
        lines, id_map, original_map = [], {}, {}
        if raw_reference:
            # 双段格式：原始结果参考 + 分句后的结果
            lines.append("原始结果{")
            for i, raw in enumerate(raw_reference):
                lines.append(f"  [{i}] {raw}")
            lines.append("}")
            lines.append("")
            lines.append("分句后的结果{")
            for idx, item in enumerate(texts):
                row_idx = item[0]
                safe_text = item[1].replace("\n", " ").strip()
                seq = idx + 1  # 1-based 序号
                lines.append(f"  {seq}. {safe_text}")
                id_map[seq] = row_idx       # 序号 → 表格行号
                original_map[row_idx] = item[1]
            lines.append("}")
        else:
            for idx, item in enumerate(texts):
                row_idx = item[0]
                safe_text = item[1].replace("\n", " ").strip()
                lines.append(f"{ID_PREFIX}{idx}] {safe_text}")
                id_map[idx] = row_idx
                original_map[row_idx] = item[1]
        return id_map, "\n".join(lines), original_map

    def _build_context_block(self, texts, context_window):
        """为每个条目构建上下文参考文本块。"""
        all_results = [r[1] for r in texts]
        ctx = []
        for i in range(len(texts)):
            start = max(0, i - context_window)
            end = min(len(all_results), i + context_window + 1)
            ctx_lines = [f"[{j}] {all_results[j]}" for j in range(start, end) if j != i]
            ctx.append("\n".join(ctx_lines[-5:]))
        if not any(c for c in ctx):
            return ""
        block = "以下是一些额外的上下文信息（供参考，不要修改）：\n"
        for i, c in enumerate(ctx):
            if c:
                block += f"--- 条目 {i} 的上下文 ---\n{c}\n"
        return block + "\n"

    def _build_correction_prompt(self, batch_text, context_block):
        """构建纠错 prompt（翻译模式 vs 校对模式）。"""
        user_hint = self._prompt_template.strip()
        custom_hint = ""
        if user_hint and "文本校对" not in user_hint[:20] and "翻译以下" not in user_hint[:20]:
            custom_hint = f"用户额外参考（按需采纳，不必拘泥）：{user_hint}\n"

        base = f"{context_block}以下是需要处理的内容：\n{batch_text}\n\n{custom_hint}"
        if self._translate_mode:
            return f"{base}请将上述 OCR 文本翻译为中文。每行保持 {ID_PREFIX}行号] 前缀，行号从0开始连续。"
        return f"{base}请校对文本错误。输出格式：每行保持 {ID_PREFIX}行号] 前缀，行号从0开始连续。"

    def _try_parse_json_batch(self, result, id_map, original_map):
        """尝试从 JSON 响应解析批量结果。成功返回 dict，失败返回 None，全空返回 {}。"""
        try:
            data = json.loads(result) if isinstance(result, str) else result
        except (json.JSONDecodeError, TypeError):
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
    def _fill_missing_with_original(corrected_map, id_map, original_map, n):
        """对 AI 未返回的行，用原文自动填充。"""
        for idx_in_batch in range(n):
            if idx_in_batch in id_map:
                row_idx = id_map[idx_in_batch]
                if row_idx not in corrected_map:
                    corrected_map[row_idx] = original_map.get(row_idx, "")

    @staticmethod
    def _append_retry_hint(prompt, reason, attempt):
        """在 prompt 末尾追加重试提示。"""
        return prompt + f"\n\n[[系统：上次{reason}（第{attempt + 1}次）。请修正后重新输出。]]"

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

        # 回退：宽松模式匹配 "数字. 文本" 或 "数字) 文本" 格式
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

    # ── 分句模式 ─────────────────────────────────────────────

    def segment_sentences(self, texts: list, max_retries: int = 3) -> tuple[dict[int, str], dict[str, tuple[int, int]]]:
        """对 OCR 碎片文本进行语义分句。

        Args:
            texts: [(row_idx, raw_text, time_start, time_end), ...]
            max_retries: 最大重试次数

        Returns:
            ({batch_idx: segmented_text}, {segmented_text: (start_batch_idx, end_batch_idx)})
            — 文本映射 + 每条分句对应的批次索引范围（用于代码层取时间轴）
        """
        if not texts:
            return {}, {}

        n = len(texts)
        lines = []
        for idx, (row_idx, raw_text, ts, te) in enumerate(texts):
            safe_text = raw_text.replace("\n", " ").strip()
            lines.append(f"[{idx}] {safe_text}")

        prompt = self._build_segmentation_prompt(lines)

        # 使用模式对应的 system prompt
        mode = self._config.get("segmentation_mode", "2lines")
        presets = self._config.get("segmentation_prompts", {})
        preset = presets.get(mode, {})
        seg_system = preset.get("system", self._segmentation_system_prompt)

        saved_system = self._correction_system_prompt
        saved_json = self._json_mode
        self._correction_system_prompt = seg_system
        self._json_mode = True

        try:
            last_error = ""
            for attempt in range(max_retries + 1):
                result = self._call_llm(
                    prompt=prompt,
                    system_prompt=self._build_system_prompt(env_context=self._env_context),
                    resp_type="json",
                    log_title="segmentation",
                    _tag="segment",
                )
                if result is None:
                    last_error = "API 返回空"
                    continue

                content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                # 传递原始行文本用于对齐校验；直接传 result 避免 json.dumps → json.loads 往返
                original_texts = [t[1].replace("\n", " ").strip() for t in texts]
                parsed = self._parse_segmentation_result(result, n, original_texts)
                if parsed is not None:
                    _alog(f"  SEG PARSED text_map_len={len(parsed[0])} range_map_len={len(parsed[1])}")
                    _alog(f"  SEG text_map keys={list(parsed[0].keys())[:10]}")
                if parsed is None and attempt < max_retries:
                    logger.warning("分句解析失败 (第%d次)，重试...", attempt + 1)
                    prompt = self._append_retry_hint(prompt, "JSON 解析失败，请重试", attempt)
                    continue
                if parsed is not None:
                    return parsed
                last_error = "解析结果为空"

            logger.error("分句最终失败: %s", last_error)
            return {}, {}
        finally:
            self._correction_system_prompt = saved_system
            self._json_mode = saved_json

    def _build_segmentation_prompt(self, lines: list[str]) -> str:
        """构建分句 prompt —— CoT 模式：分析 → 双方案 → 比较 → 选择。"""
        mode = self._config.get("segmentation_mode", "2lines")
        presets = self._config.get("segmentation_prompts", {})
        preset = presets.get(mode, {})
        hint = preset.get("prompt", self._segmentation_prompt).strip()
        batch_text = "\n".join(lines)
        return (
            f"## Role\n"
            f"{hint}\n\n"
            f"## Task\n"
            f"将碎片化的OCR识别文本合并为完整的字幕句子。\n\n"
            f"## Rules\n"
            f"- 保持原文逐字不变（仅可在分句衔接处添加必要标点如逗号、句号）\n"
            f"- 每条合并后的字幕应自然完整，避免过短碎片或过长堆砌\n"
            f"- 相邻行仅在语义连贯时合并，独立成句的行保持独立\n"
            f"- 禁止添加、删除或改写任何文字\n\n"
            f"## Steps\n"
            f"1. 分析文本的语义结构，识别完整的句子边界和话题转换点\n"
            f"2. 生成两个备选合并方案，用 range 标记合并区间\n"
            f"3. 比较两个方案的优劣（连贯性、长度均衡性、断句合理性）\n"
            f"4. 选择最佳方案\n\n"
            f"## Input\n"
            f"{batch_text}\n\n"
            f"## Output (JSON only, no other text)\n"
            f'{{"analysis": "语义结构分析", '
            f'"plan1": {{"segments": [{{"range": [0, 1], "text": "逐字拼接的文本"}}, ...]}}, '
            f'"plan2": {{"segments": [{{"range": [0, 1], "text": "逐字拼接的文本"}}, ...]}}, '
            f'"assess": "比较两个方案的优劣", '
            f'"choice": 1}}'
        )

    def _parse_segmentation_result(self, result: str, total_count: int,
                                    original_texts: list[str] | None = None
                                    ) -> tuple[dict[int, str], dict[str, tuple[int, int]]] | None:
        """解析 LLM 分句 JSON 响应，支持 CoT 格式（plan1/plan2/choice）和旧格式（segments）。

        新增：SequenceMatcher 原文对齐校验，防止 LLM 改写原文内容。

        Returns:
            ({batch_idx: segmented_text}, {segmented_text: (start_idx, end_idx)}) 或 None
        """
        from difflib import SequenceMatcher

        try:
            data = json.loads(result) if isinstance(result, str) else result
        except (json.JSONDecodeError, TypeError):
            return None

        # ── 提取 segments 列表（兼容 CoT 和旧格式）──
        segments = None
        if isinstance(data, dict):
            # CoT 格式：根据 choice 选择 plan1 或 plan2
            choice = data.get("choice")
            if choice in (1, 2):
                plan_key = f"plan{choice}"
                plan = data.get(plan_key, {})
                if isinstance(plan, dict):
                    segments = plan.get("segments")
            # 旧格式：直接读取 segments
            if segments is None:
                segments = data.get("segments")
        if not isinstance(segments, list):
            return None

        text_map: dict[int, str] = {}
        range_map: dict[str, tuple[int, int]] = {}

        # ── 语言连接符 ──
        joiner = ""  # 中文默认无空格连接
        if original_texts:
            sample = "".join(original_texts[:min(3, len(original_texts))])
            alpha_count = sum(1 for c in sample if c.isascii() and c.isalpha())
            if alpha_count > len(sample) * 0.5:
                joiner = " "

        # 文本归一化函数：去标点空格、转小写
        _normalize = lambda s: re.sub(r'[^\w]', '', s).lower()

        for seg in segments:
            if not isinstance(seg, dict):
                continue
            rng = seg.get("range", [])
            text = seg.get("text", "").strip()
            if not text or not isinstance(rng, list) or len(rng) != 2:
                continue
            try:
                start_idx = int(rng[0])
                end_idx = int(rng[1])
            except (ValueError, TypeError):
                continue
            if start_idx < 0 or end_idx >= total_count or start_idx > end_idx:
                continue

            # ── P2: 原文对齐校验 ──
            if original_texts:
                original_joined = joiner.join(original_texts[start_idx:end_idx + 1])
                if _normalize(original_joined):
                    similarity = SequenceMatcher(
                        None, _normalize(text), _normalize(original_joined)
                    ).ratio()
                    if similarity < 0.95:
                        logger.warning(
                            "分句对齐失败 (sim=%.3f): LLM=%r vs 原文=%r",
                            similarity, text[:60], original_joined[:60],
                        )
                        return None  # 触发重试

            range_map[text] = (start_idx, end_idx)
            text_map[start_idx] = text

        if not text_map:
            return None
        return (text_map, range_map)

    # ── 校对模式 ─────────────────────────────────────────────

    def proofread(self, original_text: str, corrected_text: str) -> str | None:
        """对纠错/翻译后的文本进行二次校对。

        Args:
            original_text: OCR 原始文本
            corrected_text: 已纠错或翻译后的文本

        Returns:
            校对后的文本，失败返回 None
        """
        if not self._proofread_enabled:
            return corrected_text
        if not original_text.strip() or not corrected_text.strip():
            return corrected_text

        prompt = self._resolve_placeholders(
            self._proofread_prompt,
            raw_text=original_text,
            context="",
            env_context=self._env_context,
        )
        # 额外替换 {待校对文本}
        prompt = prompt.replace("{待校对文本}", corrected_text)

        result = self._call_llm(
            prompt=prompt,
            system_prompt="你是一个专业的字幕校对审核员。只输出最终的校对结果文本。",
            resp_type=None,
            log_title="proofread",
            _tag="proofread",
        )
        if result is None:
            return corrected_text
        content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        return content.strip() or corrected_text
