# -*- coding: utf-8 -*-
import os
import re
import json
from urllib.parse import quote, unquote, urlparse
from flask import Flask, render_template, request, jsonify, Response
import requests

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
app.config['TEMPLATES_AUTO_RELOAD'] = True

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'api.json')

with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    CONFIG = json.load(f)

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
})
SESSION.verify = False

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 每次启动从 github.com/qist/tvbox 同步播放源(后台线程, 失败则沿用 qist_cache.json)
import qist_sync


@app.route('/api/qist_status')
def api_qist_status():
    return jsonify(qist_sync.STATE)


@app.route('/api/qist_refresh', methods=['POST'])
def api_qist_refresh():
    qist_sync.start(CONFIG, SESSION, BASE_DIR)
    return jsonify({'ok': True})


# ---- 启动时检测直播源: 报错或没有频道的源不再显示 ----
LIVE_CHECK_PATH = os.path.join(BASE_DIR, 'live_check.json')
LIVE_STATUS = {}   # url -> {'ok': bool, 'count': int, 'err': str}
LIVE_STATE = {'status': 'idle', 'total': 0, 'done': 0, 'hidden': 0}
try:
    with open(LIVE_CHECK_PATH, 'r', encoding='utf-8') as _f:
        LIVE_STATUS.update(json.load(_f))   # 上次的结果, 启动即可先隐藏失效源
except Exception:
    pass


def _check_one_live(url):
    try:
        r = SESSION.get(url, timeout=(8, 25))
        if r.status_code != 200:
            return {'ok': False, 'count': 0, 'err': f'HTTP {r.status_code}'}
        text = _decode(r.content)
        chs = parse_m3u(text, url) or parse_txt(text)
        return {'ok': len(chs) > 0, 'count': len(chs), 'err': '' if chs else '没有频道'}
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'count': 0, 'err': str(e)[:80]}


def _run_live_check(wait_thread=None):
    from concurrent.futures import ThreadPoolExecutor
    if wait_thread is not None:
        wait_thread.join(timeout=60)    # 等 qist 同步先把源合并进来
    urls = []
    for l in CONFIG.get('lives', []):
        u = l.get('url', '')
        if u.startswith('http') and u not in urls:
            urls.append(u)
    LIVE_STATE.update(status='checking', total=len(urls), done=0)

    def work(u):
        LIVE_STATUS[u] = _check_one_live(u)
        LIVE_STATE['done'] += 1

    with ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(work, urls))
    LIVE_STATE['hidden'] = sum(1 for u in urls if not LIVE_STATUS.get(u, {}).get('ok', True))
    LIVE_STATE['status'] = 'done'
    try:
        with open(LIVE_CHECK_PATH, 'w', encoding='utf-8') as f:
            json.dump(LIVE_STATUS, f, ensure_ascii=False)
    except Exception:
        pass
    print(f"[live-check] 检测完成: 共 {len(urls)} 个, 隐藏 {LIVE_STATE['hidden']} 个")


def start_live_check(wait_thread=None):
    import threading
    t = threading.Thread(target=_run_live_check, args=(wait_thread,), daemon=True)
    t.start()
    return t


@app.route('/api/live_status')
def api_live_status():
    return jsonify(LIVE_STATE)


def site_capability(s):
    """网页端能否直接运行该站源: cms / py / tvbox(仅TVBox可用)"""
    t = s.get('type')
    api = str(s.get('api', ''))
    if t == 1 and api.startswith('http'):
        return 'cms'
    if t == 3 and api.startswith('./lib/') and api.endswith('.py'):
        return 'py'
    return 'tvbox'


def _site_by_key(key):
    for s in CONFIG.get('sites', []):
        if s.get('key') == key:
            return s
    return None


# ---- PWA ----
@app.route('/manifest.webmanifest')
def pwa_manifest():
    m = {
        'name': 'Florist-TV', 'short_name': 'Florist-TV', 'description': '直播 / 点播 / 解析',
        'start_url': '/', 'scope': '/', 'display': 'standalone', 'orientation': 'any',
        'background_color': '#1a1a2e', 'theme_color': '#16213e',
        'icons': [
            {'src': '/static/icon-192.png', 'sizes': '192x192', 'type': 'image/png'},
            {'src': '/static/icon-512.png', 'sizes': '512x512', 'type': 'image/png'},
            {'src': '/static/icon-maskable-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'},
        ],
    }
    return Response(json.dumps(m, ensure_ascii=False), mimetype='application/manifest+json')


