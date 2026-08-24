# VeWealth 回测架构重构设计：Strategy 与 Policy 解耦（方案2）

> **Status: 🚧 Partially Implemented** — P0 (contracts, profiles, validators, registry availability) is done. P1 (orchestrator, individual policy implementations, service migration) is pending. P2 (runtime validation) and P3 (decision trace) are not yet started.

## 1. 背景与目标

当前回测框架中，`strategy_select` 的核心交易流程存在服务层硬编码（`BacktestService._run_strategy_select_mode`），导致：

- 策略与引擎耦合，难以复用与扩展
- “同日多标的如何选择”缺少统一强约束
- 策略是否“可用”缺少可判定标准

本次重构目标：

1. 实现 **策略（Strategy）与执行策略（Policy）分离**
2. 建立 **双层校验（注册时 + 运行前）**，不完整策略标记不可用
3. 明确策略完成定义（Definition of Done），未达标不可执行
4. 在不破坏现有 API 的前提下渐进落地

---

## 2. 总体架构

采用“策略产候选 + Policy 负责编排执行”的流水线：

`generate_candidates -> rank -> select -> allocate -> risk_check -> execute -> record`

### 2.1 模块分层

- **Strategy 层**：只负责信号逻辑与候选生成
- **Policy 层**：负责排名、筛选、配仓、风控、执行
- **Orchestrator 层**：统一编排策略与 policy 执行顺序
- **Validation 层**：静态/动态校验
- **Persistence 层**：回测结果与决策轨迹入库

---

## 3. 核心契约定义（MVP）

## 3.1 Strategy 抽象接口

```python
class BaseStrategy(ABC):
    strategy_id: str
    name: str
    description: str

    @classmethod
    @abstractmethod
    def param_schema(cls) -> list[dict]: ...

    @classmethod
    @abstractmethod
    def required_columns(cls) -> set[str]: ...

    @classmethod
    @abstractmethod
    def default_policy_profile(cls) -> str: ...

    @abstractmethod
    def generate_candidates(self, market_df: pd.DataFrame, params: dict) -> pd.DataFrame:
        """
        必需字段：trade_date, symbol
        推荐字段：signal_strength, reason, meta
        """
```

## 3.2 Policy 五件套接口（强制）

```python
class RankingPolicy(ABC):
    def rank(self, candidates_df, context) -> pd.DataFrame: ...

class SelectionPolicy(ABC):
    def select(self, ranked_df, portfolio_state, context) -> pd.DataFrame: ...

class AllocationPolicy(ABC):
    def allocate(self, selected_df, equity, risk_state, context) -> pd.DataFrame: ...

class RiskPolicy(ABC):
    def check_pre_trade(self, orders_df, portfolio_state, context) -> pd.DataFrame: ...

class ExecutionPolicy(ABC):
    def simulate_fill(self, orders_df, bar_df, cost_model, context) -> list[dict]: ...
```

> 说明：SelectionPolicy 必须定义“同日多标的冲突解决规则”（例如 Top-K / 阈值 + 优先级）。

## 3.3 PolicyProfile（策略执行画像）

```yaml
profile_id: vsd_v1_default
ranking_policy: signal_then_liquidity_v1
selection_policy: top_k_v1
allocation_policy: equal_weight_v1
risk_policy: cn_a_basic_risk_v1
execution_policy: cn_a_t1_open_fill_v1
```

策略必须绑定一个有效 PolicyProfile，否则不可用。

---

## 4. 可用性判定与校验机制

## 4.1 注册时静态校验（不通过=不可注册）

校验项：

1. Strategy 元数据完整：`strategy_id/name/description/param_schema`
2. `required_columns/default_policy_profile` 完整
3. 绑定的 PolicyProfile 存在且 5 个 policy 均可加载
4. 每个 policy 接口方法齐全（反射检查）
5. 参数 schema 默认值合法（类型、边界）

失败处理：

- 策略状态标记为 `unavailable`
- 写明 `unusable_reasons`
- 默认不出现在可运行策略列表

## 4.2 运行前动态校验（不通过=拒绝执行）

校验项：

1. 请求参数符合策略 schema
2. 数据列满足 `required_columns`
3. SelectionPolicy 有容量约束（`top_k`/`max_positions` 等）
4. Allocation 输出权重和 <= 1（允许现金仓位）
5. RiskPolicy 具备单票/总仓位基础约束检查能力
6. ExecutionPolicy 声明市场执行规则（A股 T+1、涨跌停、手数）

失败处理：

- 返回 400
- 错误信息可读（缺少能力项 + 修复建议）

---

## 5. 策略“实现完毕”定义（DoD）

策略仅在以下全部满足时标记 `usable=true`：

1. 候选生成可运行（支持空候选，逻辑不报错）
2. 绑定有效 PolicyProfile
3. 同日多标的冲突规则确定
4. 配仓规则确定
5. 风控规则可执行
6. 成交仿真规则明确
7. dry-run 回测测试通过（最小样本 + 边界样本）

否则：

- 标记为 `usable=false`
- 可展示为“草稿策略/不可用策略”
- 禁止进入 `/backtest/run` 与 `/backtest/jobs`

---

## 6. 与现有 VeWealth 代码的映射关系

当前关键现状：

- `volume_shrink_drop_v1.generate_signals()` 基本为空
- `BacktestService._run_strategy_select_mode()` 承担大量策略+执行逻辑
- `engine.py` 偏单标的回测逻辑

重构映射：

1. `volume_shrink_drop_v1` 迁移为 `generate_candidates()`
2. 现有服务层硬编码逻辑下沉到 policy 组件
3. 新增组合级 orchestrator 调用链
4. 保留 engine 中可复用成交与成本细节

---

## 7. 渐进实施路线

## Phase 1（MVP解耦）

- 新增 `app/services/backtest/policies/` 与五类基类
- 新增 `policy_profile_registry.py`
- 新增 `orchestrator.py`
- 将 `_run_strategy_select_mode` 改为 orchestrator 驱动
- 保持现有 API 结构不变（前端无感）

## Phase 2（可用性治理）

- registry 增加 `usable/unusable_reasons`
- `/backtest/strategies` 默认仅返回可用策略
- 增加 `include_unusable=true` 供管理端调试
- run/jobs 入口增加统一动态校验

## Phase 3（可解释性）

- 存储决策轨迹（候选分数、入选原因、风控拒绝原因）
- 前端详情页展示 policy 链路与拒单统计

---

## 8. 风险与回滚策略

主要风险：

1. 迁移期行为偏差（收益曲线与旧版不一致）
2. policy 配置错误导致策略不可用率上升
3. 组合层引入后性能下降

应对：

- 增加旧/新执行器并行对比开关（灰度）
- 提供默认 policy profile，避免裸策略
- 增加 profile 级缓存与批量数据处理

回滚：

- 保留旧 `_run_strategy_select_mode` 路径开关
- 关键问题时可回退旧路径保障可用性

---

## 9. 验收标准

1. 策略与执行编排解耦完成，服务层无策略特判硬编码
2. 不完整策略无法通过可用性校验并且不可执行
3. 同日多标的选择规则在 SelectionPolicy 中强制存在
4. 现有 API 可兼容，前端无需同步大改
5. `volume_shrink_drop_v1` 迁移后结果与预期一致（允许小幅可解释差异）

---

## 10. 本次决策结论

采用 **方案2：Strategy + Policy 分离架构**。

策略只负责“找机会”，policy 负责“怎么选、怎么买、怎么控风险、怎么成交”。
通过双层校验与 DoD，确保“不完整策略 = 不可用策略”。
