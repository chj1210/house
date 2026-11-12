"""
台北市購屋評估分析平台 — 核心資料擷取、風控排除與 AVM 對標驗證腳本
執行方式：
    python run_pipeline.py
"""

import json
import os
import sys
from src.scraper_591 import (
    FontDeobfuscator591,
    Taipei591Scraper,
    get_sample_taipei_591_listings,
)
from src.lvr_sync import get_sample_taipei_lvr_transactions
from src.avm_engine import TaipeiAVMEngine
from src.notifier import InstantAlertNotifier


def verify_font_deobfuscator() -> None:
    """驗證 591 動態加密數字字型還原邏輯"""
    # 模擬 591 將數字 4, 3, 8, 0 映射至 Private Use Area (0xE004, 0xE003, 0xE008, 0xE000)
    mock_cmap = {
        0xE000: "0",
        0xE001: "1",
        0xE002: "2",
        0xE003: "3",
        0xE004: "4",
        0xE005: "5",
        0xE006: "6",
        0xE007: "7",
        0xE008: "8",
        0xE009: "9",
    }
    obfuscated_html_price = "&#xe004;&#xe003;&#xe008;&#xe000;萬 (權狀 &#xe004;&#xe005;.&#xe002;&#xe000; 坪)"
    decoded = FontDeobfuscator591.decode_text(obfuscated_html_price, mock_cmap)
    assert decoded == "4380萬 (權狀 45.20 坪)", f"解密失敗: {decoded}"
    print(f"[✓] 591 動態加密字型破解驗證通過: '{obfuscated_html_price}' -> '{decoded}'")


