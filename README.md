# 量化系统 — 财联社数据采集与解析

## 项目简介

本项目为 A 股量化研究辅助工具，专注于**财联社（CLS）** 焦点复盘数据的自动爬取、OCR 图片识别与结构化解析，为量化选股、板块轮动分析、涨停复盘等场景提供数据支撑。

---

## 目录结构

```
量化系统/
├── cls_py/                        # 财联社爬虫与解析模块
│   ├── cls_review_spider.py       # 爬虫主程序：抓取焦点复盘文章列表与详情
│   └── cls_parse_daily.py         # 解析器：将结构化 OCR 数据提取为 JSON
└── 财联社数据/                     # 数据输出目录（自动生成）
    ├── cls_review_structured.json  # 爬虫原始输出（含 OCR 文本）
    └── cls_review_daily.json       # 解析后日报数据
```

---

## 模块说明

### `cls_py/` — 财联社爬虫文件

#### `cls_review_spider.py` — 爬虫主程序

抓取财联社 [焦点复盘专题](https://www.cls.cn/subject/1135)（专题 ID: 1135）的文章数据。

**核心功能：**
- `make_sign()` — 逆向财联社前端 JS 签名算法（MD5 + SHA1 双重哈希）
- `fetch_article_list()` — 游标翻页拉取文章列表（每批约 20 条）
- `fetch_article_detail()` — 解析页面内嵌 SSR JSON 获取正文与图片
- `ocr_image_url()` — 使用 RapidOCR 对复盘图片做离线 OCR 识别
- `crawl()` — 主爬取入口，支持多批次、带延迟、OCR 开关控制
- `save_json()` / `save_csv()` — 输出 JSON 与 CSV 两种格式

**依赖：** `requests` `rapidocr_onnxruntime` `Pillow` `numpy`

---

#### `cls_parse_daily.py` — 结构化解析器

读取爬虫输出的 `cls_review_structured.json`，提取三类核心数据写入 `cls_review_daily.json`。

**解析内容：**

| 字段 | 来源 | 说明 |
|------|------|------|
| `hot_topics` | 文章正文 | 主线热点板块及详细描述 |
| `focus_stocks` | OCR 图片 2 | 市场焦点股（含股票代码、涨幅、涨停时间、逻辑） |
| `limit_up_sectors` | OCR 图片 3 | 各涨停板块及所属个股、归因分析 |

---

## 快速开始

### 安装依赖

```bash
pip install requests rapidocr-onnxruntime Pillow numpy
```

### 运行爬虫

```bash
# 在项目根目录执行
python cls_py/cls_review_spider.py
```

默认爬取最近 3 批（约 60 篇），开启 OCR 识别，输出至 `财联社数据/` 目录。

### 运行解析器

```bash
python cls_py/cls_parse_daily.py
```

读取 `财联社数据/cls_review_structured.json`，输出 `财联社数据/cls_review_daily.json`。

---

## 输出数据示例

```json
{
  "title": "【焦点复盘】大金融、半导体等核心资产低迷...",
  "publish_time_str": "2025-01-02 17:47:49",
  "hot_topics": [
    { "topic": "消费", "detail": "..." }
  ],
  "focus_stocks": [
    { "name": "中百集团", "code": "000759", "boards": "6连板", "pct": "10.02%", "time": "13:28", "logic": "零售+微信小店" }
  ],
  "limit_up_sectors": [
    { "sector": "零售", "attribution": "...", "count": 3, "stocks": [...] }
  ]
}
```

---

## 注意事项

- 爬虫内置限速（默认 1.5 秒间隔），请勿大幅缩短以免触发封禁
- `财联社数据/` 目录下的数据文件已加入 `.gitignore`，不纳入版本控制
- OCR 依赖离线模型，首次运行会自动下载 ONNX 模型文件（约 20MB）

---

## License

MIT
