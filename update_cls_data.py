"""
财联社焦点复盘数据增量更新脚本
用法: python update_cls_data.py

流程:
  1. 读取本地 JSON 或查询 Supabase，确定"最新已有日期"
  2. 爬取财联社 API，筛选出更新的文章
  3. OCR 解析图片 → 结构化数据
  4. 修正 name/logic 字段错位（优先 mootdx 代码反查）
  5. 追加到 data/cls_review_daily.json（按日期去重，保持升序）
  6. 推送新记录到 Supabase
  7. git commit + push（触发 GitHub Actions 备份同步）
"""

import json
import os
import re
import subprocess
import sys
import time
import io
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 读取同目录下的 .env 文件（比 bat 里 set 更可靠）
_env_file = Path(__file__).parent / '.env'
if _env_file.exists():
    for _line in _env_file.read_text(encoding='utf-8').splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _, _v = _line.partition('=')
            os.environ.setdefault(_k.strip(), _v.strip())

# ── 路径 / 配置 ───────────────────────────────────────────────
ROOT      = Path(__file__).parent
DATA_FILE = ROOT / 'data' / 'cls_review_daily.json'
CLS_PY    = ROOT / 'cls_py'

MAX_PAGES = 3    # 最多爬几批（每批≈20篇，正常情况1批即可）
DELAY     = 1.5  # 请求间隔秒

sys.path.insert(0, str(CLS_PY))
import cls_review_spider as spider
import cls_parse_daily   as parser


# ── 1. 获取本地 JSON 最新日期 ─────────────────────────────────
def local_latest_date() -> str | None:
    """返回本地 JSON 中最新的 publish_date（YYYY-MM-DD），没有则 None"""
    if not DATA_FILE.exists():
        return None
    with open(DATA_FILE, encoding='utf-8') as f:
        data = json.load(f)
    if not data:
        return None
    return max(item['publish_time_str'][:10] for item in data)


# ── 2. 爬取新文章 ─────────────────────────────────────────────
def fetch_new_articles(since_date: str | None) -> list[dict]:
    """
    爬取最近的文章列表，过滤出 publish_date > since_date 的新文章。
    since_date 格式 YYYY-MM-DD，为 None 则取全部。
    """
    print(f"[爬虫] 查询文章列表（since={since_date}）...")
    reader = spider.init_ocr_reader()

    new_articles = []
    last_time = None

    for page in range(1, MAX_PAGES + 1):
        print(f"  第 {page} 批...")
        try:
            items = spider.fetch_article_list(last_article_time=last_time)
        except Exception as e:
            print(f"  列表请求失败: {e}")
            break

        if not items:
            break

        found_old = False
        for item in items:
            article = spider.parse_article(item)
            pub_date = article['publish_time_str'][:10]

            if since_date and pub_date <= since_date:
                found_old = True
                continue  # 跳过已有数据

            # 只抓焦点复盘（标题含"焦点复盘"）
            if '焦点复盘' not in article.get('title', ''):
                continue

            print(f"  [新文章] {pub_date}  {article['title'][:40]}")
            try:
                detail = spider.fetch_article_detail(article['id'])
                article = spider.parse_detail(article, detail, reader=reader)
            except Exception as e:
                print(f"  [WARN] 详情获取失败 id={article['id']}: {e}")
                continue

            new_articles.append(article)
            time.sleep(DELAY * 0.5)

        last_time = items[-1].get('article_time')

        if found_old or len(items) < 20:
            break

        time.sleep(DELAY)

    print(f"[爬虫] 共获取 {len(new_articles)} 篇新文章")
    return new_articles


# ── 3. 解析单篇文章 ──────────────────────────────────────────
def _adapt_ocr(images_ocr_raw: list) -> list:
    """
    爬虫保存的 images_ocr 是纯字符串列表；
    parse 脚本期望 [{'text': str, 'lines': list}, ...]。
    """
    result = []
    for item in images_ocr_raw:
        if isinstance(item, str):
            lines = [l.strip() for l in item.split('\n') if l.strip()]
            result.append({'text': item, 'lines': lines})
        else:
            result.append(item)
    return result


