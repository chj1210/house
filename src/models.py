"""
核心資料模型定義 (Data Models)
台北市購屋評估分析平台 (Taipei Real Estate Intelligence Platform)
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any


@dataclass
class BrokerChannel:
    """單一房仲刊登管道資訊"""
    listing_id: str          # 591 刊登編號 (如 "S16800123")
    broker_agency: str       # 仲介品牌/店頭 (如 "信義房屋大安店")
    agent_name: str          # 經紀人姓名
    asking_price_wan: float  # 該管道開價總價 (萬元)
    url: str                 # 591 原始連結
    published_date: str      # 刊登日期 (YYYY-MM-DD)
    updated_date: str        # 最後更新日期


@dataclass
class PriceDropEvent:
    """歷史價格調整軌跡"""
    date: str
    old_price_wan: float
    new_price_wan: float

    @property
    def drop_wan(self) -> float:
        return round(self.old_price_wan - self.new_price_wan, 1)

    @property
    def drop_pct(self) -> float:
        if self.old_price_wan <= 0:
            return 0.0
        return round((self.new_price_wan - self.old_price_wan) / self.old_price_wan * 100, 2)


@dataclass
class Raw591Listing:
    """從 591 擷取並完成動態字型解密後之原始刊登物件"""
    listing_id: str
    title: str
    district: str                  # 台北市 12 行政區 (如 "大安區")
    village: str                   # 里別 (如 "龍安里")
    address_road: str              # 路段/巷弄 (如 "和平東路二段118巷")
    community_name: Optional[str]  # 社區大樓名稱 (公寓可為 None 或空字串)
    building_type: str             # "電梯大樓", "華廈", "公寓", "透天"
    zoning: str                    # 都市計畫使用分區 (如 "第三種住宅區", "第二種商業區", "第三種工業區")
    asking_price_wan: float        # 開價總價 (萬元)
    initial_price_wan: float       # 初次上架開價總價 (萬元)
    total_ping: float              # 權狀總坪數 (含車位)
    main_building_ping: float      # 主建物坪數
    accessory_ping: float          # 附屬建物坪數 (陽台、雨遮等)
    common_area_ping: float        # 公設坪數 (不含車位)
    has_parking: bool              # 是否含車位
    parking_type: Optional[str]    # "坡道平面", "坡道機械", "升降平面", "升降機械", None
    parking_ping: float            # 車位坪數 (若未拆分則為 0.0)
    parking_price_wan: float       # 刊登標示之車位價格 (萬元，若未標示則為 0.0)
    floor: int                     # 所在樓層 (地下室為負數，如 -1 代表 B1)
    total_floors: int              # 總樓層數
    age_years: float               # 屋齡 (年)
    rooms: int                     # 房數
    halls: int                     # 廳數
    baths: int                     # 衛數
    lat: float                     # 緯度 (WGS84)
    lng: float                     # 經度 (WGS84)
    description: str               # 屋況特色描述全文
    broker_agency: str             # 刊登仲介公司
    agent_name: str                # 聯絡人
    url: str                       # 591 物件網址
    days_on_market: int            # 已上架天數
    price_history: List[PriceDropEvent] = field(default_factory=list)
    mrt_station: str = ""          # 鄰近捷運站


@dataclass
class LVRTransaction:
    """內政部實價登錄 / 臺北地政雲 歷史成交紀錄"""
    tx_id: str
    district: str
    village: str
    address_masked: str            # 去識別化門牌 (如 "和平東路二段118巷1~30號")
    community_name: Optional[str]  # 社區名稱 (對應臺北地政雲社區欄位)
    building_type: str             # "電梯大樓", "華廈", "公寓", "透天"
    tx_date: str                   # 成交日期 (YYYY-MM-DD)
    total_price_wan: float         # 成交總價 (萬元)
    total_ping: float              # 移轉總坪數 (含車位)
    parking_ping: float            # 車位坪數
    parking_price_wan: float       # 車位總價 (萬元)
    net_building_ping: float       # 扣除車位後建物坪數
    net_unit_price_wan: float      # 拆算車位後之真實建物單價 (萬元/坪)
    floor: int                     # 成交樓層
    total_floors: int              # 總樓層
    age_at_tx: float               # 成交時屋齡
    lat: float
    lng: float
    special_note: str = ""         # 備註欄 (如 "親友、員工、共有人或其他特殊關係間之交易")


@dataclass
class RiskCheckResult:
    """四道排他性風險過濾結果"""
    is_excluded: bool              # 是否一票否決排除
    needs_manual_review: bool      # 是否需人工複核 (如疑似凶宅黑話或異常低價)
    pass_foreclosure: bool         # [✓ 非法拍/債權處分]
    pass_haunted: bool             # [✓ 凶宅庫與黑話未命中]
    pass_structural: bool          # [✓ 非海砂/輻射/危樓列管]
    pass_property_type: bool       # [✓ 正常產權住宅(非地上權/非工業宅/非地下室/非頂加灌水)]
    exclusion_reasons: List[str] = field(default_factory=list)
    warning_flags: List[str] = field(default_factory=list)


@dataclass
class BenchmarkComparable:
    """用來計算實價基準價之對標成交樣本明細"""
    tx_id: str
    community_or_address: str
    tx_date: str
    floor_info: str
    age_years: float
    net_ping: float
    net_unit_price_wan: float
    adjusted_unit_price_wan: float # 經樓層與時間校正後之對標單價
    distance_m: float              # 與目標物件距離 (公尺)
    match_tier: str                # "Tier 1 (同社區)" 或 "Tier 2 (300m同質)"


@dataclass
class EvaluatedProperty:
    """完成歸戶、車位拆算、風險排除與實價對標後之完整評估報告"""
    entity_id: str                 # 歸戶後唯一實體識別碼
    primary_listing_id: str        # 全網最低價之 591 刊登編號
    title: str
    district: str
    village: str
    address_road: str
    community_name: Optional[str]
    building_type: str
    zoning: str
    mrt_station: str
    floor: int
    total_floors: int
    age_years: float
    layout: str                    # 如 "3房2廳2衛"
    lat: float
    lng: float

    # 價格與車位拆算資訊
    lowest_asking_price_wan: float # 全網最低開價總價 (萬元)
    highest_asking_price_wan: float# 同物件其他房仲最高開價 (萬元)
    initial_asking_price_wan: float# 初始上架總價 (萬元)
    total_ping: float              # 權狀總坪數
    net_building_ping: float       # 扣除車位後淨建物坪數
    indoor_ping: float             # 主建物 + 附屬建物實坪
    real_indoor_ratio_pct: float   # 實坪比 (%)
    public_ratio_pct: float        # 公設比 (%)
    has_parking: bool
    parking_type: Optional[str]
    parking_ping_deducted: float   # 拆算扣除之車位坪數
    parking_price_deducted_wan: float # 拆算扣除之車位價格 (萬元)
    parking_split_method: str      # "刊登明確拆分" / "帶入社區/同區車位實價均價拆算" / "無車位"

    raw_unit_price_wan: float      # 未拆車位前表面單價 (總價/總坪數) - 容易產生假性低價
    net_asking_unit_price_wan: float # P_ask：拆算車位後真實建物淨單價 (萬元/坪)

    # 實價登錄對標結果 (AVM)
    benchmark_unit_price_wan: float # P_benchmark：實價登錄基準單價 (萬元/坪)
    benchmark_tier: str            # 對標層級 ("Tier 1 同社區對標" / "Tier 2 半徑300m同質對標" / "Tier 3 行政區同型態")
    discount_rate_pct: float       # 實價折價率 (%) = (P_ask - P_benchmark) / P_benchmark * 100%
    initial_discount_rate_pct: float # 降價前初始折價率 (%)
    estimated_market_total_wan: float # AVM 估算合理總價 (含車位，萬元)
    potential_savings_wan: float   # 相較實價基準潛在折讓金額 (萬元)

    # 優先順序分級與處置建議
    priority_grade: str            # "S", "DROP_S", "A", "B", "REVIEW", "EXCLUDED"
    priority_label: str            # 顯示標籤
    strategy_advice: str           # 購屋出價與議價策略建議

    # 風險檢查與多房仲歸戶
    risk_result: RiskCheckResult
    days_on_market: int
    price_drop_count: int
    total_price_drop_wan: float
    total_price_drop_pct: float
    broker_channels: List[BrokerChannel] = field(default_factory=list)
    comparables: List[BenchmarkComparable] = field(default_factory=list)
    price_history: List[PriceDropEvent] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
