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

import ast
import gettext
import locale
import os
import warnings
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


# 语言关键字 → 受支持语言代码（按优先级匹配，均为小写子串）
# 覆盖 POSIX 的 "zh_CN.UTF-8" 与 Windows 的 "Chinese (Simplified)_China" 两种形态
_LANGUAGE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("chinese (simplified)", "zh_CN"),
    ("chinese_simplified", "zh_CN"),
    ("zh_hans", "zh_CN"),
    ("zh_cn", "zh_CN"),
    ("chinese", "zh_CN"),
    ("japanese", "ja_JP"),
    ("ja_jp", "ja_JP"),
    ("english", "en_US"),
    ("en_us", "en_US"),
)

# 短语言代码 → 受支持语言代码
_LANGUAGE_SHORT_CODES = {"zh": "zh_CN", "en": "en_US", "ja": "ja_JP"}


def _normalize_lang(raw: str) -> str:
    """把任意形态的系统语言标识归一为受支持的语言代码（未知回落 en_US）。

    处理 POSIX（zh_CN.UTF-8 / zh-CN / ja）与 Windows
    （Chinese (Simplified)_China / Japanese_Japan）两类命名。
    """
    text = str(raw or "").strip()
    if not text:
        return "en_US"

    # 去掉 POSIX 修饰符（"zh_CN.UTF-8@euro" → "zh_CN"）
    text = text.split("@", 1)[0]
    text = text.replace("-", "_")
    head = text.split(".", 1)[0]
    if head in SUPPORTED_LANGUAGES:
        return head

    lowered = text.lower()
    for keyword, code in _LANGUAGE_KEYWORDS:
        if keyword in lowered:
            return code

    short = head.split("_", 1)[0].lower()
    return _LANGUAGE_SHORT_CODES.get(short, "en_US")


def _get_system_lang() -> str:
    """检测系统语言，返回受支持的语言代码。

    不使用已废弃的 ``locale.getdefaultlocale()``（Python 3.15 移除）：
    依次尝试环境变量（POSIX 生效）→ ``locale.getlocale()``（Windows 生效）。
    """
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "").strip()
        if value:
            # LANGUAGE 可能是冒号分隔列表（"zh_CN:en_US"），取首个
            return _normalize_lang(value.split(":", 1)[0])

    try:
        detected = locale.getlocale(locale.LC_CTYPE)[0]
    except (locale.Error, TypeError, ValueError):
        detected = None
    return _normalize_lang(detected or "")


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


def _unescape_po(text: str) -> str:
    """还原 .po 引号内的转义序列（``\\n`` / ``\\"`` / ``\\\\`` / ``\\t`` 等）。

    此前直接使用引号之间的原始文本，于是 msgid 里的 ``\\n`` 是**两个字符**
    （反斜杠 + n），与代码中真实的换行符永不相等 —— 所有含换行的条目运行时
    都查不到译文，静默回落中文原文（gettext 的 .po→.mo 编译会做这步还原，
    本模块是手写解析器，此前漏了）。
    """
    if "\\" not in text:
        return text
    try:
        with warnings.catch_warnings():
            # 译文里可能出现非法的 Python 转义（如正则 "\d"），literal_eval
            # 仍能还原，只是会发 SyntaxWarning —— 这里不需要噪音
            warnings.simplefilter("ignore")
            value = ast.literal_eval(f'"{text}"')
    except (SyntaxError, ValueError):
        return text
    return value if isinstance(value, str) else text


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
                    msgid = _unescape_po("".join(msgid_lines))
                    if msgid:
                        catalog[msgid] = _unescape_po("".join(msgstr_lines))

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
    _listeners: list = []

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

    def register_listener(self, callback):
        """注册语言切换监听器。callback(lang_code) 在语言切换时被调用。"""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def unregister_listener(self, callback):
        """注销语言切换监听器。"""
        if callback in self._listeners:
            self._listeners.remove(callback)
