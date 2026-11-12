"""
即時秒殺推播模組 (LINE Notify / Messaging API & Telegram Bot Alert Formatter)
當系統偵測到「低於實價登錄 10% 以下 (S級)」或「降價突破 -10% (DROP_S級)」且通過四道排他風控之物件時，
於 15 分鐘排程內自動產生結構化推播訊息並發送。
"""

import os
from typing import List
import httpx
from src.models import EvaluatedProperty


class InstantAlertNotifier:
    """LINE 與 Telegram 秒殺物件推播器"""

    def __init__(self, line_token: str = "", tg_bot_token: str = "", tg_chat_id: str = ""):
        self.line_token = line_token or os.environ.get("LINE_NOTIFY_TOKEN", "")
        self.tg_bot_token = tg_bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.tg_chat_id = tg_chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")

    @staticmethod
    def format_alert_message(prop: EvaluatedProperty) -> str:
        """產生單一 S 級／降價突破型物件之推播通知卡片文字"""
        comm_label = f"【{prop.community_name}】" if prop.community_name else f"【{prop.building_type}】"
        broker_count = len(prop.broker_channels)
        best_broker = prop.broker_channels[0] if prop.broker_channels else None
        price_spread = round(prop.highest_asking_price_wan - prop.lowest_asking_price_wan, 0)

        broker_line = ""
        if best_broker:
            broker_line = f"{best_broker.broker_agency}（{best_broker.agent_name}）"
            if broker_count > 1 and price_spread > 0:
                broker_line += f"｜全網 {broker_count} 家重複刊登中最低價（比最高開價省 {price_spread:.0f} 萬！）"

        drop_info = ""
        if prop.price_drop_count > 0:
            drop_info = (
                f"\n📉 降價軌跡：原開 {prop.initial_asking_price_wan:.0f} 萬 → 現開 {prop.lowest_asking_price_wan:.0f} 萬 "
                f"（累計降價 {prop.price_drop_count} 次 / 共降 {prop.total_price_drop_wan:.0f} 萬）"
            )

        comp_lines = []
        for idx, c in enumerate(prop.comparables[:3], start=1):
            comp_lines.append(
                f"  {idx}. {c.tx_date}｜{c.floor_info}｜淨單價 {c.net_unit_price_wan:.1f} 萬/坪 (校正後 {c.adjusted_unit_price_wan:.1f})"
            )
        comp_block = "\n".join(comp_lines) if comp_lines else "  （詳見平台報告）"

        msg = (
            f"🚨 {prop.priority_label}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 {prop.district}{prop.village} {comm_label} {prop.address_road}\n"
            f"🏠 {prop.layout}｜{prop.floor}F/{prop.total_floors}F｜屋齡 {prop.age_years:.0f} 年｜近捷運{prop.mrt_station}\n"
            f"💰 全網最低開價：{prop.lowest_asking_price_wan:,.0f} 萬（AVM 合理市值約 {prop.estimated_market_total_wan:,.0f} 萬，潛在價差 {prop.potential_savings_wan:,.0f} 萬）"
            f"{drop_info}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 【車位拆算與實價對標】\n"
            f"• 開價建物淨單價 (P_ask)：{prop.net_asking_unit_price_wan:.2f} 萬/坪\n"
            f"• 實價登錄基準價 (P_bench)：{prop.benchmark_unit_price_wan:.2f} 萬/坪 ({prop.benchmark_tier})\n"
            f"• 實價折價率：{prop.discount_rate_pct:+.2f}% 🔥\n"
            f"• 車位拆算：{prop.parking_split_method}\n"
            f"• 實坪比：{prop.real_indoor_ratio_pct:.1f}%（室內實坪 {prop.indoor_ping:.1f} 坪 / 淨建物 {prop.net_building_ping:.1f} 坪）\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🛡️ 【四道排他風控綠燈】\n"
            f"[✓ 非法拍] [✓ 凶宅庫/黑話未命中] [✓ 非海砂/輻射列管] [✓ 正常產權(非地上權/非工業宅)]\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📑 【對標實價登錄近3筆】\n{comp_block}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏆 最佳接洽管道：{broker_line}\n"
            f"🔗 591 連結：{best_broker.url if best_broker else ''}"
        )
        return msg

    def get_s_tier_alerts(self, properties: List[EvaluatedProperty]) -> List[str]:
        """篩選出所有 S 級與降價突破型 S 級物件並生成推播訊息"""
        alerts = []
        for p in properties:
            if p.priority_grade in ("S", "DROP_S") and not p.risk_result.is_excluded:
                alerts.append(self.format_alert_message(p))
        return alerts

    def send_telegram_alert(self, message: str) -> bool:
        """若有設定 TELEGRAM_BOT_TOKEN 與 TELEGRAM_CHAT_ID 則發送真實推播"""
        if not (self.tg_bot_token and self.tg_chat_id):
            return False
        url = f"https://api.telegram.org/bot{self.tg_bot_token}/sendMessage"
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.post(url, json={"chat_id": self.tg_chat_id, "text": message})
                return r.status_code == 200
        except Exception:
            return False
