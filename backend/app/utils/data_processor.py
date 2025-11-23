"""
数据处理工具
负责数据的清洗、转换、聚合等操作
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional
from sklearn.mixture import GaussianMixture
from scipy import stats
from app.core.logger import get_module_logger

# 获取logger
logger = get_module_logger("data_processor")


class DataProcessor:
    """数据处理器 - 负责高级数据分析和转换"""

    @staticmethod
    def to_chart_data(df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        将DataFrame转换为图表数据格式

        Args:
            df: 包含OHLC数据的DataFrame

        Returns:
            图表数据列表
        """
        if df.empty:
            return []

        chart_data = []
        for _, row in df.iterrows():
            chart_data.append(
                {
                    "datetime": str(row.get("datetime", "")),
                    "price": float(row.get("close", 0)),
                    "volume": float(row.get("volume", 0)),
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                }
            )

        return chart_data

    @staticmethod
    def fit_gaussian_mixture(
        chart_data: List[Dict[str, Any]], n_components: int = 3, max_components: int = 5
    ) -> Optional[Dict[str, Any]]:
        """
        对价格-成交量分布进行高斯混合模型拟合（多峰拟合）

        Args:
            chart_data: 图表数据列表
            n_components: 初始高斯分量数量
            max_components: 最大高斯分量数量

        Returns:
            拟合结果，包含参数和拟合曲线数据
        """
        if not chart_data or len(chart_data) < 10:
            return None

        try:
            # 1. 准备数据：按价格聚合成交量
            price_volume_map = {}
            for point in chart_data:
                price = round(point["price"], 2)
                volume = point["volume"]
                if price in price_volume_map:
                    price_volume_map[price] += volume
                else:
                    price_volume_map[price] = volume

            prices = np.array(list(price_volume_map.keys()))
            volumes = np.array(list(price_volume_map.values()))

            if len(prices) < 5:
                return None

            # 2. 准备加权样本（用成交量作为权重）
            # 将每个价格按其成交量权重重复
            weighted_prices = []
            for price, volume in zip(prices, volumes):
                # 标准化权重，避免样本过多
                weight = int(max(1, volume / np.mean(volumes)))
                weighted_prices.extend([price] * weight)

            X = np.array(weighted_prices).reshape(-1, 1)

            # 3. 使用BIC自动选择最优的高斯分量数
            bic_scores = []
            models = []

            for n in range(1, min(max_components + 1, len(prices))):
                try:
                    gmm = GaussianMixture(
                        n_components=n,
                        covariance_type="full",
                        max_iter=100,
                        random_state=42,
                    )
                    gmm.fit(X)
                    bic_scores.append(gmm.bic(X))
                    models.append(gmm)
                except:
                    break

            if not models:
                return None

            # 选择BIC最小的模型
            best_idx = np.argmin(bic_scores)
            best_gmm = models[best_idx]

            # 4. 生成拟合曲线数据
            price_min, price_max = prices.min(), prices.max()
            price_range = np.linspace(price_min, price_max, 200)

            # 计算总的概率密度
            densities = np.exp(best_gmm.score_samples(price_range.reshape(-1, 1)))

            # 归一化到总成交量
            total_volume = volumes.sum()
            # 计算密度的积分（使用梯形法则）
            density_integral = np.trapz(densities, price_range)
            normalized_densities = densities * (total_volume / density_integral)

            # 5. 生成每个高斯分量的曲线
            components = []
            means = best_gmm.means_.flatten()
            covariances = best_gmm.covariances_.flatten()
            weights = best_gmm.weights_

            for i in range(best_gmm.n_components):
                mean = means[i]
                std = np.sqrt(covariances[i])
                weight = weights[i]

                # 计算该分量的密度
                component_density = weight * stats.norm.pdf(price_range, mean, std)
                # 归一化到总成交量
                component_integral = np.trapz(component_density, price_range)
                component_volume = component_density * (total_volume / density_integral)

                components.append(
                    {
                        "mean": float(mean),
                        "std": float(std),
                        "weight": float(weight),
                        "volume": float(weight * total_volume),
                    }
                )

            # 6. 构建拟合曲线数据
            fit_curve = []
            for price, density in zip(price_range, normalized_densities):
                fit_curve.append({"price": float(price), "fitVolume": float(density)})

            return {
                "n_components": best_gmm.n_components,
                "components": components,
                "fit_curve": fit_curve,
                "bic": float(bic_scores[best_idx]),
            }

        except Exception as e:
            logger.error(f"GMM拟合失败: {str(e)}", exc_info=True)
            return None
