"""
排他性風險過濾引擎 (Hard Exclusion & Risk Filter Engine)
負責在計算折價率之前執行四道硬性過濾，避免將「瑕疵低價屋 / 特殊產權屋」誤判為「超值好屋」。
"""

import re
from typing import List, Set, Dict, Tuple
from src.models import Raw591Listing, RiskCheckResult


class TaipeiRiskFilterEngine:
    """
    台北市四道排他性風險過濾器：
    1. 法拍屋／債權處分排除（司法院／台灣金服公告比對 + NLP 關鍵字）
    2. 凶宅（非自然死亡）雙重攔截（公開凶宅庫／裁判書門牌比對 + 房仲黑話 NLP 偵測）
    3. 台北市「假性低價」特殊產權排除（地上權／使用權社區白名單與關鍵字、工業宅、純地下室、頂加灌水）
    4. 北市結構安全列管排除（建管處海砂屋、紅黃單危樓、核安會輻射屋列管清冊）
    """

    # 1. 法拍屋與特殊債權關鍵字
    FORECLOSURE_KEYWORDS: List[str] = [
        "法拍", "銀拍", "金拍", "點交", "不點交", "拍定", "代標",
        "債權讓與", "持分出售", "未辦保存登記", "法院拍賣", "強制執行",
        "一拍", "二拍", "三拍", "應買公告", "特拍", "台灣金服"
    ]

    # 2. 房仲事故屋（凶宅）隱晦黑話關鍵字
    HAUNTED_SLANG_KEYWORDS: List[str] = [
        "事故", "有故事", "惜售行情七折", "特殊屋況電洽", "祈福屋",
        "基督徒", "無忌諱者佳", "非自然", "百無禁忌", "福主",
        "特殊事故", "曾有遺憾", "宗教信仰者佳", "行情六折", "屋況特殊誠可議"
    ]

    # 3A. 台北市知名地上權／使用權住宅社區清單（開價常為市價 55%~70%，極易霸榜低於實價清單）
    TAIPEI_SUPERFICIES_COMMUNITIES: Set[str] = {
        "華固新天地", "京站花園廣場", "京站", "台北花園", "仁愛本真",
        "基泰建中", "昇陽麗石", "大將首驛", "派樂地", "信義峰閣",
        "歡喜居", "自慢藏", "日勝幸福站", "吉美璞立(地上權)", "睿泰川端"
    }

    SUPERFICIES_KEYWORDS: List[str] = [
        "地上權", "使用權", "無土地持分", "設定地上權", "租賃權住宅",
        "使用權住宅", "不具土地所有權", "70年地上權", "50年地上權"
    ]

    # 3B. 工業宅與違規使用關鍵字
    INDUSTRIAL_KEYWORDS: List[str] = [
        "乙工", "工業宅", "工業住宅", "工三", "工二", "策略性工業區",
        "一般事務所(工業區)"
    ]

    # 3C. 頂樓加蓋灌水關鍵字（把無權狀違建坪數混入開價總坪數壓低單價）
    ROOFTOP_INFLATION_PATTERNS: List[str] = [
        r"含頂加[\d\.]+坪",
        r"含增建[\d\.]+坪計算",
        r"使用坪數[\d\.]+坪\(含頂加\)",
        r"頂加一併計入坪數",
        r"權狀外加頂樓增建計價"
    ]

    # 4. 結構安全（海砂、輻射、紅黃單）關鍵字
    STRUCTURAL_KEYWORDS: List[str] = [
        "海砂", "高氯離子", "氯離子偏高", "輻射屋", "輻射列管",
        "紅單列管", "黃單列管", "危樓列管"
    ]

    def __init__(
        self,
        foreclosure_registry: List[Dict[str, str]] = None,
        haunted_registry: List[Dict[str, str]] = None,
        structural_hazard_registry: List[Dict[str, str]] = None,
    ):
        """
        初始化外部風險資料庫清冊（可由司法院、台灣凶宅網、北市建管處 Open Data 同步載入）
        """
        self.foreclosure_registry = foreclosure_registry or self._default_foreclosure_registry()
        self.haunted_registry = haunted_registry or self._default_haunted_registry()
        self.structural_hazard_registry = (
            structural_hazard_registry or self._default_structural_hazard_registry()
        )

    @staticmethod
    def _default_foreclosure_registry() -> List[Dict[str, str]]:
        """預設司法院／台灣金服法拍公告同步清單樣本"""
        return [
            {
                "district": "中山區",
                "road": "林森北路399巷",
                "community": "林森春曉",
                "floor": "6",
                "source": "臺灣臺北地方法院 115年度司執字第10492號（不點交法拍）"
            },
            {
                "district": "萬華區",
                "road": "西園路二段",
                "community": "",
                "floor": "3",
                "source": "台灣金融資產服務公司（台灣金服）115年度金拍字第088號"
            }
        ]

    @staticmethod
    def _default_haunted_registry() -> List[Dict[str, str]]:
        """預設公開凶宅資料庫＋司法院民法第354條瑕疵擔保判決門牌清單"""
        return [
            {
                "district": "中山區",
                "road": "錦州街",
                "community": "錦州雙星",
                "floor": "9",
                "source": "台灣凶宅網暨臺北地院112年度訴字第881號判決（非自然死亡紀錄）"
            },
            {
                "district": "信義區",
                "road": "吳興街284巷",
                "community": "",
                "floor": "4",
                "source": "公開事故屋資料庫列管（重大社會新聞地理編碼命中）"
            }
        ]

    @staticmethod
    def _default_structural_hazard_registry() -> List[Dict[str, str]]:
        """預設臺北市建管處海砂屋／危樓列管與核安會輻射屋清冊"""
        return [
            {
                "district": "內湖區",
                "road": "康寧路三段189巷",
                "community": "康寧舊國宅A區",
                "hazard_type": "高氯離子混凝土建築物（海砂屋列管須拆除重建）",
                "source": "臺北市建築管理工程處海砂屋列管清冊"
            },
            {
                "district": "松山區",
                "road": "民生東路五段200巷",
                "community": "民生特定輻射列管大樓",
                "hazard_type": "核安會列管放射性污染建築物（輻射屋）",
                "source": "核能安全委員會輻射屋查詢系統"
            }
        ]

    @staticmethod
    def _normalize_text(text: str) -> str:
        if not text:
            return ""
        return text.replace("臺", "台").strip()

    def evaluate(self, listing: Raw591Listing) -> RiskCheckResult:
        """
        對單一 591 刊登物件執行四道排他性風險檢查
        """
        full_text = self._normalize_text(f"{listing.title} {listing.description}")
        comm_norm = self._normalize_text(listing.community_name or "")
        road_norm = self._normalize_text(listing.address_road)
        zoning_norm = self._normalize_text(listing.zoning)

        exclusion_reasons: List[str] = []
        warning_flags: List[str] = []

        pass_foreclosure = True
        pass_haunted = True
        pass_structural = True
        pass_property_type = True
        needs_manual_review = False

        # =====================================================================
        # 第一道：法拍屋／債權處分排除
        # =====================================================================
        for rec in self.foreclosure_registry:
            if (
                rec["district"] == listing.district
                and rec["road"] in road_norm
                and (not rec["floor"] or str(listing.floor) == rec["floor"])
            ):
                pass_foreclosure = False
                exclusion_reasons.append(f"【法拍公告命中】{rec['source']}")

        matched_fc_kw = [kw for kw in self.FORECLOSURE_KEYWORDS if kw in full_text]
        if matched_fc_kw:
            pass_foreclosure = False
            exclusion_reasons.append(f"【法拍/特殊債權文本攔截】命中關鍵字：{', '.join(matched_fc_kw)}")

        # =====================================================================
        # 第二道：凶宅（非自然死亡）雙重攔截
        # =====================================================================
        for rec in self.haunted_registry:
            comm_match = rec["community"] and rec["community"] in comm_norm
            road_match = rec["road"] and rec["road"] in road_norm
            floor_match = not rec["floor"] or str(listing.floor) == rec["floor"]
            if rec["district"] == listing.district and (comm_match or road_match) and floor_match:
                pass_haunted = False
                exclusion_reasons.append(f"【凶宅資料庫命中】{rec['source']}")

        matched_haunted_slang = [kw for kw in self.HAUNTED_SLANG_KEYWORDS if kw in full_text]
        if matched_haunted_slang:
            pass_haunted = False
            needs_manual_review = True
            exclusion_reasons.append(
                f"【疑似凶宅黑話攔截】屋況描述出現敏感詞彙（{', '.join(matched_haunted_slang)}），已自動排除並標記人工確認"
            )

        # =====================================================================
        # 第三道：台北市常見「假性低價」特殊產權排除（地上權、工業宅、地下室、頂加灌水）
        # =====================================================================
        # 3A. 地上權／使用權住宅檢查
        for sup_comm in self.TAIPEI_SUPERFICIES_COMMUNITIES:
            if sup_comm in comm_norm or sup_comm in full_text:
                pass_property_type = False
                exclusion_reasons.append(
                    f"【北市地上權/使用權社區排除】命中列管地上權社區「{sup_comm}」（無土地所有權，不應與一般所有權實價混算）"
                )
                break

        matched_sup_kw = [kw for kw in self.SUPERFICIES_KEYWORDS if kw in full_text]
        if matched_sup_kw:
            pass_property_type = False
            exclusion_reasons.append(
                f"【地上權/使用權關鍵字排除】命中關鍵字：{', '.join(matched_sup_kw)}"
            )

        # 3B. 工業宅排除
        if "工業" in zoning_norm or "工二" in zoning_norm or "工三" in zoning_norm:
            pass_property_type = False
            exclusion_reasons.append(
                f"【都市計畫工業區排除】土地使用分區為「{listing.zoning}」（工業宅不得作一般住宅對標）"
            )
        else:
            matched_ind_kw = [kw for kw in self.INDUSTRIAL_KEYWORDS if kw in full_text]
            if matched_ind_kw:
                pass_property_type = False
                exclusion_reasons.append(
                    f"【工業宅文本排除】命中關鍵字：{', '.join(matched_ind_kw)}"
                )

        # 3C. 純地下室排除 (B1~B3)
        if listing.floor < 0:
            pass_property_type = False
            exclusion_reasons.append(
                f"【純地下室物件排除】所在樓層為 B{abs(listing.floor)}，採光與產權性質特殊予以排除"
            )

        # 3D. 頂樓加蓋灌水入權狀總坪數檢查
        for pat in self.ROOFTOP_INFLATION_PATTERNS:
            if re.search(pat, full_text):
                pass_property_type = False
                exclusion_reasons.append(
                    "【頂加坪數灌水排除】偵測到將無權狀頂樓加蓋坪數混入總坪數計算，會造成假性低單價"
                )
                break

        # 檢查主建物+附屬+公設+車位與權狀總坪數是否嚴重不符（頂加灌水常見手法）
        accounted_ping = (
            listing.main_building_ping
            + listing.accessory_ping
            + listing.common_area_ping
            + listing.parking_ping
        )
        if accounted_ping > 0 and (listing.total_ping - accounted_ping) > 3.5:
            pass_property_type = False
            exclusion_reasons.append(
                f"【權狀坪數灌水異常】刊登總坪數({listing.total_ping}坪)大於權狀明細加總({accounted_ping:.1f}坪)達 {listing.total_ping - accounted_ping:.1f} 坪，疑似混入違建增建坪數"
            )

        # =====================================================================
        # 第四道：北市結構安全列管排除（海砂屋、紅黃單危樓、輻射屋）
        # =====================================================================
        for rec in self.structural_hazard_registry:
            comm_match = rec["community"] and rec["community"] in comm_norm
            road_match = rec["road"] and rec["road"] in road_norm
            if rec["district"] == listing.district and (comm_match or road_match):
                pass_structural = False
                exclusion_reasons.append(
                    f"【結構安全列管命中】{rec['hazard_type']}（來源：{rec['source']}）"
                )

        matched_struct_kw = [kw for kw in self.STRUCTURAL_KEYWORDS if kw in full_text]
        if matched_struct_kw:
            pass_structural = False
            exclusion_reasons.append(
                f"【結構安全風險關鍵字攔截】命中關鍵字：{', '.join(matched_struct_kw)}"
            )

        is_excluded = not (
            pass_foreclosure and pass_haunted and pass_structural and pass_property_type
        )

        return RiskCheckResult(
            is_excluded=is_excluded,
            needs_manual_review=needs_manual_review,
            pass_foreclosure=pass_foreclosure,
            pass_haunted=pass_haunted,
            pass_structural=pass_structural,
            pass_property_type=pass_property_type,
            exclusion_reasons=exclusion_reasons,
            warning_flags=warning_flags,
        )
