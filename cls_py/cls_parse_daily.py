"""
财联社焦点复盘结构化解析
输入: cls_review_structured.json
输出: cls_review_daily.json
"""

import json
import re
import sys
import io
import os

# ── 正则模式 ────────────────────────────────────────────────
BOARD_PAT       = re.compile(r'\d+天\d+板|\d+连板|首板')
PCT_PAT         = re.compile(r'^\d+\.?\d*%$')
TIME_PAT        = re.compile(r'^\d{1,2}:\s*\d{2}$')
CODE_PAT        = re.compile(r'^\d{6}$')
ATTR_PAT        = re.compile(r'^归因[：:](.*)$')
HEADER_KW       = {'板数', '涨跌幅', '股票名称', '涨停时间', '上涨逻辑', '名称', '市场焦点股'}
SKIP_KW         = {'财联社大涨股解读', '今日涨停分析图', '土频道', '股市频道', '股市频造'}
# OCR 有时先读到概念词再读到股票名，导致 name/logic 互换的检测模式
_CONCEPT_SIGNAL = re.compile(r'[+＋A-Za-z]')   # 含+或英文 → 大概率是概念词而非股票名
_PURE_CN_NAME   = re.compile(r'^[一-鿿]{2,6}$') # 纯汉字2-6字 → 股票名特征
# 扩展股票名模式：含 N/XD/DR 前缀、A/B 后缀、英文缩写混排等
_STOCK_NAME_PAT = re.compile(
    r'^(?:'
    r'[一-鿿]{2,6}'               # 纯汉字: 格力电器
    r'|[一-鿿]{2,5}[A-Ca-c]'     # 汉字+A/B/C股: 深康佳A
    r'|(?:N|XD|DR)[一-鿿]{1,5}'  # 新股/除权/DR前缀: N惠通 XD乐惠国 DR快克智
    r'|[A-Z]{2,5}[一-鿿]{2,4}'   # 缩写前缀+汉字: GQY视讯 IDC电源
    r'|[一-鿿]{2,4}[A-Z]{2,5}'   # 汉字+缩写后缀: 液冷IDC
    r')$'
)


def _fix_name_logic(cur: dict) -> dict:
    """OCR读序错误时 name/logic 会互换，在 CODE 行触发后做后置修正。"""
    name  = cur.get('name', '')
    logic = cur.get('logic', '')
    if not (name and logic):
        return cur
    # OCR行截断残留：name为单字（如"体"来自"半导体"），logic为真实股票名
    if len(name) == 1 and _STOCK_NAME_PAT.match(logic):
        cur['name'], cur['logic'] = logic, name
        return cur
    # 原有：name含+或英文概念词，logic为纯汉字股票名
    if _CONCEPT_SIGNAL.search(name) and _PURE_CN_NAME.match(logic):
        cur['name'], cur['logic'] = logic, name.lstrip('+＋')
        return cur
    # 扩展：name以+开头（OCR概念前缀），logic为含字母变体的股票名
    if re.match(r'^[+＋]', name) and _STOCK_NAME_PAT.match(logic):
        cur['name'], cur['logic'] = logic, name.lstrip('+＋')
    return cur


# ── 1. 主线热点 (content_text) ─────────────────────────────
TOPIC_KEYWORDS = [
    '储能', '光伏', '绿电', '算电协同', '光通信', 'CPO', 'PCB', '存储芯片',
    '电网', '化工', '农业', 'AI硬件', '算力', '半导体', '医药', '军工',
    '新能源', '消费', '锂电', '氢能', '核能', '钢铁', '贵金属', '有色',
]

def _guess_topic_name(para: str) -> str:
    for kw in TOPIC_KEYWORDS:
        if kw in para:
            return kw
    # 取第一个中文词（两字以上）
    m = re.search(r'[\u4e00-\u9fa5]{2,6}(?:产业链|概念|板块|方向|赛道)?', para)
    return m.group() if m else para[:8]

def extract_hot_topics(content_text: str) -> list[dict]:
    m = re.search(r'主线热点\n(.+?)(?:\n后市展望|\n今日涨停分析图|$)',
                  content_text, re.DOTALL)
    if not m:
        return []
    section = m.group(1).strip()
    paras = [p.strip() for p in section.split('\n') if p.strip()]
    return [{'topic': _guess_topic_name(p), 'detail': p} for p in paras]


