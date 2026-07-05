from fastapi import APIRouter, HTTPException, Query
from market_feed.manager import feed_manager
from services.metrics_service import metrics_service

router = APIRouter(prefix="/api/v1")

@router.post("/control/start")
async def start_feed():
    try:
        await feed_manager.start()
        return {"status": "success", "message": "Market feed started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/control/pause")
async def pause_feed():
    try:
        await feed_manager.pause()
        return {"status": "success", "message": "Market feed paused"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/control/stop")
async def stop_feed():
    try:
        await feed_manager.stop()
        return {"status": "success", "message": "Market feed stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/control/speed")
async def set_speed(speed: float = Query(..., description="Replay speed multiplier (0 = Max speed)")):
    try:
        await feed_manager.set_speed(speed)
        return {"status": "success", "message": f"Playback speed updated to {speed}x"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/control/step")
async def step_feed():
    try:
        await feed_manager.step()
        return {"status": "success", "message": "Advanced playback by 1 packet"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/status")
def get_status():
    return feed_manager.get_status()

@router.get("/metrics")
def get_metrics():
    return metrics_service.get_metrics()
