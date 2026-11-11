"""采集、计算与校验主流程。"""
import logging
import os
from datetime import datetime

import numpy as np
import pandas as pd

from . import db, sources

log = logging.getLogger("pipeline")

QUALITY_RANK = {"real": 0, "derived": 1, "approx": 2, "stale": 3, "gap": 4}


def setup_logging(log_dir):
    os.makedirs(log_dir, exist_ok=True)
    logfile = os.path.join(log_dir, datetime.now().strftime("%Y-%m-%d") + ".log")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(logfile, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)


def _worst(*qualities):
    return max(qualities, key=lambda q: QUALITY_RANK.get(q, 0))


def _fetch_all(cfg, conn, mode):
    ok_count, problems = 0, []

    # 1) 指数日线
    try:
        rows = sources.index_daily(
            cfg["index_secid"], cfg["history_start"].replace("-", ""))
        db.upsert(conn, "index_daily", ("date", "close"), rows)
        latest_date = rows[-1][0]
        log.info("指数日线 %d 条，最新交易日 %s 收盘 %.2f",
                 len(rows), latest_date, rows[-1][1])
        ok_count += 1
    except Exception as e:  # noqa: BLE001
        problems.append("指数日线采集失败: %s" % e)
        log.error("指数日线采集失败: %s", e)
        row = conn.execute(
            "SELECT date, close FROM index_daily ORDER BY date DESC LIMIT 1").fetchone()
        if not row:
            return 0, problems
        latest_date = row[0]

    # 2) 对比指数点位（沪深300 / 上证指数）
    for item in cfg.get("extra_indexes", []):
        try:
            rows = sources.index_daily(
                item["secid"], cfg["history_start"].replace("-", ""))
            db.upsert(conn, "index_price", ("code", "date", "close"),
                      [(item["code"], d, v) for d, v in rows])
            log.info("对比指数 %s %d 条", item["name"], len(rows))
        except Exception as e:  # noqa: BLE001
            problems.append("对比指数 %s 采集失败: %s" % (item["name"], e))
            log.error("对比指数 %s 采集失败: %s", item["name"], e)

    # 3) 指数市值锚点
    try:
        cap = sources.index_cap(cfg["index_secid"])
        close = conn.execute(
            "SELECT close FROM index_daily WHERE date=?", (latest_date,)).fetchone()
        db.upsert(conn, "index_anchor",
                  ("date", "total_cap", "float_cap", "close"),
                  [(latest_date, cap["total_cap"], cap["float_cap"],
                    close[0] if close else None)])
        log.info("指数市值锚点 %s：总市值 %.2f 万亿，流通市值 %.2f 万亿",
                 latest_date, cap["total_cap"] / 1e12, cap["float_cap"] / 1e12)
        ok_count += 1
    except Exception as e:  # noqa: BLE001
        problems.append("指数市值锚点采集失败: %s" % e)
        log.error("指数市值锚点采集失败: %s", e)

    # 3) 成分 ETF 净值 / 季度份额 / 当日快照
    max_pages = None if mode == "backfill" else 3
    for m in cfg["etf_members"]:
        code, secid = m["code"], m["secid"]
        navs = None
        try:
            navs = sources.fund_nav(code, max_pages=max_pages)
            db.upsert(conn, "etf_nav", ("date", "code", "nav"),
                      [(d, code, v) for d, v in navs])
            quarters = sources.fund_quarter_shares(code)
            db.upsert(conn, "etf_shares_q", ("date", "code", "shares"),
                      [(d, code, v) for d, v in quarters])
            ok_count += 1
        except Exception as e:  # noqa: BLE001
            problems.append("%s %s 历史数据采集失败: %s" % (code, m["name"], e))
            log.error("%s %s 历史数据采集失败: %s", code, m["name"], e)

        try:
            snap = sources.etf_snapshot(secid)
            nav_map = dict(navs) if navs else {}
            nav = nav_map.get(latest_date)
            if nav is None and nav_map:
                nav = nav_map[max(nav_map)]
            shares = snap["shares"]
            if not shares or not nav:
                raise sources.FetchError("份额或净值缺失")
            db.upsert(conn, "etf_daily",
                      ("date", "code", "name", "shares", "scale", "quality"),
                      [(latest_date, code, m["name"], shares, shares * nav, "real")])
        except Exception as e:  # noqa: BLE001
            prev = conn.execute(
                "SELECT shares, scale FROM etf_daily WHERE code=? "
                "AND date<? ORDER BY date DESC LIMIT 1", (code, latest_date)).fetchone()
            if prev:
                db.upsert(conn, "etf_daily",
                          ("date", "code", "name", "shares", "scale", "quality"),
                          [(latest_date, code, m["name"], prev[0], prev[1], "stale")])
                problems.append("%s %s 当日快照失败，沿用前值" % (code, m["name"]))
            else:
                problems.append("%s %s 当日快照失败且无前值" % (code, m["name"]))
            log.error("%s %s 当日快照失败: %s", code, m["name"], e)

    return ok_count, problems


