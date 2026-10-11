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
# backend/app/core/config.py）；env 文件读不到时用与后端一致的默认值（警告 12 GB /
# 临界 10 GB，依据见 config.py 该字段上方注释）。口径是**可用
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
# 但 awk 的规则是「一方不是数值就按**字符串**比较」，所以喂进去之前必须先确认是数值
# （见下面的 is_number），否则阈值写错会静默改变比较语义 —— 那正是这条闸门要消灭的
# 失效形态（VEW-67 评审 M1）。
#
# 退出码契约：
#   0 = 结论是通过（可能带 ::warning:: 注解）
#   1 = 结论是不通过（post-prune 的封顶失败 / 低于临界）
#   2 = **闸门给不出结论**（参数非法、阈值非数值、df 读不到）—— 绝不静默当通过
# pre-deploy 的「只报数、不中止」由调用方 `|| true` 保证（见两个 workflow），
# 所以它在任何退出码下都不会挡住发布。

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

# 只接受非负十进制数（可带小数）。不接受单位后缀、行内注释、空值等任何别的东西：
# awk 遇到非数值会退化成字符串比较，`9 < "10GB"` 为假 → 9 GB 在临界 10 GB 下被判正常，
# 静默放行（VEW-67 评审 M1 实测）。判不出结论就必须报错，不能当通过。
is_number() {
  printf '%s' "$1" | grep -Eq '^[0-9]+([.][0-9]+)?$'
}

# 阈值优先级：命令行 > env 文件 > 与后端一致的默认值（队长拍板 12/10，依据见 config.py）。
# 记录来源，报错时能指到具体哪一项。
WARN_GB=12
WARN_SRC="默认值"
CRITICAL_GB=10
CRITICAL_SRC="默认值"
if [ -n "$WARN_GB_OVERRIDE" ]; then
  WARN_GB="$WARN_GB_OVERRIDE"
  WARN_SRC="命令行 --warn-gb"
else
  v=$(env_value DISK_HEADROOM_WARN_GB)
  if [ -n "$v" ]; then
    WARN_GB="$v"
    WARN_SRC="${ENV_FILE} 的 DISK_HEADROOM_WARN_GB"
  fi
fi
if [ -n "$CRITICAL_GB_OVERRIDE" ]; then
  CRITICAL_GB="$CRITICAL_GB_OVERRIDE"
  CRITICAL_SRC="命令行 --critical-gb"
else
  v=$(env_value DISK_HEADROOM_CRITICAL_GB)
  if [ -n "$v" ]; then
    CRITICAL_GB="$v"
    CRITICAL_SRC="${ENV_FILE} 的 DISK_HEADROOM_CRITICAL_GB"
  fi
fi

bad=""
is_number "$WARN_GB" || bad="警告阈值（${WARN_SRC}）= '${WARN_GB}'"
is_number "$CRITICAL_GB" || bad="${bad:+${bad}；}临界阈值（${CRITICAL_SRC}）= '${CRITICAL_GB}'"
if [ -n "$bad" ]; then
  echo "::error::磁盘余量阈值不是数值：${bad}。只接受形如 10 或 7.5 的 GB 数 —— 带单位后缀（10GB）或行内注释会让比较退化成字符串比较、静默放行，判不出结论一律按失败处理（VEW-67）"
  exit 2
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
    # 判不出结果一律 exit 2：这条闸门的存在意义就是「削不动了有人知道」，静默放过
    # 等于退回 VEW-67 要修的那个状态。pre-deploy 不会被它挡住（调用方有 `|| true`）。
    exit 2
  fi
fi

if ! is_number "$FREE_GB"; then
  echo "::error::磁盘余量读数不是数值：'${FREE_GB}'（来源：$( [ -n "$FREE_GB_OVERRIDE" ] && echo '命令行 --free-gb' || echo "df ${TARGET_PATH}" )）—— 判不出结论一律按失败处理（VEW-67）"
  exit 2
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
  echo "::warning::/ 可用空间 ${FREE_GB}GB 低于警告阈值 ${WARN_GB}GB：尚可部署，但余量已进入需要留意的区间 —— 该谈「腾空间还是扩盘」了（VEW-67）"
  exit 0
fi

echo "✅ 盘余量正常：${TARGET_PATH} 可用 ${FREE_GB}GB（≥ ${WARN_GB}GB）"
exit 0
