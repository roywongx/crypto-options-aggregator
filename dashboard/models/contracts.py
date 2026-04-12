# Models - Pydantic models for API
from pydantic import BaseModel, Field
from typing import Optional

class ScanParams(BaseModel):
    currency: str = Field(default="BTC", description="币种")
    min_dte: int = Field(default=14, ge=1, le=365, description="最小到期天数")
    max_dte: int = Field(default=25, ge=1, le=365, description="最大到期天数")
    max_delta: float = Field(default=0.4, ge=0.01, le=1.0, description="最大Delta")
    margin_ratio: float = Field(default=0.2, ge=0.05, le=1.0, description="保证金比率")
    option_type: str = Field(default="PUT", pattern="^(PUT|CALL)$")
    strike: Optional[float] = Field(default=None, description="特定行权价")
    strike_range: Optional[str] = Field(default=None, description="行权价范围，如 60000-65000")

class RollCalcParams(BaseModel):
    currency: str = Field(default="BTC", description="币种")
    option_type: str = Field(default="PUT", pattern="^(PUT|CALL)$", description="期权类型")
    old_strike: float = Field(..., description="原持仓行权价")
    old_qty: float = Field(default=1.0, gt=0, description="原持仓数量")
    close_cost_total: float = Field(..., gt=0, description="平仓总成本(USDT)")
    reserve_capital: float = Field(default=50000.0, ge=0, description="可用后备资金(USDT)")
    target_max_delta: float = Field(default=0.35, ge=0.01, le=0.8, description="目标最大Delta")
    min_dte: int = Field(default=7, ge=1)
    max_dte: int = Field(default=90, ge=1)
    max_qty_multiplier: float = Field(default=3.0, ge=1.0, description="最大倍投倍数")
    margin_ratio: float = Field(default=0.2, ge=0.05, le=1.0)

class QuickScanParams(BaseModel):
    currency: str = Field(default="BTC", pattern="^(BTC|ETH|SOL|XRP)$")
    min_dte: int = Field(default=14, ge=1, le=365)
    max_dte: int = Field(default=35, ge=1, le=365)
    max_delta: float = Field(default=0.4, ge=0.01, le=1.0)
    margin_ratio: float = Field(default=0.2, ge=0.05, le=1.0)
    option_type: str = Field(default="PUT", pattern="^(PUT|CALL)$")
    strike: Optional[float] = Field(default=None, gt=0)
    strike_range: Optional[str] = Field(default=None)

    def model_post_init(self, __context):
        if self.min_dte > self.max_dte:
            raise ValueError(f"min_dte ({self.min_dte}) must be <= max_dte ({self.max_dte})")

class RecoveryCalcParams(BaseModel):
    currency: str = Field(default="BTC", description="币种")
    current_loss: float = Field(..., gt=0, description="当前浮亏金额(USDT)")
    target_apr: float = Field(default=200, ge=50, le=500, description="目标年化收益率(%)")
    max_contracts: int = Field(default=10, ge=1, le=50, description="最大合约数量")
    max_delta: float = Field(default=0.45, ge=0.1, le=0.8, description="最大Delta容忍")

class SandboxParams(BaseModel):
    currency: str = Field(default="BTC", pattern="^(BTC|ETH|SOL)$")
    spot_price: float = Field(..., gt=0)
    volatility: float = Field(..., gt=0, description="波动率 (%)")
    risk_free_rate: float = Field(default=0.02, ge=0, le=0.2, description="无风险利率")
    time_to_expiry: float = Field(..., gt=0, description="到期时间 (年)")
    scenarios: list = Field(default_factory=list, description="情景列表")
