#!/usr/bin/env bash
# Florist-TV 一键启动 (Termux / Linux): bash start.sh
#   - 如果是 git 克隆下来的, 每次启动先自动拉取最新版本
#   - 首次运行或依赖清单变化时自动安装依赖
#   - 端口默认 8080, 可用 PORT=9000 bash start.sh 修改
#   - 不想自动更新: NO_UPDATE=1 bash start.sh
#   - 私有仓库: 先运行一次 bash start.sh token 保存访问令牌(只读即可), 令牌只存在本机 .gh_token
cd "$(dirname "$0")" || exit 1
export PORT="${PORT:-8080}"

# 保存访问令牌: bash start.sh token   (输入时不显示; 令牌文件不会被更新覆盖, 也不会进仓库)
if [ "$1" = "token" ]; then
  printf "粘贴 GitHub 令牌(github_pat_...), 回车确认: "
  read -rs t; echo
  [ -z "$t" ] && { echo "未输入, 已取消"; exit 1; }
  ( umask 077; printf '%s\n' "$t" > .gh_token )
  echo "已保存到 $(pwd)/.gh_token"
  shift
  [ "$#" -eq 0 ] && set -- run
fi
[ "$1" = "run" ] && shift
TOKEN="${GH_TOKEN:-$(head -n1 .gh_token 2>/dev/null | tr -d ' \r\n')}"

# ---- 1. 自动更新 ----
if [ -d .git ] && [ -z "$NO_UPDATE" ] && command -v git >/dev/null 2>&1; then
  old=$(git rev-parse --short HEAD 2>/dev/null)
  echo "[更新] 检查新版本..."
  gitauth=()
  [ -n "$TOKEN" ] && gitauth=(-c "http.extraHeader=Authorization: Basic $(printf 'x-access-token:%s' "$TOKEN" | base64 | tr -d '\n')")
  if timeout 40 git "${gitauth[@]}" pull --ff-only --quiet 2>/dev/null; then
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
elif [ ! -d .git ] && [ -z "$NO_UPDATE" ] && [ -z "$_UPDATED" ] && command -v curl >/dev/null 2>&1; then
  # 没装 git: 用 curl 下载仓库压缩包覆盖 (Termux 自带 curl 和 tar)
  REPO="${REPO:-Florist-xu/move}"; BR="${BRANCH:-master}"
  TGZ="https://github.com/$REPO/archive/refs/heads/$BR.tar.gz"
  tmp="${TMPDIR:-/tmp}/florist-tv-update.tgz"
  echo "[更新] 下载最新版本..."
  ok=""
  if [ -n "$TOKEN" ]; then
    # 有令牌(私有仓库): 走 GitHub API 下载, 只能直连
    if curl -fsSL --connect-timeout 8 --max-time 90 -H "Authorization: Bearer $TOKEN" \
         -o "$tmp" "https://api.github.com/repos/$REPO/tarball/$BR" && tar tzf "$tmp" >/dev/null 2>&1; then ok=1
    else
      echo "[更新] 令牌下载失败: 令牌可能过期/无权限, 或网络无法直连 GitHub"
    fi
  else
    for u in "$TGZ" "https://ghfast.top/$TGZ" "https://gh-proxy.com/$TGZ"; do
      if curl -fsSL --connect-timeout 8 --max-time 90 -o "$tmp" "$u" && tar tzf "$tmp" >/dev/null 2>&1; then ok=1; break; fi
    done
  fi
  if [ -n "$ok" ]; then
    new=$(cksum "$tmp" | cut -d' ' -f1)
    if [ "$(cat .pkg_ver 2>/dev/null)" != "$new" ]; then
      tar xzf "$tmp" --strip-components=1 && echo "$new" > .pkg_ver && echo "[更新] 已更新到最新版本"
      rm -f "$tmp"
      _UPDATED=1 exec bash "$0" "$@"
    else
      echo "[更新] 已是最新版本"
    fi
    rm -f "$tmp"
  else
    echo "[更新] 下载失败(网络不通), 继续使用当前版本"
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
ver=$(grep -m1 '^## v' CHANGELOG.md 2>/dev/null | awk '{print $2}')
echo "Florist-TV ${ver:-} (更新记录见 CHANGELOG.md)"
pkill -f web_player.py 2>/dev/null && sleep 1     # 结束旧进程, 避免端口被占
termux-wake-lock 2>/dev/null
IP=$(ifconfig 2>/dev/null | grep -Eo "inet (192|10|172)\.[0-9.]+" | head -1 | cut -d" " -f2)
echo "访问地址: http://${IP:-手机IP}:$PORT    (本机: http://localhost:$PORT)"
exec python web_player.py
