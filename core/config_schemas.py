"""各配置文件的 Schema 定义 —— 用于 config_schema.validate_config() 验证。"""

# ── ocr_engines.json ──
OCR_ENGINES_SCHEMA = {
    "required": ["engines", "default_engine"],
    "properties": {
        "engines": {
            "type": "dict",
            "pattern_properties": {
                "properties": {
                    "type": {"type": "str", "enum": ["local", "api"]},
                    "enabled": {"type": "bool"},
                    "config": {"type": "dict"},
                }
            }
        },
        "default_engine": {"type": "str"},
    }
}

# ── asr_engines.json ──
ASR_ENGINES_SCHEMA = {
    "required": ["engine", "model_size", "device"],
    "properties": {
        "enabled": {"type": "bool"},
        "engine": {"type": "str"},
        "model_size": {"type": "str"},
        "model_dir": {"type": "str"},
        "language": {"type": "str", "enum": ["auto", "zh", "en", "ja", "ko"]},
        "device": {"type": "str", "enum": ["cpu", "cuda"]},
        "compute_type": {"type": "str", "enum": ["float16", "float32", "int8", "int8_float16"]},
        "batch_size": {"type": "int", "min": 1, "max": 64},
        "beam_size": {"type": "int", "min": 1, "max": 10},
        "vad_enabled": {"type": "bool"},
        "vad_min_silence_ms": {"type": "int", "min": 100, "max": 5000},
        "vad_threshold": {"type": "float", "min": 0.0, "max": 1.0},
        "word_timestamps": {"type": "bool"},
        "condition_on_previous_text": {"type": "bool"},
        "no_speech_threshold": {"type": "float", "min": 0.0, "max": 1.0},
        "compression_ratio_threshold": {"type": "float", "min": 0.0, "max": 10.0},
        "temperature": {"type": "str"},
        "hotwords": {"type": "str"},
        "initial_prompt": {"type": "str"},
        "asr_region_name": {"type": "str"},
    }
}

# ── ai_correction.json ──
AI_CORRECTION_SCHEMA = {
    "required": ["engine"],
    "properties": {
        "enabled": {"type": "bool"},
        "engine": {"type": "str"},
        "correction_prompt": {"type": "str"},
        "retry_on_failure": {"type": "int", "min": 0, "max": 10},
        "api_key": {"type": "str"},
        "base_url": {"type": "str"},
        "model": {"type": "str"},
        "timeout": {"type": "int", "min": 1, "max": 300},
        "batch_size": {"type": "int", "min": 1, "max": 50},
        "context_window": {"type": "int", "min": 0, "max": 10},
        "retry": {"type": "int", "min": 0, "max": 10},
        "summary_prompt": {"type": "str"},
        "correction_system_prompt": {"type": "str"},
        "output_format": {"type": "str"},
        "stream_mode": {"type": "bool"},
        "json_mode": {"type": "bool"},
        "seg_time_gap": {"type": "float", "min": 0.0, "max": 60.0},
        "enable_polish": {"type": "bool"},
        "polish_prompt": {"type": "str"},
    }
}

# ── api_presets.json ──
API_PRESETS_SCHEMA = {
    "required": ["presets"],
    "properties": {
        "presets": {
            "type": "dict",
            "pattern_properties": {
                "properties": {
                    "api_key": {"type": "str"},
                    "base_url": {"type": "str"},
                    "model": {"type": "str"},
                    "timeout": {"type": "int", "min": 1, "max": 300},
                }
            }
        },
        "default_preset": {"type": "str"},
    }
}

# ── prompt_templates.json ──
PROMPT_TEMPLATES_SCHEMA = {
    "required": ["templates"],
    "properties": {
        "templates": {
            "type": "list",
            "items": {
                "type": "dict",
                "properties": {
                    "name": {"type": "str", "required": True},
                    "category": {"type": "str"},
                    "description": {"type": "str"},
                    "prompt": {"type": "str"},
                    "applicable_regions": {"type": "list"},
                }
            }
        }
    }
}

# ── filters.json ──
FILTERS_SCHEMA = {
    "properties": {
        "keywords": {"type": "list"},
    }
}

# ── ui_config.json ──
UI_CONFIG_SCHEMA = {
    "properties": {
        "window": {
            "type": "dict",
            "properties": {
                "default_width": {"type": "int", "min": 800},
                "default_height": {"type": "int", "min": 600},
                "min_width": {"type": "int", "min": 400},
                "min_height": {"type": "int", "min": 300},
            }
        },
        "theme": {
            "type": "dict",
            "properties": {
                "default": {"type": "str", "enum": ["dark", "light"]},
            }
        },
    }
}

# ── 注册表：文件名 → schema ──
SCHEMA_REGISTRY = {
    "ocr_engines.json": OCR_ENGINES_SCHEMA,
    "asr_engines.json": ASR_ENGINES_SCHEMA,
    "ai_correction.json": AI_CORRECTION_SCHEMA,
    "api_presets.json": API_PRESETS_SCHEMA,
    "prompt_templates.json": PROMPT_TEMPLATES_SCHEMA,
    "filters.json": FILTERS_SCHEMA,
    "ui_config.json": UI_CONFIG_SCHEMA,
}