def parse_raw_article(raw: dict) -> dict:
    """将爬虫原始文章转换为结构化日报格式"""
    ocr = _adapt_ocr(raw.get('images_ocr', []))

    focus_text   = ocr[2]['text']  if len(ocr) > 2 else ''
    sector_lines = ocr[3]['lines'] if len(ocr) > 3 else []

    return {
        'title':            raw.get('title', ''),
        'publish_time_str': raw.get('publish_time_str', ''),
        'hot_topics':       parser.extract_hot_topics(raw.get('content_text', '')),
        'focus_stocks':     parser.parse_focus_stocks(focus_text),
        'limit_up_sectors': parser.parse_limit_up_sectors(sector_lines),
    }


# ── 4. 修正 name/logic 字段错位 ──────────────────────────────
def build_code_map() -> dict:
    """用 mootdx 建立 code→真实股票名 映射"""
    try:
        import pandas as pd
        from mootdx.quotes import Quotes
        client = Quotes.factory(market='std')
        df = pd.concat([client.stocks(market=0), client.stocks(market=1)], ignore_index=True)
        m = {str(r['code']).zfill(6): str(r['name']).strip().rstrip('\x00')
             for _, r in df.iterrows()}
        print(f"[mootdx] 加载 {len(m)} 条 code→name 映射")
        return m
    except Exception as e:
        print(f"[mootdx] 不可用，仅用启发式规则: {e}")
        return {}


HAS_PLUS = re.compile(r'[+＋]')
PURE_CN  = re.compile(r'^[一-鿿]{2,6}$')
# 扩展股票名模式：含 N/XD/DR 前缀、A/B 后缀、英文缩写混排等
STOCK_NAME = re.compile(
    r'^(?:'
    r'[一-鿿]{2,6}'               # 纯汉字
    r'|[一-鿿]{2,5}[A-Ca-c]'     # 汉字+A/B/C股
    r'|(?:N|XD|DR)[一-鿿]{1,5}'  # 新股/除权/DR前缀
    r'|[A-Z]{2,5}[一-鿿]{2,4}'   # 缩写前缀+汉字
    r'|[一-鿿]{2,4}[A-Z]{2,5}'   # 汉字+缩写后缀
    r')$'
)


def fix_record(rec: dict, code_map: dict) -> dict:
    """对单条个股记录做 name/logic 修正（in-place）"""
    name  = (rec.get('name')  or '').strip()
    logic = (rec.get('logic') or '').strip()
    code  = (rec.get('code')  or '').strip()

    if not name or not logic:
        return rec

    # ⓪ OCR截断残留：name为单字（如"体"来自"半导体"），logic是真实股票名
    #    无需 code_map，STOCK_NAME 匹配即可确认互换安全
    if len(name) == 1 and STOCK_NAME.match(logic):
        rec['name'], rec['logic'] = logic, name
        return rec

    # ① name 以 + 开头：模式优先（保留 N/XD/DR 等历史名），code_map 仅兜底
    if re.match(r'^[+＋]', name):
        if STOCK_NAME.match(logic):
            # 模式匹配成功 → 互换，保留历史名（N惠通、XD乐惠国等）
            rec['name'], rec['logic'] = logic, name.lstrip('+＋')
            return rec
        if code and code in code_map:
            real = code_map[code]
            if PURE_CN.match(real):   # 只接受纯汉字，避免指数代码污染
                rec['name'] = real
                rec['logic'] = name.lstrip('+＋')
                return rec

    # ② mootdx 代码反查（适用于 name 不以 + 开头的错位情形）
    if code and code in code_map:
        real = code_map[code]
        logic_match = (logic == real) or (real in logic) or (logic in real)
        name_match  = (name  == real) or (real in name)  or (name  in real)
        if logic_match and not name_match:
            rec['name'], rec['logic'] = logic, name.lstrip('+＋')
            return rec

    # ③ 原有宽泛检测（name含英文概念词，logic纯汉字）
    if (HAS_PLUS.search(name) or (re.search(r'[A-Za-z]', name)
                                   and not re.match(r'^[一-鿿]{2,5}[AB]$', name))):
        if PURE_CN.match(logic):
            rec['name'], rec['logic'] = logic, name.lstrip('+＋')

    return rec


