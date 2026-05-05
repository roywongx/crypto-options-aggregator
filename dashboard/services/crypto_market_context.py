"""
加密市场上下文构建器
在每个 LLM 分析请求之前构建结构化市场快照，注入到 system prompt
"""
import logging
from typing import Dict, Any, Optional
from datetime import datetime

from config import config

logger = logging.getLogger(__name__)

_context_cache: Dict[str, Any] = {}
_cache_time: Optional[datetime] = None


class CryptoMarketContext:
    """加密市场结构上下文构建器"""

    STRUCTURAL_KNOWLEDGE = [
        "永续合约（Perpetual Swap）占币圈衍生品交易量的90%以上，期货/现货成交量比天然偏高（5-20x），这不等于「过度杠杆」。",
        "资金费率（Funding Rate）的正常波动范围是-0.03%到+0.10%（8小时费率），持续正值是牛市常态，不代表马上会均值回归。",
        "稳定币（USDT/USDC）的交易所余额变化是重要的「买盘火力」指标——流入=潜在购买需求，流出=资金撤退。",
        "清算瀑布（Liquidation Cascade）是币圈特有的风险事件——当价格触发大量强平订单时，会形成连锁反应，放大价格波动。",
        "BTC市值占比（BTC Dominance）是判断「山寨季」的核心指标——BTC.D下降+BTC价格稳定=资金轮动到山寨币的信号。",
        "永续基差（Perp Basis）比成交量比值更能反映杠杆程度——基差>15%年化才是真正的杠杆过热信号。",
        "OI（未平仓合约量）与价格的背离关系是市场方向的高质量先行指标——OI↑价格↓=空头加仓看空，OI↓价格↑=空头平仓（逼空风险）。",
    ]

    @classmethod
    def build(cls, data: Dict[str, Any], currency: str = "BTC") -> Dict[str, Any]:
        """构建完整市场上下文快照"""
        global _context_cache, _cache_time

        now = datetime.now()
        if _context_cache and _cache_time:
            age_seconds = (now - _cache_time).total_seconds()
            if age_seconds < config.MARKET_CONTEXT_CACHE_TTL:
                return _context_cache

        spot = data.get("spot", 0)
        dvol = data.get("dvol", 0)
        dvol_z = data.get("dvol_z", 0)

        mvrv_z = data.get("mvrv_z", 0)
        nupl = data.get("nupl", 0)

        cycle_phase = cls._determine_cycle_phase(mvrv_z, nupl, spot)

        perp_basis = data.get("perp_basis", {})
        stablecoin = data.get("stablecoin_reserve", {})
        liq_heat = data.get("liquidation_heat", {})

        context = {
            "cycle": {
                "phase": cycle_phase,
                "btc_dominance": data.get("btc_dominance", 0),
                "dvol_regime": cls._dvol_regime_label(dvol, dvol_z),
            },
            "structure": {
                "perp_dominance": True,
                "contango_depth": perp_basis.get("basis_annualized", 0) if isinstance(perp_basis, dict) else 0,
                "stablecoin_flow": stablecoin.get("label", "未知") if isinstance(stablecoin, dict) else "未知",
                "liquidation_heat": liq_heat.get("heat_level", "L0") if isinstance(liq_heat, dict) else "L0",
            },
            "narrative": {
                "dominant_sectors": cls._infer_sectors(data),
                "macro_overlay": cls._infer_macro(data),
            },
            "warnings": cls._build_warnings(data),
            "structural_knowledge": cls.STRUCTURAL_KNOWLEDGE,
            "updated_at": now.isoformat(),
        }

        _context_cache = context
        _cache_time = now
        return context

    @classmethod
    def to_prompt_text(cls, context: Dict[str, Any]) -> str:
        """将上下文对象序列化为自然语言"""
        cycle = context.get("cycle", {})
        structure = context.get("structure", {})

        parts = [
            "## 当前加密市场结构背景",
            f"- 市场周期阶段: {cycle.get('phase', '未知')}",
            f"- BTC市占率: {cycle.get('btc_dominance', 'N/A')}%",
            f"- 波动率区间: {cycle.get('dvol_regime', '未知')}",
            f"- 永续基差: {structure.get('contango_depth', 'N/A')}% 年化",
            f"- 稳定币流向: {structure.get('stablecoin_flow', '未知')}",
            f"- 清算压力: {structure.get('liquidation_heat', '未知')}",
            "",
            "## 加密市场结构性常识（请在分析中应用）",
        ]

        for i, k in enumerate(context.get("structural_knowledge", []), 1):
            parts.append(f"{i}. {k}")

        warnings = context.get("warnings", [])
        if warnings:
            parts.append("")
            parts.append("## 当前特别关注")
            for w in warnings:
                parts.append(f"- ⚠️ {w}")

        return "\n".join(parts)

    @classmethod
    def _determine_cycle_phase(cls, mvrv_z: float, nupl: float, spot: float) -> str:
        """综合判断市场周期阶段（即使链上数据缺失也有合理默认值）"""
        if mvrv_z > 3.5 or nupl > 0.7:
            return "牛市顶部（高估值风险）"
        elif mvrv_z > 2.0 or nupl > 0.5:
            return "牛市中期"
        elif mvrv_z > 0.0 or nupl > 0.0:
            return "牛市早期/积累"
        elif mvrv_z > -1.0 or nupl > -0.5:
            return "熊市早期"
        elif mvrv_z < -2.0 or nupl < -0.5:
            return "熊市底部（历史低估）"
        return "横盘整理（方向不明）"

    @classmethod
    def _dvol_regime_label(cls, dvol: float, dvol_z: float) -> str:
        if dvol <= 0:
            return "未知"
        if dvol > config.DVOL_PANIC_THRESHOLD:
            return "恐慌波动"
        elif dvol > config.DVOL_HIGH_THRESHOLD:
            return "高波动"
        elif dvol > config.DVOL_LOW_THRESHOLD:
            return "中波动"
        return "低波动"

    @classmethod
    def _infer_sectors(cls, data: Dict[str, Any]) -> list:
        sectors = []
        btc_dom = data.get("btc_dominance", 0)
        if btc_dom > 55:
            sectors.append("BTC主导")
        else:
            sectors.append("山寨币轮动")
        return sectors

    @classmethod
    def _infer_macro(cls, data: Dict[str, Any]) -> str:
        fear_greed = data.get("fear_greed", 50)
        if fear_greed <= 25:
            return "恐慌情绪（可能过度）"
        elif fear_greed >= 75:
            return "贪婪情绪（警惕回调）"
        return "中性情绪"

    @classmethod
    def _build_warnings(cls, data: Dict[str, Any]) -> list:
        warnings = []
        perp_basis = data.get("perp_basis", {})
        if isinstance(perp_basis, dict):
            basis = perp_basis.get("basis_annualized", 0)
            if basis > config.PERP_BASIS_THRESHOLD_HIGH:
                pct = perp_basis.get("hybrid_assessment", {}).get("percentile", {}).get("pct", 50)
                warnings.append(f"永续基差 {basis}% 年化（{pct}%百分位），杠杆水平偏高")

        oi_div = data.get("oi_price_divergence", {})
        if isinstance(oi_div, dict) and oi_div.get("divergence") in ("bearish", "long_capitulation"):
            warnings.append(f"OI-价格背离: {oi_div.get('divergence_label', '')}")

        liq_heat = data.get("liquidation_heat", {})
        if isinstance(liq_heat, dict) and liq_heat.get("heat_level") in ("L2", "L3"):
            warnings.append(f"清算压力 L{liq_heat.get('heat_level')}，注意连锁清算风险")

        return warnings
