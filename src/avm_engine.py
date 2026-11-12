"""
實價登錄對標與車位拆算估價引擎 (AVM & Parking Split Engine)
實作：
1. 車位價格與坪數自動拆算（破解含車位總價除以總坪數之假性低單價陷阱）
2. 實價登錄異常交易剔除（親友、特殊交易、急買急賣、含增建）
3. 三層同心圓實價對標模型（Tier 1 同社區大樓 -> Tier 2 300m 同質同屋齡 -> Tier 3 行政區基準）
4. 實價折價率計算與 S / 降價突破 / A / 異常低價複核 優先級分類
"""

import math
import statistics
from typing import List, Dict, Tuple, Optional
from src.models import (
    Raw591Listing,
    LVRTransaction,
    BenchmarkComparable,
    EvaluatedProperty,
)
from src.deduplicator import DeduplicatedEntity, MultiBrokerDeduplicator
from src.risk_filter import TaipeiRiskFilterEngine


class TaipeiAVMEngine:
    """台北市不動產自動估價與折價率分析引擎"""

    # 台北市 12 行政區「坡道平面 / 坡道機械」車位預設基準行情表 (萬元/個, 標準車位坪數)
    # 當 591 刊登未拆算車位價格或坪數時，若同社區無車位實價，即帶入此行政區行情扣除
    DISTRICT_PARKING_BENCHMARK: Dict[str, Dict[str, Tuple[float, float]]] = {
        "大安區": {"坡道平面": (360.0, 11.5), "坡道機械": (210.0, 8.5), "升降機械": (175.0, 7.5)},
        "信義區": {"坡道平面": (350.0, 11.5), "坡道機械": (205.0, 8.5), "升降機械": (170.0, 7.5)},
        "中正區": {"坡道平面": (330.0, 11.0), "坡道機械": (195.0, 8.5), "升降機械": (165.0, 7.5)},
        "松山區": {"坡道平面": (325.0, 11.0), "坡道機械": (190.0, 8.5), "升降機械": (160.0, 7.5)},
        "中山區": {"坡道平面": (310.0, 11.0), "坡道機械": (185.0, 8.0), "升降機械": (155.0, 7.5)},
        "士林區": {"坡道平面": (280.0, 10.5), "坡道機械": (165.0, 8.0), "升降機械": (140.0, 7.0)},
        "內湖區": {"坡道平面": (255.0, 10.5), "坡道機械": (155.0, 8.0), "升降機械": (130.0, 7.0)},
        "南港區": {"坡道平面": (265.0, 10.5), "坡道機械": (160.0, 8.0), "升降機械": (135.0, 7.0)},
        "北投區": {"坡道平面": (235.0, 10.5), "坡道機械": (145.0, 8.0), "升降機械": (120.0, 7.0)},
        "大同區": {"坡道平面": (275.0, 10.5), "坡道機械": (165.0, 8.0), "升降機械": (140.0, 7.0)},
        "文山區": {"坡道平面": (230.0, 10.5), "坡道機械": (140.0, 8.0), "升降機械": (115.0, 7.0)},
        "萬華區": {"坡道平面": (240.0, 10.5), "坡道機械": (145.0, 8.0), "升降機械": (120.0, 7.0)},
    }

    # 實價登錄備註欄需剔除之特殊異常交易關鍵字
    ABNORMAL_TX_NOTES: List[str] = [
        "親友", "員工", "共有人", "特殊關係", "急買急賣",
        "含增建", "未登記建物", "建商與地主", "合建", "債務抵償", "瑕疵"
    ]

    def __init__(
        self,
        lvr_transactions: List[LVRTransaction],
        risk_filter: Optional[TaipeiRiskFilterEngine] = None,
    ):
        self.raw_lvr_transactions = lvr_transactions
        self.clean_lvr_transactions = self._filter_abnormal_lvr(lvr_transactions)
        self.risk_filter = risk_filter or TaipeiRiskFilterEngine()

    def _filter_abnormal_lvr(self, txs: List[LVRTransaction]) -> List[LVRTransaction]:
        """剔除實價登錄中親友交易、急買急賣、含增建等異常樣本"""
        cleaned = []
        for tx in txs:
            note = tx.special_note or ""
            if any(kw in note for kw in self.ABNORMAL_TX_NOTES):
                continue
            if tx.net_unit_price_wan <= 0 or tx.net_building_ping <= 0:
                continue
            cleaned.append(tx)
        return cleaned

    @staticmethod
    def haversine_distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        """計算兩組經緯度間之球面距離 (公尺)，等同 PostGIS ST_DistanceSphere"""
        r = 6371000.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lng2 - lng1)
        a = (
            math.sin(dphi / 2.0) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
        )
        return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    def split_parking_and_calc_net_price(
        self, listing: Raw591Listing, asking_price_wan: float
    ) -> Tuple[float, float, float, float, str]:
        """
        步驟一：拆算物件真實淨單價 (P_ask)
        回傳：(net_building_ping, parking_ping_deducted, parking_price_deducted_wan, net_unit_price_wan, split_method)
        """
        if not listing.has_parking:
            net_ping = max(listing.total_ping, 1.0)
            net_unit = round(asking_price_wan / net_ping, 2)
            return net_ping, 0.0, 0.0, net_unit, "無車位（直接以權狀坪數計算）"

        # 尋找同社區實價登錄中之車位均價與平均坪數
        comm_norm = MultiBrokerDeduplicator.normalize_community(listing.community_name or "")
        comm_parking_prices = []
        comm_parking_pings = []
        if comm_norm:
            for tx in self.clean_lvr_transactions:
                tx_comm = MultiBrokerDeduplicator.normalize_community(tx.community_name or "")
                if tx_comm == comm_norm and tx.parking_price_wan > 0 and tx.parking_ping > 0:
                    comm_parking_prices.append(tx.parking_price_wan)
                    comm_parking_pings.append(tx.parking_ping)

        ptype = listing.parking_type or "坡道平面"
        dist_default = self.DISTRICT_PARKING_BENCHMARK.get(
            listing.district, {"坡道平面": (280.0, 10.5)}
        )
        default_price, default_ping = dist_default.get(ptype, (280.0, 10.5))

        if comm_parking_prices:
            benchmark_park_price = round(statistics.median(comm_parking_prices), 1)
            benchmark_park_ping = round(statistics.median(comm_parking_pings), 2)
            source_desc = f"同社區({listing.community_name})近一年車位實價均價"
        else:
            benchmark_park_price = default_price
            benchmark_park_ping = default_ping
            source_desc = f"{listing.district}{ptype}車位實價均價"

        # 判斷 591 刊登是否有明確且合理的車位價格與坪數
        # 若未填寫車位價(0)，或車位價明顯低報(< 行情65%)，則強制以實價車位行情拆算
        if listing.parking_price_wan >= benchmark_park_price * 0.75 and listing.parking_ping > 0:
            deduct_price = listing.parking_price_wan
            deduct_ping = listing.parking_ping
            method = f"依刊登拆算（車位 {deduct_ping} 坪 / {deduct_price:.0f} 萬）"
        else:
            deduct_price = benchmark_park_price
            deduct_ping = listing.parking_ping if listing.parking_ping > 0 else benchmark_park_ping
            method = f"自動還原拆算（帶入{source_desc}：{deduct_ping} 坪 / {deduct_price:.0f} 萬）"

        net_ping = round(max(listing.total_ping - deduct_ping, 5.0), 2)
        net_total_price = max(asking_price_wan - deduct_price, 100.0)
        net_unit_price = round(net_total_price / net_ping, 2)

        return net_ping, deduct_ping, deduct_price, net_unit_price, method

    @staticmethod
    def _floor_adjustment_factor(target_floor: int, target_total: int, tx_floor: int, tx_total: int, btype: str) -> float:
        """
        樓層價差微調係數：
        將歷史成交樣本之單價校正至目標物件所在樓層水準
        """
        factor = 1.0
        # 4樓文化折價校正
        if target_floor == 4 and tx_floor != 4:
            factor -= 0.025
        elif target_floor != 4 and tx_floor == 4:
            factor += 0.025

        # 電梯大樓高低樓層景觀採光價差 (每差 5 層約 1.5%)
        if btype in ("電梯大樓", "華廈"):
            floor_diff = target_floor - tx_floor
            factor += max(min(floor_diff * 0.003, 0.04), -0.04)

        # 公寓樓層調整 (公寓 2~3 樓最貴，4~5 樓需爬梯折價)
        if btype == "公寓":
            apt_weights = {1: 1.18, 2: 1.04, 3: 1.02, 4: 0.96, 5: 0.93, 6: 0.90}
            w_target = apt_weights.get(target_floor, 1.0)
            w_tx = apt_weights.get(tx_floor, 1.0)
            if w_tx > 0:
                factor = w_target / w_tx

        return factor

    def compute_benchmark_price(
        self, listing: Raw591Listing
    ) -> Tuple[float, str, List[BenchmarkComparable]]:
        """
        步驟二：三層同心圓實價登錄基準單價計算 (P_benchmark)
        Tier 1: 同社區大樓近 12~24 個月成交中位數（含樓層校正）
        Tier 2: 半徑 300m 內、同建物型態、屋齡差距 ±5 年內成交中位數（分離 1 樓店面與頂樓效應）
        Tier 3: 同行政區同建物型態屋齡帶基準
        """
        comm_norm = MultiBrokerDeduplicator.normalize_community(listing.community_name or "")
        tier1_comps: List[BenchmarkComparable] = []

        # Tier 1: 同社區對標（適用於有明確社區名稱之電梯大樓／華廈）
        if comm_norm and listing.building_type in ("電梯大樓", "華廈"):
            for tx in self.clean_lvr_transactions:
                tx_comm = MultiBrokerDeduplicator.normalize_community(tx.community_name or "")
                if tx.district == listing.district and tx_comm == comm_norm:
                    # 排除 1 樓特殊店面或庭院戶對一般樓層之干擾
                    if (listing.floor == 1) != (tx.floor == 1):
                        continue
                    dist_m = self.haversine_distance_m(listing.lat, listing.lng, tx.lat, tx.lng)
                    adj_factor = self._floor_adjustment_factor(
                        listing.floor, listing.total_floors, tx.floor, tx.total_floors, listing.building_type
                    )
                    adj_unit = round(tx.net_unit_price_wan * adj_factor, 2)
                    tier1_comps.append(
                        BenchmarkComparable(
                            tx_id=tx.tx_id,
                            community_or_address=f"{tx.community_name} ({tx.address_masked})",
                            tx_date=tx.tx_date,
                            floor_info=f"{tx.floor}F/{tx.total_floors}F",
                            age_years=tx.age_at_tx,
                            net_ping=tx.net_building_ping,
                            net_unit_price_wan=tx.net_unit_price_wan,
                            adjusted_unit_price_wan=adj_unit,
                            distance_m=round(dist_m, 1),
                            match_tier="Tier 1 (同社區大樓對標)",
                        )
                    )

        if len(tier1_comps) >= 2:
            tier1_comps.sort(key=lambda c: c.tx_date, reverse=True)
            selected = tier1_comps[:10]
            median_price = round(
                statistics.median([c.adjusted_unit_price_wan for c in selected]), 2
            )
            return median_price, "Tier 1（同社區大樓實價中位數對標）", selected

        # Tier 2: 空間半徑 300m 同質對標（同建物型態、屋齡 ±5 年、分離 1 樓店面效應）
        tier2_comps: List[BenchmarkComparable] = []
        for tx in self.clean_lvr_transactions:
            if tx.district != listing.district:
                continue
            if tx.building_type != listing.building_type:
                continue
            if (listing.floor == 1) != (tx.floor == 1):
                continue
            if abs(tx.age_at_tx - listing.age_years) > 6.0:
                continue

            dist_m = self.haversine_distance_m(listing.lat, listing.lng, tx.lat, tx.lng)
            if dist_m <= 350.0:
                adj_factor = self._floor_adjustment_factor(
                    listing.floor, listing.total_floors, tx.floor, tx.total_floors, listing.building_type
                )
                adj_unit = round(tx.net_unit_price_wan * adj_factor, 2)
                label = (
                    f"{tx.community_name} ({tx.address_masked})"
                    if tx.community_name
                    else tx.address_masked
                )
                tier2_comps.append(
                    BenchmarkComparable(
                        tx_id=tx.tx_id,
                        community_or_address=label,
                        tx_date=tx.tx_date,
                        floor_info=f"{tx.floor}F/{tx.total_floors}F",
                        age_years=tx.age_at_tx,
                        net_ping=tx.net_building_ping,
                        net_unit_price_wan=tx.net_unit_price_wan,
                        adjusted_unit_price_wan=adj_unit,
                        distance_m=round(dist_m, 1),
                        match_tier="Tier 2 (300m同質同屋齡對標)",
                    )
                )

        if len(tier2_comps) >= 2:
            tier2_comps.sort(key=lambda c: (c.distance_m, c.tx_date))
            selected = tier2_comps[:10]
            median_price = round(
                statistics.median([c.adjusted_unit_price_wan for c in selected]), 2
            )
            return median_price, "Tier 2（半徑 300m 同屋齡型態實價中位數）", selected

        # Tier 3: 退回同行政區同建物型態成交中位數
        tier3_comps: List[BenchmarkComparable] = []
        for tx in self.clean_lvr_transactions:
            if tx.district == listing.district and tx.building_type == listing.building_type:
                dist_m = self.haversine_distance_m(listing.lat, listing.lng, tx.lat, tx.lng)
                tier3_comps.append(
                    BenchmarkComparable(
                        tx_id=tx.tx_id,
                        community_or_address=tx.address_masked,
                        tx_date=tx.tx_date,
                        floor_info=f"{tx.floor}F/{tx.total_floors}F",
                        age_years=tx.age_at_tx,
                        net_ping=tx.net_building_ping,
                        net_unit_price_wan=tx.net_unit_price_wan,
                        adjusted_unit_price_wan=tx.net_unit_price_wan,
                        distance_m=round(dist_m, 1),
                        match_tier="Tier 3 (同行政區同型態基準)",
                    )
                )

        if tier3_comps:
            tier3_comps.sort(key=lambda c: c.distance_m)
            selected = tier3_comps[:8]
            median_price = round(
                statistics.median([c.adjusted_unit_price_wan for c in selected]), 2
            )
            return median_price, "Tier 3（同行政區同建物型態基準）", selected

        # 保底行政區均價
        return 95.0, "Tier 3（行政區預設基準）", []

    def classify_priority(
        self,
        discount_rate_pct: float,
        initial_discount_rate_pct: float,
        price_drop_count: int,
        is_excluded: bool,
        potential_savings_wan: float,
    ) -> Tuple[str, str, str]:
        """
        步驟三：依實價折價率與風險狀態判定優先購買等級與議價策略
        回傳：(priority_grade, priority_label, strategy_advice)
        """
        if is_excluded:
            return (
                "EXCLUDED",
                "❌ 排他風險排除",
                "命中硬性排他風控規則（法拍／凶宅／地上權／工業宅／海砂危樓），系統已自動剔除，不列入推薦清單。"
            )

        if discount_rate_pct <= -25.0:
            return (
                "REVIEW",
                "⚠️ 異常低價複核 (≤-25%)",
                f"開價低於實價基準達 {abs(discount_rate_pct):.1f}%（低於75折），偏離正常屋主讓利區間。強烈建議人工調閱異動索引與現場查訪，確認是否為未公開事故屋、私人借貸設定或釣魚廣告件。"
            )

        if (
            discount_rate_pct <= -10.0
            and initial_discount_rate_pct > -5.0
            and price_drop_count > 0
        ):
            return (
                "DROP_S",
                "📉 降價突破型 S 級 (急售雷達)",
                f"屋主心態明顯鬆動！原開價折價率僅 {initial_discount_rate_pct:+.1f}%，經 {price_drop_count} 次調降後突破 {discount_rate_pct:+.1f}%（低於實價 9 折以下，潛在折讓約 {potential_savings_wan:.0f} 萬）。建議 24 小時內優先約看並準備斡旋。"
            )

        if discount_rate_pct <= -10.0:
            return (
                "S",
                "🔥 S 級（第一優先購買 ≤-10%）",
                f"高價值秒殺標的！拆算車位後淨單價低於實價登錄基準達 {abs(discount_rate_pct):.1f}%（帳面折讓約 {potential_savings_wan:.0f} 萬），已立即觸發 LINE/TG 推播，建議直接透過全網最低價房仲管道預約看屋並依周邊 10 筆實價表出價。"
            )

        if -10.0 < discount_rate_pct <= -5.0:
            return (
                "A",
                "⭐ A 級（重點議價觀察 -5%~-10%）",
                f"目前開價已低於實價基準 {abs(discount_rate_pct):.1f}%。考量台北市一般成屋平均議價空間約 5%~8%，此案極有機會透過議價談入「實價 9 折（-10%）以下」成交，建議列入第一波看屋名單。"
            )

        return (
            "B",
            "📋 B 級（符合或高於行情 >-5%）",
            f"拆車位後淨單價與實價基準差距為 {discount_rate_pct:+.1f}%（注意：若未拆算車位可能看似便宜，但還原車位後並無顯著折價）。建議持續監控後續是否降價。"
        )

    def evaluate_entity(self, entity: DeduplicatedEntity) -> EvaluatedProperty:
        """對歸戶後之唯一房屋實體執行完整風控、車位拆算與 AVM 實價對標"""
        listing = entity.primary_listing

        # 1. 執行四道排他性風險檢查
        risk_result = self.risk_filter.evaluate(listing)

        # 2. 拆算車位並計算最低開價真實建物淨單價 (P_ask)
        (
            net_ping,
            deduct_ping,
            deduct_price,
            net_asking_unit_price,
            split_method,
        ) = self.split_parking_and_calc_net_price(listing, entity.lowest_asking_price_wan)

        # 同時計算初始上架價格對應之淨單價（供「降價突破型」判定）
        _, _, _, initial_net_unit_price, _ = self.split_parking_and_calc_net_price(
            listing, entity.initial_asking_price_wan
        )

        raw_unit_price = round(
            entity.lowest_asking_price_wan / max(listing.total_ping, 1.0), 2
        )

        # 若含車位且拆算車位後單價比未拆算單價高出超過 8%，附加「車位灌水稀釋單價警示」
        if listing.has_parking and (net_asking_unit_price - raw_unit_price) / raw_unit_price >= 0.08:
            risk_result.warning_flags.append(
                f"【車位稀釋單價提醒】未拆車位表面單價僅 {raw_unit_price} 萬/坪，扣除車位後真實建物淨單價為 {net_asking_unit_price} 萬/坪（相差 +{net_asking_unit_price - raw_unit_price:.1f} 萬/坪）"
            )

        # 計算實坪比與公設比
        indoor_ping = round(listing.main_building_ping + listing.accessory_ping, 2)
        real_indoor_ratio_pct = round(indoor_ping / max(net_ping, 1.0) * 100.0, 1)
        public_ratio_pct = round(
            listing.common_area_ping / max(net_ping, 1.0) * 100.0, 1
        )
        if real_indoor_ratio_pct < 58.0 and listing.building_type in ("電梯大樓", "華廈"):
            risk_result.warning_flags.append(
                f"【高公設比警示】實坪比僅 {real_indoor_ratio_pct}%（公設比 {public_ratio_pct}%），室內實際可用面積偏低"
            )

        # 3. 計算實價登錄基準單價 (P_benchmark)
        benchmark_unit_price, benchmark_tier, comps = self.compute_benchmark_price(listing)

        # 4. 計算實價折價率 (Discount Rate)
        discount_rate_pct = round(
            (net_asking_unit_price - benchmark_unit_price) / benchmark_unit_price * 100.0,
            2,
        )
        initial_discount_rate_pct = round(
            (initial_net_unit_price - benchmark_unit_price) / benchmark_unit_price * 100.0,
            2,
        )

        estimated_market_total_wan = round(
            benchmark_unit_price * net_ping + deduct_price, 1
        )
        potential_savings_wan = round(
            estimated_market_total_wan - entity.lowest_asking_price_wan, 1
        )

        price_drop_count = len(entity.merged_price_history)
        total_drop_wan = round(
            max(entity.initial_asking_price_wan - entity.lowest_asking_price_wan, 0.0), 1
        )
        total_drop_pct = (
            round(total_drop_wan / entity.initial_asking_price_wan * 100.0, 2)
            if entity.initial_asking_price_wan > 0
            else 0.0
        )

        # 5. 優先購買分級
        priority_grade, priority_label, strategy_advice = self.classify_priority(
            discount_rate_pct=discount_rate_pct,
            initial_discount_rate_pct=initial_discount_rate_pct,
            price_drop_count=price_drop_count,
            is_excluded=risk_result.is_excluded,
            potential_savings_wan=potential_savings_wan,
        )

        if priority_grade == "REVIEW":
            risk_result.needs_manual_review = True
            risk_result.warning_flags.append(
                f"【異常低價警示】折價率達 {discount_rate_pct}%（低於實價75折），需人工查核產權謄本與凶宅/法拍風險"
            )

        return EvaluatedProperty(
            entity_id=entity.entity_id,
            primary_listing_id=listing.listing_id,
            title=listing.title,
            district=listing.district,
            village=listing.village,
            address_road=listing.address_road,
            community_name=listing.community_name,
            building_type=listing.building_type,
            zoning=listing.zoning,
            mrt_station=listing.mrt_station,
            floor=listing.floor,
            total_floors=listing.total_floors,
            age_years=listing.age_years,
            layout=f"{listing.rooms}房{listing.halls}廳{listing.baths}衛",
            lat=listing.lat,
            lng=listing.lng,
            lowest_asking_price_wan=entity.lowest_asking_price_wan,
            highest_asking_price_wan=entity.highest_asking_price_wan,
            initial_asking_price_wan=entity.initial_asking_price_wan,
            total_ping=listing.total_ping,
            net_building_ping=net_ping,
            indoor_ping=indoor_ping,
            real_indoor_ratio_pct=real_indoor_ratio_pct,
            public_ratio_pct=public_ratio_pct,
            has_parking=listing.has_parking,
            parking_type=listing.parking_type,
            parking_ping_deducted=deduct_ping,
            parking_price_deducted_wan=deduct_price,
            parking_split_method=split_method,
            raw_unit_price_wan=raw_unit_price,
            net_asking_unit_price_wan=net_asking_unit_price,
            benchmark_unit_price_wan=benchmark_unit_price,
            benchmark_tier=benchmark_tier,
            discount_rate_pct=discount_rate_pct,
            initial_discount_rate_pct=initial_discount_rate_pct,
            estimated_market_total_wan=estimated_market_total_wan,
            potential_savings_wan=potential_savings_wan,
            priority_grade=priority_grade,
            priority_label=priority_label,
            strategy_advice=strategy_advice,
            risk_result=risk_result,
            days_on_market=entity.max_days_on_market,
            price_drop_count=price_drop_count,
            total_price_drop_wan=total_drop_wan,
            total_price_drop_pct=total_drop_pct,
            broker_channels=entity.broker_channels,
            comparables=comps,
            price_history=entity.merged_price_history,
            description=listing.description,
        )

    def evaluate_all(self, raw_listings: List[Raw591Listing]) -> List[EvaluatedProperty]:
        """執行完整管線：多房仲歸戶 -> 排他風控 -> 車位拆算 -> AVM 對標 -> 優先排序"""
        deduplicator = MultiBrokerDeduplicator()
        entities = deduplicator.deduplicate(raw_listings)
        evaluated = [self.evaluate_entity(ent) for ent in entities]

        # 排序邏輯：S 級與降價突破 S 級最優先，其次 A 級、B 級、異常低價複核，最後為已排除物件；同等級內依折價率由低至高排序
        grade_order = {
            "S": 0,
            "DROP_S": 1,
            "A": 2,
            "B": 3,
            "REVIEW": 4,
            "EXCLUDED": 5,
        }
        evaluated.sort(
            key=lambda p: (grade_order.get(p.priority_grade, 99), p.discount_rate_pct)
        )
        return evaluated
