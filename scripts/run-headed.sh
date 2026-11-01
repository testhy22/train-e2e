#!/usr/bin/env bash
# 本地可见调试入口：把浏览器窗口真的显示出来。
#
# 用途仅限「本地调试看过程」。CI 路径不经过这里 —— CI 里 hotels 仍是 headless，
# trains 走 xvfb-run（见 .github/workflows/ci.yml）。本脚本**不会**被 CI 调用。
#
# 用法：
#   scripts/run-headed.sh suites/hotels -m hotels
#   scripts/run-headed.sh suites/thirdparty -m ui --slowmo=800
#   SLOWMO=300 scripts/run-headed.sh suites/hotels -m hotels        # 还是看不清时放慢
#   HUMAN_SEED=42 scripts/run-headed.sh suites/hotels -m hotels    # 固定停顿序列，便于复现
#
# 固定设置 HEADED=1 HUMAN=1；其余 pytest 参数原样透传。
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f pytest.ini ]; then
  echo "✗ 没找到 pytest.ini，请在仓库根目录运行本脚本。" >&2
  exit 2
fi

# ---------------------------------------------------------------- 看不到窗口就说清楚
# 只做提示并退出，不用 xvfb 冒充「可见」：那会把「看不见」伪装成「看得见」，
# 比直接报错更糟 —— 用户会一直等一个永远不会出现的窗口。
if [ "$(uname -s)" = "Linux" ]; then
  if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    cat >&2 <<'EOF'
✗ 当前环境看不到窗口：没有 DISPLAY / WAYLAND_DISPLAY。

  本脚本只用于本地有显示服务的环境。无头服务器上不要用 xvfb 假装可见 ——
  那样窗口不会真的出现，只会让「可见调试」名不副实。
  CI 路径请用 .github/workflows/ci.yml 里各自 job 的配置。
EOF
    exit 3
  fi
fi

# ---------------------------------------------------------------- 固定开关
export HEADED=1
export HUMAN=1
# HUMAN_SEED 只在调用者显式给了才导出；否则每次随机，人工观察更接近真实使用。
if [ -n "${HUMAN_SEED:-}" ]; then
  export HUMAN_SEED
  seed_desc="${HUMAN_SEED}（固定）"
else
  seed_desc="随机（未固定；复现时序问题请设 HUMAN_SEED）"
fi

# ---------------------------------------------------------------- 可选放慢
# 拟人节奏本身已经让操作看得清；--slowmo 是**额外**的全局固定延迟，会让节奏变机械，
# 只在「还是看不清单个动作」时短时使用。
ARGS=()
if [ -n "${SLOWMO:-}" ]; then
  ARGS+=("--slowmo=${SLOWMO}")
  slowmo_desc="${SLOWMO}ms（叠加在拟人节奏之上）"
else
  slowmo_desc="未启用（拟人节奏已开启；看不清时用 SLOWMO=300 或 800）"
fi

# ---------------------------------------------------------------- 目标 suite
targets=()
for arg in "$@"; do
  case "$arg" in
    suites/*|tests/*|*.py) targets+=("$arg") ;;
  esac
done
if [ ${#targets[@]} -gt 0 ]; then
  target_desc="${targets[*]}"
else
  target_desc="（未指定路径，按 pytest 默认全量）"
fi

# ---------------------------------------------------------------- 打印后执行
cmd_desc="python -m pytest"
if [ ${#ARGS[@]} -gt 0 ]; then
  cmd_desc="${cmd_desc} ${ARGS[*]}"
fi
cmd_desc="${cmd_desc} $*"

cat <<EOF
=== 本地可见调试（有头）===
  HEADED     = 1
  HUMAN      = 1
  HUMAN_SEED = ${seed_desc}
  SLOWMO     = ${slowmo_desc}
  目标 suite = ${target_desc}

  命令：${cmd_desc}

  说明：HEADED=1 会自动开启拟人节奏；默认不要再叠加 --slowmo。
        窗口在本机桌面上真实显示，跑完自动关闭。
EOF

# 注意：空数组不能写成 "${ARGS[@]:-}" —— 那会展开成一个**空字符串参数**传给 pytest，
# 报 "file or directory not found: "。必须显式分支。
if [ ${#ARGS[@]} -gt 0 ]; then
  exec python -m pytest "${ARGS[@]}" "$@"
else
  exec python -m pytest "$@"
fi
