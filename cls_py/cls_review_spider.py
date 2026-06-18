"""
财联社焦点复盘爬虫
目标: https://www.cls.cn/subject/1135
"""

import hashlib
import requests
import json
import time
import os
from datetime import datetime


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "Referer": "https://www.cls.cn/subject/1135",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

SUBJECT_ID = 1135  # 焦点复盘专题ID


def make_sign(params: dict) -> str:
    """
    生成请求签名（逆向自财联社前端JS）
    算法: MD5( SHA1( sorted_qs ) )
    排序规则: 按 key 字典序（大写字母在前）
    """
    sorted_keys = sorted(params.keys())
    qs = "&".join(f"{k}={params[k]}" for k in sorted_keys)
    sha1 = hashlib.sha1(qs.encode()).hexdigest()
    return hashlib.md5(sha1.encode()).hexdigest()


def fetch_article_list(last_article_time: int = None) -> list:
    """获取焦点复盘文章列表（游标翻页）"""
    url = f"https://www.cls.cn/api/subject/{SUBJECT_ID}/article"
    params = {
        "app": "CailianpressWeb",
        "os": "web",
        "Subject_Id": str(SUBJECT_ID),
        "sv": "8.4.6",
    }
    if last_article_time is not None:
        params["last_article_time"] = str(last_article_time)

    params["sign"] = make_sign(params)

    resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errno") != 0:
        raise ValueError(f"接口返回错误: {data}")
    return data.get("data", [])


def fetch_article_detail(article_id: int) -> dict:
    """获取文章详情（解析HTML页面内嵌SSR JSON数据）"""
    import re as _re

    detail_headers = {**HEADERS, "Accept": "text/html,application/xhtml+xml,*/*", "Referer": "https://www.cls.cn/"}
    url = f"https://www.cls.cn/detail/{article_id}"
    resp = requests.get(url, headers=detail_headers, timeout=15)
    resp.raise_for_status()

    scripts = _re.findall(r"<script[^>]*>(.*?)</script>", resp.text, _re.DOTALL)
    for script in scripts:
        if "content" not in script or len(script) < 200:
            continue
        try:
            data = json.loads(script)
        except Exception:
            continue
        # 递归查找含 content 字段的文章对象
        result = _find_article_node(data)
        if result:
            return result
    raise ValueError(f"未能从页面解析到文章详情 id={article_id}")


