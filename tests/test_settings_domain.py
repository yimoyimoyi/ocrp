"""core/settings 域对象包单元测试（方案 A 批次 1）。

覆盖：域对象序列化往返、死键清理、默认值补齐、KEY_MAP 一致性、
RebuildRouter 路由判定。
"""

import json
from pathlib import Path

import pytest

from core.settings.domains import AsrConfig, CorrectionConfig, OcrEnginesConfig, UiStateConfig
from core.settings.rebuild import RebuildRouter


@pytest.fixture
def cfg_dir(tmp_path: Path) -> Path:
    return tmp_path / "config"


def _write(cfg_dir: Path, filename: str, data: dict):
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / filename).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ══════════════════════════════════════════════════════════════════
# AsrConfig
# ══════════════════════════════════════════════════════════════════


class TestAsrConfig:
    def test_load_defaults_when_missing(self, cfg_dir):
        obj = AsrConfig(cfg_dir)
        assert obj.get("engine") == "whisperx"
        assert obj.get("model_size") == "large-v3"
        assert obj.get("device") == "cuda"

    def test_roundtrip(self, cfg_dir):
        obj = AsrConfig(cfg_dir)
        obj.update({"model_size": "small", "language": "en", "vad_enabled": True})
        obj2 = AsrConfig(cfg_dir)
        assert obj2.get("model_size") == "small"
        assert obj2.get("language") == "en"
        assert obj2.get("vad_enabled") is True

    def test_unknown_keys_passthrough(self, cfg_dir):
        # 未知键透传保留（前向兼容手改文件），只清确认死键
        _write(cfg_dir, "asr_engines.json", {"engine": "whisperx", "model_size": "tiny", "custom_key": 1})
        obj = AsrConfig(cfg_dir)
        assert obj.get("custom_key") == 1

    def test_defaults_fill_missing_keys(self, cfg_dir):
        _write(cfg_dir, "asr_engines.json", {"engine": "whisperx"})
        obj = AsrConfig(cfg_dir)
        assert obj.get("beam_size") == 5  # 缺键补默认
        assert obj.get("model_size") == "large-v3"

    def test_apply_model_selection_abs_path(self, cfg_dir):
        obj = AsrConfig(cfg_dir)
        obj.apply_model_selection("D:/models/asr/large-v3")
        assert obj.get("model_size") == "large-v3"
        assert obj.get("model_dir") == "D:/models/asr"

    def test_apply_model_selection_standard_name(self, cfg_dir):
        obj = AsrConfig(cfg_dir)
        obj.apply_model_selection("small")
        assert obj.get("model_size") == "small"

    def test_resolve_model_path_saved_priority(self, cfg_dir):
        obj = AsrConfig(cfg_dir)
        assert obj.resolve_model_path("tiny") == "tiny"
        # 标准名且本地无对应目录 → 回退标准名
        assert obj.resolve_model_path("") == "large-v3"


# ══════════════════════════════════════════════════════════════════
# CorrectionConfig
# ══════════════════════════════════════════════════════════════════


class TestCorrectionConfig:
    def test_dead_keys_scrubbed(self, cfg_dir):
        _write(
            cfg_dir,
            "ai_correction.json",
            {
                "engine": "llamacpp",
                "enabled": True,
                "retry": 2,  # 死键（真键是 retry_on_failure）
                "use_template": True,
                "segmentation_mode": "2lines",
                "prompts": {"default": ""},
                "enable_proofread": True,
                "seg_time_gap": 5.0,
            },
        )
        obj = CorrectionConfig(cfg_dir)
        for dead in ("retry", "use_template", "segmentation_mode", "prompts", "enable_proofread"):
            assert dead not in obj.get_all(), f"死键未清理: {dead}"
        assert obj.get("seg_time_gap") == 5.0  # 合法键保留
        assert obj.get("translate_mode") is False  # 缺键补默认

    def test_roundtrip(self, cfg_dir):
        obj = CorrectionConfig(cfg_dir)
        obj.update({"enabled": True, "retry_on_failure": 3, "translate_mode": True})
        obj2 = CorrectionConfig(cfg_dir)
        assert obj2.get("enabled") is True
        assert obj2.get("retry_on_failure") == 3
        assert obj2.get("translate_mode") is True

    def test_as_engine_config(self, cfg_dir):
        obj = CorrectionConfig(cfg_dir)
        obj.update({"api_key": "k", "base_url": "http://x", "model": "m", "timeout": 42})
        assert obj.as_engine_config() == {"api_key": "k", "base_url": "http://x", "model": "m", "timeout": 42}

    def test_reset_to_defaults(self, cfg_dir):
        obj = CorrectionConfig(cfg_dir)
        obj.update({"enabled": True, "api_key": "secret"})
        obj.reset_to_defaults()
        assert obj.get("enabled") is False
        assert obj.get("api_key") == ""
        obj2 = CorrectionConfig(cfg_dir)
        assert obj2.get("enabled") is False  # 已写盘