SW_JS = r"""
const CACHE = 'florist-tv-v2';
const SHELL = ['/', '/static/icon-192.png', '/static/apple-touch-icon.png'];
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== 'GET') return;
  // 接口和视频流永远走网络
  if (url.origin === location.origin && url.pathname.startsWith('/api/')) return;
  if (req.mode === 'navigate') {
    // 页面: 优先网络(保证拿到最新版), 断网时用缓存
    e.respondWith(fetch(req).then(r => { const c = r.clone(); caches.open(CACHE).then(x => x.put('/', c)); return r; }).catch(() => caches.match('/')));
    return;
  }
  // 静态资源(图标 / CDN 上的播放器脚本): 先用缓存, 后台更新
  const isStatic = (url.origin === location.origin && url.pathname.startsWith('/static/')) || /cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com|unpkg\.com/.test(url.host);
  if (!isStatic) return;
  e.respondWith(caches.open(CACHE).then(async c => {
    const hit = await c.match(req);
    const net = fetch(req).then(r => { if (r && (r.ok || r.type === 'opaque')) c.put(req, r.clone()); return r; }).catch(() => hit);
    return hit || net;
  }));
});
"""


@app.route('/sw.js')
def pwa_sw():
    return Response(SW_JS, mimetype='application/javascript',
                    headers={'Cache-Control': 'no-cache', 'Service-Worker-Allowed': '/'})


