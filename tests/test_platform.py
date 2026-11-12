"""
台北市購屋評估分析平台 — 自動化測試腳本 (Unit & API Integration Tests)
執行方式：
    python -m unittest discover -s tests
"""

import unittest
from fastapi.testclient import TestClient
from app import app
from src.scraper_591 import FontDeobfuscator591, get_sample_taipei_591_listings
from src.lvr_sync import get_sample_taipei_lvr_transactions
from src.avm_engine import TaipeiAVMEngine


class TestTaipeiRealEstatePlatform(unittest.TestCase):
    def setUp(self):
        self.raw_listings = get_sample_taipei_591_listings()
        self.lvr_txs = get_sample_taipei_lvr_transactions()
        self.engine = TaipeiAVMEngine(lvr_transactions=self.lvr_txs)
        self.evaluated = self.engine.evaluate_all(self.raw_listings)
        self.client = TestClient(app)

    def test_font_deobfuscation(self):
        cmap = {0xE001: "1", 0xE007: "7", 0xE008: "8", 0xE000: "0"}
        self.assertEqual(
            FontDeobfuscator591.decode_text("&#xe001;&#xe007;&#xe008;&#xe000;萬", cmap),
            "1780萬",
        )

    def test_multi_broker_deduplication(self):
        # 16 筆原始刊登應歸戶為 12 個唯一房屋實體
        self.assertEqual(len(self.raw_listings), 16)
        self.assertEqual(len(self.evaluated), 12)
        # 敦南觀止 8F 應合併 3 家房仲並選出最低價 4380 萬
        dunnan = next(p for p in self.evaluated if p.community_name == "敦南觀止")
        self.assertEqual(len(dunnan.broker_channels), 3)
        self.assertEqual(dunnan.lowest_asking_price_wan, 4380.0)
        self.assertEqual(dunnan.highest_asking_price_wan, 4580.0)

    def test_parking_split_trap_detection(self):
        # 信義帝景大樓未拆車位表面單價為 100.0 萬/坪，拆車位後應為 126.84 萬/坪 (+7.47% B級)
        xinyi = next(p for p in self.evaluated if p.community_name == "信義帝景大樓")
        self.assertAlmostEqual(xinyi.raw_unit_price_wan, 100.0, places=1)
        self.assertAlmostEqual(xinyi.net_asking_unit_price_wan, 126.84, places=1)
        self.assertEqual(xinyi.priority_grade, "B")
        self.assertGreater(xinyi.discount_rate_pct, 0.0)

    def test_hard_exclusions(self):
        excluded_Map = {
            p.community_name: p for p in self.evaluated if p.priority_grade == "EXCLUDED"
        }
        self.assertIn("京站花園廣場", excluded_Map)
        self.assertIn("華固新天地", excluded_Map)
        self.assertIn("錦州雙星", excluded_Map)
        self.assertIn("林森春曉", excluded_Map)
        self.assertIn("康寧舊國宅A區", excluded_Map)

    def test_fastapi_endpoints(self):
        resp = self.client.get("/api/properties")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["summary"]["s_count"], 3)
        self.assertEqual(data["summary"]["a_count"], 2)
        self.assertEqual(data["summary"]["excluded_count"], 5)

        # 測試單筆自訂試算 API (帶入凶宅黑話)
        custom_resp = self.client.post(
            "/api/evaluate_custom",
            json={
                "title": "測試事故物件",
                "district": "中山區",
                "community_name": "測試社區",
                "asking_price_wan": 1200,
                "total_ping": 25.0,
                "has_parking": False,
                "description": "屋主誠售，有故事祈福屋，無忌諱者佳",
            },
        )
        self.assertEqual(custom_resp.status_code, 200)
        c_data = custom_resp.json()
        self.assertEqual(c_data["priority_grade"], "EXCLUDED")
        self.assertFalse(c_data["risk_result"]["pass_haunted"])


if __name__ == "__main__":
    unittest.main()
