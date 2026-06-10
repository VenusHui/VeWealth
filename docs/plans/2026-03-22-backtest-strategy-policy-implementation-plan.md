# VeWealth 实施计划：Strategy + Policy 解耦（文件级任务清单）

> **Status: 🚧 Partially Implemented** (as of 2026-06)
> - ✅ P0: Contracts (`contracts.py`), policy base (`policies/base.py`), profiles (`policies/profiles.py`), registry (`policies/registry.py`), strategy validator (`validators/strategy_validator.py`), strategy management service done.
> - 🚧 P1: Orchestrator (`orchestrator.py`) not yet created. Individual policy implementations (ranking/, selection/, allocation/, risk/, execution/) not yet created. `BacktestService` still uses legacy path.
> - ❌ P2: Runtime validator not started.
> - ❌ P3: Decision trace not started.

基于设计文档：`2026-03-22-backtest-strategy-policy-architecture-design.md`

## 0. 实施原则

- 保持现有 API 兼容（`/api/backtest/run`, `/api/backtest/jobs`, `/api/backtest/strategies`）
- 先引入新编排层，再迁移旧逻辑，最后收口旧路径
- 全程保留回滚开关（旧 `_run_strategy_select_mode` 可临时启用）
- 每个阶段都必须有可验证验收标准

---

## 1. P0 - 基础骨架（契约 + 注册 + Profile）

目标：把“可用策略”的硬约束建起来，不改核心执行逻辑。

### 1.1 新增策略契约与类型定义

**新增文件**
- `backend/app/services/backtest/strategies/contracts.py`

**内容**
- `BaseStrategyV2` 抽象类：
  - `param_schema()`
  - `required_columns()`
  - `default_policy_profile()`
  - `generate_candidates()`
- 候选数据最小字段约束常量：`trade_date`, `symbol`

**验收标准**
- 代码可 import
- 抽象方法缺失时策略类无法实例化

---

### 1.2 新增 Policy 五件套抽象接口

**新增文件**
- `backend/app/services/backtest/policies/base.py`

**内容**
- `RankingPolicy`, `SelectionPolicy`, `AllocationPolicy`, `RiskPolicy`, `ExecutionPolicy`
- 基础上下文对象（可先用 TypedDict/dataclass）

**验收标准**
- 五类接口可被反射校验
- SelectionPolicy 文档明确“必须定义同日多标的冲突规则”

---

### 1.3 新增 PolicyProfile 注册表

**新增文件**
- `backend/app/services/backtest/policies/registry.py`
- `backend/app/services/backtest/policies/profiles.py`

**内容**
- policy registry（按 `policy_id -> class`）
- profile registry（按 `profile_id -> 5个policy_id`）
- 先实现一套默认 profile：`vsd_v1_default`

**验收标准**
- 可以通过 profile_id 成功解析出五件套实例
- 缺失 policy 时报可读错误

---

### 1.4 增加策略静态校验器

**新增文件**
- `backend/app/services/backtest/validators/strategy_validator.py`

**内容**
- 校验 strategy 元数据
- 校验 default profile 完整性
- 校验 policy 接口完整性
- 产出：`usable: bool`, `unusable_reasons: list[str]`

**验收标准**
- 故意构造不完整策略时，被标记 `usable=false`
- 错误信息包含具体缺失项

---

### 1.5 改造 strategy registry 输出可用性

**修改文件**
- `backend/app/services/backtest/registry.py`

**改造点**
- `list_strategies()` 返回新增字段：`usable`, `unusable_reasons`, `policy_profile`
- `get_strategy()` 对不可用策略默认拒绝执行（可保留内部 bypass）

**验收标准**
- `/api/backtest/strategies` 能看到可用性字段
- 不完整策略默认不应进入可执行列表

---

## 2. P1 - 新编排层落地（orchestrator）

目标：将 `strategy_select` 从服务层硬编码迁移到策略+policy流水线。

### 2.1 新增组合编排器

**新增文件**
- `backend/app/services/backtest/orchestrator.py`

**内容**
- 主流程：
  1. strategy.generate_candidates
  2. ranking.rank
  3. selection.select
  4. allocation.allocate
  5. risk.check_pre_trade
  6. execution.simulate_fill
  7. 汇总 equity/trades/warnings/diagnostics

**验收标准**
- 输入标准化 market data 后能跑通完整流水线
- 每一步失败均返回可读错误（含 step name）

---

### 2.2 迁移 volume_shrink_drop_v1 为候选策略

**修改文件**
- `backend/app/services/backtest/strategies/volume_shrink_drop_v1.py`

**改造点**
- 从 `generate_signals()` 切换为 `generate_candidates()`
- 只负责候选生成与候选原因，不做选股配仓
- 声明 `default_policy_profile = vsd_v1_default`

**验收标准**
- 候选结果包含 `trade_date/symbol`
- 在同样数据下候选命中数与旧逻辑可对齐（允许轻微差异但可解释）

