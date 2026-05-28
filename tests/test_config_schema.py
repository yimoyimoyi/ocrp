"""配置 Schema 验证单元测试。"""


class TestValidateConfig:
    """测试 validate_config 核心验证逻辑。"""

    def test_valid_config(self):
        from core.config_schema import validate_config

        schema = {"required": ["name"], "properties": {"name": {"type": "str"}}}
        ok, errors = validate_config({"name": "test"}, schema)
        assert ok
        assert errors == []

    def test_missing_required_field(self):
        from core.config_schema import validate_config

        schema = {"required": ["name"], "properties": {"name": {"type": "str"}}}
        ok, errors = validate_config({}, schema)
        assert not ok
        assert any("缺少必填字段" in e for e in errors)

    def test_wrong_type(self):
        from core.config_schema import validate_config

        schema = {"properties": {"count": {"type": "int"}}}
        ok, errors = validate_config({"count": "not_an_int"}, schema)
        assert not ok
        assert any("期望 int" in e for e in errors)

    def test_enum_validation(self):
        from core.config_schema import validate_config

        schema = {"properties": {"device": {"type": "str", "enum": ["cpu", "cuda"]}}}
        ok, _ = validate_config({"device": "cpu"}, schema)
        assert ok

        ok, errors = validate_config({"device": "gpu"}, schema)
        assert not ok
        assert any("不在允许范围" in e for e in errors)

    def test_range_validation(self):
        from core.config_schema import validate_config

        schema = {"properties": {"timeout": {"type": "int", "min": 1, "max": 300}}}
        ok, _ = validate_config({"timeout": 30}, schema)
        assert ok

        ok, errors = validate_config({"timeout": 500}, schema)
        assert not ok
        assert any("> 最大值" in e for e in errors)

    def test_nested_object(self):
        from core.config_schema import validate_config

        schema = {
            "properties": {
                "window": {
                    "type": "dict",
                    "properties": {
                        "width": {"type": "int", "min": 100},
                    }
                }
            }
        }
        ok, _ = validate_config({"window": {"width": 800}}, schema)
        assert ok

        ok, errors = validate_config({"window": {"width": 50}}, schema)
        assert not ok

    def test_pattern_properties(self):
        from core.config_schema import validate_config

        schema = {
            "properties": {
                "engines": {
                    "type": "dict",
                    "pattern_properties": {
                        "properties": {
                            "type": {"type": "str", "enum": ["local", "api"]},
                        }
                    }
                }
            }
        }
        ok, _ = validate_config({"engines": {"paddle": {"type": "local"}}}, schema)
        assert ok

        ok, errors = validate_config({"engines": {"bad": {"type": "unknown"}}}, schema)
        assert not ok

    def test_required_property(self):
        from core.config_schema import validate_config

        schema = {
            "properties": {
                "name": {"type": "str", "required": True},
                "optional": {"type": "str"},
            }
        }
        ok, errors = validate_config({"optional": "x"}, schema)
        assert not ok
        assert any("必填属性缺失" in e for e in errors)

    def test_extra_fields_allowed(self):
        from core.config_schema import validate_config

        schema = {"properties": {"name": {"type": "str"}}}
        # Extra fields should not cause errors
        ok, _ = validate_config({"name": "test", "extra": 123}, schema)
        assert ok


class TestSchemaRegistry:
    """测试各 Schema 定义格式正确。"""

    def test_all_schemas_have_properties(self):
        from core.config_schemas import SCHEMA_REGISTRY

        for name, schema in SCHEMA_REGISTRY.items():
            assert "properties" in schema, f"{name} missing properties"

    def test_ocr_engines_schema_valid(self):
        from core.config_schema import validate_config
        from core.config_schemas import OCR_ENGINES_SCHEMA

        cfg = {
            "engines": {
                "paddleocr": {"type": "local", "enabled": True, "config": {}},
            },
            "default_engine": "paddleocr",
        }
        ok, _ = validate_config(cfg, OCR_ENGINES_SCHEMA)
        assert ok

    def test_asr_schema_valid(self):
        from core.config_schema import validate_config
        from core.config_schemas import ASR_ENGINES_SCHEMA

        cfg = {
            "engine": "whisperx", "model_size": "large-v3", "device": "cpu",
            "compute_type": "int8", "language": "zh",
        }
        ok, _ = validate_config(cfg, ASR_ENGINES_SCHEMA)
        assert ok

    def test_ai_correction_schema_valid(self):
        from core.config_schema import validate_config
        from core.config_schemas import AI_CORRECTION_SCHEMA

        cfg = {"engine": "openai_vision", "enabled": False}
        ok, _ = validate_config(cfg, AI_CORRECTION_SCHEMA)
        assert ok

    def test_api_presets_schema_valid(self):
        from core.config_schema import validate_config
        from core.config_schemas import API_PRESETS_SCHEMA

        cfg = {
            "presets": {
                "default": {"api_key": "", "base_url": "http://localhost", "model": "", "timeout": 30}
            },
            "default_preset": "default",
        }
        ok, _ = validate_config(cfg, API_PRESETS_SCHEMA)
        assert ok

    def test_filters_schema_valid(self):
        from core.config_schema import validate_config
        from core.config_schemas import FILTERS_SCHEMA

        cfg = {"keywords": ["test"]}
        ok, _ = validate_config(cfg, FILTERS_SCHEMA)
        assert ok
