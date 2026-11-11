"""命令行入口：python main.py [--mode daily|backfill] [--render-only]"""
import argparse
import logging
import os
from datetime import datetime

import yaml

from app import db, pipeline, render

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = logging.getLogger("main")


def load_cfg():
    with open(os.path.join(BASE, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for key in ("db", "raw", "output", "vendor", "logs"):
        cfg["paths"][key] = os.path.join(BASE, cfg["paths"][key])
    return cfg


def main():
    parser = argparse.ArgumentParser(description="沪深300 头部ETF占比看板")
    parser.add_argument("--mode", choices=["daily", "backfill"], default="daily")
    parser.add_argument("--render-only", action="store_true", help="只重新生成页面")
    args = parser.parse_args()

    cfg = load_cfg()
    pipeline.setup_logging(cfg["paths"]["logs"])

    if args.render_only:
        conn = db.connect(cfg["paths"]["db"])
        print(render.build(conn, cfg))
        return 0

    if args.mode == "daily" and datetime.now().weekday() >= 5:
        LOG.info("今天是周末，非交易日，跳过采集")
        return 0

    conn, ok, message = pipeline.refresh(cfg, args.mode)
    out = render.build(conn, cfg)
    LOG.info("完成：%s", out)
    if message != "正常":
        LOG.warning("运行提示：%s", message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