# ══════════════════════════════════════════════════════════════════
# OcrEnginesConfig
# ══════════════════════════════════════════════════════════════════


class TestOcrEnginesConfig:
    def test_dead_keys_scrubbed_nested(self, cfg_dir):
        _write(
            cfg_dir,
            "ocr_engines.json",
            {
                "engines": {"ollama_vision": {"type": "api", "config": {"retry": 2, "model": "x"}}},
                "default_engine": "ollama_vision",
            },
        )
        obj = OcrEnginesConfig(cfg_dir)
        cfg = obj.get_engine_config("ollama_vision")["config"]
        assert "retry" not in cfg
        assert cfg["model"] == "x"

    def test_get_current_engine_default(self, cfg_dir):
        obj = OcrEnginesConfig(cfg_dir)
        assert obj.get_current_engine() == "llamacpp"

    def test_set_engine_config_roundtrip(self, cfg_dir):
        obj = OcrEnginesConfig(cfg_dir)
        obj.set_engine_config("paddleocr", {"device": "cpu"})
        obj2 = OcrEnginesConfig(cfg_dir)
        assert obj2.get_engine_config("paddleocr")["config"]["device"] == "cpu"

    def test_use_gpu_scrubbed_to_device(self, cfg_dir):
        """R5 收敛：use_gpu 与 device 双键并存 → 保留 device 删除 use_gpu。

        历史矛盾状态（use_gpu:false + device:gpu）以 device 为准。
        """
        _write(
            cfg_dir,
            "ocr_engines.json",
            {
                "engines": {
                    "paddleocr": {
                        "type": "local",
                        "config": {"use_gpu": False, "device": "gpu"},  # 历史矛盾状态
                    }
                },
                "default_engine": "paddleocr",
            },
        )
        obj = OcrEnginesConfig(cfg_dir)
        cfg = obj.get_engine_config("paddleocr")["config"]
        assert "use_gpu" not in cfg  # 已收敛删除
        assert cfg["device"] == "gpu"  # device 保留（唯一计算设备开关）

    def test_use_gpu_derives_device_when_missing(self, cfg_dir):
        """旧文件缺 device 时由 use_gpu 派生一次后删除。"""
        _write(
            cfg_dir,
            "ocr_engines.json",
            {
                "engines": {"ollama_vision": {"type": "api", "config": {"use_gpu": True}}},
                "default_engine": "ollama_vision",
            },
        )
        obj = OcrEnginesConfig(cfg_dir)
        cfg = obj.get_engine_config("ollama_vision")["config"]
        assert "use_gpu" not in cfg
        assert cfg["device"] == "gpu"


# ══════════════════════════════════════════════════════════════════
# UiStateConfig
# ══════════════════════════════════════════════════════════════════


class TestUiStateConfig:
    def test_ui_state_mode_keys_whitelist(self):
        keys = UiStateConfig.UI_STATE_MODE_KEYS
        # 白名单不含业务镜像键（asr_model_path 是 UI 选择状态例外）
        assert not any(k.startswith("asr_") and k != "asr_model_path" for k in keys)
        assert not any(
            k.startswith("corr_") and k not in ("corr_preset", "corr_concurrency", "corr_rpm", "corr_summary_prompt")
            for k in keys
        )
        assert "seg_time_gap" not in keys
        assert "asr_model_path" in keys
        assert "corr_preset" in keys


# ══════════════════════════════════════════════════════════════════
# KEY_MAP 一致性（R7 注册中心收敛的基石）
# ══════════════════════════════════════════════════════════════════


