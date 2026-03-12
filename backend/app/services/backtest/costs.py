"""交易成本模型"""

from dataclasses import dataclass


@dataclass
class CostModel:
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.001
    slippage_rate: float = 0.0005

    def buy_cost(self, amount: float) -> float:
        commission = max(amount * self.commission_rate, self.min_commission)
        return commission

    def sell_cost(self, amount: float) -> float:
        commission = max(amount * self.commission_rate, self.min_commission)
        stamp_tax = amount * self.stamp_tax_rate
        return commission + stamp_tax

    def apply_buy_slippage(self, price: float) -> float:
        return price * (1 + self.slippage_rate)

    def apply_sell_slippage(self, price: float) -> float:
        return price * (1 - self.slippage_rate)
