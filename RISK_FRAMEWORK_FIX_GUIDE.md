# BTC 风险框架 & 抄底建议 - 修复指南

**创建时间：** 2026年4月12日  
**优先级：** 🔴 高  
**状态：** 待修复  

---

## 📋 **修复任务概览**

本次修复包含5个核心问题，按优先级排序：

1. **🚨 保证金计算 bug** - 最紧急 (1-2天)
2. **📊 硬编码支撑位问题** - 中等优先级 (3-5天)
3. **🎯 抄底建议逻辑缺陷** - 中等优先级 (3-5天)
4. **📈 DVOL 集成不充分** - 低优先级 (1-2周)
5. **🎨 前端展示不足** - 低优先级 (1-2周)

---

## 🚨 **任务 1：修复保证金计算 bug**

**文件位置：** `dashboard/services/risk_framework.py`

**问题描述：**
当权利金较高时，`calc_margin_put` 和 `calc_margin_call` 函数会返回负数保证金，这违反了金融逻辑。

**测试用例：**
```python
# 当前错误行为
calc_margin_put(55000, 55000, 25000)  # 返回 -13450.0 (错误!)
calc_margin_put(60000, 55000, 30000)  # 返回 -17900.0 (错误!)
```

### **修复步骤：**

1. **打开文件：** `dashboard/services/risk_framework.py`

2. **找到这两个函数：**
   - `calc_margin_put` (第54-57行)
   - `calc_margin_call` (第60-63行)

3. **替换为修正后的代码：**

```python
@staticmethod
def calc_margin_put(strike: float, spot: float, premium_usd: float, margin_ratio: float = 0.2) -> float:
    """
    修正后的 Put 保证金计算
    基于：最大潜在亏损 * 风险系数
    """
    # 计算最大潜在亏损 (假设价格跌到0)
    max_loss = strike - premium_usd
    
    # 基础保证金 = 最大亏损 * 保证金比例
    base_margin = max_loss * margin_ratio
    
    # 最小保证金要求 = 行权价 * 10%
    min_margin = strike * 0.1
    
    # 取较大值，确保保证金为正数
    return max(min_margin, base_margin)

@staticmethod
def calc_margin_call(strike: float, spot: float, premium_usd: float, margin_ratio: float = 0.2) -> float:
    """
    修正后的 Call 保证金计算
    """
    # Call 的最大亏损理论上无限，使用行权价作为基准
    base_margin = strike * margin_ratio - premium_usd
    
    # 最小保证金要求
    min_margin = strike * 0.1
    
    # 确保为正数
    return max(min_margin, base_margin)
```

4. **验证修复：**
```python
# 测试修复后的结果
print(calc_margin_put(55000, 55000, 25000))  # 应返回正数，如 5500
print(calc_margin_put(60000, 55000, 30000))  # 应返回正数，如 6000
print(calc_margin_call(55000, 55000, 25000)) # 应返回正数
```

### **验证标准：**
- [ ] 所有测试用例返回正数
- [ ] 最小保证金要求得到满足 (strike * 0.1)
- [ ] 保证金比例在合理范围内 (10-20%)

---

## 📊 **任务 2：动态支撑位系统**

**文件位置：** `dashboard/config.py` 和 `dashboard/services/risk_framework.py`

**问题描述：**
`BTC_REGULAR_FLOOR = 55000.0` 和 `BTC_EXTREME_FLOOR = 45000.0` 是静态值，无法适应市场变化。

### **修复步骤：**

1. **创建新的动态支撑位计算器：**
   新建文件：`dashboard/services/support_calculator.py`