def _find_article_node(obj, depth: int = 0) -> dict | None:
    """递归搜索含 content 字段的文章节点"""
    if depth > 12:
        return None
    if isinstance(obj, dict):
        if "content" in obj and isinstance(obj.get("content"), str) and len(obj["content"]) > 50:
            return obj
        for v in obj.values():
            found = _find_article_node(v, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_article_node(item, depth + 1)
            if found:
                return found
    return None


def parse_article(raw: dict) -> dict:
    """从原始数据中提取关键字段"""
    ts = raw.get("article_time", 0)
    article_id = raw.get("article_id")
    return {
        "id": article_id,
        "title": raw.get("article_title", ""),
        "brief": raw.get("article_brief", ""),
        "author": raw.get("article_author", ""),
        "publish_time": ts,
        "publish_time_str": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "",
        "url": f"https://www.cls.cn/detail/{article_id}",
        "read_num": raw.get("read_num", 0),
        "share_num": raw.get("share_num", 0),
        "comments_num": raw.get("comments_num", 0),
        "content": "",
        "content_text": "",
        "images": [],
        "images_count": 0,
        "images_ocr": [],
    }


def extract_images(content_html: str) -> list[str]:
    """从 HTML 正文中提取所有 img src 链接"""
    import re
    return re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', content_html)


_OCR_SLICE_H = 2000  # 超过此高度时按切片分段 OCR，避免长图识别乱码

def ocr_image_url(url: str, reader) -> str:
    """下载图片并用 RapidOCR 识别文字，返回合并后的文本"""
    from PIL import Image
    import numpy as np
    import io
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    pil_img = Image.open(io.BytesIO(resp.content)).convert("RGB")
    w, h = pil_img.size

    if h <= _OCR_SLICE_H:
        result, _ = reader(np.array(pil_img))
        return "\n".join(line[1] for line in result) if result else ""

    # 长图：按切片逐段识别后合并
    lines = []
    for y in range(0, h, _OCR_SLICE_H):
        crop = np.array(pil_img.crop((0, y, w, min(y + _OCR_SLICE_H, h))))
        result, _ = reader(crop)
        if result:
            lines.extend(line[1] for line in result)
    return "\n".join(lines)


def init_ocr_reader():
    """初始化 RapidOCR 引擎（模型内置，无需下载）"""
    from rapidocr_onnxruntime import RapidOCR
    print("  [OCR] 初始化 RapidOCR 引擎...")
    reader = RapidOCR()
    print("  [OCR] 引擎就绪")
    return reader


def parse_detail(article: dict, detail: dict, reader=None) -> dict:
    """将详情数据合并到文章字典"""
    import re
    content_html = detail.get("content", "") or detail.get("article_content", "")
    content_text = re.sub(r"<[^>]+>", "", content_html).strip()
    images = extract_images(content_html)
    images_ocr = []
    if reader and images:
        for img_url in images:
            try:
                text = ocr_image_url(img_url, reader)
                images_ocr.append(text)
            except Exception as e:
                images_ocr.append(f"[OCR失败: {e}]")
    article["content"] = content_html
    article["content_text"] = content_text
    article["images"] = images
    article["images_count"] = len(images)
    article["images_ocr"] = images_ocr
    return article


def crawl(max_pages: int = 3, delay: float = 1.5, fetch_detail: bool = True,
          ocr: bool = False) -> list[dict]:
    """
    爬取焦点复盘数据

    Args:
        max_pages:    最多爬取批次（每批约20条）
        delay:        每次请求间隔（秒）
        fetch_detail: 是否爬取每篇文章的详情正文
        ocr:          是否对正文图片做OCR识别

    Returns:
        articles: 文章列表
    """
    reader = init_ocr_reader() if ocr else None
    articles = []
    last_time = None

    for page in range(1, max_pages + 1):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 正在获取第 {page} 批...")
        try:
            items = fetch_article_list(last_article_time=last_time)
        except Exception as e:
            print(f"  请求失败: {e}")
            break

        if not items:
            print("  无更多数据，停止爬取。")
            break

        for item in items:
            article = parse_article(item)

            if fetch_detail:
                try:
                    detail = fetch_article_detail(article["id"])
                    article = parse_detail(article, detail, reader=reader)
                    ocr_info = f"  OCR{sum(len(t) for t in article['images_ocr'])}字" if ocr else ""
                    print(f"  [OK] {article['publish_time_str']}  {article['title'][:40]}  正文{len(article['content_text'])}字  图片{article['images_count']}张{ocr_info}")
                except Exception as e:
                    print(f"  [WARN] 详情获取失败 id={article['id']}: {e}")
                time.sleep(delay * 0.5)
            else:
                print(f"  [OK] {article['publish_time_str']}  {article['title'][:40]}")

            articles.append(article)

        # 游标：取本批最后一篇的时间戳
        last_time = items[-1].get("article_time")

        if len(items) < 20:
            break

        time.sleep(delay)

    return articles


def save_json(articles: list[dict], path: str):
    """保存为 JSON"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)
    print(f"\n已保存 {len(articles)} 条数据 -> {path}")


def save_csv(articles: list[dict], path: str):
    """保存为 CSV"""
    import csv

    if not articles:
        return
    keys = list(articles[0].keys())
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for row in articles:
            flat = {k: ("\n---\n".join(v) if k == "images_ocr" else "|".join(v) if isinstance(v, list) else v) for k, v in row.items()}
            writer.writerow(flat)
    print(f"已保存 CSV -> {path}")


if __name__ == "__main__":
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "财联社数据")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=== 财联社焦点复盘爬虫 ===")
    print(f"目标专题ID: {SUBJECT_ID}")
    print()

    articles = crawl(max_pages=3, delay=1.5, ocr=True)

    if articles:
        json_path = os.path.join(output_dir, f"cls_review_{timestamp}.json")
        csv_path = os.path.join(output_dir, f"cls_review_{timestamp}.csv")
        save_json(articles, json_path)
        save_csv(articles, csv_path)
    else:
        print("\n未获取到数据，请检查网络或接口是否变更。")
