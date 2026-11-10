# -*- coding: utf-8 -*-
"""启动时从 https://github.com/qist/tvbox 同步播放源(直播源 / 解析线路 / 采集站)并合并进内存配置。

- 启动立即读取本地缓存 qist_cache.json (保证离线也有数据),随后后台线程拉取最新版本并刷新缓存。
- 只合并网页端能直接运行的内容: lives、parses、type=1 的 JSON 采集站。
  csp_* / drpy2 的 JS 站源依赖 TVBox 运行时, 网页端跑不了, 不合并。
- 过滤成人内容关键词。
"""
import hashlib
import json
import os
import re
import threading
import time

REPO_RAW = 'raw.githubusercontent.com/qist/tvbox/master/'
# 依次尝试: 直连 -> 镜像
BASES = [
    'https://' + REPO_RAW,
    'https://ghfast.top/https://' + REPO_RAW,
    'https://gh-proxy.com/https://' + REPO_RAW,
]
# 仓库里的配置入口(不含 9918.json 等成人向配置)
CONFIG_FILES = ['jsm.json', 'js.json', '0821.json']

BLOCK = re.compile(r'成人|麻豆|(?<![A-Za-z])AV(?![A-Za-z])|禁|福利|色猫|老色|18\+|porn|sex|adult|lsp', re.I)

STATE = {'status': 'idle', 'updated': 0, 'lives': 0, 'parses': 0, 'sites': 0, 'error': ''}
_LOCK = threading.Lock()


def _fetch_text(session, path):
    last = None
    for base in BASES:
        try:
            r = session.get(base + path, timeout=15)
            if r.status_code == 200 and r.content:
                r.encoding = 'utf-8'
                return r.text, base
            last = f'HTTP {r.status_code}'
        except Exception as e:  # noqa: BLE001
            last = str(e)
    raise RuntimeError(f'{path}: {last}')


def _loads(text):
    # TVBox 配置允许整行 // 注释
    text = text.lstrip('﻿')
    text = re.sub(r'^\s*//.*$', '', text, flags=re.M)
    return json.loads(text)


def _abs(url, base):
    """./list.txt 之类相对地址转成仓库 raw 绝对地址"""
    url = (url or '').strip()
    if url.startswith('./'):
        return base + url[2:]
    return url


def collect(session):
    lives, parses, sites = [], [], []
    errors = []
    for name in CONFIG_FILES:
        try:
            text, base = _fetch_text(session, name)
            d = _loads(text)
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
            continue
        for l in d.get('lives', []) or []:
            url = _abs(l.get('url'), base)
            if not url.startswith('http') or '127.0.0.1' in url or BLOCK.search(l.get('name', '')):
                continue
            lives.append({'name': l.get('name', ''), 'type': l.get('type', 0), 'url': url,
                          'playerType': l.get('playerType', 0), 'epg': l.get('epg', ''),
                          'logo': l.get('logo', '')})
        for p in d.get('parses', []) or []:
            url = (p.get('url') or '').strip()
            if url.startswith('http') and not BLOCK.search(p.get('name', '')):
                parses.append({'name': p.get('name', ''), 'type': p.get('type', 0), 'url': url,
                               'ext': p.get('ext', 0)})
        for s in d.get('sites', []) or []:
            api = _fix_mirror(str(s.get('api', '')).strip())
            if s.get('type') == 1 and api.startswith('http') and not BLOCK.search(s.get('name', '')):
                sites.append({'type': 1, 'name': s.get('name', ''), 'api': api,
                              'searchable': s.get('searchable', 1), 'filterable': s.get('filterable', 0)})
    if not (lives or parses or sites):
        raise RuntimeError('; '.join(errors) or 'empty')
    return {'lives': lives, 'parses': parses, 'sites': sites, 'time': int(time.time())}


# 已失效的 GitHub 镜像代理前缀, 去掉后直接访问 raw.githubusercontent.com
DEAD_MIRRORS = ('https://github.moeyy.xyz/', 'https://ghp.ci/')


def _fix_mirror(url):
    for m in DEAD_MIRRORS:
        if url.startswith(m):
            url = url[len(m):]
            if url.startswith('raw.githubusercontent.com/'):
                url = 'https://' + url
            break
    return url


def _site_id(api):
    # 去掉 ?ac=list 之类和末尾斜杠, 方便与现有采集站去重
    return re.sub(r'[?#].*$', '', api).rstrip('/').lower()


def merge(config, data):
    """把 data 合并进 config(原地修改, 重复项跳过, 可重复调用)"""
    with _LOCK:
        lives = config.setdefault('lives', [])
        seen = {l.get('url') for l in lives}
        n_l = 0
        for l in data.get('lives', []):
            l['url'] = _fix_mirror(l['url'])
            if l['url'] not in seen:
                seen.add(l['url'])
                lives.append(l)
                n_l += 1

        parses = config.setdefault('parses', [])
        seen = {p.get('url') for p in parses}
        n_p = 0
        for p in data.get('parses', []):
            p['url'] = _fix_mirror(p['url'])
            if p['url'] not in seen:
                seen.add(p['url'])
                parses.append(p)
                n_p += 1

        sites = config.setdefault('sites', [])
        seen = {_site_id(str(s.get('api', ''))) for s in sites}
        n_s = 0
        for s in data.get('sites', []):
            sid = _site_id(s['api'])
            if sid in seen:
                continue
            seen.add(sid)
            s = dict(s)
            s['key'] = 'qist_' + hashlib.md5(sid.encode()).hexdigest()[:8]
            sites.append(s)
            n_s += 1
    STATE.update(lives=n_l, parses=n_p, sites=n_s)
    return n_l, n_p, n_s


def refresh(config, session, cache_path):
    """拉取最新并合并, 成功后写缓存"""
    STATE['status'] = 'syncing'
    try:
        data = collect(session)
        merge(config, data)
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        STATE.update(status='ok', updated=data['time'], error='')
        print(f"[qist] 同步完成: 新增直播源 {STATE['lives']} / 解析 {STATE['parses']} / 采集站 {STATE['sites']}")
    except Exception as e:  # noqa: BLE001
        STATE.update(status='error', error=str(e))
        print(f'[qist] 同步失败(沿用缓存): {e}')


def start(config, session, base_dir):
    """启动时调用: 先载入缓存, 再后台刷新"""
    cache_path = os.path.join(base_dir, 'qist_cache.json')
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            cached = json.load(f)
        merge(config, cached)
        STATE.update(updated=cached.get('time', 0))
        print(f"[qist] 已载入缓存: 直播源 {STATE['lives']} / 解析 {STATE['parses']} / 采集站 {STATE['sites']}")
    except Exception:  # noqa: BLE001
        pass
    t = threading.Thread(target=refresh, args=(config, session, cache_path), daemon=True)
    t.start()
    return t
