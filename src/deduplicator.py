"""
多房仲重複刊登歸戶引擎 (Multi-Broker Entity Resolution & Deduplicator)
解決台北市同一物件由多家房仲同時刊登、開價不同、坪數微幅四捨五入之重複問題，
自動歸戶並找出「全網最低開價管道」。
"""

import re
from dataclasses import dataclass, field
from typing import List, Tuple
from src.models import Raw591Listing, BrokerChannel, PriceDropEvent


@dataclass
class DeduplicatedEntity:
    """歸戶後之唯一房屋實體"""
    entity_id: str
    primary_listing: Raw591Listing          # 開價最低之最佳房仲刊登
    lowest_asking_price_wan: float          # 全網最低開價 (萬元)
    highest_asking_price_wan: float         # 同物件最高開價 (萬元)
    initial_asking_price_wan: float         # 歷史最初上架開價 (萬元)
    max_days_on_market: int                 # 最長已上架天數
    broker_channels: List[BrokerChannel] = field(default_factory=list)
    merged_price_history: List[PriceDropEvent] = field(default_factory=list)


class MultiBrokerDeduplicator:
    """
    歸戶條件：
    1. 相同行政區 (district)
    2. 相同社區名稱 (若有社區) 或 相同正規化路段巷弄 (address_road)
    3. 相同所在樓層與總樓層 (floor, total_floors)
    4. 權狀總坪數誤差在 ±0.5 坪以內
    5. 屋齡誤差在 ±1.5 年以內
    """

    PING_TOLERANCE = 0.5
    AGE_TOLERANCE = 1.5

    @staticmethod
    def normalize_road(road: str) -> str:
        if not road:
            return ""
        s = road.replace("臺", "台").strip()
        # 統一全形數字與空白
        s = re.sub(r"\s+", "", s)
        return s

    @staticmethod
    def normalize_community(comm: str) -> str:
        if not comm:
            return ""
        s = comm.replace("臺", "台").strip()
        s = re.sub(r"[\s\(\)（）「」『』]", "", s)
        return s

    def _is_same_property(self, a: Raw591Listing, b: Raw591Listing) -> bool:
        if a.district != b.district:
            return False
        if a.floor != b.floor or a.total_floors != b.total_floors:
            return False
        if abs(a.total_ping - b.total_ping) > self.PING_TOLERANCE:
            return False
        if abs(a.age_years - b.age_years) > self.AGE_TOLERANCE:
            return False

        comm_a = self.normalize_community(a.community_name or "")
        comm_b = self.normalize_community(b.community_name or "")
        if comm_a and comm_b:
            return comm_a == comm_b

        road_a = self.normalize_road(a.address_road)
        road_b = self.normalize_road(b.address_road)
        return bool(road_a and road_a == road_b)

    def deduplicate(self, listings: List[Raw591Listing]) -> List[DeduplicatedEntity]:
        """
        將原始 591 刊登清單歸戶為唯一物件實體清單
        """
        clusters: List[List[Raw591Listing]] = []

        for item in listings:
            matched_cluster = None
            for cluster in clusters:
                if self._is_same_property(item, cluster[0]):
                    matched_cluster = cluster
                    break
            if matched_cluster is not None:
                matched_cluster.append(item)
            else:
                clusters.append([item])

        entities: List[DeduplicatedEntity] = []
        for idx, cluster in enumerate(clusters, start=1):
            # 依開價由低至高排序，若開價相同則取已上架較久或資訊較完整者
            sorted_cluster = sorted(
                cluster,
                key=lambda x: (x.asking_price_wan, -x.days_on_market)
            )
            primary = sorted_cluster[0]
            lowest_price = sorted_cluster[0].asking_price_wan
            highest_price = max(x.asking_price_wan for x in sorted_cluster)
            initial_price = max(
                max(x.initial_price_wan for x in sorted_cluster),
                highest_price
            )
            max_dom = max(x.days_on_market for x in sorted_cluster)

            channels: List[BrokerChannel] = []
            history_map = {}

            for listing in sorted_cluster:
                channels.append(
                    BrokerChannel(
                        listing_id=listing.listing_id,
                        broker_agency=listing.broker_agency,
                        agent_name=listing.agent_name,
                        asking_price_wan=listing.asking_price_wan,
                        url=listing.url,
                        published_date="",
                        updated_date="今日更新"
                    )
                )
                for ev in listing.price_history:
                    key = (ev.date, ev.old_price_wan, ev.new_price_wan)
                    history_map[key] = ev

            merged_history = sorted(history_map.values(), key=lambda e: e.date)

            entity_id = f"TPE-ENT-{idx:04d}"
            entities.append(
                DeduplicatedEntity(
                    entity_id=entity_id,
                    primary_listing=primary,
                    lowest_asking_price_wan=lowest_price,
                    highest_asking_price_wan=highest_price,
                    initial_asking_price_wan=initial_price,
                    max_days_on_market=max_dom,
                    broker_channels=channels,
                    merged_price_history=merged_history,
                )
            )

        return entities
