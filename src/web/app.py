from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from src.web.api import (
    channel_router,
    device_router,
    point_router,
    point_mapping_router,
    point_tree_router,
    device_group_router,
)
from src.device_controller import get_device_controller
from src.web.api.schemas import BaseResponse
from src.web.log import log


def create_app():
    app = FastAPI(
        title="EMS Simulator API",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # 配置CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册路由
    app.include_router(channel_router)
    app.include_router(device_router)
    app.include_router(point_router)
    app.include_router(point_mapping_router)
    app.include_router(point_tree_router)
    app.include_router(device_group_router)

    return app


app = create_app()


@app.on_event("startup")
async def startup_event():
    """FastAPI启动事件，初始化设备控制器"""
    app.state.device_controller = await get_device_controller()
    return app.state.device_controller


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error(f"服务器内部错误: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content=BaseResponse(code=500, message=f"服务器内部错误: {str(exc)}", data={}).dict(),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8888, log_level="info")