def _member_series(conn, code, dates):
    """单只 ETF 在给定交易日上的规模序列与质量标记。"""
    nav = pd.read_sql_query(
        "SELECT date, nav FROM etf_nav WHERE code=? ORDER BY date", conn, params=(code,))
    quarters = pd.read_sql_query(
        "SELECT date, shares FROM etf_shares_q WHERE code=? ORDER BY date", conn, params=(code,))
    if nav.empty or quarters.empty:
        return None

    nav["date"] = pd.to_datetime(nav["date"])
    quarters["date"] = pd.to_datetime(quarters["date"])

    dts = pd.DatetimeIndex(dates)
    nav_s = nav.set_index("date")["nav"].reindex(dts, method="ffill")

    q = quarters.drop_duplicates("date").sort_values("date")
    knots_d = list(q["date"])
    knots_v = list(q["shares"].to_numpy(dtype=float))

    observed = pd.read_sql_query(
        "SELECT date, shares, scale, quality FROM etf_daily "
        "WHERE code=? AND quality IN ('real','stale')", conn, params=(code,))
    if not observed.empty:
        observed["date"] = pd.to_datetime(observed["date"])
        observed = observed.drop_duplicates("date").set_index("date")
        real_only = observed[observed["quality"] == "real"]
        if not real_only.empty:
            latest_real = real_only.index.max()
            latest_shares = real_only.loc[latest_real, "shares"]
            # 用最新真实份额延伸插值终点，避免当季曲线长期停留在上季末份额
            if latest_real > knots_d[-1] and latest_shares:
                knots_d.append(latest_real)
                knots_v.append(float(latest_shares))

    # 按交易日序号插值（避免长假期间出现单日跳变）
    n = len(dts)
    knot_pos = []
    for k in knots_d:
        idx_pos = int(dts.searchsorted(k, side="right")) - 1
        knot_pos.append(float(min(max(idx_pos, 0), n - 1)))
    x = np.arange(n, dtype=float)
    shares_s = pd.Series(np.interp(x, np.asarray(knot_pos), np.asarray(knots_v)),
                         index=dts)
    shares_s[dts < min(knots_d)] = np.nan

    scale = nav_s * shares_s
    quality = pd.Series("approx", index=dts)
    quality[shares_s.isna()] = "na"          # 基金尚未成立
    quality[(quality != "na") & scale.isna()] = "gap"

    if not observed.empty:
        hit = dts.isin(observed.index)
        scale[hit] = observed["scale"].reindex(dts[hit]).to_numpy()
        quality[hit] = observed["quality"].reindex(dts[hit]).to_numpy()
    return scale.fillna(0.0), shares_s.fillna(0.0), quality


