"""国际化模块单元测试。"""

import pytest


@pytest.fixture(autouse=True)
def _restore_language_state():
    """语言是**全局**状态：本模块的用例会切到 en_US/ja_JP，必须还原。

    否则后续依赖具体文案的用例（如 test_settings_domain 里搜索中文"置信度"、
    断言状态栏含"找到"）会在日语界面下失败 —— 与用例本身无关的顺序依赖。
    """
    import core.i18n as i18n_mod

    saved_translation = i18n_mod._translation
    saved_lang = i18n_mod.LanguageManager._current_lang
    yield
    i18n_mod._translation = saved_translation
    i18n_mod.LanguageManager._current_lang = saved_lang


class TestI18n:
    """测试 i18n 初始化和翻译功能。"""

    def test_setup_i18n_default(self):
        from core.i18n import _, setup_i18n

        setup_i18n()
        # Default (system locale) should return translated strings
        result = _("就绪")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_setup_i18n_zh_cn(self):
        from core.i18n import _, setup_i18n

        setup_i18n("zh_CN")
        assert _("就绪") == "就绪"
        assert _("ORCP - OCR 处理工具") == "ORCP - OCR 处理工具"

    def test_setup_i18n_en_us(self):
        from core.i18n import _, setup_i18n

        setup_i18n("en_US")
        assert _("就绪") == "Ready"
        assert _("ORCP - OCR 处理工具") == "ORCP - OCR Processing Tool"
        assert _("▶ 开始处理") == "▶ Start Processing"

    def test_setup_i18n_fallback(self):
        from core.i18n import _, setup_i18n

        setup_i18n("fr_FR")  # No French translation
        # Should fall back to identity (return msgid as-is)
        result = _("就绪")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_ngettext_singular(self):
        from core.i18n import ngettext, setup_i18n

        setup_i18n()
        assert ngettext("one file", "many files", 1) == "one file"


def _parse_po(path) -> dict:
    """极简 .po 解析：msgid/msgstr 单行形式（本仓库不使用多行换行拼接）。"""
    import ast

    entries = {}
    msgid = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("msgid "):
            msgid = ast.literal_eval(raw[6:].strip())
        elif raw.startswith("msgstr ") and msgid is not None:
            entries[msgid] = ast.literal_eval(raw[7:].strip())
            msgid = None
    return entries


def _parse_po_with_ids(path):
    """返回 (msgid 出现顺序列表, {msgid: msgstr})。用于检出重复条目。"""
    import ast

    ids = []
    entries = {}
    cur = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("msgid "):
            cur = ast.literal_eval(raw[6:].strip())
            ids.append(cur)
        elif raw.startswith("msgstr ") and cur is not None:
            entries[cur] = ast.literal_eval(raw[7:].strip())
            cur = None
    return ids, entries


def _po_path(lang):
    from pathlib import Path

    return Path(__file__).resolve().parent.parent / "locale" / lang / "LC_MESSAGES" / "orcp.po"


class TestTranslationCatalogIntegrity:
    """.po 目录结构完整性。

    回归：catalog 中曾存在重复 msgid（两份 msgstr 恰好相同，属冗余；一旦其中一份
    被改动就会变成"哪份生效"的静默歧义），且 ja_JP 缺少 en_US/zh_CN 有的条目 ——
    日语界面会静默回落中文。
    """

    LANGS = ("zh_CN", "en_US", "ja_JP")

    def test_no_duplicate_msgids(self):
        for lang in self.LANGS:
            ids, entries = _parse_po_with_ids(_po_path(lang))
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            assert duplicates == [], f"{lang} 存在重复 msgid: {duplicates}"
            assert len(entries) == len(ids), f"{lang} 条目数与 msgid 行数不一致"

    def test_locales_cover_the_same_msgids(self):
        """三种语言必须覆盖同一批 msgid（否则该语言静默回落中文）。"""
        per_lang = {lang: set(_parse_po_with_ids(_po_path(lang))[1]) for lang in self.LANGS}
        for lang in ("en_US", "ja_JP"):
            missing = sorted(per_lang["zh_CN"] - per_lang[lang])
            assert missing == [], f"{lang} 缺少 zh_CN 中的条目: {missing}"
            extra = sorted(per_lang[lang] - per_lang["zh_CN"])
            assert extra == [], f"{lang} 存在 zh_CN 没有的条目: {extra}"

    def test_placeholders_match_across_locales(self):
        """同一 msgid 的占位符集合在三种语言中必须一致。

        占位符丢失会让 ``_("...").format(...)`` 抛 KeyError；多出占位符同样会。
        """
        import re

        pattern = re.compile(r"\{[^{}]*\}")
        per_lang = {lang: _parse_po_with_ids(_po_path(lang))[1] for lang in self.LANGS}
        mismatches = []
        for msgid in per_lang["zh_CN"]:
            if not msgid:
                continue
            sets = {lang: frozenset(pattern.findall(per_lang[lang][msgid])) for lang in self.LANGS}
            if len(set(sets.values())) > 1:
                mismatches.append((msgid, {k: sorted(v) for k, v in sets.items()}))
        assert mismatches == [], f"占位符跨语言不一致: {mismatches[:5]}"

    def test_no_empty_msgstr(self):
        for lang in self.LANGS:
            _, entries = _parse_po_with_ids(_po_path(lang))
            empty = [k for k, v in entries.items() if k and not v.strip()]
            assert empty == [], f"{lang} 存在空 msgstr: {empty[:5]}"


