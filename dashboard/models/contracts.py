# Dashboard models - contracts
from pydantic import BaseModel, Field
from typing import Optional, List

class ScanParams(BaseModel):
    currency: str = Field(default="BTC", description="币种")
    min_dte: int = Field(default=14, ge=1, le=365, description="最小到期天数")
    max_dte: int = Field(default=25, ge=1, le=365, description="最大到期天数")
    max_delta: float = Field(default=0.4, ge=0.01, le=1.0, description="最大Delta")
    margin_ratio: float = Field(default=0.2, ge=0.05, le=1.0, description="保证金比率")
    option_type: str = Field(default="PUT", pattern="^(PUT|CALL)$")
    strike: Optional[float] = Field(default=None, description="特定行权价")
    strike_range: Optional[str] = Field(default=None, description="行权价范围")

class RollCalcParams(BaseModel):
    currency: str = Field(default="BTC", description="币种")
    option_type: str = Field(default="PUT", pattern="^(PUT|CALL)$", description="期权类型")
    old_strike: float = Field(..., description="原持仓行权价")
    old_qty: float = Field(default=1.0, gt=0, description="原持仓数量")
    close_cost_total: float = Field(..., gt=0, description="平仓总成本(USDT)")
    reserve_capital: float = Field(default=50000.0, ge=0, description="可用后备资金")
    target_max_delta: float = Field(default=0.35, ge=0.01, le=0.8, description="目标最大Delta")
    min_dte: int = Field(default=7, ge=1)
    max_dte: int = Field(default=90, ge=1)
    max_qty_multiplier: float = Field(default=3.0, ge=1.0, description="最大倍投倍数")
    margin_ratio: float = Field(default=0.2, ge=0.05, le=1.0)

class QuickScanParams(BaseModel):
    currency: str = Field(default="BTC", description="币种")
    option_type: Optional[str] = Field(default=None, pattern="^(PUT|CALL)?$")

class RecoveryCalcParams(BaseModel):
    currency: str = Field(default="BTC")
    initial_investment: float = Field(default=10000.0, gt=0)
    current_recovery_ratio: float = Field(default=0.0, ge=0, le=1.0)
    target_recovery_ratio: float = Field(default=0.3, ge=0, le=1.0)
    available_capital: float = Field(default=50000.0, gt=0)