```python
"""
动态支撑位计算器
基于技术分析和链上数据计算动态支撑位
"""
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

class DynamicSupportCalculator:
    def __init__(self, currency: str = "BTC"):
        self.currency = currency
        self.support_levels = {}
    
    def get_dynamic_floors(self) -> dict:
        """获取动态支撑位"""
        try:
            # 方法1: 200日移动平均线
            ma200 = self._get_200day_ma()
            
            # 方法2: 斐波那契回撤位
            fib_levels = self._get_fibonacci_levels()
            
            # 方法3: 链上数据 (已实现价格)
            on_chain_price = self._get_on_chain_price()

            # 综合计算支撑位
            regular_floor = self._calculate_regular_floor(ma200, fib_levels, on_chain_price)
            extreme_floor = self._calculate_extreme_floor(regular_floor, fib_levels)
            
            return {
                "regular": regular_floor,
                "extreme": extreme_floor,
                "components": {
                    "ma200": ma200,
                    "fib_levels": fib_levels,
                    "on_chain": on_chain_price
                },
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            print(f"计算动态支撑位失败: {e}")
            # 回退到硬编码值
            return {
                "regular": 55000.0,
                "extreme": 45000.0,
                "components": {},
                "timestamp": datetime.now().isoformat(),
                "fallback": True
            }
    
    def _get_200day_ma(self) -> float:
        """获取200日移动平均线"""
        # 这里需要接入实际的价格数据源
        # 示例实现，需要替换为实际API调用
        return 60000.0  # 示例值
    
    def _get_fibonacci_levels(self) -> dict:
        """计算斐波那契回撤位"""
        # 获取最近的高点和低点
        high = 73000  # 示例值，应该从API获取
        low = 38000   # 示例值，应该从API获取
        
        diff = high - low
        return {
            "0.236": high - diff * 0.236,
            "0.382": high - diff * 0.382,
            "0.500": high - diff * 0.500,
            "0.618": high - diff * 0.618,
            "0.786": high - diff * 0.786
        }
    
    def _get_on_chain_price(self) -> float:
        """获取链上已实现价格"""
        # 这里需要接入链上数据API
        # 示例实现
        return 50000.0  # 示例值
    
    def _calculate_regular_floor(self, ma200: float, fib_levels: dict, on_chain: float) -> float:
        """计算常规支撑位"""
        # 综合多个指标
        supports = [
            ma200,
            fib_levels.get("0.382", 50000),
            on_chain
        ]
        # 取平均值作为常规支撑
        return sum(supports) / len(supports)
    
    def _calculate_extreme_floor(self, regular_floor: float, fib_levels: dict) -> float:
        """计算极端支撑位"""
        # 极端支撑位通常在常规支撑位下方10-20%
        extreme1 = regular_floor * 0.85  # 15% below regular
        extreme2 = fib_levels.get("0.618", regular_floor * 0.8)
        return min(extreme1, extreme2)
```

2. **修改 RiskFramework 类：**

```python
# 修改 risk_framework.py
from services.support_calculator import DynamicSupportCalculator

class RiskFramework:
    """v7.0: 动态风险框架"""
    
    def __init__(self):
        self.support_calculator = DynamicSupportCalculator()
        self._cached_floors = None
        self._cache_timestamp = None
    
    @property
    def REGULAR_FLOOR(self):
        """动态获取常规支撑位"""
        floors = self._get_floors()
        return floors["regular"]
    
    @property
    def EXTREME_FLOOR(self):
        """动态获取极端支撑位"""
        floors = self._get_floors()
        return floors["extreme"]
    
    def _get_floors(self) -> dict:
        """获取支撑位，带缓存"""
        now = datetime.now()
        
        # 缓存1小时
        if (self._cached_floors and self._cache_timestamp and 
            (now - self._cache_timestamp).seconds < 3600):
            return self._cached_floors
        
        # 重新计算
        self._cached_floors = self.support_calculator.get_dynamic_floors()
        self._cache_timestamp = now
        
        return self._cached_floors
    
    def get_status(self, spot: float) -> str:
        """动态风险状态判断"""
        floors = self._get_floors()
        regular = floors["regular"]
        extreme = floors["extreme"]
        
        if spot > regular * 1.1:
            return "NORMAL"
        elif spot > regular:
            return "NEAR_FLOOR"
        elif spot > extreme:
            return "ADVERSE"
        else:
            return "PANIC"
```

