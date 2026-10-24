"""Built-in runtime dashboard (运行看板): runs inside the bot process on an internal HTTP port."""

from .runtime import Runtime, mask_secret
from .server import DashboardServer, dashboard_settings

__all__ = ["DashboardServer", "Runtime", "dashboard_settings", "mask_secret"]