def recompute(conn, cfg):
    """按锚点推算分母、合成分子，计算每日比值。"""
    idx = pd.read_sql_query("SELECT date, close FROM index_daily ORDER BY date", conn)
    if idx.empty:
        raise RuntimeError("指数日线为空，无法计算")
    idx["date"] = pd.to_datetime(idx["date"])

    anchors = pd.read_sql_query(
        "SELECT date, float_cap, close FROM index_anchor WHERE close IS NOT NULL", conn)
    if anchors.empty:
        raise RuntimeError("缺少指数市值锚点，无法计算分母")
    anchors["date"] = pd.to_datetime(anchors["date"])

    idx["denominator"] = np.nan
    idx["den_quality"] = "real"
    best = pd.Series(pd.Timedelta(days=36500), index=idx.index)
    for _, a in anchors.iterrows():
        diff = (idx["date"] - a["date"]).abs()
        mask = diff < best
        if not mask.any():
            continue
        best[mask] = diff[mask]
        idx.loc[mask, "denominator"] = a["float_cap"] * idx.loc[mask, "close"] / a["close"]
        idx.loc[mask, "den_quality"] = np.where(
            diff[mask] == pd.Timedelta(0), "real", "derived")

    dates = idx["date"]
    numerator = pd.Series(0.0, index=idx.index)
    member_quality = pd.Series("real", index=idx.index)
    member_rows = []
    for m in cfg["etf_members"]:
        series = _member_series(conn, m["code"], dates)
        if series is None:
            member_quality = pd.Series(
                [_worst(a, "gap") for a in member_quality], index=idx.index)
            continue
        scale, shares, quality = series
        numerator += scale.to_numpy()
        member_quality = pd.Series(
            [a if b == "na" else _worst(a, b)
             for a, b in zip(member_quality, quality)], index=idx.index)
        member_rows.extend(
            (d.strftime("%Y-%m-%d"), m["code"], m["name"], float(s), float(v), q)
            for d, s, v, q in zip(dates, shares, scale, quality))
    db.upsert(conn, "etf_daily",
              ("date", "code", "name", "shares", "scale", "quality"), member_rows)

    idx["numerator"] = numerator
    idx["ratio"] = np.where(idx["denominator"] > 0,
                            numerator / idx["denominator"] * 100, np.nan)
    idx["quality"] = [
        _worst(d, n) for d, n in zip(idx["den_quality"], member_quality)]

    notes = []
    for _, r in idx.iterrows():
        if pd.isna(r["ratio"]):
            notes.append("缺少分母")
            continue
        parts = []
        if r["den_quality"] == "derived":
            parts.append("分母按锚点推算")
        if r["quality"] == "approx":
            parts.append("份额季度插值")
        if r["quality"] == "stale":
            parts.append("部分ETF沿用前值")
        if r["quality"] == "gap":
            parts.append("数据缺失")
        notes.append("；".join(parts))
    idx["note"] = notes

    rows = [
        (r.date.strftime("%Y-%m-%d"), float(r.numerator), float(r.denominator),
         float(r.ratio), r.quality, r.note)
        for r in idx.itertuples() if not np.isnan(r.ratio)
    ]
    db.upsert(conn, "ratio_daily",
              ("date", "numerator", "denominator", "ratio", "quality", "note"), rows)
    log.info("比值计算完成：%d 个交易日，最新 %s = %.2f%%",
             len(rows), rows[-1][0], rows[-1][3])
    return len(rows)


def validate(conn, cfg):
    """数据校验，返回告警列表。"""
    alert_cfg = cfg.get("alert", {})
    df = pd.read_sql_query(
        "SELECT date, ratio, quality FROM ratio_daily ORDER BY date", conn)
    alerts = []
    if df.empty:
        return ["比值表为空"]

    lo = alert_cfg.get("ratio_min", 0.05)
    hi = alert_cfg.get("ratio_max", 30.0)
    out_of_range = df[(df["ratio"] < lo) | (df["ratio"] > hi)]
    if not out_of_range.empty:
        alerts.append("比值超出合理区间 [%s, %s]%%：%s" % (
            lo, hi, ",".join(out_of_range["date"].tail(5))))

    jump_limit = alert_cfg.get("jump_pct", 15.0)
    pct = df["ratio"].pct_change().abs() * 100
    jumps = df[pct > jump_limit]
    if not jumps.empty:
        alerts.append("单日比值变化超过 %s%%：%s" % (
            jump_limit, ",".join(jumps["date"].tail(5))))

    latest = df.iloc[-1]
    count = conn.execute(
        "SELECT COUNT(*) FROM etf_daily WHERE date=? AND quality='real'",
        (latest["date"],)).fetchone()[0]
    expected = len(cfg["etf_members"])
    if count < expected:
        alerts.append("%s 仅采集到 %d/%d 只ETF，标注为部分缺失"
                      % (latest["date"], count, expected))

    for a in alerts:
        log.warning("校验告警: %s", a)
    return alerts


def refresh(cfg, mode="daily"):
    started = datetime.now()
    conn = db.connect(cfg["paths"]["db"])
    sources.configure(cfg["paths"]["raw"])
    os.makedirs(cfg["paths"]["raw"], exist_ok=True)

    ok_count, problems = _fetch_all(cfg, conn, mode)
    try:
        rows = recompute(conn, cfg)
        alerts = validate(conn, cfg)
        ok = True
        message = "；".join(problems + alerts) if (problems or alerts) else "正常"
    except Exception as e:  # noqa: BLE001
        rows, ok, message = 0, False, "计算失败: %s" % e
        log.exception("计算失败")
    db.log_run(conn, started.isoformat(timespec="seconds"), mode, ok, rows, message)
    log.info("运行结束 mode=%s ok=%s rows=%d", mode, ok, rows)
    return conn, ok, message