class TestKeyMaps:
    def test_asr_key_map_unique_both_sides(self):
        mp_keys = list(AsrConfig.KEY_MAP)
        file_keys = list(AsrConfig.KEY_MAP.values())
        assert len(mp_keys) == len(set(mp_keys))
        assert len(file_keys) == len(set(file_keys))

    def test_corr_key_map_unique_both_sides(self):
        mp_keys = list(CorrectionConfig.KEY_MAP)
        file_keys = list(CorrectionConfig.KEY_MAP.values())
        assert len(mp_keys) == len(set(mp_keys))
        assert len(file_keys) == len(set(file_keys))

    def test_key_map_file_keys_exist_in_defaults(self):
        for key in AsrConfig.KEY_MAP.values():
            assert key in AsrConfig.DEFAULTS, f"映射文件键不在 DEFAULTS: {key}"
        for key in CorrectionConfig.KEY_MAP.values():
            assert key in CorrectionConfig.DEFAULTS, f"映射文件键不在 DEFAULTS: {key}"

    def test_domain_defaults_match_templates(self):
        """域默认值与模板一致（批次 5 将由域默认值派生模板，此处先锁一致）。"""
        from core.config_manager import _CONFIG_TEMPLATES

        for key in AsrConfig.DEFAULTS:
            assert key in _CONFIG_TEMPLATES["asr_engines.json"], f"asr 域默认值缺模板键: {key}"
        for key in _CONFIG_TEMPLATES["asr_engines.json"]:
            assert key in AsrConfig.DEFAULTS
        for key in CorrectionConfig.DEFAULTS:
            assert key in _CONFIG_TEMPLATES["ai_correction.json"], f"corr 域默认值缺模板键: {key}"
        for key in _CONFIG_TEMPLATES["ai_correction.json"]:
            assert key in CorrectionConfig.DEFAULTS
        # ocr 域默认值与模板逐引擎 config 一致
        tpl = _CONFIG_TEMPLATES["ocr_engines.json"]
        for name, eng in OcrEnginesConfig.DEFAULTS["engines"].items():
            assert name in tpl["engines"]
            assert eng["config"] == tpl["engines"][name]["config"], f"ocr 引擎 {name} 配置不一致"


# ══════════════════════════════════════════════════════════════════
# RebuildRouter 路由判定
# ══════════════════════════════════════════════════════════════════


class TestRebuildRouter:
    def test_asr_routing(self):
        r = RebuildRouter()
        # 模型/设备/批处理类 → 重启
        for key in ("model_size", "model_dir", "device", "compute_type", "batch_size", "vad_enabled"):
            assert r.route_for("asr", key) == "asr", key
        # 解码类 → 免重启 sync
        for key in ("beam_size", "temperature", "hotwords", "initial_prompt"):
            assert r.route_for("asr", key) == "sync", key
        assert r.route_for("asr", "hf_endpoint") == "none"

    def test_correction_routing(self):
        r = RebuildRouter()
        for key in ("api_key", "base_url", "model", "timeout", "engine"):
            assert r.route_for("correction", key) == "corrector", key
        for key in ("enabled", "translate_mode", "stream_mode", "json_mode", "enable_polish"):
            assert r.route_for("correction", key) == "live", key
        # R12 例外：extract_environment 由 workflow 直接消费，不得触达 AICorrector
        assert r.route_for("correction", "extract_environment") == "none"
        assert r.route_for("correction", "correction_prompt") == "none"

    def test_ocr_routing(self):
        r = RebuildRouter()
        assert r.route_for("ocr", "paddleocr") == "ocr"
        assert r.route_for("ui", "s_ocr_version") == "none"  # UI 键由 notify_ui_key 单独路由（批次 3）

    def test_language_routes_to_sync(self):
        """language 变化走免重启 sync（v3 改进：语言切换不杀子进程）。"""
        r = RebuildRouter()
        assert r.route_for("asr", "language") == "sync"


# ══════════════════════════════════════════════════════════════════
# 外部修改检测（2.4：mtime 指纹）
# ══════════════════════════════════════════════════════════════════


class TestExternalChangeDetection:
    def test_check_external_changes_detects_edit(self, tmp_path):
        from core.settings.registry import ConfigRegistry

        reg = ConfigRegistry(tmp_path)
        reg.asr.commit()  # 先落盘（AsrConfig 构造不写盘）
        reg._snapshot_mtimes()
        assert reg.check_external_changes() == []
        # 外部修改文件（写入不同内容 → mtime 变化 + 内容与内存不一致）
        reg.asr._path.write_text(
            json.dumps({"engine": "whisperx", "model_size": "external-change"}),
            encoding="utf-8",
        )
        changed = reg.check_external_changes()
        assert "asr_engines.json" in changed

    def test_check_external_changes_no_false_positive(self, tmp_path):
        from core.settings.registry import ConfigRegistry

        reg = ConfigRegistry(tmp_path)
        reg.asr.commit()
        reg._snapshot_mtimes()
        # 通过域对象写入（正常保存路径）不触发外部检测
        reg.asr.set("language", "en")
        reg.asr.commit()
        assert reg.check_external_changes() == []
