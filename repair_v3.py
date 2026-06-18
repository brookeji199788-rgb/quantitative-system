"""
第三轮历史数据修复 — 从根源清洗 name/logic 字段截断/错位

覆盖的 bug 模式：
  模式 B  单字残留（"体"、"器"、"人"、"池"）
          原因：OCR 行截断，前一条记录逻辑字段末尾单字溢出成为下一条的 name
          修复：无需 code_map，name=单字且 logic 匹配股票名 → 直接互换

  模式 A  短词错位（"其他"、"经济"、"预期"、"变更"、"概念"等）
          原因：OCR 读到归因文本/概念标签词，误入 name 槽位
          修复：code_map[code] == logic → 互换（logic 是真实股名，name 是碎片）

  模式 C  更长的截断片段（"力租赁"、"瓷材料"、"洁净室"、"核聚变" 等）
          原因：同模式 B，但被截断的逻辑词更长
          修复：同模式 A（code_map 验证）

  对于模式 B 中 logic 也不完整的情况（name 互换后，logic 仍是单字残留），
  保留该单字，不伪造完整词。

用法: python repair_v3.py
"""

import json
import re
import sys
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT      = Path(__file__).parent
DATA_FILE = ROOT / 'data' / 'cls_review_daily.json'

sys.path.insert(0, str(ROOT / 'cls_py'))

# ── 正则（与 update_cls_data.py 保持一致）──────────────────────
PURE_CN    = re.compile(r'^[一-鿿]{2,6}$')
STOCK_NAME = re.compile(
    r'^(?:'
    r'[一-鿿]{2,6}'
    r'|[一-鿿]{2,5}[A-Ca-c]'
    r'|(?:N|XD|DR)[一-鿿]{1,5}'
    r'|[A-Z]{2,5}[一-鿿]{2,4}'
    r'|[一-鿿]{2,4}[A-Z]{2,5}'
    r')$'
)


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
        print(f'[mootdx] 不可用，仅处理模式 B（单字）: {e}')
        return {}


def fix_record_v3(rec: dict, code_map: dict) -> tuple[dict, str]:
    """
    返回 (修正后的 rec, 修复说明)；未修复则说明为空字符串。
    """
    name  = (rec.get('name')  or '').strip()
    logic = (rec.get('logic') or '').strip()
    code  = (rec.get('code')  or '').strip()

    if not name or not logic:
        return rec, ''

    # 模式 B：单字残留 — 不需要 code_map
    if len(name) == 1 and STOCK_NAME.match(logic):
        rec['name'], rec['logic'] = logic, name
        return rec, f'B(单字) {name!r}→{logic!r}'

    # 模式 A/C：code_map 精确匹配
    if code and code in code_map:
        real = code_map[code]
        if not real:
            return rec, ''
        # logic 精确等于真实名，且 name 不是真实名
        name_ok = (name == real) or (real in name) or (name in real and len(name) >= len(real))
        if logic == real and not name_ok:
            old_name = name
            rec['name'], rec['logic'] = real, name
            return rec, f'A/C(code_map) {old_name!r}→{real!r}'

    return rec, ''


def iter_records(data: list):
    """生成 (article_idx, loc_label, stock_dict) 三元组（in-place 可修改 stock_dict）"""
    for art in data:
        date = art['publish_time_str'][:10]
        for s in art.get('focus_stocks', []):
            yield date, 'focus', s
        for sec in art.get('limit_up_sectors', []):
            for s in sec.get('stocks', []):
                yield date, sec['sector'], s


def main():
    print('=' * 65)
    print('第三轮修复：OCR截断/错位 (模式 A/B/C)')
    print('=' * 65)

    with open(DATA_FILE, encoding='utf-8') as f:
        data = json.load(f)

    # 统计修复前状态
    total_b_before = sum(1 for _, _, s in iter_records(data) if len(s.get('name','')) == 1)
    print(f'\n[修复前] 单字 name (模式B): {total_b_before} 条')

    # 加载 code_map
    code_map = build_code_map()

    # 执行修复
    fixed_b, fixed_ac = 0, 0
    b_records, ac_records = [], []

    for date, loc, rec in iter_records(data):
        orig_name  = rec.get('name', '')
        orig_logic = rec.get('logic', '')
        rec, how = fix_record_v3(rec, code_map)
        if how:
            entry = {
                'date': date, 'loc': loc,
                'code': rec.get('code', ''),
                'old_name': orig_name,
                'old_logic': orig_logic,
                'new_name': rec.get('name', ''),
                'new_logic': rec.get('logic', ''),
                'how': how,
            }
            if how.startswith('B'):
                fixed_b += 1
                b_records.append(entry)
            else:
                fixed_ac += 1
                ac_records.append(entry)

    # 打印详情
    print(f'\n── 模式 B（单字残留）修复 {fixed_b} 条 ──')
    for e in b_records:
        print(f'  {e["date"]}  {e["loc"]:<14}  code={e["code"]:<8}  '
              f'{e["old_name"]!r:>5} / {e["old_logic"]!r}  →  '
              f'name={e["new_name"]!r}  logic={e["new_logic"]!r}')
    if fixed_b:
        print('  注：logic 字段保留了原单字残留（原始 OCR 数据不完整，不伪造补全）')

    print(f'\n── 模式 A/C（code_map 验证互换）修复 {fixed_ac} 条 ──')
    for e in ac_records[:80]:   # 最多打印80条，避免刷屏
        print(f'  {e["date"]}  {e["loc"]:<14}  code={e["code"]:<8}  '
              f'{e["old_name"]!r:>8} / {e["old_logic"]!r}  →  name={e["new_name"]!r}')
    if fixed_ac > 80:
        print(f'  ...（共 {fixed_ac} 条，仅显示前 80 条）')

    total_fixed = fixed_b + fixed_ac
    if total_fixed == 0:
        print('\n无需修复，已跳过写入。')
        return

    # 写回
    bak = DATA_FILE.with_suffix('.json.bak2')
    import shutil
    shutil.copy2(DATA_FILE, bak)
    print(f'\n[备份] {bak}')

    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f'[写入] {DATA_FILE}')
    print(f'\n✓ 共修复 {total_fixed} 条（模式B={fixed_b}，模式A/C={fixed_ac}）')

    # 验证
    total_b_after = sum(1 for _, _, s in iter_records(data) if len(s.get('name','')) == 1)
    print(f'\n[修复后] 单字 name (模式B) 残留: {total_b_after} 条')
    if total_b_after:
        print('  （以下记录无法通过模式匹配自动修复，需人工确认）')
        for date, loc, s in iter_records(data):
            if len(s.get('name','')) == 1:
                print(f'  {date}  {loc}  code={s.get("code","")}  '
                      f'name={s.get("name","")!r}  logic={s.get("logic","")!r}')


if __name__ == '__main__':
    main()
