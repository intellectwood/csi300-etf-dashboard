"""生成单文件静态看板（图表库内联，离线可看）。"""
import json
import logging
import os
from datetime import datetime

import pandas as pd

log = logging.getLogger("render")

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>沪深300 头部ETF规模占流通市值比例</title>
<style>
  :root{
    --bg:#0a0e1a; --bg2:#0f1526; --card:#141b2e; --line:#232c44;
    --txt:#e8ecf6; --sub:#8b97b0; --accent:#4da3ff; --up:#ff6b6b; --down:#31c48d;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:radial-gradient(1200px 600px at 20% -10%,#16224a 0%,var(--bg) 55%);
       color:var(--txt);font-family:"PingFang SC","Microsoft YaHei",-apple-system,Segoe UI,sans-serif;
       min-height:100vh;padding:28px 32px 56px}
  .wrap{max-width:1240px;margin:0 auto}
  header{display:flex;justify-content:space-between;align-items:flex-end;
         gap:16px;flex-wrap:wrap;margin-bottom:22px}
  h1{font-size:21px;font-weight:600;letter-spacing:.4px}
  h1 span{color:var(--accent)}
  .meta{color:var(--sub);font-size:12.5px;line-height:1.7;text-align:right}
  .meta b{color:var(--txt);font-weight:600}
  .kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px}
  .card{background:linear-gradient(180deg,var(--card),var(--bg2));
        border:1px solid var(--line);border-radius:14px;padding:16px 18px}
  .card .k{color:var(--sub);font-size:12.5px;letter-spacing:.3px}
  .card .v{font-size:26px;font-weight:600;margin-top:8px;font-variant-numeric:tabular-nums}
  .card .v small{font-size:14px;color:var(--sub);font-weight:400;margin-left:4px}
  .card .s{font-size:12px;color:var(--sub);margin-top:6px}
  .up{color:var(--up)} .down{color:var(--down)}
  .panel{background:linear-gradient(180deg,var(--card),var(--bg2));
         border:1px solid var(--line);border-radius:14px}
  .chart-head{display:flex;justify-content:space-between;align-items:center;
              padding:16px 20px 4px;flex-wrap:wrap;gap:8px}
  .chart-head h2{font-size:14px;font-weight:600;color:#cfd8ea}
  .legend{font-size:12px;color:var(--sub);display:flex;gap:14px;flex-wrap:wrap}
  .legend .lg{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
  .legend .lg i{width:12px;height:3px;border-radius:2px;display:inline-block}
  #chart{height:420px;width:100%}
  .grid{display:grid;grid-template-columns:1.25fr 1fr;gap:14px;margin-top:18px}
  .panel h3{font-size:14px;font-weight:600;padding:16px 20px 10px;color:#cfd8ea}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{padding:9px 20px;text-align:right;border-top:1px solid var(--line);
        font-variant-numeric:tabular-nums}
  th{color:var(--sub);font-weight:500;font-size:12px;border-top:none}
  th:first-child,td:first-child{text-align:left}
  tr:hover td{background:#182136}
  .tag{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;
       border:1px solid var(--line);color:var(--sub)}
  .tag.real{color:#31c48d;border-color:#1d6b4c}
  .tag.approx{color:#e0a800;border-color:#6b551d}
  .tag.derived{color:#4da3ff;border-color:#1f4d7a}
  .tag.stale{color:#ff8a3d;border-color:#7a4420}
  .tag.gap{color:#ff6b6b;border-color:#7a2020}
  .notes{padding:0 20px 18px;font-size:12.5px;color:var(--sub);line-height:1.9}
  .notes b{color:#cfd8ea;font-weight:600}
  .badge{display:inline-block;margin:2px 6px 2px 0;padding:2px 9px;border-radius:6px;
         background:#182136;border:1px solid var(--line);font-size:11.5px;color:var(--sub)}
  .alert{color:#ffb454}
  footer{margin-top:22px;color:#5c6884;font-size:11.5px;text-align:center;line-height:1.8}
  @media (max-width:980px){
    .kpis{grid-template-columns:repeat(2,1fr)}
    .grid{grid-template-columns:1fr}
    body{padding:18px 14px 40px}
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>沪深300 <span>头部ETF规模</span> 占指数流通市值比例</h1>
      <div class="meta" style="text-align:left;margin-top:6px">
        口径：锁定 10 只头部沪深300ETF 合计规模 ÷ 沪深300指数流通市值
      </div>
    </div>
    <div class="meta">
      最新交易日 <b id="latestDate">-</b><br>
      页面生成 <b id="genTime">-</b>
    </div>
  </header>

  <section class="kpis">
    <div class="card">
      <div class="k">占流通市值比例</div>
      <div class="v" id="kpiRatio">-<small>%</small></div>
      <div class="s" id="kpiRatioChg">-</div>
    </div>
    <div class="card">
      <div class="k">头部ETF合计规模</div>
      <div class="v" id="kpiNum">-<small>亿元</small></div>
      <div class="s">10 只锁定名单合计</div>
    </div>
    <div class="card">
      <div class="k">沪深300流通市值</div>
      <div class="v" id="kpiDen">-<small>万亿元</small></div>
      <div class="s" id="kpiDenSrc">-</div>
    </div>
    <div class="card">
      <div class="k">区间</div>
      <div class="v" id="kpiRange">-</div>
      <div class="s" id="kpiDays">-</div>
    </div>
  </section>

  <section class="panel">
    <div class="chart-head">
      <h2>日频比例曲线</h2>
      <div class="legend" id="legend"></div>
    </div>
    <div id="chart"></div>
  </section>

  <section class="grid">
    <div class="panel">
      <h3>头部 ETF 明细（最新交易日）</h3>
      <table>
        <thead><tr>
          <th>代码 / 名称</th><th>规模(亿元)</th><th>占分子</th><th>占流通市值</th><th>较上日</th><th>质量</th>
        </tr></thead>
        <tbody id="memberBody"></tbody>
      </table>
    </div>
    <div class="panel">
      <h3>口径与数据质量</h3>
      <div class="notes" id="notes"></div>
    </div>
  </section>

  <footer>
    数据来源：东方财富（指数行情 / ETF行情 / 基金净值 / 季度份额），仅供研究参考，不构成投资建议。<br>
    分母在非锚点日按指数点位比例推算；历史ETF规模由季度份额插值 × 每日净值近似。
  </footer>
</div>

<script>__ECHARTS__</script>
<script>
const D = __DATA__;

function fmt(n, d){ return n.toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d}); }

document.getElementById('latestDate').textContent = D.latest.date;
document.getElementById('genTime').textContent = D.generated_at;

const r = D.latest.ratio, chg = D.latest.chg, chgPct = D.latest.chg_pct;
document.getElementById('kpiRatio').innerHTML = fmt(r,2) + '<small>%</small>';
const cls = chg >= 0 ? 'up' : 'down';
const arrow = chg >= 0 ? '▲' : '▼';
document.getElementById('kpiRatioChg').innerHTML =
  '<span class="' + cls + '">' + arrow + ' ' + fmt(Math.abs(chg),3) + 'pp (' +
  fmt(Math.abs(chgPct),2) + '%)</span> 较上一交易日';
document.getElementById('kpiNum').innerHTML = fmt(D.latest.numerator/1e8,0) + '<small>亿元</small>';
document.getElementById('kpiDen').innerHTML = fmt(D.latest.denominator/1e12,2) + '<small>万亿元</small>';
document.getElementById('kpiDenSrc').textContent = D.latest.den_quality === 'real'
  ? '官方快照（' + D.anchor.date + '）' : '锚点推算（锚点 ' + D.anchor.date + '）';
document.getElementById('kpiRange').innerHTML =
  '<span style="font-size:19px">' + fmt(D.latest.min_ratio,2) + '% ~ ' + fmt(D.latest.max_ratio,2) + '%</span>';
document.getElementById('kpiDays').textContent = '历史最低 ~ 最高 · 共 ' + D.series.length + ' 个交易日';

const rows = D.members.map(function(m){
  const chgTxt = m.chg === null ? '-' :
    (m.chg >= 0 ? '<span class="up">+' : '<span class="down">') + fmt(m.chg,1) + '</span>';
  return '<tr><td>' + m.code + ' <span style="color:#8b97b0">' + m.name + '</span></td>' +
    '<td>' + fmt(m.scale/1e8,1) + '</td>' +
    '<td>' + fmt(m.share_of_num,2) + '%</td>' +
    '<td>' + fmt(m.share_of_den,3) + '%</td>' +
    '<td>' + chgTxt + '</td>' +
    '<td><span class="tag ' + m.quality + '">' + m.quality + '</span></td></tr>';
}).join('');
document.getElementById('memberBody').innerHTML = rows;

document.getElementById('notes').innerHTML = D.notes.map(function(n){
  return '<div>' + n + '</div>';
}).join('') +
  '<div style="margin-top:12px">' + D.quality_badges + '</div>' +
  (D.alerts ? '<div class="alert" style="margin-top:12px">' + D.alerts + '</div>' : '');

const chart = echarts.init(document.getElementById('chart'), null, {renderer:'canvas'});
const dates = D.series.map(function(x){ return x[0]; });
const values = D.series.map(function(x){ return x[1]; });
const quals = D.series.map(function(x){ return x[2]; });

const idxSeries = D.indexes.map(function(ix, k){
  var color = k === 0 ? '#ffb454' : '#a78bfa';
  return {
    name: ix.name, type: 'line', yAxisIndex: 1, data: ix.values,
    showSymbol: false, connectNulls: true,
    lineStyle: {width: 1.1, color: color, opacity: .85},
    itemStyle: {color: color}
  };
});

(function renderLegend(){
  var items = [{name:'占比（左轴 %）', color:'#4da3ff'}].concat(
    D.indexes.map(function(ix, k){ return {name: ix.name + '（右轴 点）',
      color: k === 0 ? '#ffb454' : '#a78bfa'}; }));
  document.getElementById('legend').innerHTML = items.map(function(it){
    return '<span class="lg"><i style="background:' + it.color + '"></i>' +
           it.name + '</span>';
  }).join('');
})();

chart.setOption({
  backgroundColor: 'transparent',
  color: ['#4da3ff', '#ffb454', '#a78bfa'],
  legend: {show: false},
  grid: {left: 62, right: 66, top: 22, bottom: 60},
  tooltip: {
    trigger: 'axis',
    backgroundColor: 'rgba(20,27,46,.96)',
    borderColor: '#2b3550',
    textStyle: {color: '#e8ecf6', fontSize: 12},
    formatter: function(ps){
      const i = ps[0].dataIndex;
      const s = D.series[i];
      var html = '<b>' + s[0] + '</b><br>' +
        '比例：<b>' + fmt(s[1],2) + '%</b><br>' +
        '分子：' + fmt(s[3]/1e8,1) + ' 亿元<br>' +
        '分母：' + fmt(s[4]/1e12,2) + ' 万亿元<br>';
      D.indexes.forEach(function(ix){
        var v = ix.values[i];
        html += ix.name + '：' + (v === null || v === undefined ? '-' : fmt(v,2)) + '<br>';
      });
      return html + '质量：' + s[2];
    }
  },
  xAxis: {
    type: 'category',
    data: dates,
    boundaryGap: false,
    axisLine: {lineStyle: {color: '#2b3550'}},
    axisLabel: {color: '#8b97b0', fontSize: 11,
      formatter: function(v){ return v.slice(0, 7); }},
    axisTick: {show: false}
  },
  yAxis: [{
    type: 'value',
    name: '%',
    nameTextStyle: {color: '#8b97b0'},
    axisLabel: {color: '#8b97b0', fontSize: 11,
      formatter: function(v){ return v.toFixed(2); }},
    splitLine: {lineStyle: {color: 'rgba(43,53,80,.55)'}}
  }, {
    type: 'value',
    name: '点',
    position: 'right',
    scale: true,
    nameTextStyle: {color: '#8b97b0'},
    axisLabel: {color: '#8b97b0', fontSize: 11,
      formatter: function(v){ return v >= 1000 ? (v / 1000).toFixed(1) + 'k' : v.toFixed(0); }},
    axisLine: {show: false},
    splitLine: {show: false}
  }],
  dataZoom: [
    {type: 'inside', start: 0, end: 100},
    {type: 'slider', height: 22, bottom: 24, borderColor: '#2b3550',
     backgroundColor: '#101728', fillerColor: 'rgba(77,163,255,.14)',
     handleStyle: {color: '#4da3ff'}, textStyle: {color: '#8b97b0', fontSize: 10}}
  ],
  series: [{
    name: '比例',
    type: 'line',
    data: values,
    showSymbol: false,
    symbolSize: 5,
    lineStyle: {width: 1.6, color: '#4da3ff'},
    itemStyle: {color: '#4da3ff'},
    areaStyle: {
      color: new echarts.graphic.LinearGradient(0,0,0,1,[
        {offset:0, color:'rgba(77,163,255,.32)'},
        {offset:1, color:'rgba(77,163,255,0)'}
      ])
    },
    markPoint: {
      symbol: 'circle', symbolSize: 9,
      itemStyle: {color: '#ffd166', borderColor: '#0a0e1a', borderWidth: 2},
      label: {show: true, position: 'top', color: '#ffd166', fontSize: 11,
              formatter: fmt(values[values.length-1], 2) + '%'},
      data: [{coord: [dates[dates.length-1], values[values.length-1]]}]
    }
  }].concat(idxSeries)
});
window.addEventListener('resize', function(){ chart.resize(); });
</script>
</body>
</html>
"""


def _fmt_quality_badges(counts):
    order = ["real", "derived", "approx", "stale", "gap"]
    label = {"real": "真实采集", "derived": "分母推算", "approx": "份额插值",
             "stale": "沿用前值", "gap": "缺失"}
    parts = []
    for q in order:
        if counts.get(q):
            parts.append('<span class="badge">%s %d</span>' % (label[q], counts[q]))
    return "".join(parts) or '<span class="badge">无数据</span>'


def build(conn, cfg):
    ratios = pd.read_sql_query(
        "SELECT date, numerator, denominator, ratio, quality, note "
        "FROM ratio_daily ORDER BY date", conn)
    if ratios.empty:
        raise RuntimeError("比值表为空，先运行采集")

    latest = ratios.iloc[-1]
    latest_ratio = float(latest["ratio"])
    prev_ratio = float(ratios.iloc[-2]["ratio"]) if len(ratios) > 1 else latest_ratio
    chg = latest_ratio - prev_ratio
    chg_pct = (chg / prev_ratio * 100) if prev_ratio else 0.0

    anchor_row = conn.execute(
        "SELECT date, total_cap, float_cap FROM index_anchor ORDER BY date DESC LIMIT 1"
    ).fetchone()
    anchor = {"date": anchor_row[0] if anchor_row else "-",
              "total_cap": anchor_row[1] if anchor_row else 0,
              "float_cap": anchor_row[2] if anchor_row else 0}

    members = []
    mdf = pd.read_sql_query(
        "SELECT date, code, name, scale, quality FROM etf_daily "
        "WHERE date=? ORDER BY scale DESC", conn, params=(latest["date"],))
    dates = sorted(ratios["date"].tolist())
    prev_date = dates[-2] if len(dates) > 1 else None
    prev_scale = {}
    if prev_date:
        p = pd.read_sql_query(
            "SELECT code, scale FROM etf_daily WHERE date=?", conn, params=(prev_date,))
        prev_scale = dict(zip(p["code"], p["scale"]))
    for r in mdf.itertuples():
        ps = prev_scale.get(r.code)
        members.append({
            "code": r.code,
            "name": r.name,
            "scale": float(r.scale),
            "quality": r.quality,
            "share_of_num": float(r.scale / latest["numerator"] * 100) if latest["numerator"] else 0,
            "share_of_den": float(r.scale / latest["denominator"] * 100) if latest["denominator"] else 0,
            "chg": (float(r.scale - ps) / 1e8) if ps else None,
        })

    counts = ratios["quality"].value_counts().to_dict()
    gap_dates = ratios[ratios["quality"].isin(["gap", "stale"])]["date"].tolist()
    gap_line = ("存在缺口/沿用前值日期：%s" % "、".join(gap_dates[-5:])) if gap_dates else "无缺口日期"

    notes = [
        "<b>分子</b>：锁定 10 只头部沪深300ETF 的场内规模合计（份额 × 单位净值），"
        "名单不随每日排名变化，避免曲线失真。",
        "<b>分母</b>：沪深300指数流通市值。官方快照日标记 <span class='tag real'>real</span>，"
        "其余日期按「锚点市值 × 当日点位 ÷ 锚点点位」推算，标记 <span class='tag derived'>derived</span>。"
        "锚点逐日累积，近年数据会越来越准；越早的历史段因股本扩张存在分母高估、比例偏低的风险。",
        "<b>历史段</b>：ETF 逐日份额免费源不可得，历史规模由季度末份额线性插值 × 当日单位净值近似，"
        "标记 <span class='tag approx'>approx</span>。",
        "<b>口径说明</b>：仅统计 ETF 本体，不含联接基金与场外指数基金，避免重复计算；"
        "分母为交易所流通市值口径，与中证自由流通市值口径存在差异。",
        "<b>数据状态</b>：%s。最新锚点：%s（流通市值 %.2f 万亿元，总市值 %.2f 万亿元）。"
        % (gap_line, anchor["date"], anchor["float_cap"] / 1e12, anchor["total_cap"] / 1e12),
    ]

    last_run = conn.execute(
        "SELECT run_at, message, ok FROM job_run ORDER BY id DESC LIMIT 1").fetchone()
    alerts = ""
    if last_run and last_run[1] and last_run[1] != "正常":
        alerts = "最近一次运行提示：" + last_run[1]

    series = [
        [r.date, round(float(r.ratio), 4), r.quality,
         round(float(r.numerator)), round(float(r.denominator))]
        for r in ratios.itertuples()
    ]

    prices = pd.read_sql_query("SELECT code, date, close FROM index_price", conn)
    price_map = {}
    for code, sub in prices.groupby("code"):
        price_map[str(code)] = dict(zip(sub["date"], sub["close"]))
    indexes = []
    for item in cfg.get("extra_indexes", []):
        m = price_map.get(item["code"], {})
        indexes.append({
            "name": item["name"],
            "values": [round(float(m[d]), 2) if d in m else None
                       for d in ratios["date"]],
        })

    data = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "latest": {
            "date": latest["date"],
            "ratio": float(latest["ratio"]),
            "numerator": float(latest["numerator"]),
            "denominator": float(latest["denominator"]),
            "chg": round(chg, 4),
            "chg_pct": round(chg_pct, 3),
            "den_quality": "real" if anchor["date"] == latest["date"] else "derived",
            "min_ratio": float(ratios["ratio"].min()),
            "max_ratio": float(ratios["ratio"].max()),
        },
        "anchor": anchor,
        "series": series,
        "indexes": indexes,
        "members": members,
        "notes": notes,
        "quality_badges": _fmt_quality_badges(counts),
        "alerts": alerts,
    }

    vendor_path = cfg["paths"]["vendor"]
    if not os.path.exists(vendor_path):
        raise RuntimeError("缺少图表库文件：%s" % vendor_path)
    with open(vendor_path, "r", encoding="utf-8") as f:
        echarts_js = f.read()

    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = (TEMPLATE
            .replace("__ECHARTS__", echarts_js)
            .replace("__DATA__", payload))

    out_path = cfg["paths"]["output"]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("页面已生成：%s（%d 个交易日）", out_path, len(series))
    return out_path
