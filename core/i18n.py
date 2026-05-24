# -*- coding: utf-8 -*-
"""国际化（i18n）模块 —— 基于 Python gettext 的翻译支持。

用法：
    from core.i18n import _
    label.setText(_("就绪"))

翻译文件目录：
    locale/zh_CN/LC_MESSAGES/orcp.mo  (简体中文)
    locale/en_US/LC_MESSAGES/orcp.mo  (English - fallback)
"""

import gettext
import locale
import os
from pathlib import Path

_LOCALE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "locale"
_DOMAIN = "orcp"

_translation: gettext.NullTranslations = gettext.NullTranslations()


def setup_i18n(lang: str = "") -> None:
    """初始化翻译系统。

    Args:
        lang: 语言代码，如 "zh_CN"、"en_US"。为空时自动检测系统语言。
    """
    global _translation

    if not lang:
        # locale.getdefaultlocale is deprecated in 3.13+
        try:
            lang = locale.getdefaultlocale()[0] or "en_US"
        except Exception:
            lang = "en_US"

    # 规范化语言代码
    lang = lang.replace("-", "_")
    if "_" not in lang:
        lang_map = {"zh": "zh_CN", "en": "en_US"}
        lang = lang_map.get(lang, lang)

    # 如果是中文（源语言），使用 identity 翻译器
    if lang.startswith("zh"):
        _translation = gettext.NullTranslations()
        _translation.install()
        return

    # 尝试加载 .mo 文件，失败则加载 .po 文件
    try:
        _translation = gettext.translation(
            _DOMAIN, localedir=str(_LOCALE_DIR),
            languages=[lang], fallback=False,
        )
    except Exception:
        _translation = _load_po(lang)

    _translation.install()


def _load_po(lang: str) -> gettext.NullTranslations:
    """从 .po 文件直接加载翻译（.mo 损坏时的回退方案）。"""
    po_path = _LOCALE_DIR / lang / "LC_MESSAGES" / f"{_DOMAIN}.po"
    catalog = {}
    if po_path.exists():
        try:
            with open(po_path, "r", encoding="utf-8") as f:
                msgid = msgstr = None
                for line in f:
                    line = line.strip()
                    if line.startswith('msgid "'):
                        msgid = line[7:-1]
                        msgstr = None
                    elif line.startswith('msgstr "'):
                        msgstr = line[8:-1]
                        if msgid is not None and msgstr is not None and msgid:
                            catalog[msgid] = msgstr
                        msgid = msgstr = None
        except Exception:
            pass

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


def compile_po(po_path: str, mo_path: str) -> int:
    """将 .po 文件编译为 .mo 文件。返回编译的条目数。"""
    import struct
    msgs = {}
    with open(po_path, "r", encoding="utf-8") as f:
        msgid = msgstr = None
        for line in f:
            line = line.strip()
            if line.startswith('msgid "'):
                msgid = line[7:-1]
                msgstr = None
            elif line.startswith('msgstr "'):
                msgstr = line[8:-1]
                if msgid is not None and msgstr is not None:
                    if msgid:
                        msgs[msgid] = msgstr
                    msgid = msgstr = None
    keys = list(msgs.keys())
    n = len(keys)
    with open(mo_path, "wb") as f:
        f.write(b"\xde\x12\x04\x95")
        f.write(struct.pack("<I", 0))
        f.write(struct.pack("<I", n))
        f.write(struct.pack("<I", 28))
        f.write(struct.pack("<I", 28 + n * 8))
        f.write(struct.pack("<I", 0))
        f.write(struct.pack("<I", 0))
        o_offset = 28 + n * 16
        t_offset = o_offset
        for k in keys:
            enc = k.encode("utf-8")
            f.write(struct.pack("<II", len(enc), o_offset))
            o_offset += len(enc) + 1
        for k in keys:
            enc = msgs[k].encode("utf-8")
            f.write(struct.pack("<II", len(enc), t_offset))
            t_offset += len(enc) + 1
        for k in keys:
            f.write(k.encode("utf-8") + b"\x00")
        for k in keys:
            f.write(msgs[k].encode("utf-8") + b"\x00")
    return n
