from dataclasses import dataclass
from typing import List, Optional
from enum import Enum

class GridDirection(Enum):
    PUT = "put"
    CALL = "call"

class RecommendationLevel(Enum):
    BEST = 5
    GOOD = 4
    OK = 3
    CAUTION = 2
    SKIP = 1

@dataclass
class GridLevel:
    direction: GridDirection
    strike: float
    expiry: str
    dte: int
    premium_usd: float
    apr: float
    distance_pct: float
    iv: float
    delta: float
    oi: int
    volume: int
    liquidity_score: float
    recommendation: RecommendationLevel
    reason: str

@dataclass
class GridRecommendation:
    currency: str
    spot_price: float
    timestamp: str
    put_levels: List[GridLevel]
    call_levels: List[GridLevel]
    dvol_signal: str
    recommended_ratio: str
    total_potential_premium: float

@dataclass
class GridScenario:
    target_price: float
    put_results: list
    call_results: list
    spot_pnl: float
    total_pnl: float
    vs_hold_pnl: float

@dataclass
class VolDirectionSignal:
    dvol_current: float
    dvol_30d_avg: float
    dvol_percentile: float
    skew: dict
    signal: str
    reason: str
    suggested_ratio: str