def fix_review(review: dict, code_map: dict) -> dict:
    """修正一条日报里所有个股的 name/logic"""
    for s in review.get('focus_stocks', []):
        fix_record(s, code_map)
    for sector in review.get('limit_up_sectors', []):
        for s in sector.get('stocks', []):
            fix_record(s, code_map)
    return review


# ── 5. 追加到本地 JSON ────────────────────────────────────────
def append_to_local(new_reviews: list[dict]) -> list[dict]:
    """
    把新日报追加到 data/cls_review_daily.json。
    按 publish_date 去重，最终按日期升序排列。
    返回真正被追加的新条目。
    """
    existing = []
    if DATA_FILE.exists():
        with open(DATA_FILE, encoding='utf-8') as f:
            existing = json.load(f)

    existing_dates = {item['publish_time_str'][:10] for item in existing}
    added = [r for r in new_reviews if r['publish_time_str'][:10] not in existing_dates]

    if not added:
        print("[本地] 无新数据需要追加")
        return []

    merged = sorted(existing + added, key=lambda x: x['publish_time_str'])
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"[本地] 追加 {len(added)} 条 → {DATA_FILE}")
    return added


# ── 6. 推送到 Supabase ────────────────────────────────────────
def push_to_supabase(reviews: list[dict]):
    """推送新日报到 Supabase（复用 sync_to_supabase 的 sync_review 逻辑）"""
    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_KEY')
    if not url or not key:
        print("[Supabase] 未设置环境变量，跳过推送")
        return

    sys.path.insert(0, str(ROOT))
    import sync_to_supabase as sync_mod
    for review in reviews:
        try:
            sync_mod.sync_review(review)
        except Exception as e:
            print(f"  [ERROR] {review['publish_time_str'][:10]} 推送失败: {e}")


# ── 7. Git commit + push ──────────────────────────────────────
def git_push(added: list[dict]):
    """commit 新增数据并 push（触发 GitHub Actions 备份同步）"""
    dates = [r['publish_time_str'][:10] for r in added]
    msg = f"data: 新增财联社复盘 {', '.join(dates)}"
    try:
        subprocess.run(['git', 'add', str(DATA_FILE)], check=True, cwd=ROOT)
        subprocess.run(['git', 'commit', '-m', msg], check=True, cwd=ROOT)
        subprocess.run(['git', 'push'], check=True, cwd=ROOT)
        print(f"[Git] 已推送: {msg}")
    except subprocess.CalledProcessError as e:
        print(f"[Git] 推送失败（可忽略，数据已本地保存）: {e}")


# ── 主流程 ────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("财联社焦点复盘数据增量更新")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    # 1. 最新已有日期
    since = local_latest_date()
    print(f"[本地] 最新已有日期: {since or '（无历史数据）'}")

    # 2. 爬取新文章
    raw_articles = fetch_new_articles(since_date=since)
    if not raw_articles:
        print("\n没有新文章，退出。")
        return

    # 3. 解析
    print("\n[解析] 开始解析 OCR 数据...")
    code_map = build_code_map()
    new_reviews = []
    for raw in raw_articles:
        try:
            review = parse_raw_article(raw)
            review = fix_review(review, code_map)
            new_reviews.append(review)
            print(f"  [OK] {review['publish_time_str'][:10]}  焦点股 {len(review['focus_stocks'])} 只  板块 {len(review['limit_up_sectors'])} 个")
        except Exception as e:
            print(f"  [WARN] 解析失败 {raw.get('publish_time_str','')[:10]}: {e}")

    if not new_reviews:
        print("\n解析结果为空，退出。")
        return

    # 4. 追加本地 JSON
    added = append_to_local(new_reviews)
    if not added:
        return

    # 5. 推送 Supabase
    print("\n[Supabase] 推送新数据...")
    push_to_supabase(added)

    # 6. Git
    print("\n[Git] 提交并推送...")
    git_push(added)

    print("\n✓ 更新完成！")
    for r in added:
        print(f"  {r['publish_time_str'][:10]}  {r['title'][:50]}")


if __name__ == '__main__':
    main()
