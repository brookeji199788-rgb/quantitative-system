"""
修复 cls_review_daily.json 中 6/16、6/17 的 limit_up_sectors 空数据。

步骤:
  1. 从 CLS API 重新获取最近文章列表，找到 6/16 和 6/17 的文章 ID
  2. 重新爬取文章详情 + OCR 图片
  3. 打印 image[3] 的原始 OCR 文本（诊断用）
  4. 重新解析 limit_up_sectors
  5. 原地更新 data/cls_review_daily.json
"""

import json
import sys
import io
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ROOT      = Path(__file__).parent
DATA_FILE = ROOT / 'data' / 'cls_review_daily.json'

sys.path.insert(0, str(ROOT / 'cls_py'))
import cls_review_spider as spider
import cls_parse_daily   as parser

TARGET_DATES = {'2026-06-16', '2026-06-17'}


def adapt_ocr(images_ocr_raw: list) -> list:
    result = []
    for item in images_ocr_raw:
        if isinstance(item, str):
            lines = [l.strip() for l in item.split('\n') if l.strip()]
            result.append({'text': item, 'lines': lines})
        else:
            result.append(item)
    return result


def find_target_articles() -> list[dict]:
    """从 API 查找目标日期文章，返回原始 article 列表"""
    print(f"[查询] 搜索日期: {TARGET_DATES}")
    found = []
    last_time = None

    for page in range(1, 5):
        items = spider.fetch_article_list(last_article_time=last_time)
        if not items:
            break

        for item in items:
            art = spider.parse_article(item)
            pub_date = art['publish_time_str'][:10]
            if pub_date in TARGET_DATES and '焦点复盘' in art.get('title', ''):
                found.append({'id': art['id'], 'date': pub_date, 'title': art['title']})
                print(f"  [找到] {pub_date}  id={art['id']}  {art['title'][:40]}")

        last_time = items[-1].get('article_time')
        # 如果已经到了更早的日期就停止
        last_date = spider.parse_article(items[-1])['publish_time_str'][:10]
        if last_date < '2026-06-16':
            break
        time.sleep(0.8)

    return found


def refetch_and_parse(art_info: dict, reader) -> list | None:
    """重新爬取并解析单篇文章，返回 limit_up_sectors 列表"""
    art_id   = art_info['id']
    pub_date = art_info['date']

    print(f"\n[重取] {pub_date}  id={art_id}")
    try:
        detail  = spider.fetch_article_detail(art_id)
        article = {'id': art_id, 'title': art_info['title'],
                   'publish_time_str': '', 'content': '', 'content_text': '',
                   'images': [], 'images_count': 0, 'images_ocr': []}
        article = spider.parse_detail(article, detail, reader=reader)
    except Exception as e:
        print(f"  [ERROR] 获取详情失败: {e}")
        return None

    ocr = adapt_ocr(article.get('images_ocr', []))
    print(f"  图片数量: {len(ocr)}")
    for i, o in enumerate(ocr):
        print(f"  image[{i}]: {len(o['lines'])} 行")

    sector_lines = ocr[3]['lines'] if len(ocr) > 3 else []
    if not sector_lines:
        print(f"  [WARN] image[3] 不存在或为空")
        for idx in range(len(ocr)):
            lines = ocr[idx]['lines']
            if any('归因' in l for l in lines):
                print(f"  [提示] image[{idx}] 中发现'归因'，改用此索引")
                sector_lines = lines
                break

    sectors = parser.parse_limit_up_sectors(sector_lines)
    print(f"  解析结果: {len(sectors)} 个板块")
    for s in sectors:
        print(f"    [{s['sector']}] {s['count']} 只  归因: {s['attribution'][:40]}")

    return sectors


def main():
    print("=" * 50)
    print("修复 limit_up_sectors（6/16、6/17）")
    print("=" * 50)

    # 1. 找文章
    targets = find_target_articles()
    if not targets:
        print("\n[ERROR] 未找到目标文章，请检查网络或 API")
        return

    # 2. 初始化 OCR
    reader = spider.init_ocr_reader()

    # 3. 逐篇重取并解析
    repairs = {}
    for art_info in targets:
        sectors = refetch_and_parse(art_info, reader)
        if sectors is not None:
            repairs[art_info['date']] = sectors
        time.sleep(1)

    if not repairs:
        print("\n未能修复任何条目")
        return

    # 4. 更新本地 JSON
    print("\n[更新] 写入 data/cls_review_daily.json...")
    with open(DATA_FILE, encoding='utf-8') as f:
        data = json.load(f)

    updated = 0
    for item in data:
        pub_date = item['publish_time_str'][:10]
        if pub_date in repairs:
            old_count = len(item.get('limit_up_sectors', []))
            item['limit_up_sectors'] = repairs[pub_date]
            new_count = len(item['limit_up_sectors'])
            print(f"  {pub_date}: {old_count} → {new_count} 个板块")
            updated += 1

    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 完成！更新了 {updated} 条记录")


if __name__ == '__main__':
    main()