3. **更新配置文件：**

```python
# config.py - 添加动态支撑位配置
class Config:
    # ... 现有配置 ...
    
    # v7.0: 动态支撑位配置
    SUPPORT_CALCULATION_METHOD = "dynamic"  # "static" 或 "dynamic"
    SUPPORT_CACHE_TTL_SECONDS = 3600  # 1小时缓存
    
    # 回退值 (当动态计算失败时使用)
    BTC_REGULAR_FLOOR_FALLBACK = 55000.0
    BTC_EXTREME_FLOOR_FALLBACK = 45000.0
```

### **验证标准：**
- [ ] 支撑位随市场变化而调整
- [ ] 缓存机制正常工作 (1小时)
- [ ] 回退机制在API失败时生效
- [ ] 风险状态判断正确

---

## 🎯 **任务 3：智能抄底建议系统**

**文件位置：** `dashboard/main.py` - `get_bottom_fishing_advice` 函数

**问题描述：**
建议过于笼统，缺乏具体参数和个性化。

### **修复步骤：**

1. **创建智能建议生成器：**
   新建文件：`dashboard/services/smart_advisor.py`

```python
"""
智能抄底建议生成器
提供具体、可执行的建议
"""
from typing import Dict, Any, List
from services.risk_framework import RiskFramework

class SmartBottomFishingAdvisor:
    def __init__(self):
        self.risk_framework = RiskFramework()
    
    def generate_advice(self, spot: float, currency: str = "BTC", 
                       user_profile: Dict = None) -> Dict[str, Any]:
        """
        生成具体的抄底建议
        
        Args:
            spot: 当前价格
            currency: 货币
            user_profile: 用户画像 {
                "risk_tolerance": "low/medium/high",
                "portfolio_size": 100000,
                "existing_positions": [],
                "time_horizon": "short/medium/long"
            }
        """
        if user_profile is None:
            user_profile = {
                "risk_tolerance": "medium",
                "portfolio_size": 100000,
                "existing_positions": [],
                "time_horizon": "medium"
            }
        
        status = self.risk_framework.get_status(spot)
        
        # 根据风险状态和用户画像生成建议
        if status == "NORMAL":
            return self._generate_normal_advice(spot, user_profile)
        elif status == "NEAR_FLOOR":
            return self._generate_near_floor_advice(spot, user_profile)
        elif status == "ADVERSE":
            return self._generate_adverse_advice(spot, user_profile)
        else:  # PANIC
            return self._generate_panic_advice(spot, user_profile)
    
    def _generate_normal_advice(self, spot: float, profile: Dict) -> Dict:
        """正常市场建议"""
        risk_tolerance = profile["risk_tolerance"]
        portfolio_size = profile["portfolio_size"]
        
        # 根据风险承受能力调整参数
        if risk_tolerance == "low":
            delta_range = "0.10-0.20"
            dte_range = "30-45天"
            position_size = portfolio_size * 0.05  # 5% of portfolio
        elif risk_tolerance == "medium":
            delta_range = "0.15-0.25"
            dte_range = "21-35天"
            position_size = portfolio_size * 0.10  # 10% of portfolio
        else:  # high
            delta_range = "0.20-0.30"
            dte_range = "14-28天"
            position_size = portfolio_size * 0.15  # 15% of portfolio
        
        return {
            "status": "NORMAL",
            "advice": [
                f"当前价格 ${spot:,.0f} 处于正常区间",
                "市场健康，适合稳定获取权利金",
                "建议保持低杠杆，避免过度暴露"
            ],
            "recommended_actions": [
                {
                    "action": "卖出 OTM Put 期权",
                    "parameters": {
                        "delta_range": delta_range,
                        "dte_range": dte_range,
                        "strike_range": f"${int(spot * 0.9):,}-${int(spot * 0.95):,}",
                        "position_size": f"${position_size:,.0f}",
                        "max_contracts": int(position_size / (spot * 0.1))  # 基于保证金估算
                    },
                    "expected_apr": "150-250%",
                    "max_loss": f"${position_size * 0.5:,.0f}",
                    "reasoning": "低Delta Put提供高概率盈利，同时获取时间价值"
                }
            ],
            "risk_management": {
                "stop_loss": f"价格跌破 ${int(spot * 0.85):,} 时止损",
                "roll_strategy": "如果价格接近行权价，提前滚仓",
                "max_position": f"最多持有 {int(position_size / (spot * 0.1))} 张合约"
            }
        }
    
    def _generate_near_floor_advice(self, spot: float, profile: Dict) -> Dict:
        """接近支撑位建议"""
        # 类似实现，但更保守
        pass
    
    def _generate_adverse_advice(self, spot: float, profile: Dict) -> Dict:
        """逆境市场建议"""
        # 激进策略，高杠杆快平仓
        pass
    
    def _generate_panic_advice(self, spot: float, profile: Dict) -> Dict:
        """恐慌市场建议"""
        # 止损建议
        pass
```

