"""设备管理模块"""

from fastapi import APIRouter

from src.web.api.device.router import device_router

__all__ = ["device_router"]