# ---- 版本号 / 更新记录: 都来自 CHANGELOG.md, 最上面一条就是当前版本 ----
def _read_changelog():
    log = []
    try:
        with open(os.path.join(BASE_DIR, 'CHANGELOG.md'), encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                m = re.match(r'^##\s*v?([0-9][0-9.]*)\s*(?:\((.*?)\))?', line)
                if m:
                    log.append({'version': m.group(1), 'date': m.group(2) or '', 'items': []})
                elif log and line.startswith('- '):
                    log[-1]['items'].append(line[2:].strip())
    except OSError:
        pass
    return log


CHANGELOG = _read_changelog()
VERSION = CHANGELOG[0]['version'] if CHANGELOG else ''


@app.route('/api/version')
def api_version():
    return jsonify({'version': VERSION, 'log': CHANGELOG})


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/config')
def api_config():
    cfg = CONFIG  # 内存配置: api.json + 启动时从 qist/tvbox 同步的内容

    sites = []
    for s in cfg.get('sites', []):
        if site_capability(s) == 'tvbox':
            continue
        sites.append({
            'key': s.get('key', ''),
            'name': s.get('name', ''),
            'type': s.get('type', 0),
            'api': s.get('api', ''),
            'ext': s.get('ext', '') if isinstance(s.get('ext'), str) else '',
            'cap': site_capability(s),
        })

    lives = []
    for l in cfg.get('lives', []):
        st = LIVE_STATUS.get(l.get('url', ''), {})
        if st.get('ok') is False:      # 检测报错/没有频道: 不显示
            continue
        lives.append({
            'count': st.get('count', 0),
            'name': l.get('name', ''),
            'type': l.get('type', 0),
            'url': l.get('url', ''),
            'playerType': l.get('playerType', 0),
            'epg': l.get('epg', ''),
            'logo': l.get('logo', ''),
        })

    parses = []
    for p in cfg.get('parses', []):
        parses.append({
            'name': p.get('name', ''),
            'type': p.get('type', 0),
            'url': p.get('url', ''),
            'ext': p.get('ext', 0),
        })

    return jsonify({
        'sites': sites,
        'lives': lives,
        'parses': parses,
        'warningText': cfg.get('warningText', ''),
        'liveState': LIVE_STATE,
        'version': VERSION,
    })


def _decode(content):
    for enc in ('utf-8-sig', 'gb18030'):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode('utf-8', 'replace')


@app.route('/api/live_channels')
def api_live_channels():
    url = request.args.get('url', '')
    if not url:
        return jsonify({'error': 'no url'}), 400

    try:
        r = SESSION.get(url, timeout=15)
        text = _decode(r.content)
        channels = parse_m3u(text, url)
        if not channels:
            channels = parse_txt(text)
        return jsonify({'channels': merge_channels(channels)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def merge_channels(channels):
    """同组同名频道合并为一个频道，多个地址作为多条线路"""
    out, index = [], {}
    for ch in channels:
        key = (ch['group'], ch['name'])
        if key in index:
            item = out[index[key]]
            for u in ch['urls']:
                if u not in item['urls']:
                    item['urls'].append(u)
            if not item['logo'] and ch.get('logo'):
                item['logo'] = ch['logo']
        else:
            index[key] = len(out)
            out.append({'name': ch['name'], 'urls': list(ch['urls']),
                        'logo': ch.get('logo', ''), 'group': ch['group']})
    return out


def parse_txt(text):
    """TVBox txt 格式: `分组,#genre#` 换行后 `频道名,地址[#地址2]`"""
    channels = []
    group = ''
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('//') or ',' not in line:
            continue
        name, _, rest = line.partition(',')
        name, rest = name.strip(), rest.strip()
        if rest == '#genre#':
            group = name
            continue
        urls = []
        for u in rest.split('#'):
            u = u.split('$')[0].strip()
            if re.match(r'^(https?|rtmp|rtsp)://', u):
                urls.append(u)
        if name and urls:
            channels.append({'name': name, 'urls': urls, 'logo': '', 'group': group})
    return channels


def parse_m3u(text, base_url=''):
    channels = []
    lines = text.strip().splitlines()
    group_ext = ''
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('#EXTGRP:'):
            group_ext = line[8:].strip()
        if line.startswith('#EXTINF:'):
            logo = ''
            group = ''
            m = re.search(r'tvg-logo="([^"]*)"', line)
            if m:
                logo = m.group(1)
            m = re.search(r'group-title="([^"]*)"', line)
            if m:
                group = m.group(1)
            name = ''
            m = re.search(r'tvg-name="([^"]*)"', line)
            comma_idx = line.rfind(',')
            if comma_idx > 0:
                name = line[comma_idx + 1:].strip()
            if not name and m:
                name = m.group(1)
            i += 1
            while i < len(lines) and (lines[i].strip() == '' or lines[i].strip().startswith('#')):
                if lines[i].strip().startswith('#EXTGRP:'):
                    group_ext = lines[i].strip()[8:].strip()
                if lines[i].strip().startswith('#EXTINF:'):
                    break
                i += 1
            if i < len(lines):
                stream_url = lines[i].strip()
                if stream_url and not stream_url.startswith('#'):
                    channels.append({'name': name, 'urls': [stream_url], 'logo': logo,
                                     'group': group or group_ext})
                    group_ext = ''
                    i += 1
                    continue
                if stream_url.startswith('#EXTINF:'):
                    continue
        i += 1
    return channels


def rewrite_m3u8_urls(content, base_url):
    parsed = urlparse(base_url)
    base_origin = f"{parsed.scheme}://{parsed.netloc}"
    base_dir = base_url.rsplit('/', 1)[0] + '/'

    lines = content.splitlines()
    result = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            if not stripped.startswith('http') and not stripped.startswith('/'):
                line = base_origin + '/' + stripped if stripped.startswith('/') else base_dir + stripped
            elif stripped.startswith('/'):
                line = base_origin + stripped
        result.append(line)
    return '\n'.join(result)


@app.route('/api/proxy')
def api_proxy():
    url = request.args.get('url', '')
    if not url:
        return jsonify({'error': 'no url'}), 400

    url = unquote(url)
    try:
        headers = {
            'User-Agent': SESSION.headers['User-Agent'],
            'Referer': urlparse(url).scheme + '://' + urlparse(url).netloc + '/',
        }
        r = SESSION.get(url, headers=headers, timeout=15, stream=True, verify=False)

        content_type = r.headers.get('Content-Type', 'application/octet-stream')

        excluded = ['text/html', 'image/', 'font/', 'application/javascript']
        if any(x in content_type for x in excluded):
            return Response(r.content, content_type=content_type,
                          headers={'Access-Control-Allow-Origin': '*'})

        is_m3u8 = ('mpegurl' in content_type.lower() or 'm3u8' in content_type.lower() or
                    url.endswith('.m3u8') or url.endswith('.m3u'))
        if is_m3u8:
            body = r.text
            body = rewrite_m3u8_urls(body, url)
            resp = Response(body, content_type='application/vnd.apple.mpegurl')
            resp.headers['Access-Control-Allow-Origin'] = '*'
            resp.headers['Cache-Control'] = 'no-cache'
            return resp

        def generate():
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk

        resp = Response(generate(), content_type=content_type)
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Cache-Control'] = 'no-cache'
        return resp
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/parse')
def api_parse():
    url = request.args.get('url', '')
    service_idx = int(request.args.get('service', 1))

    parses = CONFIG.get('parses', [])
    if not url:
        return jsonify({'error': 'no url'}), 400

    if service_idx == 0:
        results = []
        for i, p in enumerate(parses):
            if p.get('type') == 0 and p.get('url'):
                try:
                    parse_url = p['url'] + quote(url)
                    r = SESSION.get(parse_url, timeout=8)
                    data = r.json()
                    if data.get('code') == 200 and data.get('data', {}).get('url'):
                        results.append({
                            'service': p.get('name', f'Service {i}'),
                            'url': data['data']['url'],
                        })
                except:
                    pass
        return jsonify({'results': results})
    else:
        if service_idx <= len(parses):
            p = parses[service_idx - 1]
            parse_url = p.get('url', '') + quote(url)
            try:
                r = SESSION.get(parse_url, timeout=10)
                return jsonify({'url': parse_url, 'html': r.text[:5000]})
            except Exception as e:
                return jsonify({'error': str(e)}), 500
        return jsonify({'error': 'invalid service'}), 400


def _cms_url(api, **params):
    sep = '&' if '?' in api else '?'
    q = '&'.join(f"{k}={quote(str(v))}" for k, v in params.items() if v not in (None, ''))
    return api + sep + q


def _cms_get(api, _timeout=12, **params):
    r = SESSION.get(_cms_url(api, **params), timeout=_timeout)
    r.encoding = r.apparent_encoding if not r.encoding or r.encoding.lower() == 'iso-8859-1' else r.encoding
    return json.loads(r.text.lstrip('﻿'))


def _cms_norm(d, pg):
    return {
        'class': d.get('class') or [],
        'list': d.get('list') or [],
        'page': int(d.get('page') or pg or 1),
        'pagecount': int(d.get('pagecount') or 1),
    }


@app.route('/api/cms')
def api_cms():
    """苹果CMS(type 1) 影片详情 (搜索结果点开后用)"""
    site = _site_by_key(request.args.get('key', ''))
    if not site or site_capability(site) != 'cms':
        return jsonify({'error': 'not a cms site'}), 400
    if request.args.get('method', 'detail') != 'detail':
        return jsonify({'error': 'unknown method'}), 400
    try:
        return jsonify(_cms_norm(_cms_get(site['api'], ac='detail', ids=request.args.get('ids', '')), 1))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _search_one(site, wd):
    cap = site_capability(site)
    try:
        if cap == 'cms':
            lst = _cms_get(site['api'], _timeout=6, ac='detail', wd=wd).get('list') or []
        elif cap == 'py':
            sp, err = _load_spider(site['api'], site.get('ext') if isinstance(site.get('ext'), str) else '')
            if err:
                raise RuntimeError(err)
            res = sp.searchContent(wd, False, '1') or {}
            lst = res.get('list') or []
        else:
            return []
        return [dict(v, _site=site['key'], _siteName=site.get('name', '')) for v in lst[:30]]
    except Exception:
        raise


SEARCH_DEADLINE = 10   # 秒: 超过后不再等待慢站源
SEARCH_TTL = 600       # 搜索结果缓存 10 分钟
SKIP_FAILS = 3         # 连续失败这么多次后暂时跳过
SKIP_COOL = 900        # 跳过 15 分钟后再给一次机会
SEARCH_CACHE = {}      # wd -> (time, [(site_key, name, list)])
SITE_STAT = {}         # key -> {'fail': 连续失败, 't': 上次失败时间, 'ms': 平均耗时}


def _search_sites():
    now = _time.time()
    out = []
    for s in CONFIG.get('sites', []):
        if not s.get('searchable', 1) or site_capability(s) not in ('cms', 'py'):
            continue
        st = SITE_STAT.get(s['key'])
        if st and st['fail'] >= SKIP_FAILS and now - st['t'] < SKIP_COOL:
            continue
        out.append(s)
    # 历史上快的站源先发请求
    out.sort(key=lambda s: SITE_STAT.get(s['key'], {}).get('ms', 0))
    return out


def _search_tracked(site, wd):
    t0 = _time.time()
    st = SITE_STAT.setdefault(site['key'], {'fail': 0, 't': 0, 'ms': 0})
    try:
        r = _search_one(site, wd)
    except Exception:
        st['fail'] += 1
        st['t'] = _time.time()
        return []
    ms = int((_time.time() - t0) * 1000)
    st['fail'] = 0
    st['ms'] = ms if not st['ms'] else int(st['ms'] * 0.6 + ms * 0.4)
    return r


def _search_iter(wd):
    """并发搜索, 每个站源完成就产出一组结果; 总时长受 SEARCH_DEADLINE 限制; 结果缓存"""
    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FTimeout
    hit = SEARCH_CACHE.get(wd)
    if hit and _time.time() - hit[0] < SEARCH_TTL:
        rows = hit[1]
        for i, (key, name, lst) in enumerate(rows, 1):
            yield {'key': key, 'name': name}, lst, i, len(rows)
        return
    sites = _search_sites()
    ex = ThreadPoolExecutor(max_workers=32)
    futs = {ex.submit(_search_tracked, s, wd): s for s in sites}
    done, got, finished = 0, [], True
    try:
        for f in as_completed(futs, timeout=SEARCH_DEADLINE):
            done += 1
            s = futs[f]
            lst = f.result()
            if lst:
                got.append((s['key'], s.get('name', ''), lst))
            yield s, lst, done, len(sites)
    except FTimeout:
        finished = False
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    if got:
        if len(SEARCH_CACHE) > 100:
            SEARCH_CACHE.clear()
        SEARCH_CACHE[wd] = (_time.time(), got)


@app.route('/api/search')
def api_search():
    wd = request.args.get('wd', '').strip()
    if not wd:
        return jsonify({'error': 'no wd'}), 400
    results = [{'key': s['key'], 'name': s.get('name', ''), 'list': lst}
               for s, lst, _, _ in _search_iter(wd) if lst]
    return jsonify({'results': results})


@app.route('/api/search_stream')
def api_search_stream():
    """NDJSON 流式搜索: 每个站源返回就推一行, 前端边收边显示"""
    wd = request.args.get('wd', '').strip()
    if not wd:
        return jsonify({'error': 'no wd'}), 400

    def gen():
        total = len(_search_sites())
        n = 0
        for s, lst, done, total in _search_iter(wd):
            n = done
            row = {'done': done, 'total': total}
            if lst:
                row['group'] = {'key': s['key'], 'name': s.get('name', ''), 'list': lst}
            yield json.dumps(row, ensure_ascii=False) + '\n'
        yield json.dumps({'end': True, 'done': n, 'total': total}, ensure_ascii=False) + '\n'

    return Response(gen(), mimetype='application/x-ndjson',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# ---- 封面图缓存代理 ----
IMG_DIR = os.path.join(BASE_DIR, 'img_cache')


def _safe_img_url(u):
    import ipaddress, socket
    pr = urlparse(u)
    if pr.scheme not in ('http', 'https') or not pr.hostname:
        return False
    try:
        for info in socket.getaddrinfo(pr.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
    except Exception:
        return False
    return True


IMG_MAX_BYTES = 200 * 1024 * 1024   # 封面缓存上限 200MB, 超过后删最久没用的
_IMG_WRITES = [0]


def _prune_img_cache():
    _IMG_WRITES[0] += 1
    if _IMG_WRITES[0] % 50:        # 每写入 50 张检查一次
        return
    try:
        files = [os.path.join(IMG_DIR, f) for f in os.listdir(IMG_DIR) if not f.endswith('.ct')]
        info = [(os.path.getatime(f), os.path.getsize(f), f) for f in files]
        total = sum(i[1] for i in info)
        for _, size, f in sorted(info):
            if total <= IMG_MAX_BYTES * 0.8:
                break
            for x in (f, f + '.ct'):
                try:
                    os.remove(x)
                except OSError:
                    pass
            total -= size
    except Exception:
        pass


@app.route('/api/img')
def api_img():
    import hashlib
    u = request.args.get('u', '')
    if u.startswith('//'):
        u = 'https:' + u
    if not u.startswith('http'):
        return '', 404
    h = hashlib.md5(u.encode()).hexdigest()
    path = os.path.join(IMG_DIR, h)
    hdr = {'Cache-Control': 'public, max-age=2592000'}
    if os.path.exists(path):
        with open(path, 'rb') as f:
            data = f.read()
    else:
        if not _safe_img_url(u):
            return '', 403
        try:
            r = SESSION.get(u, timeout=8, headers={'Referer': ''}, stream=True)
            ct = r.headers.get('Content-Type', '')
            if r.status_code != 200 or not ct.startswith('image/'):
                return '', 404
            data = r.raw.read(3 * 1024 * 1024 + 1, decode_content=True)
            if len(data) > 3 * 1024 * 1024:
                return Response(status=302, headers={'Location': u})
        except Exception:
            return '', 404
        os.makedirs(IMG_DIR, exist_ok=True)
        _prune_img_cache()
        with open(path, 'wb') as f:
            f.write(data)
        with open(path + '.ct', 'w') as f:
            f.write(ct)
    try:
        with open(path + '.ct') as f:
            ct = f.read()
    except Exception:
        ct = 'image/jpeg'
    return Response(data, mimetype=ct, headers=hdr)



SPIDER_CACHE = {}


def _load_spider(script, ext=''):
    cache_key = script + '|' + ext
    if cache_key in SPIDER_CACHE:
        return SPIDER_CACHE[cache_key], None

    import importlib.util
    sys_mod = __import__('sys')

    if script.startswith('./lib/'):
        script_path = os.path.join(BASE_DIR, script[2:])
    elif script.startswith('lib/'):
        script_path = os.path.join(BASE_DIR, script)
    else:
        script_path = os.path.join(BASE_DIR, 'lib', script)

    if not os.path.exists(script_path):
        return None, f'script not found: {script}'

    spec = importlib.util.spec_from_file_location('spider_module', script_path)
    mod = importlib.util.module_from_spec(spec)

    sys_path_save = sys_mod.path.copy()
    sys_mod.path.insert(0, os.path.join(BASE_DIR, 'lib'))
    sys_mod.path.insert(0, BASE_DIR)

    try:
        spec.loader.exec_module(mod)
    finally:
        sys_mod.path = sys_path_save

    spider_cls = getattr(mod, 'Spider', None)
    if not spider_cls:
        return None, 'Spider class not found'

    spider = spider_cls()
    try:
        spider.init(extend=ext)
    except Exception:
        pass
    SPIDER_CACHE[cache_key] = spider
    return spider, None


@app.route('/api/spider_call', methods=['POST'])
def api_spider_call():
    data = request.get_json()
    script = data.get('script', '')
    method = data.get('method', '')
    args = data.get('args', {})
    ext = data.get('ext', '') or ''

    if not script or not method:
        return jsonify({'error': 'missing script or method'}), 400

    if not script.endswith('.py'):
        return jsonify({'error': '仅支持 Python 爬虫'}), 400
    spider, err = _load_spider(script, ext)
    if err:
        return jsonify({'error': err}), 404

    try:
        func = getattr(spider, method, None)
        if func:
            if isinstance(args, list):
                result = func(*args)
            elif args:
                result = func(**args)
            else:
                result = func()
            list_count = len(result.get('list', [])) if isinstance(result, dict) else 0
            print(f"[spider_call] {script}.{method} -> {list_count} items")
            return jsonify({'result': result})
        return jsonify({'error': f'method {method} not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/spider_proxy')
def api_spider_proxy():
    url = request.args.get('url', '')
    if not url:
        return jsonify({'error': 'no url'}), 400

    url = unquote(url)
    try:
        headers = {
            'User-Agent': SESSION.headers['User-Agent'],
            'Referer': urlparse(url).scheme + '://' + urlparse(url).netloc + '/',
        }
        r = SESSION.get(url, headers=headers, timeout=15, stream=True, verify=False)
        content_type = r.headers.get('Content-Type', 'application/octet-stream')

        def generate():
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk

        resp = Response(generate(), content_type=content_type)
        resp.headers['Access-Control-Allow-Origin'] = '*'
        return resp
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ---- 搜索历史 / 播放历史 (SQLite 文件存储, 默认 history.db, 可用环境变量 HISTORY_DB 指定) ----
import sqlite3
import time as _time

HISTORY_DB = os.environ.get('HISTORY_DB') or os.path.join(BASE_DIR, 'history.db')


def _db():
    con = sqlite3.connect(HISTORY_DB, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _init_history_db():
    con = _db()
    try:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute("""CREATE TABLE IF NOT EXISTS search_history (
            keyword TEXT PRIMARY KEY, t INTEGER NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS play_history (
            site_key TEXT NOT NULL, vod_id TEXT NOT NULL,
            name TEXT, pic TEXT, site_name TEXT,
            li INTEGER DEFAULT 0, ei INTEGER DEFAULT 0, ep_name TEXT, flag TEXT,
            t INTEGER NOT NULL, PRIMARY KEY (site_key, vod_id))""")
        con.commit()
    finally:
        con.close()


_init_history_db()
SEARCH_LIMIT, PLAY_LIMIT = 20, 50


@app.route('/api/history/search', methods=['GET', 'POST', 'DELETE'])
def api_history_search():
    con = _db()
    try:
        if request.method == 'POST':
            kw = ((request.get_json(silent=True) or {}).get('keyword') or '').strip()
            if kw:
                con.execute('INSERT OR REPLACE INTO search_history(keyword, t) VALUES (?, ?)',
                            (kw[:100], int(_time.time() * 1000)))
                con.execute('DELETE FROM search_history WHERE keyword NOT IN '
                            '(SELECT keyword FROM search_history ORDER BY t DESC LIMIT ?)', (SEARCH_LIMIT,))
        elif request.method == 'DELETE':
            kw = request.args.get('keyword')
            if kw:
                con.execute('DELETE FROM search_history WHERE keyword = ?', (kw,))
            else:
                con.execute('DELETE FROM search_history')
        con.commit()
        rows = con.execute('SELECT keyword, t FROM search_history ORDER BY t DESC LIMIT ?', (SEARCH_LIMIT,)).fetchall()
        return jsonify({'list': [dict(r) for r in rows]})
    finally:
        con.close()


@app.route('/api/history/play', methods=['GET', 'POST', 'DELETE'])
def api_history_play():
    con = _db()
    try:
        if request.method == 'POST':
            d = request.get_json(silent=True) or {}
            if d.get('key') and d.get('id'):
                con.execute(
                    'INSERT OR REPLACE INTO play_history(site_key, vod_id, name, pic, site_name, li, ei, ep_name, flag, t) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (d['key'], str(d['id']), d.get('name', ''), d.get('pic', ''), d.get('siteName', ''),
                     int(d.get('li', 0)), int(d.get('ei', 0)), d.get('epName', ''), d.get('flag', ''),
                     int(d.get('t') or _time.time() * 1000)))
                con.execute('DELETE FROM play_history WHERE rowid NOT IN '
                            '(SELECT rowid FROM play_history ORDER BY t DESC LIMIT ?)', (PLAY_LIMIT,))
        elif request.method == 'DELETE':
            key, vid = request.args.get('key'), request.args.get('id')
            if key and vid:
                con.execute('DELETE FROM play_history WHERE site_key = ? AND vod_id = ?', (key, vid))
            else:
                con.execute('DELETE FROM play_history')
        con.commit()
        rows = con.execute('SELECT * FROM play_history ORDER BY t DESC LIMIT ?', (PLAY_LIMIT,)).fetchall()
        return jsonify({'list': [{'key': r['site_key'], 'id': r['vod_id'], 'name': r['name'], 'pic': r['pic'],
                                  'siteName': r['site_name'], 'li': r['li'], 'ei': r['ei'],
                                  'epName': r['ep_name'], 'flag': r['flag'], 't': r['t']} for r in rows]})
    finally:
        con.close()


if __name__ == '__main__':
    PORT = int(os.environ.get('PORT', 80))     # Termux/安卓无 root 不能用 80, 请设 PORT=8080
    DEBUG = os.environ.get('DEBUG') == '1'     # 开发时 DEBUG=1 (带自动重载)
    print("=" * 50)
    print(f"  Florist-TV Web Player  v{VERSION}")
    print(f"  http://localhost:{PORT}")
    print("=" * 50)
    # debug 重载器会起两个进程, 只在实际提供服务的进程里同步
    if not DEBUG or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        _t = qist_sync.start(CONFIG, SESSION, BASE_DIR)
        start_live_check(_t)
    app.run(host='0.0.0.0', port=PORT, debug=DEBUG, threaded=True)