2. **修改 API 端点：**

```python
# main.py - 修改 get_bottom_fishing_advice
from services.smart_advisor import SmartBottomFishingAdvisor

@app.get("/api/bottom-fishing/advice")
async def get_bottom_fishing_advice(
    currency: str = Query(default="BTC"),
    risk_tolerance: str = Query(default="medium"),
    portfolio_size: float = Query(default=100000),
    time_horizon: str = Query(default="medium")
):
    """v7.0: 智能抄底建议 API"""
    spot = get_spot_price(currency)
    
    # 构建用户画像
    user_profile = {
        "risk_tolerance": risk_tolerance,
        "portfolio_size": portfolio_size,
        "time_horizon": time_horizon,
        "existing_positions": []  # 可以从数据库获取
    }
    
    # 生成智能建议
    advisor = SmartBottomFishingAdvisor()
    advice = advisor.generate_advice(spot, currency, user_profile)
    
    # 添加市场数据
    advice.update({
        "currency": currency,
        "spot": spot,
        "timestamp": datetime.now().isoformat()
    })
    
    return advice
```

### **验证标准：**
- [ ] 建议包含具体参数 (行权价、到期日、仓位大小)
- [ ] 不同风险承受能力得到不同建议
- [ ] 建议具有可执行性
- [ ] 包含风险管理计划

---

## 📈 **任务 4：统一风险评估框架**

**文件位置：** `dashboard/services/dvol_analyzer.py`

**问题描述：**
波动率分析与风险框架独立工作，没有协同。

### **修复步骤：**

1. **创建统一风险评估器：**
   新建文件：`dashboard/services/unified_risk_assessor.py`

