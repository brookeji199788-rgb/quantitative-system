"""
财联社复盘数据 → Supabase 同步脚本
放在仓库根目录 cls_py/ 同级即可

使用方式：
  首次导入历史数据: python sync_to_supabase.py --all
  GitHub Actions 自动触发: 无需手动调用
"""

import json
import os
import sys
from pathlib import Path
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

DATA_FILE = "data/cls_review_daily.json"  # 大文件路径


def sync_review(data: dict):
    pub_date = data["publish_time_str"][:10]
    print(f"同步: {pub_date}")

    # 主表 upsert（日期唯一）
    res = sb.table("daily_review").upsert(
        {"publish_date": pub_date, "title": data.get("title", "")},
        on_conflict="publish_date"
    ).execute()
    review_id = res.data[0]["id"]

    # 子表先删后插（重跑不重复）
    # sector_stock 通过 sector_id 关联，需先收集旧 sector_id 再删
    old_sectors = sb.table("limit_up_sector").select("id").eq("review_id", review_id).execute()
    old_sector_ids = [row["id"] for row in (old_sectors.data or [])]
    if old_sector_ids:
        sb.table("sector_stock").delete().in_("sector_id", old_sector_ids).execute()

    for tbl, fk in [("hot_topic", "review_id"),
                    ("focus_stock", "review_id"),
                    ("limit_up_sector", "review_id")]:
        sb.table(tbl).delete().eq(fk, review_id).execute()

    # 热点话题
    topics = [
        {"review_id": review_id, "publish_date": pub_date,
         "topic": t.get("topic", ""), "detail": t.get("detail", ""),
         "sort_order": i}
        for i, t in enumerate(data.get("hot_topics", []))
    ]
    if topics:
        sb.table("hot_topic").insert(topics).execute()

    # 焦点股票
    stocks = [
        {"review_id": review_id, "publish_date": pub_date,
         "code": s.get("code", ""), "name": s.get("name", ""),
         "boards": s.get("boards", ""), "pct": s.get("pct", ""),
         "time": s.get("time", ""), "logic": s.get("logic", "")}
        for s in data.get("focus_stocks", [])
    ]
    if stocks:
        sb.table("focus_stock").insert(stocks).execute()

    # 涨停板块 + 板块个股
    for sector in data.get("limit_up_sectors", []):
        sec_res = sb.table("limit_up_sector").insert({
            "review_id": review_id, "publish_date": pub_date,
            "sector": sector.get("sector", ""),
            "attribution": sector.get("attribution", ""),
            "count": sector.get("count", 0)
        }).execute()
        sector_id = sec_res.data[0]["id"]

        sec_stocks = [
            {"sector_id": sector_id, "publish_date": pub_date,
             "sector": sector.get("sector", ""),
             "code": s.get("code", ""), "name": s.get("name", ""),
             "boards": s.get("boards", ""), "pct": s.get("pct", ""),
             "time": s.get("time", ""), "logic": s.get("logic", "")}
            for s in sector.get("stocks", [])
        ]
        if sec_stocks:
            sb.table("sector_stock").insert(sec_stocks).execute()

    print(f"  完成 {pub_date}")


def sync_all():
    """首次导入全部历史数据"""
    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)
    items = data if isinstance(data, list) else [data]
    print(f"共 {len(items)} 条复盘，开始导入...")
    for item in items:
        sync_review(item)
    print("全部完成！")


def sync_latest():
    """每次抓取后只同步最新一条（GitHub Actions 调用）"""
    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)
    items = data if isinstance(data, list) else [data]
    # 取最后一条（最新）
    latest = items[-1]
    sync_review(latest)


if __name__ == "__main__":
    if "--all" in sys.argv:
        sync_all()
    else:
        sync_latest()
