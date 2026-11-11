"""数据源封装：东方财富指数/ETF行情、基金净值、季度份额。"""
import json
import logging
import os
import re
import time

import requests

log = logging.getLogger("sources")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

RAW_DIR = None


class FetchError(RuntimeError):
    pass


def configure(raw_dir):
    global RAW_DIR
    RAW_DIR = raw_dir


def _cache(name, text):
    if not RAW_DIR:
        return
    try:
        day_dir = os.path.join(RAW_DIR, time.strftime("%Y-%m-%d"))
        os.makedirs(day_dir, exist_ok=True)
        with open(os.path.join(day_dir, name), "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        log.warning("原始响应落盘失败 %s: %s", name, e)


def _get(url, params=None, headers=None, retries=3, timeout=25):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=h, timeout=timeout)
            r.raise_for_status()
            return r.text
        except Exception as e:  # noqa: BLE001
            last = e
            if i < retries - 1:
                time.sleep(1.5 * (i + 1))
    raise FetchError("%s 请求失败: %s" % (url, last))


def index_daily(secid, beg="20150101"):
    """指数日线：[(date, close), ...]"""
    text = _get(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={
            "secid": secid, "klt": 101, "fqt": 0, "beg": beg, "end": "20500101",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        })
    _cache("index_%s.json" % secid.replace(".", "_"), text)
    data = json.loads(text).get("data") or {}
    out = []
    for line in data.get("klines") or []:
        parts = line.split(",")
        if len(parts) >= 3:
            out.append((parts[0], float(parts[2])))
    if not out:
        raise FetchError("指数日线为空: %s" % secid)
    return out


def index_cap(secid):
    """指数最新市值快照：总市值 / 流通市值（元）。"""
    text = _get(
        "https://push2.eastmoney.com/api/qt/stock/get",
        params={"secid": secid, "fields": "f43,f57,f58,f84,f85,f116,f117"})
    _cache("cap_%s.json" % secid.replace(".", "_"), text)
    d = json.loads(text).get("data") or {}
    total, floating = d.get("f116"), d.get("f117")
    if not floating:
        raise FetchError("指数流通市值为空: %s" % secid)
    return {"total_cap": float(total or 0), "float_cap": float(floating)}


def etf_snapshot(secid):
    """ETF 最新快照：场内份额（份）与总市值（元）。"""
    text = _get(
        "https://push2.eastmoney.com/api/qt/stock/get",
        params={"secid": secid, "fields": "f43,f57,f58,f84,f85,f116,f117"})
    _cache("etf_%s.json" % secid.replace(".", "_"), text)
    d = json.loads(text).get("data") or {}
    shares = d.get("f84") or d.get("f85")
    return {"shares": float(shares) if shares else None,
            "scale": float(d["f116"]) if d.get("f116") else None}


def fund_nav(code, max_pages=None, page_size=200):
    """基金历史单位净值：[(date, nav), ...] 升序。

    优先使用 pingzhongdata（含完整历史），失败时回退到分页净值接口。
    """
    try:
        text = _get("https://fund.eastmoney.com/pingzhongdata/%s.js" % code,
                    headers={"Referer": "https://fund.eastmoney.com/"})
        _cache("pingzhong_%s.js" % code, text)
        matched = re.search(r"Data_netWorthTrend\s*=\s*(\[.*?\]);", text)
        if matched:
            items = json.loads(matched.group(1))
            out = {}
            for it in items:
                x, y = it.get("x"), it.get("y")
                if x and y:
                    day = time.strftime("%Y-%m-%d", time.gmtime(x / 1000))
                    out[day] = float(y)
            if out:
                rows = sorted(out.items())
                if max_pages:
                    rows = rows[-max_pages * 60:]
                return rows
    except Exception as e:  # noqa: BLE001
        log.warning("%s pingzhongdata 失败，回退分页接口: %s", code, e)

    out = {}
    page = 1
    while True:
        text = _get(
            "https://api.fund.eastmoney.com/f10/lsjz",
            params={"fundCode": code, "pageIndex": page, "pageSize": page_size},
            headers={"Referer": "https://fundf10.eastmoney.com/"})
        if page == 1:
            _cache("nav_%s.json" % code, text)
        payload = json.loads(text)
        items = (payload.get("Data") or {}).get("LSJZList") or []
        if not items:
            break
        for it in items:
            day, nav = it.get("FSRQ"), it.get("DWJZ")
            if day and nav:
                try:
                    out[day] = float(nav)
                except (TypeError, ValueError):
                    continue
        total = payload.get("TotalCount") or 0
        if max_pages and page >= max_pages:
            break
        if total and page * page_size >= total:
            break
        page += 1
    if not out:
        raise FetchError("净值数据为空: %s" % code)
    return sorted(out.items())


def fund_quarter_shares(code):
    """基金季度期末总份额：[(date, shares), ...] 升序，单位：份。"""
    text = _get("https://fundf10.eastmoney.com/FundArchivesDatas.aspx",
                params={"type": "gmbd", "code": code},
                headers={"Referer": "https://fundf10.eastmoney.com/"})
    _cache("gmbd_%s.txt" % code, text)
    out = []
    for row in re.findall(r"<tr>(.*?)</tr>", text, re.S):
        cells = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 4 or not re.match(r"^\d{4}-\d{2}-\d{2}$", cells[0]):
            continue
        try:
            shares_yi = float(cells[3].replace(",", ""))
        except ValueError:
            continue
        out.append((cells[0], shares_yi * 1e8))
    if not out:
        raise FetchError("季度份额为空: %s" % code)
    return sorted(out)
