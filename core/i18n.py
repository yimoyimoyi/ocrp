"""国际化（i18n）模块 —— 基于 Python gettext 的翻译支持，支持运行时语言切换。

用法：
    from core.i18n import _, ngettext, LanguageManager
    label.setText(_("就绪"))

    # 运行时切换语言
    LanguageManager().switch_language("en_US")

翻译文件目录：
    locale/zh_CN/LC_MESSAGES/orcp.po  (简体中文 - 源语言)
    locale/en_US/LC_MESSAGES/orcp.po  (English)
    locale/ja_JP/LC_MESSAGES/orcp.po  (日本語)
"""

import gettext
import locale
import os
from pathlib import Path

_LOCALE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "locale"
_DOMAIN = "orcp"

_translation: gettext.NullTranslations = gettext.NullTranslations()

SUPPORTED_LANGUAGES = {
    "zh_CN": "简体中文",
    "en_US": "English",
    "ja_JP": "日本語",
}

# 语言名映射（用于菜单显示）
LANGUAGE_DISPLAY_NAMES = {
    "zh_CN": "🇨🇳 简体中文",
    "en_US": "🇺🇸 English",
    "ja_JP": "🇯🇵 日本語",
}


def _get_system_lang() -> str:
    """检测系统语言，返回语言代码。"""
    try:
        lang = locale.getdefaultlocale()[0] or "en_US"
    except Exception:
        lang = "en_US"
    lang = lang.replace("-", "_")
    if "_" not in lang:
        lang_map = {"zh": "zh_CN", "en": "en_US", "ja": "ja_JP"}
        lang = lang_map.get(lang, "en_US")
    if lang not in SUPPORTED_LANGUAGES:
        lang = "en_US"
    return lang


def setup_i18n(lang: str = "") -> str:
    """初始化翻译系统。

    Args:
        lang: 语言代码，如 "zh_CN"、"en_US"、"ja_JP"。为空时自动检测系统语言。

    Returns:
        实际使用的语言代码。
    """
    global _translation

    if not lang:
        lang = _get_system_lang()

    lang = lang.replace("-", "_")
    if "_" not in lang:
        lang_map = {"zh": "zh_CN", "en": "en_US", "ja": "ja_JP"}
        lang = lang_map.get(lang, "en_US")
    if lang not in SUPPORTED_LANGUAGES:
        lang = "en_US"

    # 中文是源语言 → 使用 identity 翻译器，直接返回原文
    if lang.startswith("zh"):
        _translation = gettext.NullTranslations()
        _translation.install()
        return lang

    # 尝试加载 .po 文件
    catalog = _load_po_catalog(lang)
    _translation = _make_po_translator(catalog)
    _translation.install()
    return lang


def _load_po_catalog(lang: str) -> dict[str, str]:
    """从 .po 文件加载翻译 catalog。"""
    po_path = _LOCALE_DIR / lang / "LC_MESSAGES" / f"{_DOMAIN}.po"
    catalog: dict[str, str] = {}
    if not po_path.exists():
        return catalog
    try:
        with open(po_path, encoding="utf-8") as f:
            msgid_lines: list[str] = []
            msgstr_lines: list[str] = []
            in_msgid = False
            in_msgstr = False

            def _flush():
                """将当前积累的 msgid/msgstr 写入 catalog。"""
                if msgid_lines and msgstr_lines:
                    msgid = "".join(msgid_lines)
                    if msgid:
                        catalog[msgid] = "".join(msgstr_lines)

            for line in f:
                line = line.strip()
                if line.startswith("#"):
                    continue
                if not line:
                    # 空行 = 条目分隔符，先刷新当前条目
                    _flush()
                    msgid_lines = []
                    msgstr_lines = []
                    in_msgid = False
                    in_msgstr = False
                    continue
                if line.startswith('msgid "'):
                    _flush()
                    msgid_lines = [line[7:-1]]
                    msgstr_lines = []
                    in_msgid = True
                    in_msgstr = False
                elif line.startswith('msgstr "'):
                    msgstr_lines = [line[8:-1]]
                    in_msgid = False
                    in_msgstr = True
                elif line.startswith('"') and in_msgid:
                    msgid_lines.append(line[1:-1])
                elif line.startswith('"') and in_msgstr:
                    msgstr_lines.append(line[1:-1])
            # 文件末尾刷新最后一条
            _flush()
    except Exception:
        pass
    return catalog


def _make_po_translator(catalog: dict[str, str]) -> gettext.NullTranslations:
    class PoTranslations(gettext.NullTranslations):
        def __init__(self, cat):
            super().__init__()
            self._catalog = cat

        def gettext(self, message):
            return self._catalog.get(message, message)

        def ngettext(self, msgid1, msgid2, n):
            return self._catalog.get(msgid1, msgid1)

    return PoTranslations(catalog)


def _(message: str) -> str:
    """标记可翻译字符串并返回翻译后的文本。"""
    return _translation.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    """复数形式翻译。"""
    return _translation.ngettext(singular, plural, n)


class LanguageManager:
    """运行时语言切换管理器（单例）。"""

    _instance = None
    _current_lang: str = "zh_CN"
    _listeners: list[callable] = []

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def current_language(self) -> str:
        return self._current_lang

    @classmethod
    def initialize(cls, lang: str) -> str:
        """初始化语言管理器并设置语言。返回实际设置的语言代码。"""
        inst = cls()
        actual = setup_i18n(lang)
        inst._current_lang = actual
        return actual

    def switch_language(self, lang: str) -> bool:
        """运行时切换语言，通知所有监听器。返回是否切换成功。"""
        if lang not in SUPPORTED_LANGUAGES:
            return False
        if lang == self._current_lang:
            return True
        actual = setup_i18n(lang)
        self._current_lang = actual
        # 通知所有监听器
        for listener in self._listeners:
            try:
                listener(actual)
            except Exception:
                pass
        return True

    def register_listener(self, callback: callable):
        """注册语言切换监听器。callback(lang_code) 在语言切换时被调用。"""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def unregister_listener(self, callback: callable):
        """注销语言切换监听器。"""
        if callback in self._listeners:
            self._listeners.remove(callback)
