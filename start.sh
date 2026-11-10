#!/usr/bin/env bash
# Florist-TV 一键启动 (Termux / Linux): bash start.sh
#   - 如果是 git 克隆下来的, 每次启动先自动拉取最新版本
#   - 首次运行或依赖清单变化时自动安装依赖
#   - 端口默认 8080, 可用 PORT=9000 bash start.sh 修改
#   - 不想自动更新: NO_UPDATE=1 bash start.sh
cd "$(dirname "$0")" || exit 1
export PORT="${PORT:-8080}"

# ---- 1. 自动更新 ----
if [ -d .git ] && [ -z "$NO_UPDATE" ] && command -v git >/dev/null 2>&1; then
  old=$(git rev-parse --short HEAD 2>/dev/null)
  echo "[更新] 检查新版本..."
  if timeout 40 git pull --ff-only --quiet 2>/dev/null; then
    new=$(git rev-parse --short HEAD 2>/dev/null)
    if [ "$old" != "$new" ]; then
      echo "[更新] 已更新: $old -> $new"
      git log --oneline "$old..$new" 2>/dev/null | head -5
      exec bash "$0" "$@"          # start.sh 自己也可能更新了, 重新执行一次
    else
      echo "[更新] 已是最新版本 ($new)"
    fi
  else
    echo "[更新] 拉取失败(网络不通或本地文件被改动), 继续使用当前版本 ($old)"
  fi
fi

# ---- 2. 依赖 (requirements.txt 变化时才重新安装) ----
req_sum=$(cksum requirements.txt 2>/dev/null | cut -d' ' -f1)
if [ "$(cat .deps_ok 2>/dev/null)" != "$req_sum" ]; then
  echo "[依赖] 安装中, 首次需要几分钟..."
  if command -v pkg >/dev/null 2>&1; then
    pkg update -y && pkg install -y python git libxml2 libxslt clang make openssl libffi
  fi
  pip install flask requests beautifulsoup4 pycryptodome || { echo "[依赖] 安装失败"; exit 1; }
  pip install pyquery || echo "[依赖] pyquery/lxml 安装失败: 仅 3 个依赖它的 Python 站源不可用, 其余不受影响"
  echo "$req_sum" > .deps_ok
fi

# ---- 3. 启动 ----
pkill -f web_player.py 2>/dev/null && sleep 1     # 结束旧进程, 避免端口被占
termux-wake-lock 2>/dev/null
IP=$(ifconfig 2>/dev/null | grep -Eo "inet (192|10|172)\.[0-9.]+" | head -1 | cut -d" " -f2)
echo "访问地址: http://${IP:-手机IP}:$PORT    (本机: http://localhost:$PORT)"
exec python web_player.py