def main() -> None:
    # 確保 Windows 終端機 UTF-8 輸出正常
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 88)
    print(" 🏢 台北市購屋評估分析平台 (Taipei Real Estate Intelligence Platform) - 核心引擎驗證")
    print("=" * 88)

    # 1. 驗證 591 動態字型解密模組
    verify_font_deobfuscator()

    # 2. 載入 591 刊登資料與台北市實價登錄資料庫
    raw_listings = get_sample_taipei_591_listings()
    lvr_transactions = get_sample_taipei_lvr_transactions()

    # 3. 初始化 AVM 實價對標與四道排他風控引擎
    engine = TaipeiAVMEngine(lvr_transactions=lvr_transactions)
    evaluated_properties = engine.evaluate_all(raw_listings)

    print(
        f"[✓] 實價登錄同步完成：載入 {len(lvr_transactions)} 筆歷史成交，"
        f"自動剔除親友/特殊交易異常樣本 {len(lvr_transactions) - len(engine.clean_lvr_transactions)} 筆，"
        f"有效對標樣本 {len(engine.clean_lvr_transactions)} 筆。"
    )
    print(
        f"[✓] 多房仲重複刊登歸戶完成：原始 591 刊登 {len(raw_listings)} 筆 -> "
        f"歸戶為 {len(evaluated_properties)} 個唯一房屋實體。"
    )
    print("-" * 88)

    # 4. 分類統計與明細輸出
    s_list = [p for p in evaluated_properties if p.priority_grade in ("S", "DROP_S")]
    a_list = [p for p in evaluated_properties if p.priority_grade == "A"]
    b_list = [p for p in evaluated_properties if p.priority_grade == "B"]
    review_list = [p for p in evaluated_properties if p.priority_grade == "REVIEW"]
    excluded_list = [p for p in evaluated_properties if p.priority_grade == "EXCLUDED"]

    print("\n【一、S 級（低於實價登錄 10% 以下）& 降價突破型 第一優先購買清單】")
    for p in s_list:
        comm = p.community_name or p.building_type
        spread = p.highest_asking_price_wan - p.lowest_asking_price_wan
        dup_info = (
            f"共 {len(p.broker_channels)} 家房仲刊登 (最高 {p.highest_asking_price_wan:.0f}萬 vs 最低 {p.lowest_asking_price_wan:.0f}萬，價差 {spread:.0f}萬)"
            if len(p.broker_channels) > 1
            else "單一房仲刊登"
        )
        print(
            f"  • [{p.entity_id}] {p.priority_label} | {p.district} {comm} ({p.address_road} {p.floor}F/{p.total_floors}F)"
        )
        print(
            f"    ├─ 開價總價：{p.lowest_asking_price_wan:,.0f} 萬 (AVM 合理總價 {p.estimated_market_total_wan:,.0f} 萬，折讓空間 {p.potential_savings_wan:,.0f} 萬)"
        )
        print(
            f"    ├─ 單價對標：拆車位後淨單價 P_ask = {p.net_asking_unit_price_wan:.2f} 萬/坪 vs 實價基準 P_bench = {p.benchmark_unit_price_wan:.2f} 萬/坪"
        )
        print(
            f"    ├─ 折價幅度：{p.discount_rate_pct:+.2f}% | 對標層級：{p.benchmark_tier}"
        )
        print(
            f"    ├─ 車位拆算：{p.parking_split_method} (未拆車位前表面單價 {p.raw_unit_price_wan:.2f} 萬/坪)"
        )
        print(
            f"    └─ 房仲歸戶：{dup_info} -> 推薦接洽：{p.broker_channels[0].broker_agency}"
        )

    print("\n【二、A 級（折價 -5% ~ -10% 重點議價觀察清單）& B 級（車位灌水還原案例）】")
    for p in a_list + b_list + review_list:
        comm = p.community_name or p.building_type
        print(
            f"  • [{p.entity_id}] {p.priority_label} | {p.district} {comm} | "
            f"最低開價 {p.lowest_asking_price_wan:,.0f}萬 | "
            f"未拆車位單價 {p.raw_unit_price_wan:.2f}萬 -> 拆車位淨單價 {p.net_asking_unit_price_wan:.2f}萬 "
            f"(實價基準 {p.benchmark_unit_price_wan:.2f}萬 / 折價率 {p.discount_rate_pct:+.2f}%)"
        )
        for wf in p.risk_result.warning_flags:
            print(f"    ⚠️ {wf}")

    print("\n【三、四道排他風控一票否決排除清單（法拍 / 凶宅黑話 / 北市地上權 / 海砂屋 / 頂加灌水）】")
    for p in excluded_list:
        comm = p.community_name or p.building_type
        print(
            f"  • [{p.entity_id}] ❌ 已攔截排除 | {p.district} {comm} (開價 {p.lowest_asking_price_wan:,.0f}萬 / 表面單價 {p.raw_unit_price_wan:.2f}萬/坪)"
        )
        for reason in p.risk_result.exclusion_reasons:
            print(f"    ⛔ {reason}")

    # 5. 輸出 JSON 評估報告與 LINE/TG 推播訊息範例
    os.makedirs("output", exist_ok=True)
    report_path = os.path.join("output", "evaluation_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "summary": {
                    "raw_listings_count": len(raw_listings),
                    "deduplicated_entities_count": len(evaluated_properties),
                    "s_tier_count": len(s_list),
                    "a_tier_count": len(a_list),
                    "b_tier_count": len(b_list),
                    "review_count": len(review_list),
                    "excluded_count": len(excluded_list),
                },
                "properties": [p.to_dict() for p in evaluated_properties],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    notifier = InstantAlertNotifier()
    alerts = notifier.get_s_tier_alerts(evaluated_properties)
    alerts_path = os.path.join("output", "s_tier_alerts.txt")
    with open(alerts_path, "w", encoding="utf-8") as f:
        f.write("\n\n" + ("\n" + "=" * 60 + "\n\n").join(alerts))

    print("\n" + "=" * 88)
    print(f"[✓] 完整評估報告已輸出至: {os.path.abspath(report_path)}")
    print(f"[✓] LINE / Telegram 秒殺推播通知已產生 ({len(alerts)} 則): {os.path.abspath(alerts_path)}")
    print("=" * 88)


if __name__ == "__main__":
    main()