class TestPoCatalogUnescaping:
    """回归：手写 .po 解析器未还原转义序列。

    gettext 的 .po→.mo 编译会还原 ``\\n`` 等转义；本模块自己解析 .po，此前直接把
    引号之间的原始文本当 key，于是 msgid 里的 ``\\n`` 是**两个字符**（反斜杠 + n），
    与代码中真实的换行符永不相等 —— 所有含换行的条目运行时都查不到译文，静默回落
    中文原文（`❌ 加载失败: {msg}\\n拖放文件到此处` 等 6 条长期失效）。
    """

    def test_unescape_helper_handles_common_escapes(self):
        from core.i18n import _unescape_po

        assert _unescape_po("a\\nb") == "a\nb"
        assert _unescape_po('say \\"hi\\"') == 'say "hi"'
        assert _unescape_po("path\\\\to") == "path\\to"
        assert _unescape_po("tab\\there") == "tab\there"
        assert _unescape_po("无转义内容") == "无转义内容"

    def test_unescape_helper_survives_invalid_escape(self):
        """译文里可能出现非法 Python 转义（如正则 \\d），不得抛异常。"""
        from core.i18n import _unescape_po

        assert _unescape_po("regex \\d+") == "regex \\d+"

    def test_newline_msgid_resolves_at_runtime(self):
        from core.i18n import _, setup_i18n

        setup_i18n("en_US")
        assert _("❌ 加载失败: {msg}\n拖放文件到此处") == "❌ Load failed: {msg}\nDrag a file here"
        setup_i18n("ja_JP")
        assert "読み込み失敗" in _("❌ 加载失败: {msg}\n拖放文件到此处")

    def test_catalog_has_no_literal_backslash_n_keys(self):
        """catalog 中不得残留含字面 ``\\n``（两个字符）的 key。"""
        from core.i18n import _load_po_catalog

        for lang in ("zh_CN", "en_US", "ja_JP"):
            catalog = _load_po_catalog(lang)
            assert catalog, f"{lang} catalog 为空"
            bad = [k for k in catalog if "\\n" in k]
            assert bad == [], f"{lang} catalog 存在未还原的换行 key: {bad[:3]}"

    def test_every_escaped_po_entry_is_loadable(self):
        """原始 .po 里 msgid 含 ``\\n`` 转义的条目，还原后必须能作为 catalog key 命中。"""
        from pathlib import Path

        from core.i18n import _load_po_catalog

        root = Path(__file__).resolve().parent.parent
        for lang in ("zh_CN", "en_US", "ja_JP"):
            catalog = _load_po_catalog(lang)
            raw = (root / "locale" / lang / "LC_MESSAGES" / "orcp.po").read_text(encoding="utf-8")
            escaped_ids = [line[7:-1] for line in raw.splitlines() if line.startswith('msgid "') and "\\n" in line]
            assert escaped_ids, f"测试前提失效：{lang} 的 .po 中应存在含转义换行的 msgid"
            for raw_id in escaped_ids:
                assert raw_id.replace("\\n", "\n") in catalog, f"{lang} 转义条目未进入 catalog: {raw_id!r}"


