"""
一次性修复 data/cls_review_daily.json 中 name 以 "+" 开头的残留记录，
并修正解析阶段把概念词（IDC电源/液冷IDC/AI应用等）误作股票名的情况。

两轮修复：
  第一轮  name 以 + 开头的16条错位记录
          优先级：STOCK_NAME模式 > code_map兜底
          说明：模式优先可保留历史名称（N惠通、XD乐惠国、GQY视讯等），
                code_map 仅在模式匹配失败（如 logic="PCB"）时用于兜底，
                且只接受纯汉字结果（避免指数代码污染，如 000016=上证50）

  第二轮  混合缩写概念词被误设为 name 的情况（IDC电源、液冷IDC、AI应用等）
          条件：name 匹配 MIXED_NAME + 无 N/XD/DR 前缀 + code_map 真实名为纯汉字
          说明：XD/DR/N 前缀是除权/新股的合法历史名称，不得改写

用法: python repair_name_logic.py
"""

import json
import re
import sys
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ROOT      = Path(__file__).parent
DATA_FILE = ROOT / 'data' / 'cls_review_daily.json'

sys.path.insert(0, str(ROOT / 'cls_py'))

# ── 正则 ─────────────────────────────────────────────────────
HAS_PLUS     = re.compile(r'[+＋]')
PURE_CN      = re.compile(r'^[一-鿿]{2,6}$')
STOCK_NAME   = re.compile(         # 扩展股票名模式
    r'^(?:'
    r'[一-鿿]{2,6}'                # 纯汉字
    r'|[一-鿿]{2,5}[A-Ca-c]'      # 汉字+A/B/C股
    r'|(?:N|XD|DR)[一-鿿]{1,5}'   # 新股/除权/DR前缀（历史名称）
    r'|[A-Z]{2,5}[一-鿿]{2,4}'    # 缩写前缀+汉字: GQY视讯 IDC电源
    r'|[一-鿿]{2,4}[A-Z]{2,5}'    # 汉字+缩写后缀: 液冷IDC
    r')$'
)
# 混合缩写模式（纯大写+汉字 或 汉字+纯大写，可能是概念词）
MIXED_NAME   = re.compile(r'^(?:[A-Z]{2,5}[一-鿿]{2,4}|[一-鿿]{2,4}[A-Z]{2,5})$')
# 合法历史前缀（N/XD/DR，第二轮跳过）
STOCK_PREFIX = re.compile(r'^(?:N|XD|DR)')


def build_code_map() -> dict:
    try:
        import pandas as pd
        from mootdx.quotes import Quotes
        client = Quotes.factory(market='std')
        df = pd.concat([client.stocks(market=0), client.stocks(market=1)], ignore_index=True)
        m = {str(r['code']).zfill(6): str(r['name']).strip().rstrip('\x00')
             for _, r in df.iterrows()}
        print(f'[mootdx] 加载 {len(m)} 条 code→name 映射')
        return m
    except Exception as e:
        print(f'[mootdx] 不可用，退回纯规则模式: {e}')
        return {}


# ── 第一轮：name 以 + 开头 ──────────────────────────────────
def fix_plus_record(rec: dict, code_map: dict) -> tuple[dict, str]:
    name  = (rec.get('name')  or '').strip()
    logic = (rec.get('logic') or '').strip()
    code  = (rec.get('code')  or '').strip()

    if not name or not logic:
        return rec, ''
    if not (name.startswith('+') or name.startswith('＋')):
        return rec, ''

    # ① 模式优先（保留历史名称：N惠通、XD乐惠国、GQY视讯等）
    if STOCK_NAME.match(logic):
        rec['name'], rec['logic'] = logic, name.lstrip('+＋')
        return rec, f'①模式匹配 name={rec["name"]}, logic={rec["logic"]}'

    # ② code_map 兜底（仅接受纯汉字结果，避免指数代码污染）
    if code and code in code_map:
        real = code_map[code]
        if PURE_CN.match(real):
            rec['name'] = real
            rec['logic'] = name.lstrip('+＋')
            return rec, f'②code_map注入 name={rec["name"]}, logic={rec["logic"]}'

    return rec, ''  # 无法自动修复


# ── 第二轮：混合缩写概念词被误作 name ─────────────────────────
def fix_concept_as_name(rec: dict, code_map: dict) -> tuple[dict, str]:
    """
    修正条件（同时满足）：
    1. name 匹配 MIXED_NAME（IDC电源、液冷IDC、AI应用、PEEK材料等）
    2. name 不以 N/XD/DR 开头（这些是合法历史名称）
    3. code_map 有真实名且为纯汉字
    4. name 与真实名不一致
    """
    name = (rec.get('name') or '').strip()
    code = (rec.get('code') or '').strip()

    if not MIXED_NAME.match(name):
        return rec, ''
    if STOCK_PREFIX.match(name):          # 合法历史前缀 → 跳过
        return rec, ''
    if not code or code not in code_map:
        return rec, ''

    real = code_map[code]
    if not PURE_CN.match(real):           # 真实名非纯汉字 → 不确定，跳过
        return rec, ''
    if (name == real) or (real in name) or (name in real):
        return rec, ''                    # 已是正确名

    old = name
    rec['name'] = real
    return rec, f'概念误判修正 {old}→{real}'


