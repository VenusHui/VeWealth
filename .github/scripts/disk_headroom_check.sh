#!/usr/bin/env bash
#
# 磁盘余量闸门（VEW-67）
#
# 为什么需要：生产 / 是单块 40 GB 盘，postgres + backend + frontend 都在上面，写满是
# 一起挂。VEW-68 给 docker 构建缓存加了 1 GB 封顶（「能削」），但封顶失败时只有一行
# 日志 —— 达成的是「能削」，不是「削不动了有人知道」。本脚本把「盘还剩多少」变成流水线
# 里可见、可失败的信号：GitHub Actions 的注解与失败是仓库内目前唯一真正会通知到人的
# 通道（没有日志采集 / 告警系统，容器 stdout 里的告警日志不会主动到达任何人）。
#
# 阈值与后端共用一组配置（backend/settings/.prod.env 的 DISK_HEADROOM_*，字段定义见
# backend/app/core/config.py）；env 文件读不到时用与后端一致的默认值。口径是**可用
# 空间**，不是使用率 —— 40 GB 盘上的风险由「一次部署构建需要几个 GB 的瞬时空间」决定。
#
# 用法：
#   disk_headroom_check.sh pre-deploy [选项]   # 部署前：只报数 + 注解，永不失败
#   disk_headroom_check.sh post-prune [选项]   # 清理构建缓存后：低于临界值即失败（通知）
#   disk_headroom_check.sh check      [选项]   # 只报数，永不失败（人工排查）
#
# 选项：
#   --path PATH        检查的挂载点（默认 /）
#   --env-file PATH    读取阈值的 env 文件（默认 backend/settings/.prod.env）
#   --free-gb N        演练：按给定余量判定，不读 df（用于预演与测试）
#   --warn-gb N        覆盖警告阈值（GB）
#   --critical-gb N    覆盖临界阈值（GB）
#
# 为什么 pre-deploy 不失败：那一刻旧容器还在跑，中止部署会挡掉一次可能救急的发布；
# 而「盘不够」这件事由同一次运行末尾的 post-prune 判定负责失败并通知，不会漏。
# （把 pre-deploy 也改成 fail-closed 属于改变部署行为，留给队长拍板。）
#
# 数值比较一律交给 awk：远端 sshd 用的是 ubuntu 的登录 shell（zsh），shell 间的算术与
# 词分割语义差异正是 VEW-68 那次静默失效的根因，awk 不依赖任何一方的语义。

set -u

MODE="check"
TARGET_PATH="/"
ENV_FILE="backend/settings/.prod.env"
FREE_GB_OVERRIDE=""
WARN_GB_OVERRIDE=""
CRITICAL_GB_OVERRIDE=""

while [ $# -gt 0 ]; do
  case "$1" in
    pre-deploy|post-prune|check)
      MODE="$1"
      ;;
    --path)
      TARGET_PATH="${2:-}"
      shift
      ;;
    --env-file)
      ENV_FILE="${2:-}"
      shift
      ;;
    --free-gb)
      FREE_GB_OVERRIDE="${2:-}"
      shift
      ;;
    --warn-gb)
      WARN_GB_OVERRIDE="${2:-}"
      shift
      ;;
    --critical-gb)
      CRITICAL_GB_OVERRIDE="${2:-}"
      shift
      ;;
    *)
      echo "未知参数: $1" >&2
      exit 2
      ;;
  esac
  shift
done

# env 文件里取一个键的值（只取最后一次赋值；容忍引号与两侧空白）。
env_value() {
  key="$1"
  [ -f "$ENV_FILE" ] || return 0
  grep -E "^[[:space:]]*${key}=" "$ENV_FILE" 2>/dev/null | tail -n 1 |
    cut -d= -f2- | tr -d "\"'" | tr -d "[:space:]"
}