---

### 2.3 实现默认 policy 组件（最小可用）

**新增文件（建议）**
- `backend/app/services/backtest/policies/ranking/signal_then_liquidity_v1.py`
- `backend/app/services/backtest/policies/selection/top_k_v1.py`
- `backend/app/services/backtest/policies/allocation/equal_weight_v1.py`
- `backend/app/services/backtest/policies/risk/cn_a_basic_risk_v1.py`
- `backend/app/services/backtest/policies/execution/cn_a_t1_open_fill_v1.py`

**验收标准**
- 同日多标的时 selection 结果确定且可复现
- allocation 输出权重和 <= 1
- execution 覆盖 A股 T+1、涨跌停、整手规则

---

### 2.4 BacktestService 接入 orchestrator

**修改文件**
- `backend/app/services/backtest/service.py`

**改造点**
- `_run_strategy_select_mode()` 从硬编码扫描逻辑切到 orchestrator
- 保留 `use_legacy_strategy_select` 回滚开关（环境变量或配置项）

**验收标准**
- 旧 API 不变
- 新老路径可切换
- run/jobs 都能成功产出结果并入库

---

## 3. P2 - 运行前动态校验（提交即拦截）

目标：策略没实现完，不允许执行。

### 3.1 新增运行前校验器

**新增文件**
- `backend/app/services/backtest/validators/runtime_validator.py`

**校验项**
- 请求参数合法
- required_columns 齐备
- selection 有容量约束（top_k / max_positions）
- allocation 权重合法
- risk/execution 声明完整

**验收标准**
- 缺任一关键能力时，`/run`、`/jobs` 返回 400 + 明确原因

---

### 3.2 路由与服务接入动态校验

**修改文件**
- `backend/app/routers/backtest.py`
- `backend/app/services/backtest/service.py`
- `backend/app/services/backtest/job_manager.py`

**验收标准**
- 同步执行与异步任务拦截行为一致
- 错误文案一致（便于前端展示）

---

## 4. P3 - 可解释性与观测

目标：让“为何选中/为何拒绝”可追溯。

### 4.1 增加决策轨迹结构

**修改/新增文件**
- `backend/app/models/backtest.py`（必要时新增 JSON 字段或新表）
- `backend/app/schemas/backtest.py`
- `backend/app/services/backtest/service.py`

**建议字段**
- candidate_score
- selection_reason
- risk_reject_reason
- policy_profile_id

**验收标准**
- 回测详情可拿到每笔交易决策轨迹
- 可统计“被风控拒绝笔数/原因分布”

---

### 4.2 详情接口扩展

**修改文件**
- `backend/app/routers/backtest.py`
- `backend/app/services/backtest/service.py`

**验收标准**
- `/runs/{id}/strategy-config` 能显示 policy 链路
- 可对 strategy_select 结果做解释回放

---

## 5. 测试计划（必须）

### 5.1 单元测试

**新增目录**
- `backend/tests/backtest/policies/`
- `backend/tests/backtest/validators/`
- `backend/tests/backtest/orchestrator/`

**关键用例**
- 不完整策略 = unusable
- selection 同日冲突 deterministic
- allocation 权重上限
- risk 拒单路径
- execution A股规则（T+1/涨跌停/整手）

### 5.2 回归测试

- 对比旧版 `volume_shrink_drop_v1` 的核心指标：
  - signal_hit_count
  - trades count
  - total_return/max_drawdown
- 差异超过阈值（如 >5%）需给解释

### 5.3 API 测试

- `/strategies` 可用性字段
- `/run` 与 `/jobs` 动态拦截一致
- 详情接口不破坏现有前端依赖

---

## 6. 发布与回滚

## 6.1 发布步骤

1. 上线 P0（仅增加能力，不切换执行路径）
2. 上线 P1 并默认旧路径，灰度启用新 orchestrator
3. 验证通过后切新路径为默认
4. 上线 P2/P3

## 6.2 回滚策略

- 通过配置开关退回 legacy `_run_strategy_select_mode`
- 保持新字段向后兼容（前端不依赖可忽略）

---

## 7. 工时建议（粗估）

- P0：1~2 天
- P1：2~4 天
- P2：1~2 天
- P3：1~2 天
- 测试与回归：1~2 天

**总计**：6~12 天（单人）

---

## 8. 里程碑验收（对外口径）

- M1：策略可用性治理上线（不可用策略自动拦截）
- M2：strategy_select 完成解耦并由 policy 驱动
- M3：决策过程可解释（可追踪入选/拒绝原因）

---

## 9. 下一步执行建议

优先进入 P0 + P1：

1. 先把契约、profile、validator 立起来
2. 再迁移 `volume_shrink_drop_v1` 到新流水线
3. 完成后立即做一次旧新结果对比回归

达到这一步后，框架就从“能跑”升级到“可扩展、可治理、可审计”。
