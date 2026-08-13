"""配置域注册中心 —— 路径 → 域对象；启动 ensure + hydrate（方案 A）。

替代 C7 手工 7 步启动编排与 _restore_business_params 人肉映射。
"""

from pathlib import Path

from core.config_manager import ConfigManager, ensure_config_files
from core.settings.domains import AsrConfig, CorrectionConfig, OcrEnginesConfig, UiStateConfig


class ConfigRegistry:
    """配置域注册中心：按路径实例化域对象，启动 ensure + hydrate。"""

    def __init__(self, config_dir: Path | str | None = None):
        self._config_dir = config_dir
        self._config_mgr = ConfigManager()
        self.ui_state = UiStateConfig(self._config_mgr)
        self.asr = AsrConfig(config_dir)
        self.correction = CorrectionConfig(config_dir)
        self.ocr_engines = OcrEnginesConfig(config_dir)
        self._mtime_snapshot: dict[str, tuple[int, int]] = {}

    @property
    def config_mgr(self) -> ConfigManager:
        """settings.json 域承载（主窗口 _config_mgr 由此共享，避免双实例）。"""
        return self._config_mgr

    # ── 生命周期 ──
    def ensure(self) -> None:
        """生成缺失的配置文件。

        非业务文件（settings/api_presets/prompt_templates/filters）走
        config_manager.ensure_config_files；业务文件模板以域 DEFAULTS 为
        唯一来源（R7 注册中心，config_manager._CONFIG_TEMPLATES 业务段
        由 test_domain_defaults_match_templates 锁定一致性）。
        """
        ensure_config_files()
        for domain in (self.asr, self.correction, self.ocr_engines):
            if not domain._path.exists():
                from core.config_manager import atomic_write_json

                atomic_write_json(domain._path, domain.DEFAULTS)

    def hydrate(self) -> None:
        """启动单钩子：ensure + 各域 load + 指纹快照（替代 C7 手工 7 步编排）。"""
        self.ensure()
        self.asr.load()
        self.correction.load()
        self.ocr_engines.load()
        self.ui_state.get_mode_params()
        self._snapshot_mtimes()

    # ── 外部修改检测（2.4：手改 JSON 与 UI 覆盖冲突防丢数据）──

    def _snapshot_mtimes(self) -> None:
        """记录各业务域文件的 (mtime_ns, size) 指纹。"""
        for domain in (self.asr, self.correction, self.ocr_engines):
            path = domain._path
            try:
                st = path.stat()
                self._mtime_snapshot[domain.FILENAME] = (st.st_mtime_ns, st.st_size)
            except OSError:
                self._mtime_snapshot.pop(domain.FILENAME, None)

    def check_external_changes(self) -> list[str]:
        """返回本次会话中被外部修改的业务配置文件文件名列表。

        指纹比 APIPresetManager 的秒级 getmtime 更严（mtime_ns + size，
        同秒内改内容也可检出）。本会话内域对象正常 commit 写入的文件
        （内容与内存一致）只刷新指纹，不视为外部修改。
        """
        from core.config_manager import load_json_with_comments

        changed: list[str] = []
        for domain in (self.asr, self.correction, self.ocr_engines):
            path = domain._path
            try:
                st = path.stat()
            except OSError:
                continue
            snapshot = self._mtime_snapshot.get(domain.FILENAME)
            if snapshot != (st.st_mtime_ns, st.st_size):
                try:
                    # 正常保存（域对象 commit）→ 内容与内存一致 → 刷新指纹继续
                    if load_json_with_comments(path) == domain.get_all():
                        self._mtime_snapshot[domain.FILENAME] = (st.st_mtime_ns, st.st_size)
                        continue
                except Exception:
                    pass
                changed.append(domain.FILENAME)
        if changed:
            self._snapshot_mtimes()  # 检测后刷新指纹，避免重复提示
        return changed

    def reload_all(self) -> None:
        """全量重读磁盘（UX：重新加载配置菜单）。"""
        self.asr.reload()
        self.correction.reload()
        self.ocr_engines.reload()
        self.ui_state.get_mode_params()

    def reset_all_to_defaults(self) -> None:
        """全域恢复出厂默认（UX：恢复出厂设置）。"""
        self.asr.reset_to_defaults()
        self.correction.reset_to_defaults()
        self.ocr_engines.reset_to_defaults()

    def domain_for(self, filename: str):
        """按文件名取域对象（外部修改检测用）。"""
        for domain in (self.asr, self.correction, self.ocr_engines):
            if filename == domain.FILENAME:
                return domain
        return None

    # ── 读透合并（ConfigPanel 消费，保证 workers/flows 零改动）──
    def get_mode_params_merged(self) -> dict:
        """UI 状态键 + 业务域读透合并（文件键经 KEY_MAP 反查铺平成 mode_params 键）。"""
        mp = dict(self.ui_state.get_mode_params())
        for mp_key, file_key in AsrConfig.KEY_MAP.items():
            mp[mp_key] = self.asr.get(file_key)
        for mp_key, file_key in CorrectionConfig.KEY_MAP.items():
            mp[mp_key] = self.correction.get(file_key)
        return mp
