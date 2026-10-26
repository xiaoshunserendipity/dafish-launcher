"""资源路径兼容层：保持旧的 `from .assets import asset_path` 仍可用。"""

from .paths import asset_exists, asset_path

__all__ = ["asset_path", "asset_exists"]
