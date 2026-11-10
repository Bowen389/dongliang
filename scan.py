"""Generate two independent after-close watchlists from the frozen V3 pool."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from urllib.parse import urlencode
from urllib.request import urlopen

from market_data import ROOT, package


def index_history():
    url = 'https://quotes.sina.cn/cn/api/jsonp.php/var%20x=/CN_MarketData.getKLineData'
    query = urlencode({'symbol': 'sh000300', 'scale': '240', 'ma': 'no', 'datalen': '250'})
    with urlopen(f'{url}?{query}', timeout=30) as response:
        body = response.read().decode('utf-8')
    rows = json.loads(body[body.index('([') + 1:body.rindex('])') + 1])
    return sorted((row['day'], float(row['close'])) for row in rows)


def candidates(bars, sessions, universe):
    if bars.duplicated(['date', 'code']).any():
        raise ValueError('Duplicate daily bar')
    groups = {code: group.set_index('date').reindex(sessions)
              for code, group in bars[bars.code.isin(universe)].groupby('code')}
    results = {'champion': [], 'optimized_a': []}
    coverage = dict(scanned=0, excluded=0, inactive_or_missing_today=0, missing_history=0)
    for code in universe:
        frame = groups.get(code)
        if frame is None or pd.isna(frame.iloc[-1]['name']):
            coverage['inactive_or_missing_today'] += 1
            continue
        today = frame.iloc[-1]
        name = str(today['name'])
        if 'ST' in name.upper() or '退' in name:
            coverage['excluded'] += 1
            continue
        valid = frame[['open', 'close', 'prev_close', 'volume']]
        valid = np.isfinite(valid).all(axis=1) & (valid > 0).all(axis=1)
        if not valid.iloc[-1] or not np.isfinite(today['amount']):
            coverage['inactive_or_missing_today'] += 1
            continue
        if not valid.all():
            coverage['missing_history'] += 1
            continue
        coverage['scanned'] += 1
        ratios = frame.close / frame.prev_close
        close = ratios.cumprod() * frame.prev_close.iloc[0]
        gain = float(ratios.iloc[-1] - 1)
        v5 = float(today.volume / frame.volume.iloc[-6:-1].mean())
        v60 = float(today.volume / frame.volume.iloc[:-1].mean())
        high = bool(close.iloc[-1] >= close.iloc[:-1].max() - 1e-9)
        row = dict(code=code, name=name, gain=gain, volume_ratio_5=v5,
                   volume_ratio_60=v60, amount_cny=float(today.amount),
                   close=float(today.close), breakout=high)
        for strategy, gain_min, v60_hi, v60_lo, strength_min, amount_min in [
            ('champion', .05, 2, 1.5, 3, 0),
            ('optimized_a', .02, 1.5, 1.2, 1, 300_000_000),
        ]:
            conditions = [gain > gain_min and v5 > 2, high and v60 > v60_hi,
                          high and v60 > v60_lo]
            strength = sum(conditions)
            if strength >= strength_min and today.amount >= amount_min:
                results[strategy].append(dict(row, strength=strength, conditions=conditions))
    for rows in results.values():
        rows.sort(key=lambda row: (-row['strength'], -row['gain'], row['code']))
    return results, coverage


def run(day, index):
    available = [(date, close) for date, close in index if date <= day]
    if len(available) < 61 or available[-1][0] != day:
        raise RuntimeError(f'No confirmed index close on {day}')
    sessions = [date for date, _ in available[-61:]]
    close = available[-1][1]
    ma20 = sum(price for _, price in available[-20:]) / 20
    allow = close >= ma20
    universe = pd.read_csv('universe.csv', dtype={'代码': str})['代码'].tolist()
    if len(universe) != 969 or len(set(universe)) != 969:
        raise RuntimeError('Invalid frozen universe')
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(package, sessions))
    bars = pd.concat([pd.read_csv(ROOT / f'{date}.csv', dtype={'code': str})
                      for date in sessions], ignore_index=True)
    results, coverage = candidates(bars, sessions, universe)
    if not coverage['scanned']:
        raise RuntimeError('No stocks could be scanned')
    strategies = {}
    for name, gap, slots, exit_basis in [
        ('champion', [-.04, .05], 5, 'close'),
        ('optimized_a', [-.03, .05], 3, 'open'),
    ]:
        strategies[name] = dict(
            technical_candidates=results[name],
            next_open_watchlist=results[name] if allow else [],
            opening_gap_range=gap,
            max_positions=slots,
            exit_sessions_after_entry=2,
            exit_basis=exit_basis,
        )
    report = dict(date=day, universe_size=len(universe), coverage=coverage,
                  market_gate=dict(source='Sina CSI300 daily', close=close, ma20=ma20, allow=allow),
                  strategies=strategies)
    out = Path('reports')
    out.mkdir(exist_ok=True)
    (out / f'{day}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = [f'# {day} 盘后信号', '',
             f'沪深300 {close:.3f}，MA20 {ma20:.3f}，新开仓门控{"开放" if allow else "关闭"}。',
             f'已完成计算 {coverage["scanned"]} / 969 只。覆盖详情见同日 JSON。', '']
    for name, title in [('champion', '冠军版'), ('optimized_a', '优化 A')]:
        plan = strategies[name]
        lo, hi = plan['opening_gap_range']
        lines += [f'## {title}', '',
                  f'技术候选 {len(results[name])} 只，次日开盘待核验 {len(plan["next_open_watchlist"])} 只。',
                  f'次日缺口区间 {lo:+.0%} 至 {hi:+.0%}，最多持仓 {plan["max_positions"]} 只。'
                  '按排名核验停牌、开盘涨跌停和重复持仓。', '',
                  '| 排名 | 代码 | 名称 | 强度 | 涨幅 | 5日量比 | 60日量比 | 成交额（亿元） |',
                  '|---:|---|---|---:|---:|---:|---:|---:|']
        for rank, row in enumerate(results[name], 1):
            lines.append(f'| {rank} | {row["code"]} | {row["name"]} | {row["strength"]} | '
                         f'{row["gain"]:+.2%} | {row["volume_ratio_5"]:.2f} | '
                         f'{row["volume_ratio_60"]:.2f} | {row["amount_cny"]/1e8:.2f} |')
        lines.append('')
    (out / f'{day}.md').write_text('\n'.join(lines), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date')
    parser.add_argument('--skip-existing', action='store_true')
    args = parser.parse_args()
    day = args.date or datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    if args.skip_existing and Path(f'reports/{day}.json').exists():
        return
    import exchange_calendars as calendars
    if not calendars.get_calendar('XSHG').is_session(day):
        print(f'{day} is not a trading session')
        return
    run(day, index_history())


if __name__ == '__main__':
    main()
