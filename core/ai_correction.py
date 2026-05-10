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

# ── 批量纠错 ID 前缀常量 ──
ID_PREFIX = "[ID:"
ID_PATTERN = re.compile(r'\[ID:(\d+)\](.*?)(?=\n\[ID:\d+\]|\Z)', re.DOTALL)


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
        print(f"[AI纠错] 已切换预设: {preset_name or '默认'}")

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
                "max_tokens": 300
            }
            url = base_url.rstrip('/') + "/chat/completions"
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                self._env_context = result.strip()
                print(f"[AI纠错] 环境提取完成:\n{self._env_context}")
                return self._env_context
            else:
                print(f"[AI纠错] 环境提取 HTTP {resp.status_code}")
                return ""
        except Exception as e:
            print(f"[AI纠错] 环境提取失败: {e}")
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
                print("[AI纠错] 本地引擎模式需要提供 image 参数")
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
        lines = []
        id_map = {}
        for idx, item in enumerate(texts):
            row_idx = item[0]
            raw_text = item[1]
            safe_text = raw_text.replace("\n", " ").strip()
            if has_time:
                ts = item[2] if len(item) > 2 and item[2] else None
                te = item[3] if len(item) > 3 and item[3] else None
                time_tag = ""
                if ts is not None:
                    time_tag = f" [{self._fmt_time(ts)}"
                    if te and te > ts:
                        time_tag += f" -> {self._fmt_time(te)}"
                    time_tag += "]"
                lines.append(f"{ID_PREFIX}{idx}]{time_tag} {safe_text}")
            else:
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
        time_instruction = (
            "每条数据带有时间轴标记 [起始时间 -> 结束时间]。"
            "你可以根据语义合并多条相近条目或拆分长句，并重新设定合理的起止时间。"
        )
        # 用户提示词仅作参考
        custom_hint = ""
        if user_hint:
            # 只取非默认的提示词作为参考
            if "文本校对" not in user_hint[:20] and "翻译以下" not in user_hint[:20]:
                custom_hint = f"用户额外参考（按需采纳，不必拘泥）：{user_hint}\n"

        if self._translate_mode:
            # ── 翻译模式 prompt ──
            prompt = (
                f"{context_block}"
                f"以下是需要处理的内容：\n"
                f"{batch_text}\n\n"
                f"{time_instruction if has_time else ''}\n"
                f"{custom_hint}"
                f"请将上述 OCR 文本翻译为中文。保持每行 {ID_PREFIX}行号] 前缀，行号从0开始连续。"
                f"时间轴标记可选，可合并可拆分。"
            )
        else:
            # ── 常规纠错模式 prompt ──
            prompt = (
                f"{context_block}"
                f"以下是需要处理的内容：\n"
                f"{batch_text}\n\n"
                f"{time_instruction if has_time else ''}\n"
                f"{custom_hint}"
                f"请校对文本错误。输出格式：每行保持 {ID_PREFIX}行号] 前缀，行号从0开始连续。"
                f"时间轴标记可选，可合并可拆分。"
            )

        # ── 重试循环 ──
        last_error = ""
        for attempt in range(max_retries + 1):
            result = self._call_api(prompt, env_context=self._env_context,
                                    max_tokens=4096 if has_time else 2048,
                                    stream_callback=stream_callback)
            if result is None:
                last_error = "API 返回空"
                continue
            parsed = self._parse_batch_result(result)
            valid, error_msg = self._validate_batch_result(parsed, n)
            if valid:
                corrected_map = {}
                for idx_in_batch, content in parsed.items():
                    if idx_in_batch in id_map:
                        row_idx = id_map[idx_in_batch]
                        original_text = dict([(r[0], r[1]) for r in texts]).get(row_idx, "")
                        if content.strip() and content.strip() != original_text.strip():
                            corrected_map[row_idx] = content.strip()
                return corrected_map
            else:
                last_error = error_msg
                print(f"[AI纠错] 批量校验失败 (第{attempt+1}次): {error_msg}")
                prompt += (
                    f"\n\n[[系统：校验失败 {error_msg}，确保 {ID_PREFIX}行号] 连续且完整。]]"
                )

        print(f"[AI纠错] 批量纠错最终失败: {last_error}")
        return {}

    @staticmethod
    def _parse_batch_result(text: str) -> Dict[int, str]:
        """从 AI 返回文本中解析 [ID:idx] 标记的内容。

        Returns:
            {idx: content} 字典
        """
        result = {}
        for match in ID_PATTERN.finditer(text):
            try:
                idx = int(match.group(1))
                content = match.group(2).strip()
                result[idx] = content
            except ValueError:
                continue
        return result

    @staticmethod
    def _validate_batch_result(parsed: Dict[int, str],
                                expected_count: int) -> Tuple[bool, str]:
        """校验解析结果是否完整。

        Returns:
            (is_valid, error_message)
        """
        if not parsed:
            return False, "未能从返回中解析出任何 [ID:行号] 标记"

        # 检查行数
        if len(parsed) != expected_count:
            return False, (
                f"行数不匹配: 期望 {expected_count} 行，实际返回 {len(parsed)} 行"
            )

        # 检查 ID 是否连续且完整
        expected_ids = set(range(expected_count))
        actual_ids = set(parsed.keys())
        if actual_ids != expected_ids:
            missing = expected_ids - actual_ids
            extra = actual_ids - expected_ids
            parts = []
            if missing:
                parts.append(f"缺失行号: {sorted(missing)}")
            if extra:
                parts.append(f"多余行号: {sorted(extra)}")
            return False, "ID 不完整: " + "; ".join(parts)

        return True, ""

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
            print(f"[AI纠错] 引擎管理器未初始化")
            return None
        try:
            eng = self._engine_manager.get_engine(self._engine_name)
            if eng is None:
                print(f"[AI纠错] 引擎 [{self._engine_name}] 不可用")
                return None
            result = eng.recognize(image)
            return result.strip() if result else None
        except Exception as e:
            print(f"[AI纠错] 本地引擎识别失败: {e}")
            return None

    def _call_api(self, prompt: str, env_context: str = "",
                  max_tokens: int = 1024,
                  stream_callback: Optional[Callable[[str], None]] = None) -> Optional[str]:
        """调用 AI API 进行纠错（从引擎配置读取连接信息）。

        Args:
            prompt: 用户提示词
            env_context: 环境上下文
            max_tokens: 最大 token 数
            stream_callback: 流式模式下的增量回调（接收每次 SSE chunk 的文本片段）

        Returns:
            完整响应的文本内容，失败返回 None
        """
        t_start = time.time()
        try:
            ec = self._get_engine_config()
            api_key = ec.get("api_key", "")
            base_url = ec.get("base_url", "https://api.openai.com/v1")
            model = ec.get("model", "gpt-4o")
            timeout = ec.get("timeout", 30)
            mtokens = max_tokens

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
                "max_tokens": mtokens
            }

            # ── JSON 模式：添加 response_format ──
            if self._json_mode:
                payload["response_format"] = {"type": "json_object"}

            # ── 流式模式 ──
            use_stream = self._stream_mode or stream_callback is not None

            url = base_url.rstrip('/') + "/chat/completions"

            # ── 后台日志：请求详情 ──
            prompt_preview = prompt[:120].replace("\n", " ")
            print(f"[AI纠错] ═══════════ API 请求 ═══════════")
            print(f"[AI纠错]   🔗 URL: {url}")
            print(f"[AI纠错]   🤖 Model: {model}")
            print(f"[AI纠错]   📝 Prompt({len(prompt)} chars): {prompt_preview}{'...' if len(prompt) > 120 else ''}")
            print(f"[AI纠错]   ⚙️  stream={use_stream}, json_mode={self._json_mode}, translate={self._translate_mode}")
            print(f"[AI纠错]   🎯 max_tokens={mtokens}")

            if use_stream:
                payload["stream"] = True
                resp = requests.post(url, json=payload, headers=headers,
                                     timeout=timeout, stream=True)
            else:
                resp = requests.post(url, json=payload, headers=headers, timeout=timeout)

            elapsed_req = time.time() - t_start
            print(f"[AI纠错]   ⏱ 首响应: {elapsed_req:.1f}s | HTTP {resp.status_code}")

            if resp.status_code != 200:
                print(f"[AI纠错]   ❌ HTTP {resp.status_code}: {resp.text[:300]}")
                return None

            if use_stream:
                # ── 流式解析 SSE ──
                full_content = ""
                chunk_count = 0
                for line in resp.iter_lines(decode_unicode=True):
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
                print(f"[AI纠错]   📦 流式接收: {chunk_count} chunks, {len(full_content)} chars")
                print(f"[AI纠错]   ⏱ 总耗时: {elapsed_total:.1f}s")
                print(f"[AI纠错]   ✅ 流式结果预览: {full_content[:100].replace(chr(10), ' ')}{'...' if len(full_content) > 100 else ''}")

                # JSON 模式验证
                if self._json_mode and full_content.strip():
                    try:
                        json.loads(full_content.strip())
                        print(f"[AI纠错]   ✅ JSON 格式有效")
                    except json.JSONDecodeError as e:
                        print(f"[AI纠错]   ⚠ JSON 解析失败: {e}")

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
                    print(f"[AI纠错]   📊 Token: prompt={usage.get('prompt_tokens','?')}, "
                          f"completion={usage.get('completion_tokens','?')}, "
                          f"total={usage.get('total_tokens','?')}")

                print(f"[AI纠错]   ⏱ 总耗时: {elapsed_total:.1f}s")
                print(f"[AI纠错]   ✅ 响应({len(content)} chars): {content[:100].replace(chr(10), ' ')}{'...' if len(content) > 100 else ''}")

                # JSON 模式验证
                if self._json_mode and content.strip():
                    try:
                        parsed = json.loads(content.strip())
                        if isinstance(parsed, dict) and "results" in parsed:
                            print(f"[AI纠错]   ✅ JSON 结果包含 {len(parsed['results'])} 条条目")
                        elif isinstance(parsed, dict):
                            print(f"[AI纠错]   ✅ JSON 结果键: {list(parsed.keys())}")
                    except json.JSONDecodeError as e:
                        print(f"[AI纠错]   ⚠ JSON 解析失败: {e}")

                return content.strip()

        except requests.exceptions.Timeout:
            print(f"[AI纠错]   ❌ 请求超时 ({timeout}s)")
            return None
        except Exception as e:
            print(f"[AI纠错]   ❌ 请求失败: {e}")
            import traceback
            traceback.print_exc()
            return None