# 阈值优先级：命令行 > env 文件 > 与后端一致的默认值
WARN_GB=10
CRITICAL_GB=5
if [ -n "$WARN_GB_OVERRIDE" ]; then
  WARN_GB="$WARN_GB_OVERRIDE"
else
  v=$(env_value DISK_HEADROOM_WARN_GB)
  [ -n "$v" ] && WARN_GB="$v"
fi
if [ -n "$CRITICAL_GB_OVERRIDE" ]; then
  CRITICAL_GB="$CRITICAL_GB_OVERRIDE"
else
  v=$(env_value DISK_HEADROOM_CRITICAL_GB)
  [ -n "$v" ] && CRITICAL_GB="$v"
fi

# 余量（GB，保留两位）
if [ -n "$FREE_GB_OVERRIDE" ]; then
  FREE_GB="$FREE_GB_OVERRIDE"
  echo "📊 磁盘余量（演练读数，未读 df）: ${TARGET_PATH} 可用 ${FREE_GB} GB"
else
  echo "📊 磁盘余量:"
  df -h "$TARGET_PATH" 2>/dev/null || true
  FREE_GB=$(df -Pk "$TARGET_PATH" 2>/dev/null | awk 'NR==2 {printf "%.2f", $4/1024/1024}')
  if [ -z "$FREE_GB" ]; then
    echo "::error::无法读取 ${TARGET_PATH} 的磁盘余量（df 失败）：盘余量不可知，需人工检查"
    # post-prune 判不出结果也算失败：这条闸门的存在意义就是「削不动了有人知道」，
    # 静默放过等于退回 VEW-67 要修的那个状态。
    [ "$MODE" = "post-prune" ] && exit 1
    exit 0
  fi
fi
echo "   可用 ${FREE_GB} GB（阈值：警告 <${WARN_GB}GB / 临界 <${CRITICAL_GB}GB）"

below_warn=$(awk -v f="$FREE_GB" -v t="$WARN_GB" 'BEGIN { print (f < t) ? 1 : 0 }')
below_critical=$(awk -v f="$FREE_GB" -v t="$CRITICAL_GB" 'BEGIN { print (f < t) ? 1 : 0 }')

# 构建缓存封顶失败的信号（由调用方传入，见 VEW-68：封顶失败此前只有一行日志）
PRUNE_FAILED="${PRUNE_FAILED:-0}"

if [ "$MODE" = "post-prune" ] && [ "$PRUNE_FAILED" != "0" ]; then
  echo "::error::构建缓存封顶失败，且盘余量 ${FREE_GB}GB（VEW-68/VEW-67）：缓存可能在持续增长，请人工检查 docker builder prune 与 / 余量"
  exit 1
fi

if [ "$below_critical" = "1" ]; then
  if [ "$MODE" = "post-prune" ]; then
    echo "::error::/ 可用空间 ${FREE_GB}GB 低于临界阈值 ${CRITICAL_GB}GB：部署构建随时可能把系统盘吃满，postgres + backend + frontend 会一起挂（VEW-67）"
    exit 1
  fi
  if [ "$MODE" = "pre-deploy" ]; then
    echo "::error::/ 可用空间 ${FREE_GB}GB 低于临界阈值 ${CRITICAL_GB}GB：本次部署构建有把系统盘吃满的风险（VEW-67）。不中止部署，但请立即处理"
  else
    echo "::error::/ 可用空间 ${FREE_GB}GB 低于临界阈值 ${CRITICAL_GB}GB（VEW-67）"
  fi
  exit 0
fi

if [ "$below_warn" = "1" ]; then
  echo "::warning::/ 可用空间 ${FREE_GB}GB 低于警告阈值 ${WARN_GB}GB：尚可部署，但已进入策略性预留区（VEW-67）"
  exit 0
fi

echo "✅ 盘余量正常：${TARGET_PATH} 可用 ${FREE_GB}GB（≥ ${WARN_GB}GB）"
exit 0