```python
"""
统一风险评估器
整合价格、波动率、链上数据等多维度风险
"""
from typing import Dict, Any
from services.risk_framework import RiskFramework
from services.dvol_analyzer import get_dvol_from_deribit

class UnifiedRiskAssessor:
    def __init__(self):
        self.risk_framework = RiskFramework()
    
    def assess_comprehensive_risk(self, spot: float, currency: str = "BTC") -> Dict[str, Any]:
        """综合风险评估"""
        
        # 1. 价格风险
        price_risk = self._assess_price_risk(spot)
        
        # 2. 波动率风险
        volatility_risk = self._assess_volatility_risk(currency)
        
        # 3. 市场情绪风险
        sentiment_risk = self._assess_sentiment_risk(currency)
        
        # 4. 流动性风险
        liquidity_risk = self._assess_liquidity_risk(currency)
        
        # 综合评分 (0-100, 越高风险越大)
        composite_score = (
            price_risk["score"] * 0.4 +
            volatility_risk["score"] * 0.3 +
            sentiment_risk["score"] * 0.2 +
            liquidity_risk["score"] * 0.1
        )
        
        # 风险等级
        if composite_score < 30:
            risk_level = "LOW"
        elif composite_score < 60:
            risk_level = "MEDIUM"
        elif composite_score < 80:
            risk_level = "HIGH"
        else:
            risk_level = "EXTREME"
        
        return {
            "composite_score": round(composite_score, 1),
            "risk_level": risk_level,
            "components": {
                "price_risk": price_risk,
                "volatility_risk": volatility_risk,
                "sentiment_risk": sentiment_risk,
                "liquidity_risk": liquidity_risk
            },
            "recommendations": self._generate_risk_recommendations(composite_score),
            "timestamp": datetime.now().isoformat()
        }
    
    def _assess_price_risk(self, spot: float) -> Dict[str, Any]:
        """价格风险评估"""
        status = self.risk_framework.get_status(spot)
        
        risk_scores = {
            "NORMAL": 20,
            "NEAR_FLOOR": 50,
            "ADVERSE": 75,
            "PANIC": 95
        }
        
        return {
            "score": risk_scores.get(status, 50),
            "status": status,
            "factors": [
                f"当前价格: ${spot:,.0f}",
                f"风险状态: {status}"
            ]
        }
    
    def _assess_volatility_risk(self, currency: str) -> Dict[str, Any]:
        """波动率风险评估"""
        try:
            dvol_data = get_dvol_from_deribit(currency)
            dvol = dvol_data.get("current", 50)
            z_score = dvol_data.get("z_score", 0)
            
            # 计算波动率风险分数
            if dvol > 80:
                score = 90
            elif dvol > 60:
                score = 70
            elif dvol > 40:
                score = 40
            elif dvol > 20:
                score = 20
            else:
                score = 10
            
            # Z-score 调整
            if abs(z_score) > 2:
                score = min(100, score + 20)
            
            return {
                "score": score,
                "dvol": dvol,
                "z_score": z_score,
                "signal": dvol_data.get("signal", "正常区间"),
                "factors": [
                    f"DVOL: {dvol:.1f}%",
                    f"Z-Score: {z_score:.2f}",
                    f"信号: {dvol_data.get('signal', '')}"
                ]
            }
        except Exception as e:
            return {
                "score": 50,
                "error": str(e),
                "factors": ["无法获取波动率数据"]
            }
    
    def _assess_sentiment_risk(self, currency: str) -> Dict[str, Any]:
        """市场情绪风险评估"""
        # 这里可以接入恐惧贪婪指数、社交媒体情绪等
        return {
            "score": 40,  # 默认值
            "factors": ["情绪数据待接入"]
        }
    
    def _assess_liquidity_risk(self, currency: str) -> Dict[str, Any]:
        """流动性风险评估"""
        # 这里可以接入买卖价差、深度等数据
        return {
            "score": 30,  # 默认值
            "factors": ["流动性数据待接入"]
        }
    
    def _generate_risk_recommendations(self, composite_score: float) -> list:
        """根据综合风险分数生成建议"""
        if composite_score < 30:
            return [
                "风险较低，可适当增加仓位",
                "适合卖出 OTM Put 获取权利金",
                "可考虑稍高的 Delta 值"
            ]
        elif composite_score < 60:
            return [
                "风险中等，保持标准仓位",
                "建议卖出 ATM 附近期权",
                "注意设置止损"
            ]
        elif composite_score < 80:
            return [
                "风险较高，减少仓位",
                "建议卖出 ITM 期权或降低 Delta",
                "准备应对策略，考虑对冲"
            ]
        else:
            return [
                "风险极高，建议清仓或对冲",
                "避免卖出裸期权",
                "保持现金，等待市场稳定"
            ]
```

