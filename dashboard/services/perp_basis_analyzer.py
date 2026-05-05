"""
永续合约基差分析器
- 年化基差计算
- 基差历史记录（用于百分位计算）
- Contango/Backwardation 判断
"""
import logging
from typing import Dict, Any
from datetime import datetime

from services.api_retry import request_with_retry
from services.crypto_thresholds import CryptoThresholds
from db.connection import execute_write

logger = logging.getLogger(__name__)


class PerpBasisAnalyzer:
    """永续合约基差分析器"""

    BINANCE_PERP_TICKER = "https://fapi.binance.com/fapi/v1/ticker/price"
    BINANCE_SPOT_TICKER = "https://api.binance.com/api/v3/ticker/price"
    FUNDING_RATE_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"

    @classmethod
    def fetch_current(cls, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        """获取当前永续/现货价格和基差"""
        try:
            perp_resp = request_with_retry(
                cls.BINANCE_PERP_TICKER,
                params={"symbol": symbol},
                timeout=10, verify=False, max_retries=2
            )
            perp_price = float(perp_resp.json().get("price", 0))

            spot_resp = request_with_retry(
                cls.BINANCE_SPOT_TICKER,
                params={"symbol": symbol},
                timeout=10, verify=False, max_retries=2
            )
            spot_price = float(spot_resp.json().get("price", 0))

            if perp_price <= 0 or spot_price <= 0:
                return {"error": "Invalid prices", "perp_price": perp_price, "spot_price": spot_price}

            # 年化基差 = (perp/spot - 1) * (365*24/8) * 100
            basis_annualized = round((perp_price / spot_price - 1) * (365 * 24 / 8) * 100, 2)

            # 获取资金费率
            funding_rate = 0.0
            try:
                fr_resp = request_with_retry(
                    cls.FUNDING_RATE_URL,
                    params={"symbol": symbol},
                    timeout=10, verify=False, max_retries=2
                )
                funding_rate = float(fr_resp.json().get("lastFundingRate", 0))
            except Exception as e:
                logger.warning("Funding rate fetch failed: %s", e)

            return {
                "perp_price": perp_price,
                "spot_price": spot_price,
                "basis_annualized": basis_annualized,
                "funding_rate": funding_rate,
                "funding_rate_pct": round(funding_rate * 100, 4),
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.warning("Perp basis fetch failed: %s", e)
            return {"error": str(e), "perp_price": 0, "spot_price": 0, "basis_annualized": 0, "funding_rate": 0}

    @classmethod
    def save_to_history(cls, currency: str, data: Dict[str, Any]) -> bool:
        """保存基差快照到历史表"""
        if data.get("error"):
            return False
        try:
            execute_write(
                """INSERT INTO perp_basis_history (timestamp, currency, perp_price, spot_price, basis_annualized, funding_rate)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (datetime.now().isoformat(), currency,
                 data["perp_price"], data["spot_price"],
                 data["basis_annualized"], data["funding_rate"])
            )
            return True
        except Exception as e:
            logger.warning("Save perp_basis_history failed: %s", e)
            return False

    @classmethod
    def analyze(cls, currency: str = "BTC",
                symbol: str = "BTCUSDT") -> Dict[str, Any]:
        """完整基差分析"""
        data = cls.fetch_current(symbol)
        cls.save_to_history(currency, data)

        if data.get("error"):
            return {"error": data["error"]}

        basis = data["basis_annualized"]
        hybrid = CryptoThresholds.hybrid_assess("perp_basis", basis, currency)

        regime = "contango"
        if basis < -2:
            regime = "backwardation"
        elif basis < 0:
            regime = "mild_backwardation"
        elif basis < 8:
            regime = "mild_contango"
        elif basis < 15:
            regime = "contango"
        else:
            regime = "steep_contango"

        return {
            **data,
            "currency": currency,
            "hybrid_assessment": hybrid,
            "regime": regime,
            "percentile": hybrid.get("percentile", {}).get("pct", 50),
        }
