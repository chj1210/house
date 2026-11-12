"""
台北市購屋評估分析平台 — FastAPI 後端與戰情室 Web 伺服器
啟動方式：
    python app.py
預設監聽：
    http://127.0.0.1:8000
"""

from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from src.models import Raw591Listing, PriceDropEvent
from src.scraper_591 import get_sample_taipei_591_listings
from src.lvr_sync import get_sample_taipei_lvr_transactions
from src.avm_engine import TaipeiAVMEngine
from src.notifier import InstantAlertNotifier

app = FastAPI(
    title="台北市購屋評估分析平台 API (Taipei Real Estate Intelligence Platform)",
    description="591 即時內容擷取、多房仲重複歸戶、車位拆算、四道排他風控（法拍/凶宅/地上權/海砂屋）與低於實價登錄 10% 對標引擎",
    version="1.0.0",
)

# 初始化核心引擎與範例資料
RAW_LISTINGS = get_sample_taipei_591_listings()
LVR_TRANSACTIONS = get_sample_taipei_lvr_transactions()
AVM_ENGINE = TaipeiAVMEngine(lvr_transactions=LVR_TRANSACTIONS)
NOTIFIER = InstantAlertNotifier()


class CustomEvalRequest(BaseModel):
    title: str = "大安區科技大樓站方正三房含坡平車位"
    district: str = "大安區"
    address_road: str = "和平東路二段118巷"
    community_name: Optional[str] = "敦南觀止"
    building_type: str = "電梯大樓"
    zoning: str = "第三種住宅區"
    asking_price_wan: float = 4350.0
    total_ping: float = 45.20
    main_building_ping: float = 21.80
    accessory_ping: float = 2.70
    common_area_ping: float = 9.20
    has_parking: bool = True
    parking_type: Optional[str] = "坡道平面"
    parking_ping: float = 11.50
    parking_price_wan: float = 0.0
    floor: int = 8
    total_floors: int = 14
    age_years: float = 14.0
    rooms: int = 3
    halls: int = 2
    baths: int = 2
    description: str = "近捷運科技大樓站，邊間採光佳，屋主誠意出售。"


@app.get("/api/properties")
def list_evaluated_properties(
    district: Optional[str] = Query(None, description="台北市行政區篩選"),
    grade: Optional[str] = Query(None, description="優先等級篩選: S, DROP_S, A, B, REVIEW, EXCLUDED"),
    max_price_wan: Optional[float] = Query(None, description="總價預算上限 (萬元)"),
    include_excluded: bool = Query(True, description="是否包含已排除風險物件"),
):
    """取得完成歸戶、車位拆算、風險排除與實價對標後之物件清單"""
    all_props = AVM_ENGINE.evaluate_all(RAW_LISTINGS)
    filtered = []
    for p in all_props:
        if not include_excluded and p.risk_result.is_excluded:
            continue
        if district and district != "ALL" and p.district != district:
            continue
        if grade and grade != "ALL":
            if grade == "S_ALL" and p.priority_grade not in ("S", "DROP_S"):
                continue
            elif grade != "S_ALL" and p.priority_grade != grade:
                continue
        if max_price_wan and p.lowest_asking_price_wan > max_price_wan:
            continue
        filtered.append(p.to_dict())

    summary = {
        "raw_listings_count": len(RAW_LISTINGS),
        "clean_lvr_count": len(AVM_ENGINE.clean_lvr_transactions),
        "abnormal_lvr_removed": len(LVR_TRANSACTIONS) - len(AVM_ENGINE.clean_lvr_transactions),
        "total_entities": len(all_props),
        "s_count": sum(1 for p in all_props if p.priority_grade in ("S", "DROP_S")),
        "a_count": sum(1 for p in all_props if p.priority_grade == "A"),
        "b_count": sum(1 for p in all_props if p.priority_grade == "B"),
        "review_count": sum(1 for p in all_props if p.priority_grade == "REVIEW"),
        "excluded_count": sum(1 for p in all_props if p.priority_grade == "EXCLUDED"),
    }
    return {"summary": summary, "properties": filtered}


@app.get("/api/properties/{entity_id}")
def get_property_due_diligence(entity_id: str):
    """取得單一物件之一鍵體檢報告 (Property Due Diligence) 與推播預覽"""
    all_props = AVM_ENGINE.evaluate_all(RAW_LISTINGS)
    for p in all_props:
        if p.entity_id == entity_id:
            data = p.to_dict()
            data["alert_preview"] = NOTIFIER.format_alert_message(p)
            return data
    raise HTTPException(status_code=404, detail="找不到指定物件實體")


@app.post("/api/evaluate_custom")
def evaluate_custom_listing(req: CustomEvalRequest):
    """即時單筆 591 物件試算：輸入開價、坪數、車位與屋況描述，立即執行風控攔截與實價折價率拆算"""
    custom_listing = Raw591Listing(
        listing_id="S-CUSTOM-01",
        title=req.title,
        district=req.district,
        village="",
        address_road=req.address_road,
        community_name=req.community_name or None,
        building_type=req.building_type,
        zoning=req.zoning,
        asking_price_wan=req.asking_price_wan,
        initial_price_wan=req.asking_price_wan,
        total_ping=req.total_ping,
        main_building_ping=req.main_building_ping,
        accessory_ping=req.accessory_ping,
        common_area_ping=req.common_area_ping,
        has_parking=req.has_parking,
        parking_type=req.parking_type if req.has_parking else None,
        parking_ping=req.parking_ping if req.has_parking else 0.0,
        parking_price_wan=req.parking_price_wan if req.has_parking else 0.0,
        floor=req.floor,
        total_floors=req.total_floors,
        age_years=req.age_years,
        rooms=req.rooms,
        halls=req.halls,
        baths=req.baths,
        lat=25.0248,
        lng=121.5436,
        description=req.description,
        broker_agency="自訂試算物件",
        agent_name="使用者測試",
        url="https://sale.591.com.tw",
        days_on_market=1,
        price_history=[],
        mrt_station="台北市捷運站",
    )
    evaluated = AVM_ENGINE.evaluate_all([custom_listing])[0]
    res = evaluated.to_dict()
    res["alert_preview"] = NOTIFIER.format_alert_message(evaluated)
    return res


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def serve_dashboard():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
