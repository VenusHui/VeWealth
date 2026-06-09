"""
数据处理工具
负责数据的清洗、转换、聚合等操作
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional, Tuple
from sklearn.mixture import GaussianMixture
from scipy import stats
from app.core.logger import get_module_logger

# 获取logger
logger = get_module_logger("data_processor")


class DataProcessor:
    """数据处理器 - 负责高级数据分析和转换"""

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
            # numpy 2.x removed trapz, replaced with trapezoid
            trapz_fn = np.trapezoid if hasattr(np, "trapezoid") else np.trapz
            density_integral = trapz_fn(densities, price_range)
            if density_integral <= 0:
                return None
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

    # ------------------------------------------------------------------
    # Volume Profile computation
    # ------------------------------------------------------------------

    @staticmethod
    def compute_volume_profile(df: pd.DataFrame, bins: int = 100) -> Dict[str, Any]:
        """从OHLCV DataFrame计算Volume Profile。

        将每根K线柱的成交量均匀分布到其最高价-最低价区间内的各个价格bin。

        Args:
            df: 包含 open, high, low, close, volume 列的DataFrame
            bins: 价格bin数量

        Returns:
            dict with keys: profile, total_volume, price_min, price_max,
                           bin_size, poc, value_area, hvn_levels, lvn_levels, vwap
        """
        if df.empty or len(df) < 2:
            return {
                "profile": [],
                "total_volume": 0.0,
                "price_min": 0.0,
                "price_max": 0.0,
                "bin_size": 0.0,
                "poc": {"price": 0.0, "volume": 0.0},
                "value_area": {"vah": 0.0, "val": 0.0, "volume_pct": 0.0},
                "hvn_levels": [],
                "lvn_levels": [],
                "vwap": 0.0,
            }

        price_min = df["low"].min()
        price_max = df["high"].max()
        if price_max <= price_min:
            price_max = price_min + 0.01

        bin_size = (price_max - price_min) / bins
        profile = np.zeros(bins)

        total_volume = 0.0
        total_vwap_num = 0.0
        total_vwap_den = 0.0

        for _, bar in df.iterrows():
            vol = float(bar["volume"])
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar.get("close", bar["high"]))
            bar_range = high - low

            if vol <= 0:
                continue

            total_volume += vol

            # VWAP: use typical price (high+low+close)/3
            typical_price = (high + low + close) / 3.0
            total_vwap_num += typical_price * vol
            total_vwap_den += vol

            if bar_range <= 0:
                # Single price bar — assign all volume to that bin
                b = int((low - price_min) / bin_size)
                b = max(0, min(b, bins - 1))
                profile[b] += vol
            else:
                vol_per_unit = vol / bar_range
                low_bin = int((low - price_min) / bin_size)
                high_bin = int((high - price_min) / bin_size)
                low_bin = max(0, min(low_bin, bins - 1))
                high_bin = max(0, min(high_bin, bins - 1))

                if low_bin == high_bin:
                    profile[low_bin] += vol
                else:
                    for b in range(low_bin, high_bin + 1):
                        bin_low = price_min + b * bin_size
                        bin_high = bin_low + bin_size
                        overlap_low = max(low, bin_low)
                        overlap_high = min(high, bin_high)
                        overlap = max(0.0, overlap_high - overlap_low)
                        profile[b] += vol_per_unit * overlap

        vwap = total_vwap_num / total_vwap_den if total_vwap_den > 0 else 0.0

        # Build profile list
        profile_list = []
        for i in range(bins):
            price_center = price_min + (i + 0.5) * bin_size
            profile_list.append(
                {
                    "price": round(float(price_center), 3),
                    "volume": round(float(profile[i]), 2),
                }
            )

        # POC
        poc_idx = int(np.argmax(profile))
        poc = {
            "price": round(float(price_min + (poc_idx + 0.5) * bin_size), 3),
            "volume": round(float(profile[poc_idx]), 2),
        }

        # Value Area
        value_area = DataProcessor.compute_value_area(profile_list, pct=0.70)

        # HVN / LVN
        hvn_levels, lvn_levels = DataProcessor.compute_hvn_lvn(
            profile,
            price_min=price_min,
            bin_size=bin_size,
        )

        return {
            "profile": profile_list,
            "total_volume": round(float(total_volume), 2),
            "price_min": round(float(price_min), 3),
            "price_max": round(float(price_max), 3),
            "bin_size": round(float(bin_size), 4),
            "poc": poc,
            "value_area": value_area,
            "hvn_levels": hvn_levels,
            "lvn_levels": lvn_levels,
            "vwap": round(float(vwap), 3),
        }

    @staticmethod
    def compute_vwap(df: pd.DataFrame) -> float:
        """计算成交量加权平均价格(VWAP)。"""
        if df.empty or "volume" not in df.columns:
            return 0.0
        vol = df["volume"].values
        typical = ((df["high"] + df["low"] + df["close"]) / 3.0).values
        total_vol = vol.sum()
        if total_vol <= 0:
            return 0.0
        return float(np.sum(typical * vol) / total_vol)

    @staticmethod
    def compute_value_area(
        profile: List[Dict[str, Any]], pct: float = 0.70
    ) -> Dict[str, float]:
        """从Volume Profile计算Value Area (VAH/VAL)。

        从POC开始向两侧扩展，直到累计成交量占比达到pct。

        Args:
            profile: [{"price": ..., "volume": ...}, ...]
            pct: 目标成交量占比

        Returns:
            {"vah": ..., "val": ..., "volume_pct": ...}
        """
        if not profile:
            return {"vah": 0.0, "val": 0.0, "volume_pct": 0.0}

        volumes = np.array([p["volume"] for p in profile])
        total_vol = volumes.sum()
        if total_vol <= 0:
            return {"vah": 0.0, "val": 0.0, "volume_pct": 0.0}

        poc_idx = int(np.argmax(volumes))
        target_vol = total_vol * pct

        low_idx = poc_idx
        high_idx = poc_idx
        accumulated = volumes[poc_idx]

        while accumulated < target_vol:
            can_expand_low = low_idx > 0
            can_expand_high = high_idx < len(profile) - 1

            if not can_expand_low and not can_expand_high:
                break

            if can_expand_low and can_expand_high:
                vol_low = volumes[low_idx - 1]
                vol_high = volumes[high_idx + 1]
                if vol_low >= vol_high:
                    low_idx -= 1
                    accumulated += vol_low
                else:
                    high_idx += 1
                    accumulated += vol_high
            elif can_expand_low:
                low_idx -= 1
                accumulated += volumes[low_idx]
            else:
                high_idx += 1
                accumulated += volumes[high_idx]

        actual_pct = round(float(accumulated / total_vol * 100), 1)
        vah = round(float(profile[high_idx]["price"]), 3)
        val = round(float(profile[low_idx]["price"]), 3)

        return {"vah": vah, "val": val, "volume_pct": actual_pct}

    @staticmethod
    def compute_hvn_lvn(
        profile_array: np.ndarray,
        price_min: float = 0.0,
        bin_size: float = 0.0,
        sigma_high: float = 1.5,
        sigma_low: float = 0.5,
    ) -> Tuple[List[float], List[float]]:
        """Identify High Volume Nodes (HVN) and Low Volume Nodes (LVN).

        Returns price levels (not raw volume values) for bins where volume
        exceeds mean+sigma_high*std (HVN) or falls below mean-sigma_low*std (LVN).

        Args:
            profile_array: 1-D numpy array of per-bin volume values.
            price_min: Lower bound of the first bin (for converting bin index to price).
            bin_size: Width of each bin.
            sigma_high: Standard-deviation threshold for HVN.
            sigma_low: Standard-deviation threshold for LVN.

        Returns:
            (hvn_price_levels: list[float], lvn_price_levels: list[float])
        """
        if profile_array is None or len(profile_array) == 0:
            return [], []

        volumes = np.array(profile_array, dtype=float)
        mean_vol = volumes.mean()
        std_vol = volumes.std()

        if std_vol <= 0:
            return [], []

        hvn_mask = volumes > mean_vol + sigma_high * std_vol
        lvn_mask = volumes < mean_vol - sigma_low * std_vol

        hvn_levels = [
            round(float(price_min + (idx + 0.5) * bin_size), 3)
            for idx, v in enumerate(volumes)
            if hvn_mask[idx]
        ]
        lvn_levels = [
            round(float(price_min + (idx + 0.5) * bin_size), 3)
            for idx, v in enumerate(volumes)
            if lvn_mask[idx]
        ]

        return hvn_levels, lvn_levels