# ── 2. 市场焦点股 (image[2] 大涨股解读) ─────────────────────
def parse_focus_stocks(ocr_text: str) -> list[dict]:
    lines = [l.strip() for l in ocr_text.split('\n') if l.strip()]
    stocks, cur = [], {}

    for line in lines:
        if any(kw in line for kw in SKIP_KW | HEADER_KW):
            continue
        if re.search(r'月\d+日财联社', line):   # 标题行
            continue

        if CODE_PAT.match(line):
            cur['code'] = line
            cur = _fix_name_logic(cur)
            if cur.get('name'):
                stocks.append(cur)
            cur = {}
        elif BOARD_PAT.search(line):
            cur['boards'] = line
        elif PCT_PAT.match(line):
            cur['pct'] = line
        elif TIME_PAT.match(line.replace(' ', '')):
            cur['time'] = line.replace(' ', '')
        elif not cur.get('name'):
            if len(line) >= 2:  # 单字是OCR截断残留，不能作为股票名
                cur['name'] = line
        else:
            cur.setdefault('logic', line)

    return stocks


# ── 3. 涨停板块 (image[3] 详细分析图) ──────────────────────
def _parse_stock_entries(stock_lines: list[str]) -> list[dict]:
    stocks, cur = [], {}
    for line in stock_lines:
        if any(kw in line for kw in SKIP_KW | HEADER_KW):
            continue
        if CODE_PAT.match(line):
            cur['code'] = line
            cur = _fix_name_logic(cur)
            if cur.get('name'):
                stocks.append(cur)
            cur = {}
        elif BOARD_PAT.search(line):
            cur['boards'] = line
        elif PCT_PAT.match(line):
            cur['pct'] = line
        elif TIME_PAT.match(line):
            cur['time'] = line
        elif not cur.get('name'):
            if len(line) >= 2:  # 单字是OCR截断残留，不能作为股票名
                cur['name'] = line
        else:
            cur.setdefault('logic', line)
    return stocks

def parse_limit_up_sectors(lines: list[str]) -> list[dict]:
    # 找到所有"归因："的位置，取其前一行作为板块名
    attr_indices = [i for i, l in enumerate(lines) if ATTR_PAT.match(l)]

    sectors = []
    for j, attr_i in enumerate(attr_indices):
        # 板块名 = 归因行之前最近的非空、非header行
        sector_name = ''
        for k in range(attr_i - 1, max(attr_i - 4, -1), -1):
            candidate = lines[k].strip()
            if candidate and candidate not in HEADER_KW and not CODE_PAT.match(candidate):
                sector_name = candidate
                break

        # 归因文本：从归因行到下一板块归因行或末尾
        next_attr = attr_indices[j + 1] if j + 1 < len(attr_indices) else len(lines)
        block = lines[attr_i:next_attr]

        attr_parts = []
        stock_lines = []
        in_attr = True
        for line in block:
            m = ATTR_PAT.match(line)
            if m:
                attr_parts.append(m.group(1))
                continue
            if in_attr:
                if line in HEADER_KW or CODE_PAT.match(line) or BOARD_PAT.search(line) or PCT_PAT.match(line):
                    in_attr = False
                else:
                    attr_parts.append(line)
                    continue
            stock_lines.append(line)

        stocks = _parse_stock_entries(stock_lines)
        sectors.append({
            'sector': sector_name,
            'attribution': ''.join(attr_parts),
            'count': len(stocks),
            'stocks': stocks,
        })

    return sectors


# ── 主处理流程 ───────────────────────────────────────────────
def process(article: dict) -> dict:
    ocr = article.get('images_ocr', [])

    focus_text = ocr[2]['text'] if len(ocr) > 2 else ''
    sector_lines = ocr[3]['lines'] if len(ocr) > 3 else []

    return {
        'title': article['title'],
        'publish_time_str': article['publish_time_str'],
        'hot_topics': extract_hot_topics(article['content_text']),
        'focus_stocks': parse_focus_stocks(focus_text),
        'limit_up_sectors': parse_limit_up_sectors(sector_lines),
    }


if __name__ == '__main__':
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), '财联社数据')
    src = os.path.join(base, 'cls_review_structured.json')
    dst = os.path.join(base, 'cls_review_daily.json')

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    with open(src, encoding='utf-8') as f:
        data = json.load(f)

    result = [process(a) for a in data]

    with open(dst, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f'已输出 {len(result)} 条 -> {dst}')

    # 打印第一条预览
    r = result[0]
    print(f'\n标题: {r["title"]}')
    print(f'时间: {r["publish_time_str"]}')
    print(f'\n主线热点 ({len(r["hot_topics"])}个):')
    for t in r['hot_topics']:
        print(f'  [{t["topic"]}] {t["detail"][:50]}...')
    print(f'\n市场焦点股 ({len(r["focus_stocks"])}只):')
    for s in r['focus_stocks']:
        print(f'  {s.get("name")} {s.get("boards","")} {s.get("pct","")} {s.get("logic","")}')
    print(f'\n涨停板块 ({len(r["limit_up_sectors"])}个板块):')
    for sec in r['limit_up_sectors']:
        names = [s.get('name','') + s.get('boards','') for s in sec['stocks']]
        print(f'  [{sec["sector"]}] 共{sec["count"]}只: {names}')
        print(f'    归因: {sec["attribution"][:60]}')
