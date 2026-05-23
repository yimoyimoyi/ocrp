# -*- coding: utf-8 -*-
"""AI 纠错模块 —— 通过引擎配置进行二次纠错。

若引擎为 API 类型（openai_vision / ollama_vision / llamacpp），
则调用对应 API 对文本进行校对；

若引擎为 local 类型（paddleocr），
则直接调用引擎的 recognize() 对原图 ROI 重新识别。
"""

import os
import json
import re
import time
import requests
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Callable

import numpy as np

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = BASE_DIR / "config"

from config_manager import _load_json_with_comments
from core.logger import get_logger

logger = get_logger(__name__)

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
            return _load_json_with_comments(path)
        except Exception:
            pass
    return {"enabled": False, "engine": "openai_vision", "retry_on_failure": 2}


def _resolve_api_config(config: dict, preset_name: str = "") -> dict:
    """解析 API 连接配置：优先使用预设，回退到 config 中的直接配置。"""
    if preset_name:
        from core.api_preset_manager import APIPresetManager
        preset = APIPresetManager().get_preset(preset_name)
        if preset:
            return dict(preset)
    return {
        "api_key": config.get("api_key", ""),
        "base_url": config.get("base_url", "http://127.0.0.1:8080"),
        "model": config.get("model", ""),
        "timeout": config.get("timeout", 30),
    }