# ── 统计辅助 ────────────────────────────────────────────────
def collect_plus_records(data: list) -> list:
    result = []
    for art in data:
        date = art['publish_time_str'][:10]
        for s in art.get('focus_stocks', []):
            nm = s.get('name') or ''
            if nm.startswith('+') or nm.startswith('＋'):
                result.append({'date': date, 'loc': 'focus', 'rec': s})
        for sec in art.get('limit_up_sectors', []):
            for s in sec.get('stocks', []):
                nm = s.get('name') or ''
                if nm.startswith('+') or nm.startswith('＋'):
                    result.append({'date': date, 'loc': sec['sector'], 'rec': s})
    return result


def main():
    print('=' * 65)
    print('修复 name 以"+"开头的残留记录（含概念词误判修正）')
    print('=' * 65)

    with open(DATA_FILE, encoding='utf-8') as f:
        data = json.load(f)

    before = collect_plus_records(data)
    print(f'\n[修复前] name以"+"开头: {len(before)} 条')
    print(f'  {"日期":<12} {"板块":<14} {"code":<8} {"name":<18} {"logic"}')
    print('  ' + '-' * 70)
    for item in before:
        r = item['rec']
        print(f'  {item["date"]:<12} {item["loc"]:<14} {r.get("code",""):<8} '
              f'{r.get("name",""):<18} {r.get("logic","")}')

    print('\n[code_map] 尝试加载 mootdx...')
    code_map = build_code_map()

    # ── 第一轮 ──────────────────────────────────────────────
    print('\n── 第一轮：修复 name 以"+"开头的记录 ──')
    fixed1, manual1 = 0, []

    for art in data:
        date = art['publish_time_str'][:10]
        for s in art.get('focus_stocks', []):
            nm = s.get('name') or ''
            if nm.startswith('+') or nm.startswith('＋'):
                orig = (s.get('code',''), s.get('name',''), s.get('logic',''))
                s, how = fix_plus_record(s, code_map)
                if how:
                    print(f'  [F1] {date} focus       {orig[0]}: {orig[1]}/{orig[2]}  →  {how}')
                    fixed1 += 1
                else:
                    manual1.append({'date': date, 'loc': 'focus', **s})

        for sec in art.get('limit_up_sectors', []):
            for s in sec.get('stocks', []):
                nm = s.get('name') or ''
                if nm.startswith('+') or nm.startswith('＋'):
                    orig = (s.get('code',''), s.get('name',''), s.get('logic',''))
                    s, how = fix_plus_record(s, code_map)
                    if how:
                        print(f'  [F1] {date} {sec["sector"]:<11} {orig[0]}: '
                              f'{orig[1]}/{orig[2]}  →  {how}')
                        fixed1 += 1
                    else:
                        manual1.append({'date': date, 'loc': sec['sector'], **s})

    after1 = collect_plus_records(data)
    print(f'\n[第一轮后] name以"+"开头: {len(after1)} 条（修复了 {fixed1} 条）')

    # ── 第二轮 ──────────────────────────────────────────────
    fixed2 = 0
    if code_map:
        print('\n── 第二轮：修正混合缩写概念词被误设为 name ──')
        for art in data:
            date = art['publish_time_str'][:10]
            for s in art.get('focus_stocks', []):
                s, how = fix_concept_as_name(s, code_map)
                if how:
                    print(f'  [F2] {date} focus       {s.get("code","")}: {how}')
                    fixed2 += 1
            for sec in art.get('limit_up_sectors', []):
                for s in sec.get('stocks', []):
                    s, how = fix_concept_as_name(s, code_map)
                    if how:
                        print(f'  [F2] {date} {sec["sector"]:<11} {s.get("code","")}: {how}')
                        fixed2 += 1
        print(f'[第二轮后] 修正概念误判: {fixed2} 条')

    # ── 人工确认 ────────────────────────────────────────────
    if manual1:
        print(f'\n[人工确认] 无法自动修复的 {len(manual1)} 条:')
        for item in manual1:
            print(f'  {item["date"]}  {item["loc"]}  code={item.get("code","")}  '
                  f'name={item.get("name","")}  logic={item.get("logic","")}')
    else:
        print('\n所有"+"开头记录已自动修复！')

    # ── 写回 ────────────────────────────────────────────────
    total = fixed1 + fixed2
    if total > 0:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f'\n✓ 共修复 {total} 条（第一轮{fixed1}+第二轮{fixed2}），已写入 {DATA_FILE}')
        final = collect_plus_records(data)
        print(f'最终 name以"+"开头残留: {len(final)} 条')
    else:
        print('\n（没有变更，未写入文件）')


if __name__ == '__main__':
    main()
