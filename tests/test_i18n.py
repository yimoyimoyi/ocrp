# -*- coding: utf-8 -*-
"""国际化模块单元测试。"""


class TestI18n:
    """测试 i18n 初始化和翻译功能。"""

    def test_setup_i18n_default(self):
        from core.i18n import setup_i18n, _

        setup_i18n()
        # Default (system locale) should return translated strings
        result = _("就绪")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_setup_i18n_zh_cn(self):
        from core.i18n import setup_i18n, _

        setup_i18n("zh_CN")
        assert _("就绪") == "就绪"
        assert _("ORCP - OCR 处理工具") == "ORCP - OCR 处理工具"

    def test_setup_i18n_en_us(self):
        from core.i18n import setup_i18n, _

        setup_i18n("en_US")
        assert _("就绪") == "Ready"
        assert _("ORCP - OCR 处理工具") == "ORCP - OCR Processing Tool"
        assert _("▶ 开始处理") == "▶ Start Processing"

    def test_setup_i18n_fallback(self):
        from core.i18n import setup_i18n, _

        setup_i18n("fr_FR")  # No French translation
        # Should fall back to identity (return msgid as-is)
        result = _("就绪")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_ngettext_singular(self):
        from core.i18n import setup_i18n, ngettext

        setup_i18n()
        assert ngettext("one file", "many files", 1) == "one file"

    def test_compile_po(self, tmp_path):
        from core.i18n import compile_po

        po = tmp_path / "test.po"
        po.write_text("""msgid ""
msgstr ""
"Language: en\\n"

msgid "hello"
msgstr "world"
""", encoding="utf-8")
        mo = tmp_path / "test.mo"
        n = compile_po(str(po), str(mo))
        assert n == 1
        assert mo.exists()
