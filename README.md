# Florist-TV

基于 TVBox 配置的网页播放器：电脑、手机、iPad 浏览器里直接用，也可以装到安卓手机（Termux）上当小服务器，局域网内其他设备打开网址即可观看。

后端 Flask（`web_player.py`），前端单页（`templates/index.html`），当前版本和更新记录见 [CHANGELOG.md](CHANGELOG.md)，页面左上角的版本号点一下也能看。

## 功能

- **直播**：直播源列表加频道列表，按分组浏览、搜索；启动后台自动检测直播源，打不开或没有频道的源不显示；支持添加自定义直播源（TVBox 配置 JSON 地址）。
- **搜索点播**：一次搜索所有网页可用的站源，结果流式显示；按匹配程度排序，同名影片合并成一张卡片、多个片源可切换；10 分钟内重复搜索走缓存。
- **播放**：腾讯视频风格的详情和播放页；多线路、分集；记住每集播放位置并续播；线路出错自动切换；手机上左右滑动快进快退；关闭弹窗会彻底停止播放。
- **解析**：粘贴视频页面地址，选择解析线路获取播放地址；也可直接播放自定义 M3U/M3U8。
- **历史**：搜索历史、播放历史（只记录从搜索结果进入的影片），保存在本机 `history.db`。
- **多端适配**：手机、iPad、电脑各有布局；支持 PWA（见下方注意事项）。

## 在电脑上运行

需要 Python 3.10+：

```bash
pip install -r requirements.txt
python web_player.py
```

浏览器打开 `http://localhost`。端口默认 80，可用环境变量改：`PORT=8080 python web_player.py`（Windows PowerShell：`$env:PORT=8080; python web_player.py`）。

## 在安卓手机上运行（Termux）

首次安装（不需要装 git）：

```bash
mkdir -p ~/florist-tv && cd ~/florist-tv && curl -fsSL https://github.com/Florist-xu/move/archive/refs/heads/master.tar.gz | tar xz --strip-components=1 && bash start.sh
```

之后每次启动：

```bash
cd ~/florist-tv && bash start.sh
```

`start.sh` 会依次做：检查并下载最新版本、依赖变化时才安装依赖、结束旧进程、申请保持唤醒、启动服务。启动后会打印访问地址，同一个 Wi-Fi 下的手机、iPad、电脑浏览器都能打开，端口默认 8080。

| 操作 | 命令 |
| --- | --- |
| 停止 | 在 Termux 里按 `Ctrl+C`；或另开一个会话执行 `pkill -f web_player.py` |
| 换端口 | `PORT=9000 bash start.sh` |
| 暂不更新 | `NO_UPDATE=1 bash start.sh` |
| 释放唤醒锁 | `termux-wake-unlock` |

## 更新

- 没装 git：`start.sh` 用 `curl` 下载仓库压缩包覆盖，只覆盖仓库里的文件，不动 `history.db`、`img_cache/` 等本机数据。
- 装了 git 并且是克隆下来的：`start.sh` 自动 `git pull --ff-only`。
- 网络不通或下载失败时，会继续使用当前版本，不影响使用。

### 仓库改成私有后

匿名下载会失败，需要一个只读访问令牌：

1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token。
2. Repository access 只选这个仓库，Permissions → Contents 设为 Read-only，生成后立即复制（只显示一次）。
3. 手机上运行 `bash start.sh token`，粘贴令牌回车（输入时不显示）。令牌保存在项目目录的 `.gh_token`，只存在本机。

也可以临时用环境变量：`GH_TOKEN=xxx bash start.sh`。令牌更新只能直连 GitHub，国内镜像对私有仓库无效。

## 目录说明

| 路径 | 说明 |
| --- | --- |
| `web_player.py` | 后端：配置、直播源检测、搜索、代理、解析、历史、PWA 路由 |
| `templates/index.html` | 前端单页（样式和脚本都在里面） |
| `qist_sync.py` | 启动时从 qist/tvbox 同步直播源、解析线路、采集站，本地缓存 `qist_cache.json` |
| `api.json` | 基础配置（站源、直播源、解析线路） |
| `lib/`、`base/` | 站源 Python 脚本和运行基类 |
| `static/` | PWA 图标 |
| `start.sh` | 手机 / Linux 一键启动和更新 |
| `CHANGELOG.md` | 更新记录，最上面一条就是当前版本号 |

运行时生成、不进仓库的文件：`history.db`、`img_cache/`、`live_check.json`、`qist_cache.json`、`.deps_ok`、`.pkg_ver`、`.gh_token`。

## 发布新版本（开发者）

1. 在 `CHANGELOG.md` 最上面加一节，格式 `## v1.4.0 (日期)`，下面用 `- ` 列出改动。
2. 提交并推送到 `master`。
3. 手机运行 `bash start.sh` 即自动更新。

## 注意事项

**网络与访问**
- 服务没有任何登录或密码保护，同一局域网的人都能访问。不要把端口映射到公网，公共 Wi-Fi 下也不要开。
- 手机和访问设备要在同一个 Wi-Fi；手机热点、路由器开了「客户端隔离」时其他设备访问不到。
- 手机 IP 可能变化，打开前看 `start.sh` 启动时打印的地址。

**网页版的限制**
- 只能运行网页端能直接跑的站源：JSON 采集站（type 1）和本地 Python 站源（type 3 的 `.py`）。配置里依赖 TVBox 的 `spider.jar` 或 JS 引擎的站源（`csp_*`、drpy2）网页端跑不了，已经不显示。
- 站源是第三方提供的，可用性随时会变，搜不到、打不开、播放卡顿都属正常；连续失败的站源会暂时跳过 15 分钟。
- 首次搜索可能要 10 秒左右，同一关键词 10 分钟内再搜很快。

**PWA（添加到主屏幕）**
- Service Worker 只在 `https` 或 `localhost` 下生效；局域网 `http://192.168.x.x` 访问时只能「添加到主屏幕」当快捷方式，没有离线缓存，这是浏览器的限制，不是故障。
- iPad / iPhone：用 Safari，分享 → 添加到主屏幕。

**手机运行（Termux）**
- 安卓可能杀后台。建议把 Termux 的电池优化设为「不受限制」，并保留 Termux 通知；`start.sh` 已申请唤醒锁。
- 首次启动要安装依赖（`pkg` 和 `pip`），需要几分钟和一定流量，网络慢时耐心等。
- `pyquery` 安装失败不影响主体，只会让 3 个依赖它的 Python 站源不可用。
- 不要在同一个端口上同时跑两份，旧进程会被 `start.sh` 自动结束。

**隐私与内容**
- 播放历史、搜索历史只存在本机 `history.db`，不上传。
- 封面通过本机 `/api/img` 缓存代理，上限约 200MB，超过会自动清理最久没用的。
- 站源和直播源内容来自第三方，本项目不存储、不分发任何影视内容，也不解除任何站点的会员或授权限制。请只用于个人学习和自用，遵守所在地法律法规，喜欢的作品请支持正版。
- 仓库如果是公开的，里面的配置和提交记录（含图标图片、提交邮箱）别人都能看到；不想公开就改成私有并配置令牌。