class AICorrector:
    """AI 纠错器 —— 独立 API 配置（不依赖 OCR 引擎）。

    使用独立的 API Key / Base URL / Model 进行文本纠错。
    """

    def __init__(self, config: Optional[dict] = None,
                 engine_manager=None, preset_name: str = ""):
        self._config = config or load_correction_config()
        self._enabled = self._config.get("enabled", False)
        self._retry = self._config.get("retry_on_failure", 2)
        self._prompt_template = self._config.get("correction_prompt",
            "你是一个文本校对专家。请根据上下文纠正OCR识别结果中的明显错误，保留原格式。")
        self._engine_name = self._config.get("engine", "llamacpp")
        self._preset_name = preset_name
        api_cfg = _resolve_api_config(self._config, preset_name)
        self._api_key = api_cfg.get("api_key", "")
        self._base_url = api_cfg.get("base_url", "http://127.0.0.1:8080")
        self._model = api_cfg.get("model", "")
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
        self._api_key = api_cfg.get("api_key", "")
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

    def apply_preset(self, preset_name: str):
        """切换 API 预设。"""
        self._preset_name = preset_name
        api_cfg = _resolve_api_config(self._config, preset_name)
        self._api_key = api_cfg.get("api_key", "")
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

    def extract_environment(self, all_texts: list) -> str:
        """从全文摘要提取环境上下文（领域、氛围、主要内容），作为 system prompt 的补充。

        Args:
            all_texts: 全部 OCR 结果文本列表

        Returns:
            环境描述字符串，失败返回空字符串
        """
        if not all_texts:
            return ""
        combined = "\n".join(str(t) for t in all_texts[:100])  # 最多100条
        if len(combined) < 20:
            return ""
        # 使用自定义 summary_prompt，追加 OCR 文本
        prompt = self._summary_prompt + "\n\nOCR文本：\n" + combined[:4000]
        try:
            ec = self._get_engine_config()
            api_key = ec.get("api_key", "")
            base_url = ec.get("base_url", "https://api.openai.com/v1")
            model = ec.get("model", "gpt-4o")
            timeout = ec.get("timeout", 30)

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是一个文本分析助手，擅长总结和归纳。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
            }
            url = base_url.rstrip('/') + "/chat/completions"
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                self._env_context = (result or "").strip()
                if self._env_context:
                    logger.info("环境提取完成: %d chars", len(self._env_context))
                else:
                    logger.warning("环境提取返回空 content, raw keys=%s", list(data.keys()))
                    # 尝试从 reasoning_content 或 text 字段兜底
                    msg = data.get("choices", [{}])[0].get("message", {})
                    for key in ("reasoning_content", "text", "content"):
                        val = msg.get(key, "")
                        if val and val.strip():
                            self._env_context = val.strip()
                            logger.info("环境提取兜底成功: %s (%d chars)", key, len(self._env_context))
                            return self._env_context
                return self._env_context
            else:
                logger.error("环境提取 HTTP %d: %s", resp.status_code, resp.text[:300])
                return ""
        except Exception as e:
            logger.error("环境提取失败: %s", e)
            return ""

    def correct(self, raw_text: str, context_texts: Optional[list] = None,
                image: Optional[np.ndarray] = None,
                stream_callback: Optional[Callable[[str], None]] = None) -> Optional[str]:
        """对一段原始 OCR 文本进行纠错（或重新识别）。

        Args:
            raw_text: OCR 识别的原始文本
            context_texts: 前后文文本列表（仅 API 模式使用）
            image: 原始帧 ROI 图像（仅本地引擎模式使用）
            stream_callback: 流式回调，接收每次 SSE chunk 的文本片段

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

        # ── API 引擎模式（单条兼容） ──
        context_str = ""
        if context_texts:
            context_str = "上下文：\n" + "\n".join(
                f"[{i+1}] {t}" for i, t in enumerate(context_texts[-5:])
            ) + "\n\n"

        if self._translate_mode:
            # 翻译模式 prompt
            user_hint = self._prompt_template.strip()
            custom = ""
            if user_hint and "文本校对" not in user_hint[:20]:
                custom = f"（风格参考：{user_hint}）"
            prompt = f"{context_str}请将以下 OCR 文本翻译为中文{custom}：\n{raw_text}"
        else:
            prompt = self._prompt_template.replace("[原始文本]", raw_text)
            if context_str:
                prompt = context_str + prompt

        for attempt in range(self._retry + 1):
            result = self._call_api(prompt, env_context=self._env_context,
                                    stream_callback=stream_callback)
            if result is not None:
                # ── JSON 模式：解析 JSON 提取实际文本 ──
                if self._json_mode:
                    try:
                        data = json.loads(result) if isinstance(result, str) else result
                        if isinstance(data, dict):
                            items = data.get("results") or data.get("items") or data.get("data") or []
                            if isinstance(items, list) and items:
                                first = items[0]
                                if isinstance(first, dict):
                                    result = first.get("text") or first.get("content") or result
                            elif isinstance(data, dict):
                                result = data.get("text") or data.get("content") or data.get("corrected") or result
                    except (json.JSONDecodeError, TypeError):
                        pass  # 非 JSON → 保持原文本

                # 用自定义输出格式标记剔除格式外壳
                fmt = self._output_format.strip()
                if fmt:
                    result = result.replace(fmt, "").strip()
                if result and result != raw_text:
                    return result
                return raw_text

        return None  # 所有重试均失败

    # ── 批量纠错 ─────────────────────────────────────────────

    def _fmt_time(self, sec: float) -> str:
        """格式化秒数为 SRT 时间串 H:MM:SS.mmm。"""
        if not sec:
            return ""
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = int((sec - int(sec)) * 1000)
        return f"{h}:{m:02d}:{s:02d}.{ms:03d}"

    def correct_batch(self, texts: List[Tuple[int, str]],
                      context_window: int = 3,
                      max_retries: int = 3,
                      stream_callback: Optional[Callable[[str], None]] = None) -> Dict[int, str]:
        """批量对多条文本进行 AI 纠错，使用 [ID:行号] 标记保证顺序与完整性。
        texts 每项支持 (row_index, raw_text) 或 (row_index, raw_text, time_sec, end_sec)。

        时间轴仅影响存入位置，不嵌入 prompt（避免 AI 回显时间标记）。

        Args:
            texts: 待处理文本列表
            context_window: 上下文窗口大小
            max_retries: 最大重试次数
            stream_callback: 流式回调（接收增量文本）

        Returns:
            字典 {row_index: corrected_text}，仅包含有变更的结果
        """
        if not texts:
            return {}

        has_time = len(texts[0]) >= 3
        # 记录时间信息（仅用于存储，不嵌入 prompt）
        time_map = {}
        lines = []
        id_map = {}
        for idx, item in enumerate(texts):
            row_idx = item[0]
            raw_text = item[1]
            safe_text = raw_text.replace("\n", " ").strip()
            if has_time:
                ts = item[2] if len(item) > 2 and item[2] else None
                te = item[3] if len(item) > 3 and item[3] else None
                if ts is not None:
                    time_map[row_idx] = (ts, te)
            lines.append(f"{ID_PREFIX}{idx}] {safe_text}")
            id_map[idx] = row_idx

        # ── 构建上下文 ──
        batch_text = "\n".join(lines)
        n = len(texts)

        # ── 构建上下文 ──
        ctx = []
        all_results = [r[1] for r in texts]
        for i in range(len(texts)):
            ctx_lines = []
            start = max(0, i - context_window)
            end = min(len(all_results), i + context_window + 1)
            for j in range(start, end):
                if j != i:
                    ctx_lines.append(f"[{j}] {all_results[j]}")
            ctx.append("\n".join(ctx_lines[-5:]))

        context_block = ""
        if any(c for c in ctx):
            context_block = "以下是一些额外的上下文信息（供参考，不要修改）：\n"
            for i, c in enumerate(ctx):
                if c:
                    context_block += f"--- 条目 {i} 的上下文 ---\n{c}\n"
            context_block += "\n"

        # ── 核心 prompt ──
        user_hint = self._prompt_template.strip()
        custom_hint = ""
        if user_hint:
            if "文本校对" not in user_hint[:20] and "翻译以下" not in user_hint[:20]:
                custom_hint = f"用户额外参考（按需采纳，不必拘泥）：{user_hint}\n"

        if self._translate_mode:
            prompt = (
                f"{context_block}"
                f"以下是需要处理的内容：\n"
                f"{batch_text}\n\n"
                f"{custom_hint}"
                f"请将上述 OCR 文本翻译为中文。每行保持 {ID_PREFIX}行号] 前缀，行号从0开始连续。"
            )
        else:
            prompt = (
                f"{context_block}"
                f"以下是需要处理的内容：\n"
                f"{batch_text}\n\n"
                f"{custom_hint}"
                f"请校对文本错误。输出格式：每行保持 {ID_PREFIX}行号] 前缀，行号从0开始连续。"
            )

        # ── 重试循环 ──
        last_error = ""
        for attempt in range(max_retries + 1):
            result = self._call_api(prompt, env_context=self._env_context,
                                    stream_callback=stream_callback)
            if result is None:
                last_error = "API 返回空"
                continue

            # ── JSON 模式：直接解析 JSON ──
            if self._json_mode:
                try:
                    data = json.loads(result) if isinstance(result, str) else result
                    items = None
                    if isinstance(data, dict):
                        items = data.get("results") or data.get("items")
                    if isinstance(items, list):
                        corrected_map = {}
                        for entry in items:
                            if isinstance(entry, dict):
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
                                    original_text = dict([(r[0], r[1]) for r in texts]).get(row_idx, "")
                                    clean = _clean_content(etext)
                                    if clean and clean != original_text.strip():
                                        corrected_map[row_idx] = clean
                        if corrected_map:
                            return corrected_map
                        if attempt < max_retries:
                            last_error = f"JSON 解析后全部为空 (共 {len(items)} 条)"
                            logger.warning("批量 JSON 全部为空 (第%d次)，重试...", attempt + 1)
                            prompt += (
                                "\n\n[[系统：上次返回中 id 字段无效或内容为空，"
                                "请确保返回 \"id\" 从0开始连续对应输入行。]]"
                            )
                            continue
                    else:
                        last_error = f"JSON 中未找到 results/items 数组 (keys={list(data.keys()) if isinstance(data, dict) else type(data).__name__})"
                        logger.warning("JSON 结构异常 (第%d次): %s", attempt + 1, last_error)
                        if attempt < max_retries:
                            prompt += (
                                f'\n\n[[系统：上次返回 JSON 中缺少 "results" 数组。]]'
                            )
                            continue
                        else:
                            logger.error("批量 JSON 解析最终失败: %s", last_error)
                            return {}
                except (json.JSONDecodeError, TypeError) as e:
                    last_error = f"JSON 解析失败: {e}"
                    logger.warning("JSON 解析失败 (第%d次): %s，回退到文本解析", attempt + 1, e)
                    # 继续走下面的文本解析

            parsed = self._parse_batch_result(result)

            # ── 解析后处理 ──
            if not parsed and attempt < max_retries:
                last_error = "未能解析出任何 [ID:行号]"
                logger.warning("解析全空 (第%d次)，重试...", attempt + 1)
                prompt += (
                    f"\n\n[[系统：上次未返回任何 {ID_PREFIX}行号] 标记。]]"
                )
                continue

            if not parsed:
                logger.warning("批量解析最终空（已达最大重试次数），跳过本批")
                return {}

            corrected_map = {}
            all_empty = True
            for idx_in_batch, content in parsed.items():
                if idx_in_batch in id_map:
                    row_idx = id_map[idx_in_batch]
                    original_text = dict([(r[0], r[1]) for r in texts]).get(row_idx, "")
                    clean = _clean_content(content)
                    if clean and clean != original_text.strip():
                        corrected_map[row_idx] = clean
                        all_empty = False

            # 🔥 仅检查全空，不检查行数匹配（AI 可能合并不返回某些行）
            if all_empty and attempt < max_retries:
                last_error = "解析后全部为空"
                logger.warning("所有行解析为空 (第%d次)，重试...", attempt + 1)
                prompt += (
                    f"\n\n[[系统：上次返回内容全部为空。]]"
                )
                continue

            # 🔥 缺失行自动填充原文：AI 没返回的行用原文代替
            original_map = dict([(r[0], r[1]) for r in texts])
            for idx_in_batch in range(n):
                if idx_in_batch in id_map:
                    row_idx = id_map[idx_in_batch]
                    if row_idx not in corrected_map:
                        corrected_map[row_idx] = original_map.get(row_idx, "")

            return corrected_map

        logger.error("批量纠错最终失败: %s", last_error)
        return {}

    @staticmethod
    def _parse_batch_result(text: str) -> Dict[int, str]:
        """从 AI 返回文本中解析 [ID:idx] 标记的内容。

        容错处理：
        - 去除 markdown 代码块包裹
        - 兼容全角/半角冒号、大小写变体
        - 当标准正则无匹配时，回退到宽松模式

        Returns:
            {idx: content} 字典
        """
        # 去除 markdown 代码块
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

    def _get_engine_config(self) -> dict:
        """返回纠错 API 的独立配置。"""
        return {
            "api_key": self._api_key,
            "base_url": self._base_url,
            "model": self._model,
            "timeout": self._timeout,
        }

    def _correct_local(self, image: np.ndarray) -> Optional[str]:
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

    def _call_api(self, prompt: str, env_context: str = "",
                  stream_callback: Optional[Callable[[str], None]] = None) -> Optional[str]:
        """调用 AI API 进行纠错（从引擎配置读取连接信息）。"""
        t_start = time.time()
        try:
            ec = self._get_engine_config()
            api_key = ec.get("api_key", "")
            base_url = ec.get("base_url", "https://api.openai.com/v1")
            model = ec.get("model", "gpt-4o")
            timeout = ec.get("timeout", 30)

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }

            if self._translate_mode:
                # 翻译模式下使用专用 system prompt
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

            # ── JSON 输出模式：追加格式指令到 system prompt ──
            if self._json_mode:
                system_msg += (
                    "\n\n你必须以 JSON 格式输出结果。"
                    '对于批量纠错，输出格式为：{"results": [{"id": 行号, "text": "纠正后文本"}, ...]}。'
                    "确保 JSON 格式严格有效，不要包含任何额外说明文字。"
                )

            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1,
            }

            # ── JSON 模式：添加 response_format ──
            if self._json_mode:
                payload["response_format"] = {"type": "json_object"}

            # ── 流式模式 ──
            use_stream = self._stream_mode or stream_callback is not None

            url = base_url.rstrip('/') + "/chat/completions"

            # ── 后台日志：请求详情 ──
            logger.info("AI 纠错 API 请求 | model=%s | stream=%s | json=%s | translate=%s",
                         model, use_stream, self._json_mode, self._translate_mode)
            logger.debug("Prompt(%d chars): %s", len(prompt), prompt[:200])

            if use_stream:
                payload["stream"] = True
                resp = requests.post(url, json=payload, headers=headers,
                                     timeout=timeout, stream=True)
            else:
                resp = requests.post(url, json=payload, headers=headers, timeout=timeout)

            elapsed_req = time.time() - t_start
            logger.info("首响应: %.1fs | HTTP %d", elapsed_req, resp.status_code)

            if resp.status_code != 200:
                logger.error("AI 纠错 HTTP %d: %s", resp.status_code, resp.text[:300])
                return None

            if use_stream:
                # ── 流式解析 SSE（按字节读取，避免 decode_unicode 破坏 UTF-8）──
                full_content = ""
                chunk_count = 0
                buffer = b""
                for raw_chunk in resp.iter_content(chunk_size=1):
                    if not raw_chunk:
                        continue
                    buffer += raw_chunk
                    if buffer.endswith(b"\n"):
                        line = buffer.decode("utf-8", errors="replace").strip()
                        buffer = b""
                        if not line:
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                break
                            try:
                                chunk_data = json.loads(data_str)
                                delta = (chunk_data.get("choices", [{}])[0]
                                         .get("delta", {}).get("content", ""))
                                if delta:
                                    full_content += delta
                                    chunk_count += 1
                                    if stream_callback:
                                        stream_callback(delta)
                            except json.JSONDecodeError:
                                continue

                elapsed_total = time.time() - t_start
                logger.info("流式接收: %d chunks, %d chars | 耗时 %.1fs", chunk_count, len(full_content), elapsed_total)

                # JSON 模式验证
                if self._json_mode and full_content.strip():
                    try:
                        json.loads(full_content.strip())
                        logger.debug("JSON 格式验证通过")
                    except json.JSONDecodeError as e:
                        logger.warning("JSON 格式验证失败: %s", e)

                return full_content.strip()

            else:
                # ── 非流式：解析完整响应 ──
                data = resp.json()
                content = (data.get("choices", [{}])[0]
                           .get("message", {}).get("content", ""))
                elapsed_total = time.time() - t_start

                # Token 使用统计
                usage = data.get("usage", {})
                if usage:
                    logger.debug("Token 使用: prompt=%s, completion=%s, total=%s",
                                 usage.get('prompt_tokens', '?'), usage.get('completion_tokens', '?'),
                                 usage.get('total_tokens', '?'))

                logger.info("AI 纠错响应: %.1fs, %d chars", elapsed_total, len(content))

                # JSON 模式验证
                if self._json_mode and content.strip():
                    try:
                        parsed = json.loads(content.strip())
                        if isinstance(parsed, dict) and "results" in parsed:
                            logger.debug("JSON 结果包含 %d 条条目", len(parsed['results']))
                        elif isinstance(parsed, dict):
                            logger.debug("JSON 结果键: %s", list(parsed.keys()))
                    except json.JSONDecodeError as e:
                        logger.warning("JSON 解析失败: %s", e)

                return content.strip()

        except requests.exceptions.Timeout:
            logger.error("AI 纠错请求超时 (%ds)", timeout)
            return None
        except Exception as e:
            logger.error("AI 纠错请求失败: %s", e)
            return None
