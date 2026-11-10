import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class Spider:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.session.verify = False

    def init(self, extend=""):
        pass

    def getName(self):
        return ""

    def isVideoFormat(self, url):
        return False

    def manualVideoCheck(self):
        return False

    def destroy(self):
        self.session.close()

    def homeContent(self, filter):
        return {"class": []}

    def homeVideoContent(self):
        return {"list": []}

    def categoryContent(self, tid, pg, filter, extend):
        return {"list": [], "page": pg}

    def detailContent(self, ids):
        return {"list": []}

    def playerContent(self, flag, id, vipFlags):
        return {"parse": 0, "url": ""}

    def searchContent(self, key, quick, pg="1"):
        return {"list": [], "page": pg}

    def localProxy(self, param):
        return None

    def fetch(self, url, params=None, headers=None, cookies=None):
        try:
            r = self.session.get(url, params=params, headers=headers, cookies=cookies, timeout=15)
            return r
        except Exception as e:
            self.log(f"fetch error: {e}")
            raise

    def post(self, url, data=None, headers=None):
        try:
            r = self.session.post(url, data=data, headers=headers, timeout=15)
            return r
        except Exception as e:
            self.log(f"post error: {e}")
            raise

    def log(self, msg):
        print(f"[Spider] {msg}")

    def getProxyUrl(self):
        return "/api/spider_proxy"

    def proxyM3u8(self, params):
        return None

    def proxyMedia(self, params):
        return None

    def proxyTs(self, params):
        return None
