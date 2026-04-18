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
    option_type: str = Field(default="ALL", pattern="^(PUT|CALL|ALL|BOTH)$")
    strike: Optional[float] = Field(default=None, gt=0)
    strike_range: Optional[str] = Field(default=None)

    def model_post_init(self, __context):
        if self.min_dte > self.max_dte:
            raise ValueError(f"min_dte ({self.min_dte}) must be <= max_dte ({self.max_dte})")

class StrategyCalcParams(BaseModel):
    """统一策略计算器参数 - 支持滚仓模式和新建模式"""
    currency: str = Field(default="BTC", description="币种")
    mode: str = Field(default="roll", pattern="^(roll|new)$", description="模式: roll=滚仓, new=新建")
    option_type: str = Field(default="PUT", pattern="^(PUT|CALL)$", description="期权类型")
    reserve_capital: float = Field(default=50000.0, ge=0, description="可用后备资金(USDT)")
    target_max_delta: float = Field(default=0.35, ge=0.01, le=0.8, description="目标最大Delta")
    min_dte: int = Field(default=7, ge=1)
    max_dte: int = Field(default=90, ge=1)
    margin_ratio: float = Field(default=0.2, ge=0.05, le=1.0)

    old_strike: Optional[float] = Field(default=None, description="原持仓行权价 (滚仓模式)")
    old_qty: float = Field(default=1.0, gt=0, description="原持仓数量 (滚仓模式)")
    close_cost_total: float = Field(default=0, ge=0, description="平仓总成本USDT (滚仓模式)")
    max_qty_multiplier: float = Field(default=3.0, ge=1.0, description="最大倍投倍数 (滚仓模式)")
    target_apr: float = Field(default=200, ge=50, le=500, description="目标年化收益率% (新建模式)")

    def model_post_init(self, __context):
        if self.mode == "roll" and self.old_strike is None:
            raise ValueError("滚仓模式需要提供 old_strike")

class SandboxParams(BaseModel):
    """马丁格尔沙盘推演参数 v2.0"""
    # 当前持仓信息
    current_strike: float = Field(default=65000, gt=0)
    option_type: str = Field(default="P", pattern="^[PC]$")
    current_qty: float = Field(default=1.0, gt=0)
    avg_premium: float = Field(default=2000, gt=0)
    avg_dte: int = Field(default=30, ge=1)
    
    # 崩盘情景
    crash_price: float = Field(default=45000, gt=1000)
    reserve_capital: float = Field(default=50000, ge=0)
    margin_ratio: float = Field(default=0.20, ge=0.05, le=1.0)
    
    # 恢复策略参数
    min_dte: int = Field(default=14, ge=1)
    max_dte: int = Field(default=180, ge=1)
    min_apr: float = Field(default=5.0, ge=1)
    max_contracts: int = Field(default=20, ge=1)
    currency: str = Field(default="BTC")