### **验证标准：**
- [ ] 综合评分合理 (0-100)
- [ ] 各风险组件分数独立计算
- [ ] 风险等级与分数匹配
- [ ] 建议与风险等级对应

---

## 🎨 **任务 5：前端增强展示**

**文件位置：** `dashboard/static/index.html` 和 `dashboard/static/app.js`

**问题描述：**
缺乏可视化、历史跟踪和风险解释。

### **修复步骤：**

1. **增强风险展示组件：**

```html
<!-- 在 index.html 中添加 -->
<section id="riskDashboard" class="card-glass rounded-xl p-5 mb-6">
    <div class="flex items-center justify-between mb-4">
        <h3 class="font-semibold text-lg">综合风险仪表板</h3>
        <div id="riskScoreBadge" class="px-3 py-1 rounded-full text-sm font-bold">
            <!-- 动态填充 -->
        </div>
    </div>
    
    <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-4">
        <!-- 价格风险 -->
        <div class="p-3 bg-gray-800/40 rounded-lg">
            <div class="text-xs text-gray-400 mb-1">价格风险</div>
            <div id="priceRiskScore" class="text-xl font-bold">--</div>
            <div id="priceRiskLevel" class="text-xs">--</div>
        </div>
        
        <!-- 波动率风险 -->
        <div class="p-3 bg-gray-800/40 rounded-lg">
            <div class="text-xs text-gray-400 mb-1">波动率风险</div>
            <div id="volRiskScore" class="text-xl font-bold">--</div>
            <div id="volRiskLevel" class="text-xs">--</div>
        </div>
        
        <!-- 情绪风险 -->
        <div class="p-3 bg-gray-800/40 rounded-lg">
            <div class="text-xs text-gray-400 mb-1">情绪风险</div>
            <div id="sentimentRiskScore" class="text-xl font-bold">--</div>
            <div id="sentimentRiskLevel" class="text-xs">--</div>
        </div>
        
        <!-- 流动性风险 -->
        <div class="p-3 bg-gray-800/40 rounded-lg">
            <div class="text-xs text-gray-400 mb-1">流动性风险</div>
            <div id="liquidityRiskScore" class="text-xl font-bold">--</div>
            <div id="liquidityRiskLevel" class="text-xs">--</div>
        </div>
    </div>
    
    <!-- 风险趋势图 -->
    <div class="mt-4">
        <h4 class="text-sm font-semibold text-gray-300 mb-2">风险趋势 (24小时)</h4>
        <div id="riskTrendChart" class="h-32 bg-gray-800/20 rounded-lg">
            <!-- 图表将在这里渲染 -->
        </div>
    </div>
</section>
```

2. **添加 JavaScript 逻辑：**

