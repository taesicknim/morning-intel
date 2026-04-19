"""
실시간 마켓 데이터 수집
- 한국/미국/아시아 주요 지수
- 환율, 유가, 금, 비트코인
- 무료 소스만 사용 (Yahoo Finance 쿼리 API, 네이버 금융)
"""
import requests, json, re
from datetime import datetime

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json,text/plain,*/*'
}

# Yahoo Finance symbol mapping
SYMBOLS = {
    # 미국 3대 지수
    'S&P 500':    '^GSPC',
    '나스닥':     '^IXIC',
    '다우존스':   '^DJI',
    # 한국
    '코스피':     '^KS11',
    '코스닥':     '^KQ11',
    # 아시아
    '니케이225':  '^N225',
    '상하이종합': '000001.SS',
    '홍콩H':      '^HSCE',
    # 유럽
    'DAX':        '^GDAXI',
    'FTSE100':    '^FTSE',
    # 환율 (KRW 기준)
    '원/달러':    'KRW=X',
    '원/엔':      'KRWJPY=X',  # may not work
    '달러/유로':  'EUR=X',
    # 원자재
    'WTI 유가':   'CL=F',
    '금':         'GC=F',
    '비트코인':   'BTC-USD',
    # 금리
    '美 10년물':  '^TNX',
}

# 한국 주요 개별 종목 (코스피 대형주)
KR_STOCKS = {
    '삼성전자':       '005930.KS',
    'SK하이닉스':     '000660.KS',
    'NAVER':          '035420.KS',
    '현대차':         '005380.KS',
    'LG에너지솔루션': '373220.KS',
    '카카오':         '035720.KS',
    '삼성바이오로직스': '207940.KS',
    '기아':           '000270.KS',
    'POSCO홀딩스':    '005490.KS',
    'KB금융':         '105560.KS',
}

def fetch_quote(symbol):
    """Yahoo Finance chart API — 일간 close 배열에서 실제 1일 변동 계산"""
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=10d'
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        result = data['chart']['result'][0]
        meta = result['meta']
        timestamps = result.get('timestamp', []) or []
        quote = result['indicators']['quote'][0]
        closes = quote.get('close', []) or []

        # 마지막 2개의 유효한 close 값 (None 제외)
        valid = [(ts, c) for ts, c in zip(timestamps, closes) if c is not None]
        if len(valid) < 2:
            return None

        last_ts, last_close = valid[-1]
        prev_ts, prev_close = valid[-2]

        change = last_close - prev_close
        change_pct = (change / prev_close) * 100 if prev_close else 0

        from datetime import datetime, timezone
        last_date = datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime('%Y-%m-%d')

        return {
            'symbol': symbol,
            'current': round(last_close, 2),
            'prev_close': round(prev_close, 2),
            'change': round(change, 2),
            'change_pct': round(change_pct, 2),
            'last_date': last_date,  # 실제 데이터 날짜 (휴장 감안)
            'currency': meta.get('currency', ''),
            'market_state': meta.get('marketState', ''),
        }
    except Exception as e:
        return None


def fetch_all_markets():
    """전체 주요 지수 수집"""
    results = {}
    for name, symbol in SYMBOLS.items():
        q = fetch_quote(symbol)
        if q:
            results[name] = q
    return results

def fetch_kr_stocks():
    """한국 주요 개별 종목 시세"""
    results = {}
    for name, symbol in KR_STOCKS.items():
        q = fetch_quote(symbol)
        if q:
            results[name] = q
    return results

def format_stocks_block(stocks):
    """종목 시세 블록 포맷"""
    if not stocks:
        return ""
    lines = ["\n[한국 대형주 종가]"]
    for name, m in stocks.items():
        arrow = '▲' if m['change'] > 0 else ('▼' if m['change'] < 0 else '—')
        lines.append(f"  {name}: {int(m['current']):,}원 ({arrow} {m['change_pct']:+.2f}%)")
    return "\n".join(lines) + "\n"


def is_market_closed_today():
    """오늘이 주말(토/일)인지 확인. 한국 공휴일은 별도 확장 가능."""
    from datetime import datetime
    return datetime.now().weekday() >= 5  # 5=토, 6=일

def format_market_block(markets):
    """Claude 프롬프트용 마켓 데이터 블록"""
    if not markets:
        return ""

    from datetime import datetime
    weekend = is_market_closed_today()

    # 대표 지수의 last_date로 라벨 결정
    sample_date = ''
    for k in ['S&P 500', '코스피', '나스닥']:
        if k in markets and markets[k].get('last_date'):
            sample_date = markets[k]['last_date']
            break

    header = f"\n=== 실시간 마켓 지수"
    if weekend:
        header += f" ({sample_date} 마지막 거래일 종가 · 현재 휴장)"
    else:
        header += f" ({sample_date} 종가)"
    header += " ===\n"

    lines = [header]
    lines.append("주의: 변동률은 직전 거래일 종가 대비 1일 변동입니다.\n")

    # 미국
    us = ['S&P 500', '나스닥', '다우존스']
    lines.append("[미국 증시]")
    for k in us:
        if k in markets:
            m = markets[k]
            arrow = '▲' if m['change'] > 0 else ('▼' if m['change'] < 0 else '—')
            lines.append(f"  {k}: {m['current']:,} ({arrow} {m['change_pct']:+.2f}%)")

    # 한국
    kr = ['코스피', '코스닥']
    lines.append("[한국 증시]")
    for k in kr:
        if k in markets:
            m = markets[k]
            arrow = '▲' if m['change'] > 0 else ('▼' if m['change'] < 0 else '—')
            lines.append(f"  {k}: {m['current']:,} ({arrow} {m['change_pct']:+.2f}%)")

    # 아시아
    asia = ['니케이225', '상하이종합', '홍콩H']
    asia_present = [k for k in asia if k in markets]
    if asia_present:
        lines.append("[아시아]")
        for k in asia_present:
            m = markets[k]
            arrow = '▲' if m['change'] > 0 else ('▼' if m['change'] < 0 else '—')
            lines.append(f"  {k}: {m['current']:,} ({arrow} {m['change_pct']:+.2f}%)")

    # 환율
    fx = ['원/달러', '달러/유로']
    fx_present = [k for k in fx if k in markets]
    if fx_present:
        lines.append("[환율]")
        for k in fx_present:
            m = markets[k]
            arrow = '▲' if m['change'] > 0 else ('▼' if m['change'] < 0 else '—')
            lines.append(f"  {k}: {m['current']:,} ({arrow} {m['change_pct']:+.2f}%)")

    # 원자재 + 금리
    etc = ['WTI 유가', '금', '비트코인', '美 10년물']
    etc_present = [k for k in etc if k in markets]
    if etc_present:
        lines.append("[원자재/금리]")
        for k in etc_present:
            m = markets[k]
            arrow = '▲' if m['change'] > 0 else ('▼' if m['change'] < 0 else '—')
            unit = '%' if '10년물' in k else ''
            lines.append(f"  {k}: {m['current']:,}{unit} ({arrow} {m['change_pct']:+.2f}%)")

    return "\n".join(lines) + "\n"


if __name__ == '__main__':
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    print("실시간 지수 수집 중...\n")
    m = fetch_all_markets()
    print(format_market_block(m))
    print(f"\n수집 성공: {len(m)}개 지수")
