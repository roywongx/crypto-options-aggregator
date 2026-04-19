import sys
import math
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timedelta

class GridStrategyEngine:
    def __init__(self):
        # 网格策略配置
        self.grid_configs = {
            'conservative': {
                'profit_taking_threshold': 0.02,
                'stop_loss_threshold': 0.05,
                'grid_spacing': 0.01,
                'max_pos_size': 0.1
            },
            'moderate': {
                'profit_taking_threshold': 0.03,
                'stop_loss_threshold': 0.08,
                'grid_spacing': 0.015,
                'max_pos_size': 0.15
            },
            'aggressive': {
                'profit_taking_threshold': 0.05,
                'stop_loss_threshold': 0.12,
                'grid_spacing': 0.02,
                'max_pos_size': 0.2
            }
        }
    def calculate_grid_levels(self, current_price: float, strategy: str, num_levels: int = 10) -> List[float]:
        """计算网格价格水平"""
        config = self.grid_configs.get(strategy, self.grid_configs['moderate'])
        spacing = config['grid_spacing']
        
        levels = []
        for i in range(-num_levels, num_levels + 1):
            level_price = current_price * (1 + spacing * i)
            levels.append(round(level_price, 2))
        
        return levels
    def generate_recommendation(self, contract: Dict, spot_price: float, volatility: float) -> Dict:
        """生成网格策略推荐"""
        # 计算Delta和Gamma
        delta = float(contract.get('delta', 0))
        gamma = float(contract.get('gamma', 0))
        
        # 基于Delta和波动率选择策略
        if abs(delta) < 0.3 and volatility < 0.5:
            strategy = 'conservative'
        elif abs(delta) < 0.6 and volatility < 0.8:
            strategy = 'moderate'
        else:
            strategy = 'aggressive'
        
        # 计算网格水平
        strike = float(contract.get('strike', spot_price))
        grid_levels = self.calculate_grid_levels(strike, strategy)
        
        # 计算预期收益
        expected_return = self._calculate_expected_return(contract, spot_price, volatility)
        
        return {
            'strategy': strategy,
            'grid_levels': grid_levels,
            'expected_return': expected_return,
            'max_pos_size': self.grid_configs[strategy]['max_pos_size'],
            'profit_taking': self.grid_configs[strategy]['profit_taking_threshold'],
            'stop_loss': self.grid_configs[strategy]['stop_loss_threshold']
        }
    def _calculate_expected_return(self, contract: Dict, spot_price: float, volatility: float) -> float:
        """计算预期收益"""
        dte = float(contract.get('dte', 30)) / 365.0
        strike = float(contract.get('strike', spot_price))
        premium = float(contract.get('premium_usd', 0))
        
        # 简单的预期收益计算
        if dte > 0:
            return premium / dte
        return 0
    def get_volatility_signal(self, volatility_history: List[float]) -> str:
        """获取波动率方向信号"""
        if len(volatility_history) < 3:
            return 'neutral'
        
        # 计算波动率趋势
        recent_change = volatility_history[-1] - volatility_history[-2]
        previous_change = volatility_history[-2] - volatility_history[-3]
        
        if recent_change > 0 and previous_change > 0:
            return 'increasing'
        elif recent_change < 0 and previous_change < 0:
            return 'decreasing'
        else:
            return 'neutral'
    def simulate_scenario(self, spot_price: float, volatility: float, days: int = 30) -> Dict:
        """模拟情景"""
        # 简单的蒙特卡洛模拟
        scenarios = []
        for _ in range(1000):
            price = spot_price
            for _ in range(days):
                # 随机价格变动
                change = price * volatility * (math.random() - 0.5) * math.sqrt(1/365)
                price = max(0.01, price + change)
            scenarios.append(price)
        
        # 计算统计数据
        scenarios.sort()
        return {
            'mean_price': sum(scenarios) / len(scenarios),
            'median_price': scenarios[len(scenarios)//2],
            'min_price': min(scenarios),
            'max_price': max(scenarios),
            'percentile_10': scenarios[int(len(scenarios)*0.1)],
            'percentile_90': scenarios[int(len(scenarios)*0.9)]
        }
    def calculate_heatmap_data(self, contracts: List[Dict], spot_price: float) -> List[Dict]:
        """计算热力图数据"""
        heatmap = []
        
        for contract in contracts:
            strike = float(contract.get('strike', 0))
            dte = float(contract.get('dte', 0))
            delta = float(contract.get('delta', 0))
            iv = float(contract.get('iv', 0))
            
            # 计算距离当前价格的百分比
            distance = (strike - spot_price) / spot_price
            
            # 计算分数
            score = self._calculate_contract_score(contract, spot_price)
            
            heatmap.append({
                'strike': strike,
                'dte': dte,
                'delta': delta,
                'iv': iv,
                'distance': distance,
                'score': score,
                'symbol': contract.get('symbol', '')
            })
        
        return heatmap
    def _calculate_contract_score(self, contract: Dict, spot_price: float) -> float:
        """计算合约评分"""
        premium = float(contract.get('premium_usd', 0))
        dte = float(contract.get('dte', 1))
        delta = float(contract.get('delta', 0))
        iv = float(contract.get('iv', 0))
        
        # 基础分数：年化收益率
        annualized_return = (premium / dte) * 365 if dte > 0 else 0
        
        # 风险调整
        risk_factor = 1.0 - abs(delta)
        iv_factor = 1.0 / (iv + 0.1) if iv > 0 else 1.0
        
        return annualized_return * risk_factor * iv_factor

# 导出单例
grid_engine = GridStrategyEngine()
