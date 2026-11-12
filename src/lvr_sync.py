"""
內政部實價登錄 Open Data 與臺北市「臺北地政雲」資料同步解析模組 (LVR Synchronizer)
負責：
1. 解析內政部不動產成交案件實際資訊 CSV (臺北市代碼 A_lvr_land_A.csv)
2. 自動執行平方公尺轉坪 (× 0.3025)、民國年轉西元年、中文字樓層轉數字
3. 自動於實價登錄端拆算車位總價與車位坪數，還原歷史成交真實建物淨單價
4. 內建與測試資料集對應之台北市歷史實價登錄資料庫（含正常交易與應被剔除之親友特殊交易樣本）
"""

import csv
import os
import re
from typing import List
from src.models import LVRTransaction


class TaipeiLVRSynchronizer:
    """內政部實價登錄與臺北地政雲同步器"""

    SQM_TO_PING = 0.3025

    CHINESE_NUM_MAP = {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    }

    @classmethod
    def parse_chinese_floor(cls, floor_str: str) -> int:
        """將實價登錄『八層』、『十四層』、『地下一層』轉換為整數樓層"""
        if not floor_str:
            return 3
        s = floor_str.replace("層", "").replace("樓", "").strip()
        if "地下" in s or s.startswith("B"):
            return -1
        if s.isdigit():
            return int(s)
        if s == "十":
            return 10
        if "十" in s:
            parts = s.split("十")
            tens = cls.CHINESE_NUM_MAP.get(parts[0], 1) if parts[0] else 1
            ones = cls.CHINESE_NUM_MAP.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
            return tens * 10 + ones
        return cls.CHINESE_NUM_MAP.get(s, 3)

    @staticmethod
    def roc_date_to_iso(roc_str: str) -> str:
        """將民國日期 '1150820' 轉為 '2026-08-20'"""
        s = re.sub(r"\D", "", str(roc_str))
        if len(s) == 7:
            year = int(s[:3]) + 1911
            return f"{year:04d}-{s[3:5]}-{s[5:7]}"
        return "2026-06-01"

    @classmethod
    def load_from_moi_csv(cls, csv_path: str) -> List[LVRTransaction]:
        """讀取內政部臺北市 A_lvr_land_A.csv 檔案並轉換為標準 LVRTransaction"""
        if not os.path.exists(csv_path):
            return []

        transactions: List[LVRTransaction] = []
        with open(csv_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader):
                # 跳過英文欄位說明列
                if row.get("鄉鎮市區") == "The villages and towns urban district":
                    continue
                tx_target = row.get("交易標的", "")
                if "車位" == tx_target or "土地" == tx_target:
                    continue

                district = row.get("鄉鎮市區", "")
                address = row.get("土地位置建物門牌", "")
                total_sqm = float(row.get("建物移轉總面積平方公尺", 0) or 0)
                park_sqm = float(row.get("車位移轉總面積平方公尺", 0) or 0)
                total_price_ntd = float(row.get("總價元", 0) or 0)
                park_price_ntd = float(row.get("車位總價元", 0) or 0)

                total_ping = round(total_sqm * cls.SQM_TO_PING, 2)
                park_ping = round(park_sqm * cls.SQM_TO_PING, 2)
                total_price_wan = round(total_price_ntd / 10000.0, 1)
                park_price_wan = round(park_price_ntd / 10000.0, 1)

                net_ping = round(max(total_ping - park_ping, 1.0), 2)
                net_price_wan = max(total_price_wan - park_price_wan, 10.0)
                net_unit_wan = round(net_price_wan / net_ping, 2)

                btype_raw = row.get("建物型態", "")
                if "公寓" in btype_raw:
                    btype = "公寓"
                elif "華廈" in btype_raw:
                    btype = "華廈"
                elif "透天" in btype_raw:
                    btype = "透天"
                else:
                    btype = "電梯大樓"

                floor = cls.parse_chinese_floor(row.get("移轉層次", ""))
                total_floors = cls.parse_chinese_floor(row.get("總樓層數", ""))
                tx_date = cls.roc_date_to_iso(row.get("交易年月日", "1150601"))

                transactions.append(
                    LVRTransaction(
                        tx_id=f"LVR-CSV-{idx:05d}",
                        district=district,
                        village="",
                        address_masked=address,
                        community_name=row.get("社區簡稱", "") or None,
                        building_type=btype,
                        tx_date=tx_date,
                        total_price_wan=total_price_wan,
                        total_ping=total_ping,
                        parking_ping=park_ping,
                        parking_price_wan=park_price_wan,
                        net_building_ping=net_ping,
                        net_unit_price_wan=net_unit_wan,
                        floor=floor,
                        total_floors=total_floors,
                        age_at_tx=15.0,
                        lat=25.0330,
                        lng=121.5430,
                        special_note=row.get("備註", ""),
                    )
                )
        return transactions