class TestSystemLangNormalization:
    """回归：旧的 _get_system_lang 依赖已废弃的 locale.getdefaultlocale()，
    且无法识别 Windows 形态的语言名（"Chinese (Simplified)_China" 会被判为
    不受支持而回落 en_US），中文 Windows 上界面语言始终错误。"""

    def test_posix_style_codes(self):
        from core.i18n import _normalize_lang

        assert _normalize_lang("zh_CN.UTF-8") == "zh_CN"
        assert _normalize_lang("zh_CN") == "zh_CN"
        assert _normalize_lang("zh-CN") == "zh_CN"
        assert _normalize_lang("ja_JP.UTF-8") == "ja_JP"
        assert _normalize_lang("en_US.UTF-8") == "en_US"

    def test_windows_style_names(self):
        from core.i18n import _normalize_lang

        assert _normalize_lang("Chinese (Simplified)_China") == "zh_CN"
        assert _normalize_lang("Japanese_Japan") == "ja_JP"
        assert _normalize_lang("English_United States") == "en_US"

    def test_short_and_unknown_codes(self):
        from core.i18n import _normalize_lang

        assert _normalize_lang("zh") == "zh_CN"
        assert _normalize_lang("ja") == "ja_JP"
        assert _normalize_lang("en") == "en_US"
        assert _normalize_lang("fr_FR") == "en_US"
        assert _normalize_lang("") == "en_US"

    def test_modifier_is_stripped(self):
        from core.i18n import _normalize_lang

        assert _normalize_lang("zh_CN.UTF-8@euro") == "zh_CN"

    def test_get_system_lang_returns_supported_code(self):
        from core.i18n import SUPPORTED_LANGUAGES, _get_system_lang

        assert _get_system_lang() in SUPPORTED_LANGUAGES

    def test_no_deprecated_getdefaultlocale_usage(self):
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        offenders = []
        for path in sorted((root / "core").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "getdefaultlocale"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "locale"
                ):
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}")
        assert offenders == [], f"locale.getdefaultlocale() 已在 Python 3.15 移除: {offenders}"


class TestGettextExtractionSafety:
    """回归：_(f"...") 的 f-string 是 JoinedStr AST 节点，xgettext 无法提取，
    运行时插值后又必然查不到译文 —— 必须用 _("...").format(...)。"""

    def test_no_fstring_inside_gettext_call(self):
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        offenders = []
        for sub in ("ui", "core"):
            for path in sorted((root / sub).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    func = node.func
                    name = getattr(func, "id", None) or getattr(func, "attr", None)
                    if name not in ("_", "ngettext"):
                        continue
                    for arg in node.args:
                        if isinstance(arg, ast.JoinedStr):
                            offenders.append(f"{path.relative_to(root)}:{node.lineno}")
        assert offenders == [], f"gettext 调用中不能使用 f-string（xgettext 无法提取）: {offenders}"

    def test_parameterized_placeholder_entries_exist_in_all_locales(self):
        """参数化翻译条目必须在三种语言中齐备，且占位符原样保留
        （占位符丢失会导致 .format() 抛 KeyError）。"""
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        required = {
            "确定要删除预设「{name}」吗？": ("{name}",),
            "找到 {count} 个匹配项": ("{count}",),
            "❌ 加载失败: {msg}\n拖放视频文件到此处": ("{msg}",),
            "❌ 加载失败: {msg}\n拖放文件到此处": ("{msg}",),
        }
        for lang in ("zh_CN", "en_US", "ja_JP"):
            entries = _parse_po(root / "locale" / lang / "LC_MESSAGES" / "orcp.po")
            for msgid, placeholders in required.items():
                assert msgid in entries, f"{lang} 缺少 msgid: {msgid!r}"
                for ph in placeholders:
                    assert ph in entries[msgid], f"{lang} 译文丢失占位符 {ph}: {entries[msgid]!r}"

    def test_po_files_are_parseable_and_non_empty(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        for lang in ("zh_CN", "en_US", "ja_JP"):
            entries = _parse_po(root / "locale" / lang / "LC_MESSAGES" / "orcp.po")
            assert len(entries) > 100, f"{lang} 翻译条目过少，可能解析失败"