```javascript
// app.js - 添加风险仪表板功能
async function loadRiskDashboard(currency = 'BTC') {
    try {
        const res = await safeFetch(`${API_BASE}/api/risk/assess?currency=${currency}`);
        const data = await res.json();
        
        updateRiskDashboardUI(data);
    } catch (e) {
        console.error('Failed to load risk dashboard:', e);
    }
}

function updateRiskDashboardUI(data) {
    // 更新综合分数
    const scoreBadge = document.getElementById('riskScoreBadge');
    scoreBadge.textContent = `综合风险: ${data.composite_score}`;
    
    // 根据风险等级设置颜色
    scoreBadge.className = 'px-3 py-1 rounded-full text-sm font-bold ';
    if (data.risk_level === 'LOW') {
        scoreBadge.classList.add('bg-green-500/20', 'text-green-400');
    } else if (data.risk_level === 'MEDIUM') {
        scoreBadge.classList.add('bg-yellow-500/20', 'text-yellow-400');
    } else if (data.risk_level === 'HIGH') {
        scoreBadge.classList.add('bg-orange-500/20', 'text-orange-400');
    } else {
        scoreBadge.classList.add('bg-red-500/20', 'text-red-400', 'animate-pulse');
    }
    
    // 更新各风险组件
    const components = data.components;
    
    // 价格风险
    document.getElementById('priceRiskScore').textContent = components.price_risk.score;
    document.getElementById('priceRiskLevel').textContent = components.price_risk.status;
    
    // 波动率风险
    document.getElementById('volRiskScore').textContent = components.volatility_risk.score;
    document.getElementById('volRiskLevel').textContent = components.volatility_risk.signal;
    
    // 类似更新其他组件...
}

// 添加风险趋势图
function renderRiskTrendChart(trendData) {
    // 使用 Chart.js 或其他图表库
    const ctx = document.getElementById('riskTrendChart').getContext('2d');
    
    new Chart(ctx, {
        type: 'line',
        data: {
            labels: trendData.map(d => d.timestamp),
            datasets: [{
                label: '风险分数',
                data: trendData.map(d => d.score),
                borderColor: 'rgb(255, 99, 132)',
                tension: 0.1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    beginAtZero: true,
                    max: 100
                }
            }
        }
    });
}
```

### **验证标准：**
- [ ] 所有风险指标正确显示
- [ ] 图表正确渲染
- [ ] 响应式设计在不同设备上工作
- [ ] 颜色和动画正确应用

---

## 📝 **修复检查清单**

**修复时请按以下顺序验证：**

### **第一步：修复保证金计算 bug**
- [ ] 替换 `calc_margin_put` 函数
- [ ] 替换 `calc_margin_call` 函数
- [ ] 运行测试用例确保返回正数
- [ ] 验证最小保证金要求 (strike * 0.1)

### **第二步：添加动态支撑位**
- [ ] 创建 `support_calculator.py` 文件
- [ ] 修改 `RiskFramework` 类使用动态支撑位
- [ ] 添加缓存机制 (1小时)
- [ ] 测试动态计算和回退机制

### **第三步：增强抄底建议**
- [ ] 创建 `smart_advisor.py` 文件
- [ ] 修改 API 端点接受用户参数
- [ ] 测试不同风险承受能力的建议
- [ ] 验证建议的具体性和可执行性

### **第四步：集成多维度风险评估**
- [ ] 创建 `unified_risk_assessor.py` 文件
- [ ] 整合价格、波动率、情绪、流动性风险
- [ ] 测试综合评分和风险等级
- [ ] 验证建议的合理性

### **第五步：增强前端展示**
- [ ] 添加风险仪表板 HTML 组件
- [ ] 实现 JavaScript 数据加载和渲染
- [ ] 添加风险趋势图
- [ ] 测试响应式设计和用户体验

---

## 🎯 **验证标准**

**每个修复完成后，请验证：**

1. **保证金计算：**
   - 所有测试用例返回正数
   - 最小保证金要求得到满足
   - 保证金比例在合理范围内 (10-20%)

2. **动态支撑位：**
   - 支撑位随市场变化而调整
   - 缓存机制正常工作
   - 回退机制在API失败时生效

3. **抄底建议：**
   - 建议包含具体参数 (行权价、到期日、仓位大小)
   - 不同风险承受能力得到不同建议
   - 建议具有可执行性

4. **风险评估：**
   - 综合评分合理 (0-100)
   - 各风险组件分数独立计算
   - 风险等级与分数匹配

5. **前端展示：**
   - 所有风险指标正确显示
   - 图表正确渲染
   - 响应式设计在不同设备上工作

---

## 📞 **技术支持**

如果修复过程中遇到问题，请联系：
- **问题反馈：** 创建 GitHub Issue
- **紧急问题：** 联系项目负责人

**注意：** 每个任务完成后，请提交 Pull Request 并等待代码审查。

---

**最后更新：** 2026年4月12日  
**文档版本：** v1.0  
**作者：** Hermes Agent