def get_sample_taipei_lvr_transactions() -> List[LVRTransaction]:
    """
    提供台北市各行政區近 12~24 個月實價登錄成交紀錄（已拆算車位後真實建物淨單價），
    供 Tier 1 (同社區) 與 Tier 2 (300m 同質公寓/大樓) 對標驗證。
    同時包含「親友特殊交易」異常樣本，驗證引擎會自動剔除異常低價實價登錄。
    """
    return [
        # --- 大安區「敦南觀止」同社區實價登錄 (Tier 1 對標基準約 136.5 萬/坪) ---
        LVRTransaction(
            tx_id="LVR-TPE-1001",
            district="大安區",
            village="龍淵里",
            address_masked="和平東路二段118巷1~30號",
            community_name="敦南觀止",
            building_type="電梯大樓",
            tx_date="2026-08-18",
            total_price_wan=4980.0,
            total_ping=45.30,
            parking_ping=11.50,
            parking_price_wan=360.0,
            net_building_ping=33.80,
            net_unit_price_wan=136.69,
            floor=9,
            total_floors=14,
            age_at_tx=13.8,
            lat=25.0248,
            lng=121.5436,
            special_note="",
        ),
        LVRTransaction(
            tx_id="LVR-TPE-1002",
            district="大安區",
            village="龍淵里",
            address_masked="和平東路二段118巷1~30號",
            community_name="敦南觀止",
            building_type="電梯大樓",
            tx_date="2026-05-11",
            total_price_wan=4890.0,
            total_ping=45.20,
            parking_ping=11.50,
            parking_price_wan=355.0,
            net_building_ping=33.70,
            net_unit_price_wan=134.57,
            floor=6,
            total_floors=14,
            age_at_tx=13.5,
            lat=25.0248,
            lng=121.5436,
            special_note="",
        ),
        LVRTransaction(
            tx_id="LVR-TPE-1003",
            district="大安區",
            village="龍淵里",
            address_masked="和平東路二段118巷1~30號",
            community_name="敦南觀止",
            building_type="電梯大樓",
            tx_date="2026-02-22",
            total_price_wan=5050.0,
            total_ping=45.50,
            parking_ping=11.50,
            parking_price_wan=365.0,
            net_building_ping=34.00,
            net_unit_price_wan=137.79,
            floor=11,
            total_floors=14,
            age_at_tx=13.2,
            lat=25.0248,
            lng=121.5436,
            special_note="",
        ),
        # 故意放一筆「敦南觀止」親友特殊交易 (單價僅 85萬/坪)，驗證引擎會自動剔除不納入基準價！
        LVRTransaction(
            tx_id="LVR-TPE-1004-ABNORMAL",
            district="大安區",
            village="龍淵里",
            address_masked="和平東路二段118巷1~30號",
            community_name="敦南觀止",
            building_type="電梯大樓",
            tx_date="2026-07-01",
            total_price_wan=3200.0,
            total_ping=45.20,
            parking_ping=11.50,
            parking_price_wan=335.0,
            net_building_ping=33.70,
            net_unit_price_wan=85.01,
            floor=7,
            total_floors=14,
            age_at_tx=13.7,
            lat=25.0248,
            lng=121.5436,
            special_note="親友、員工、共有人或其他特殊關係間之交易；",
        ),

        # --- 文山區辛亥路四段周邊 300m 內同質 5 樓公寓實價登錄 (Tier 2 對標基準約 64.5 萬/坪) ---
        LVRTransaction(
            tx_id="LVR-TPE-2001",
            district="文山區",
            village="興安里",
            address_masked="辛亥路四段101巷1~30號",
            community_name=None,
            building_type="公寓",
            tx_date="2026-09-05",
            total_price_wan=1960.0,
            total_ping=30.20,
            parking_ping=0.0,
            parking_price_wan=0.0,
            net_building_ping=30.20,
            net_unit_price_wan=64.90,
            floor=3,
            total_floors=5,
            age_at_tx=37.5,
            lat=25.0056,
            lng=121.5579,
            special_note="",
        ),
        LVRTransaction(
            tx_id="LVR-TPE-2002",
            district="文山區",
            village="興安里",
            address_masked="辛亥路四段77巷1~30號",
            community_name=None,
            building_type="公寓",
            tx_date="2026-06-19",
            total_price_wan=2010.0,
            total_ping=31.00,
            parking_ping=0.0,
            parking_price_wan=0.0,
            net_building_ping=31.00,
            net_unit_price_wan=64.84,
            floor=2,
            total_floors=5,
            age_at_tx=39.0,
            lat=25.0049,
            lng=121.5568,
            special_note="",
        ),
        LVRTransaction(
            tx_id="LVR-TPE-2003",
            district="文山區",
            village="興安里",
            address_masked="辛亥路四段130巷1~30號",
            community_name=None,
            building_type="公寓",
            tx_date="2026-04-12",
            total_price_wan=1890.0,
            total_ping=30.80,
            parking_ping=0.0,
            parking_price_wan=0.0,
            net_building_ping=30.80,
            net_unit_price_wan=61.36,
            floor=4,
            total_floors=5,
            age_at_tx=38.0,
            lat=25.0061,
            lng=121.5584,
            special_note="",
        ),
        # 同巷弄 1 樓店面效應公寓 (單價 98萬/坪)，驗證 Tier 2 對標 3 樓公寓時會自動排除 1 樓店面！
        LVRTransaction(
            tx_id="LVR-TPE-2004-1F",
            district="文山區",
            village="興安里",
            address_masked="辛亥路四段101巷1~30號",
            community_name=None,
            building_type="公寓",
            tx_date="2026-07-10",
            total_price_wan=2950.0,
            total_ping=30.10,
            parking_ping=0.0,
            parking_price_wan=0.0,
            net_building_ping=30.10,
            net_unit_price_wan=98.01,
            floor=1,
            total_floors=5,
            age_at_tx=38.0,
            lat=25.0055,
            lng=121.5577,
            special_note="一樓含庭院車庫使用",
        ),

        # --- 內湖區「民權大湖閱」同社區實價登錄 (Tier 1 基準約 110.2 萬/坪) ---
        LVRTransaction(
            tx_id="LVR-TPE-3001",
            district="內湖區",
            village="寶湖里",
            address_masked="民權東路六段180巷1~30號",
            community_name="民權大湖閱",
            building_type="電梯大樓",
            tx_date="2026-09-20",
            total_price_wan=4395.0,
            total_ping=48.00,
            parking_ping=10.50,
            parking_price_wan=255.0,
            net_building_ping=37.50,
            net_unit_price_wan=110.40,
            floor=7,
            total_floors=12,
            age_at_tx=10.8,
            lat=25.0692,
            lng=121.5990,
            special_note="",
        ),
        LVRTransaction(
            tx_id="LVR-TPE-3002",
            district="內湖區",
            village="寶湖里",
            address_masked="民權東路六段180巷1~30號",
            community_name="民權大湖閱",
            building_type="電梯大樓",
            tx_date="2026-05-30",
            total_price_wan=4350.0,
            total_ping=48.00,
            parking_ping=10.50,
            parking_price_wan=255.0,
            net_building_ping=37.50,
            net_unit_price_wan=109.20,
            floor=5,
            total_floors=12,
            age_at_tx=10.5,
            lat=25.0692,
            lng=121.5990,
            special_note="",
        ),
        LVRTransaction(
            tx_id="LVR-TPE-3003",
            district="內湖區",
            village="寶湖里",
            address_masked="民權東路六段180巷1~30號",
            community_name="民權大湖閱",
            building_type="電梯大樓",
            tx_date="2026-03-14",
            total_price_wan=4430.0,
            total_ping=48.00,
            parking_ping=10.50,
            parking_price_wan=255.0,
            net_building_ping=37.50,
            net_unit_price_wan=111.33,
            floor=8,
            total_floors=12,
            age_at_tx=10.3,
            lat=25.0692,
            lng=121.5990,
            special_note="",
        ),

        # --- 松山區「民生綠園華廈」同社區實價登錄 (Tier 1 基準約 104.8 萬/坪) ---
        LVRTransaction(
            tx_id="LVR-TPE-4001",
            district="松山區",
            village="富錦里",
            address_masked="民生東路五段69巷1~30號",
            community_name="民生綠園華廈",
            building_type="華廈",
            tx_date="2026-08-03",
            total_price_wan=3360.0,
            total_ping=32.00,
            parking_ping=0.0,
            parking_price_wan=0.0,
            net_building_ping=32.00,
            net_unit_price_wan=105.00,
            floor=6,
            total_floors=7,
            age_at_tx=28.8,
            lat=25.0594,
            lng=121.5618,
            special_note="",
        ),
        LVRTransaction(
            tx_id="LVR-TPE-4002",
            district="松山區",
            village="富錦里",
            address_masked="民生東路五段69巷1~30號",
            community_name="民生綠園華廈",
            building_type="華廈",
            tx_date="2026-04-25",
            total_price_wan=3340.0,
            total_ping=32.00,
            parking_ping=0.0,
            parking_price_wan=0.0,
            net_building_ping=32.00,
            net_unit_price_wan=104.38,
            floor=3,
            total_floors=7,
            age_at_tx=28.5,
            lat=25.0594,
            lng=121.5618,
            special_note="",
        ),

        # --- 中正區「古亭書香苑」同社區實價登錄 (Tier 1 基準約 126.5 萬/坪) ---
        LVRTransaction(
            tx_id="LVR-TPE-5001",
            district="中正區",
            village="板溪里",
            address_masked="羅斯福路二段70巷1~30號",
            community_name="古亭書香苑",
            building_type="電梯大樓",
            tx_date="2026-09-12",
            total_price_wan=4040.0,
            total_ping=38.80,
            parking_ping=8.50,
            parking_price_wan=195.0,
            net_building_ping=30.30,
            net_unit_price_wan=126.90,
            floor=10,
            total_floors=13,
            age_at_tx=15.8,
            lat=25.0268,
            lng=121.5229,
            special_note="",
        ),
        LVRTransaction(
            tx_id="LVR-TPE-5002",
            district="中正區",
            village="板溪里",
            address_masked="羅斯福路二段70巷1~30號",
            community_name="古亭書香苑",
            building_type="電梯大樓",
            tx_date="2026-06-08",
            total_price_wan=4010.0,
            total_ping=38.80,
            parking_ping=8.50,
            parking_price_wan=195.0,
            net_building_ping=30.30,
            net_unit_price_wan=125.91,
            floor=8,
            total_floors=13,
            age_at_tx=15.5,
            lat=25.0268,
            lng=121.5229,
            special_note="",
        ),

        # --- 信義區「信義帝景大樓」同社區實價登錄 (Tier 1 基準約 118.0 萬/坪，車位實價 360 萬) ---
        LVRTransaction(
            tx_id="LVR-TPE-6001",
            district="信義區",
            village="廣居里",
            address_masked="忠孝東路五段236巷1~30號",
            community_name="信義帝景大樓",
            building_type="電梯大樓",
            tx_date="2026-08-29",
            total_price_wan=4860.0,
            total_ping=51.80,
            parking_ping=13.80,
            parking_price_wan=360.0,
            net_building_ping=38.00,
            net_unit_price_wan=118.42,
            floor=8,
            total_floors=15,
            age_at_tx=11.8,
            lat=25.0402,
            lng=121.5761,
            special_note="",
        ),
        LVRTransaction(
            tx_id="LVR-TPE-6002",
            district="信義區",
            village="廣居里",
            address_masked="忠孝東路五段236巷1~30號",
            community_name="信義帝景大樓",
            building_type="電梯大樓",
            tx_date="2026-04-17",
            total_price_wan=4830.0,
            total_ping=51.80,
            parking_ping=13.80,
            parking_price_wan=360.0,
            net_building_ping=38.00,
            net_unit_price_wan=117.63,
            floor=6,
            total_floors=15,
            age_at_tx=11.5,
            lat=25.0402,
            lng=121.5761,
            special_note="",
        ),

        # --- 大同區「民權晴光大樓」同社區實價登錄 (Tier 1 基準約 90.5 萬/坪) ---
        LVRTransaction(
            tx_id="LVR-TPE-7001",
            district="大同區",
            village="蓬萊里",
            address_masked="承德路三段24巷1~30號",
            community_name="民權晴光大樓",
            building_type="電梯大樓",
            tx_date="2026-07-15",
            total_price_wan=2820.0,
            total_ping=31.00,
            parking_ping=0.0,
            parking_price_wan=0.0,
            net_building_ping=31.00,
            net_unit_price_wan=90.97,
            floor=6,
            total_floors=12,
            age_at_tx=18.8,
            lat=25.0631,
            lng=121.5192,
            special_note="",
        ),
        LVRTransaction(
            tx_id="LVR-TPE-7002",
            district="大同區",
            village="蓬萊里",
            address_masked="承德路三段24巷1~30號",
            community_name="民權晴光大樓",
            building_type="電梯大樓",
            tx_date="2026-03-08",
            total_price_wan=2790.0,
            total_ping=31.00,
            parking_ping=0.0,
            parking_price_wan=0.0,
            net_building_ping=31.00,
            net_unit_price_wan=90.00,
            floor=5,
            total_floors=12,
            age_at_tx=18.5,
            lat=25.0631,
            lng=121.5192,
            special_note="",
        ),

        # --- 中山區錦州街與林森北路一般所有權大樓實價登錄 (約 82.0 萬/坪) ---
        LVRTransaction(
            tx_id="LVR-TPE-8001",
            district="中山區",
            village="集英里",
            address_masked="錦州街1~30號",
            community_name="錦州雙星",
            building_type="電梯大樓",
            tx_date="2026-06-20",
            total_price_wan=2020.0,
            total_ping=24.60,
            parking_ping=0.0,
            parking_price_wan=0.0,
            net_building_ping=24.60,
            net_unit_price_wan=82.11,
            floor=10,
            total_floors=12,
            age_at_tx=21.8,
            lat=25.0603,
            lng=121.5284,
            special_note="",
        ),
        LVRTransaction(
            tx_id="LVR-TPE-8002",
            district="中山區",
            village="集英里",
            address_masked="錦州街1~30號",
            community_name="錦州雙星",
            building_type="電梯大樓",
            tx_date="2026-02-11",
            total_price_wan=1990.0,
            total_ping=24.60,
            parking_ping=0.0,
            parking_price_wan=0.0,
            net_building_ping=24.60,
            net_unit_price_wan=80.89,
            floor=7,
            total_floors=12,
            age_at_tx=21.5,
            lat=25.0603,
            lng=121.5284,
            special_note="",
        ),
    ]
