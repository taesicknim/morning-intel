"""
모닝 브리핑 엔진
- 5개 카테고리 뉴스 수집 -> 크로스 분석 -> 프리미엄 브리핑 생성
"""
import json, re, sqlite3, requests, xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import quote
from config import CLAUDE_API_KEY

DB = 'intel.db'
# Claude Sonnet 4.6 pricing
PRICE_INPUT  = 3.00 / 1_000_000   # $3.00 / 1M tokens
PRICE_OUTPUT = 15.00 / 1_000_000  # $15.00 / 1M tokens
MODEL = "claude-sonnet-4-6"

CAT_LABELS = {
    'economy':'글로벌 경제', 'geo':'지정학', 'tech':'기술혁신',
    'society':'사회변화', 'invest':'투자기회'
}

NEWS_QUERIES = {
    'economy': ['글로벌 경제', '금융시장 중앙은행', '환율 인플레이션 무역'],
    'geo':     ['지정학 국제분쟁', '외교 군사 안보', '미중 관계'],
    'tech':    ['AI 반도체 기술', '에너지전환 디지털', '바이오테크 우주'],
    'society': ['인구구조 노동시장', '소비트렌드 사회변화', 'MZ세대 트렌드'],
    'invest':  ['글로벌 투자 트렌드', '주식시장 유망섹터', '스타트업 자산배분']
}

# Google News 섹션별 헤드라인 (실시간 최신 뉴스)
NEWS_SECTIONS = {
    'top':      'https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko',
    'business': 'https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ko&gl=KR&ceid=KR:ko',
    'world':    'https://news.google.com/rss/headlines/section/topic/WORLD?hl=ko&gl=KR&ceid=KR:ko',
    'tech':     'https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=ko&gl=KR&ceid=KR:ko',
}

WEEKDAY_KR = ['월','화','수','목','금','토','일']

# 별도 usage tracker for briefing
briefing_usage = {'input_tokens': 0, 'output_tokens': 0, 'cost_usd': 0.0, 'calls': 0}

def _get_db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def _call_claude(prompt):
    url = 'https://api.anthropic.com/v1/messages'
    headers = {
        'x-api-key': CLAUDE_API_KEY,
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json'
    }
    body = {
        "model": MODEL,
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": prompt}]
    }
    r = requests.post(url, json=body, headers=headers, timeout=90)
    r.raise_for_status()
    data = r.json()
    usage = data.get('usage', {})
    inp = usage.get('input_tokens', 0)
    out = usage.get('output_tokens', 0)
    cost = inp * PRICE_INPUT + out * PRICE_OUTPUT
    briefing_usage['input_tokens'] += inp
    briefing_usage['output_tokens'] += out
    briefing_usage['cost_usd'] += cost
    briefing_usage['calls'] += 1

    # app의 usage에도 반영
    try:
        from app import api_usage, PRICE_INPUT as P_I, PRICE_OUTPUT as P_O
        api_usage['input_tokens'] += inp
        api_usage['output_tokens'] += out
        api_usage['cost_usd'] += cost
        api_usage['calls'] += 1
    except Exception:
        pass

    return data['content'][0]['text']

def _parse_pub_date(pub_str):
    """RSS pubDate 파싱 → datetime (UTC 가정)"""
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(pub_str)
    except Exception:
        return None

def _is_recent(pub_str, hours=48):
    """최근 N시간 이내 기사인가?"""
    dt = _parse_pub_date(pub_str)
    if not dt:
        return False
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    delta = now - dt
    return delta.total_seconds() < hours * 3600

def _fetch_news(category, max_articles=10):
    """카테고리별 뉴스 수집 (구버전, 호환용)"""
    queries = NEWS_QUERIES.get(category, ['글로벌 경제'])
    articles = []
    seen = set()
    for q in queries:
        try:
            url = f'https://news.google.com/rss/search?q={quote(q)}+when:2d&hl=ko&gl=KR&ceid=KR:ko'
            r = requests.get(url, timeout=10)
            r.encoding = 'utf-8'
            root = ET.fromstring(r.content)
            for item in root.findall('.//item'):
                title = item.find('title').text or ''
                link = item.find('link').text or ''
                pub = item.find('pubDate').text or ''
                # 48시간 이내만
                if not _is_recent(pub, hours=48):
                    continue
                if title not in seen:
                    seen.add(title)
                    parts = title.rsplit(' - ', 1)
                    articles.append({
                        'title': parts[0].strip(),
                        'source_name': parts[1].strip() if len(parts) > 1 else '',
                        'link': link,
                        'pubDate': pub
                    })
        except Exception:
            continue
    return articles[:max_articles]

def _fetch_section(section_url, max_articles=15, hours=24):
    """Google News 섹션 헤드라인 (실시간 최신)"""
    articles = []
    seen = set()
    try:
        r = requests.get(section_url, timeout=10)
        r.encoding = 'utf-8'
        root = ET.fromstring(r.content)
        for item in root.findall('.//item'):
            title = item.find('title').text or ''
            link = item.find('link').text or ''
            pub = item.find('pubDate').text or ''
            if not _is_recent(pub, hours=hours):
                continue
            if title not in seen:
                seen.add(title)
                parts = title.rsplit(' - ', 1)
                articles.append({
                    'title': parts[0].strip(),
                    'source_name': parts[1].strip() if len(parts) > 1 else '',
                    'link': link,
                    'pubDate': pub
                })
            if len(articles) >= max_articles:
                break
    except Exception:
        pass
    return articles

def _fetch_realtime_news(hours=24):
    """실시간 이슈 뉴스 통합 수집 (섹션 + 이벤트 키워드)"""
    all_articles = []
    seen_titles = set()

    # 1. 섹션별 헤드라인 (오늘의 top stories)
    for section, url in NEWS_SECTIONS.items():
        arts = _fetch_section(url, max_articles=10, hours=hours)
        for a in arts:
            if a['title'] not in seen_titles:
                seen_titles.add(a['title'])
                a['section'] = section
                all_articles.append(a)

    # 2. 실시간 이벤트 키워드 (경제 영향 관점)
    event_keywords = [
        '나스닥 사상최고', '코스피 사상최고', '다우존스',
        '호르무즈', '유가', '환율 달러',
        '연준 금리', '미국 증시', '반도체 주가',
        '엔비디아', '삼성전자 실적', 'TSMC',
        '부동산 PF', '한국 수출', '무역수지'
    ]
    for kw in event_keywords:
        try:
            url = f'https://news.google.com/rss/search?q={quote(kw)}+when:1d&hl=ko&gl=KR&ceid=KR:ko'
            r = requests.get(url, timeout=8)
            r.encoding = 'utf-8'
            root = ET.fromstring(r.content)
            for item in root.findall('.//item')[:3]:  # 키워드당 최대 3건
                title = item.find('title').text or ''
                link = item.find('link').text or ''
                pub = item.find('pubDate').text or ''
                if not _is_recent(pub, hours=hours):
                    continue
                if title not in seen_titles:
                    seen_titles.add(title)
                    parts = title.rsplit(' - ', 1)
                    all_articles.append({
                        'title': parts[0].strip(),
                        'source_name': parts[1].strip() if len(parts) > 1 else '',
                        'link': link,
                        'pubDate': pub,
                        'section': f'키워드:{kw}'
                    })
        except Exception:
            continue

    # pubDate 기준 최신순 정렬
    def _sort_key(a):
        dt = _parse_pub_date(a.get('pubDate', ''))
        return dt or datetime.min
    all_articles.sort(key=_sort_key, reverse=True)
    return all_articles


def _load_best_examples(limit=2):
    """과거 고평가 브리핑을 few-shot 예시로 로드"""
    try:
        with _get_db() as conn:
            rows = conn.execute('''
                SELECT payload FROM briefings
                WHERE rating >= 4 OR grade IN ('S','A')
                ORDER BY rating DESC, created_at DESC LIMIT ?
            ''', (limit,)).fetchall()
            return [r['payload'] for r in rows]
    except Exception:
        return []

def generate_briefing():
    """프리미엄 모닝 브리핑 생성 (실시간 뉴스 + 마켓 데이터)"""
    today = datetime.now()
    date_str = today.strftime('%Y-%m-%d')
    weekday = WEEKDAY_KR[today.weekday()]
    is_weekend = today.weekday() >= 5  # 5=토, 6=일

    # 1단계: 실시간 뉴스 수집 (18시간 이내, 섹션+이벤트 키워드, 최신순)
    articles = _fetch_realtime_news(hours=18)

    # 2단계: 실시간 마켓 지수 + 개별 종목 수집
    market_block = ""
    try:
        from market_data import fetch_all_markets, format_market_block, fetch_kr_stocks, format_stocks_block
        markets = fetch_all_markets()
        stocks = fetch_kr_stocks()
        market_block = format_market_block(markets) + format_stocks_block(stocks)
    except Exception:
        markets = {}
        stocks = {}

    # 날짜 컨텍스트
    date_context = f"오늘: {date_str} ({weekday}요일)"
    if is_weekend:
        date_context += "\n⚠️ 주말 = 주식시장 휴장. '오늘 움직임'이 아니라 '지난 거래일(금요일) 종가 + 다음 주 관전포인트' 프레임으로 작성할 것."

    # few-shot 학습
    best_examples = _load_best_examples(limit=2)
    examples_block = ""
    if best_examples:
        examples_block = "\n\n[과거 우수 브리핑 예시 — 이 수준의 깊이를 목표로 하라]\n"
        for i, ex in enumerate(best_examples, 1):
            examples_block += f"\n--- 예시 {i} ---\n{ex}\n"

    # 호환성을 위해 all_news와 total_count 유지 (fallback)
    all_news = {'realtime': articles}
    total_count = len(articles)

    # 뉴스를 시간순 통합 리스트로 (최신순)
    news_block = "\n### 실시간 뉴스 (18시간 이내, 최신순 — 상위 항목이 가장 최근) ###\n"
    news_index = {}
    for idx, a in enumerate(articles, 1):
        tag = f"[{a.get('section','')}]" if a.get('section') else ''
        marker = "🆕 " if idx <= 5 else ""
        news_block += f"{marker}{idx}. {tag} {a['title']} — {a['source_name']} ({a['pubDate'][:16]})\n"
        news_index[idx] = a
    news_block += "\n※ 🆕 표시된 상위 5개가 최신 뉴스. 이 뉴스가 오늘의 핵심 내러티브여야 함.\n"

    # 2단계: Claude 프리미엄 분석
    # 목표: 골드만 MD가 감탄할 깊이 + 직장 5년차가 출근길에 이해할 가독성
    prompt = f"""당신은 세 명의 세계적 분석가를 합친 하이브리드입니다:
- Mohamed El-Erian (PIMCO 전 CEO): 거시경제 2·3차 파급효과 추적
- Matt Levine (Bloomberg): 숫자 디테일과 통념 뒤집기
- Byron Wien: 시장이 가격에 반영 안 한 것을 찾는 능력

{date_context}

[독자 프로필]
- 한국 직장인 (30-40대, 다양한 직군)
- 본인 연봉/이직/자산(주식·부동산·연금)에 진지함
- 오늘 한국 경제·시장에 어떤 영향이 오는지가 가장 궁금
- 글로벌 이벤트는 반드시 "한국에 어떤 파급?" 관점으로 연결

═══════════════════════════════════════
[절대 규칙 -1 — 팩트 순응 (가장 먼저 읽어라)]
════════════════════════════════════
Top 3 이슈를 고르기 전에 먼저 다음을 수행하라:

STEP 1. 🆕 마크된 최신 뉴스 5건의 실제 제목을 관찰.
STEP 2. 그 제목들이 말하는 '주도적 방향'을 파악 (예: 개방/인하/상승/합의).
STEP 3. Top 3 이슈는 그 주도적 방향을 반드시 반영. 반대 방향으로 극적 서사를 만들지 말 것.

구체 체크:
- 만약 최신 뉴스 다수가 "호르무즈 개방", "유가 급락", "주가 사상 최고"라면:
  → Top 1은 반드시 "개방/완화/상승"이 중심 메시지여야 함
  → "재봉쇄 위협", "결렬 리스크" 같은 반대 시나리오는 조건부 경고(hidden_risk)에만 1문장 허용
  → 헤드라인에 "재봉쇄", "위기 반전", "폭락 위험" 같은 반대 단어 금지

LLM의 본능: "극적 서사가 좋다"는 유혹을 억제하라.
독자가 원하는 것: "오늘 실제 무슨 일이 일어났고, 그게 나한테 뭘 뜻하는가."

════════════════════════════════════
[절대 규칙 0 — 팩트 무결성 (위반 시 구독자 이탈)]
═══════════════════════════════════════
🔒 아래 규칙을 위반하면 전체 브리핑이 실패:

1. **숫자 락**: 아래 '실시간 마켓 지수' 섹션의 모든 수치는 외부 API에서 가져온 실측값.
   - 이 숫자를 절대 변경하지 말 것
   - 새로운 숫자를 만들어내지 말 것 (예: "나스닥 +6.84%"라고 쓰면 안 됨. 실제 값이 +1.52%면 +1.52%로만.)
   - 변동 방향(+/-)을 절대 바꾸지 말 것 (상승을 하락으로, 하락을 상승으로 표기 금지)

2. **휴장일 인식**: 오늘이 주말/공휴일이면 "오늘 종가"가 아니라 "지난 거래일 종가". 표기 엄수.

3. **뉴스 사실 락**: 아래 '실시간 뉴스 헤드라인'에 없는 사건을 창작 금지.
   - 예: 뉴스에 "호르무즈 긴장"이라고만 있으면 "호르무즈 완전 개방 선언"으로 확대 해석 금지
   - 불확실한 주장은 "관련 뉴스 확인 필요" 또는 생략
   - 본문에 [#N] 라벨 쓰지 말 것 (출처는 내부적으로만 매칭, 독자에게는 자연 문장)

3-1. **반대 방향 추론 금지 (매우 중요)**:
   - 뉴스가 "호르무즈 개방/통항 허용"이라고 말하면, 중심 내러티브는 반드시 '개방' 방향이어야 함
   - "재봉쇄 가능성", "협상 결렬 리스크" 같은 역방향 시나리오는 다음을 모두 만족할 때만 사용:
     (a) 뉴스에 명시적으로 그 리스크가 언급되어 있음
     (b) 중심 내러티브가 아닌 '조건부 경고(만약 ~하면)' 형태
     (c) Top3의 2번째 이하 또는 Cross Insight의 hidden_risk 부분
   - Top 1 이슈는 반드시 뉴스의 '주도적 방향'을 따라야 함 (개방이면 개방 영향, 인하면 인하 영향)
   - 헤드라인에 "재봉쇄 위협", "결렬", "역전" 같은 반대 방향 단어 금지 (뉴스가 실제 그렇게 말하지 않는 한)
   - LLM이 드라마틱한 역전 서사를 만들려는 충동을 억제하라. 독자는 정확한 현실을 원한다.

4. **날짜 정확성**: 뉴스의 발행일을 확인. 지난 주 뉴스를 "오늘 발표"처럼 쓰지 말 것.
   - 가장 최근 뉴스(발행 시간 top 5)의 주제를 우선적으로 다뤄라
   - 오래된 이슈가 여전히 '중심 내러티브'라고 착각하지 말 것

5. **미확인 숫자 금지**: 뉴스에도 없고 마켓 데이터에도 없는 수치(예: "4월 수출 -3%", "PF 연체율 5.2%")는 만들지 말 것. 꼭 필요하면 "[추정]" 명시 + 근거 계산 방법 1줄.

6. **개별 종목 주가 규칙 (매우 중요)**:
   - 종목 주가는 반드시 위 '한국 대형주 종가' 섹션에 명시된 정확한 값만 사용
   - 거기 없는 종목의 구체 주가를 만들어내지 말 것 (예: "삼성전자 8만원" 같은 허위 생성 금지)
   - "8만원 하단 알림 설정" 같은 임의의 임계값 지정 금지. 대신 "현재가 대비 -5% 하락 시" 같은 상대적 표현 사용
   - 특정 종목 매수/매도 추천 금지 — "포트폴리오 내 비중 확인" 같은 중립 행동만

7. **기업/인명 언급 규칙 (절대적)**:
   🚨 브리핑에 언급하는 모든 기업명, 제품명, 인명, 국가명은 반드시:
   (a) 위 '실시간 뉴스 헤드라인'에 등장하거나
   (b) '한국 대형주 종가'에 등장하거나
   (c) '실시간 마켓 지수'에 등장해야 함

   ❌ 금지 예시:
   - "두산로보틱스 엔비디아 AI 무대 1위" — 이 뉴스 헤드라인 목록에 두산로보틱스가 없으면 언급 금지
   - "A사가 B를 인수" — A, B가 뉴스에 없으면 금지
   - "C 지역에서 사고 발생" — 뉴스에 없으면 금지

   ✅ 올바른 예시:
   - 뉴스에 "삼성전기 엔비디아 납품 기판 양산"이 있으면 → 삼성전기 + 엔비디아 언급 OK
   - 마켓 데이터에 "코스피 6,191.92"가 있으면 → 코스피 언급 OK

   원칙: 출처 없는 구체 주장은 전면 생략. 생략이 창작보다 낫다.

═══════════════════════════════════════
[실시간 마켓 지수 — 이 숫자는 변경 불가, 그대로 인용]
═══════════════════════════════════════
{market_block}

═══════════════════════════════════════
[실시간 뉴스 헤드라인 — 24시간 이내]
═══════════════════════════════════════
{news_block}
==={examples_block}

[분석 원칙]
1. 분석은 위 마켓 데이터와 뉴스 제목에 근거해야 함. 없는 사실을 만들지 말 것.
2. 글로벌 이벤트 → 한국 경제 파급 경로를 명확히.
3. 확률/방향에 대한 추정은 OK, 단 "추정"임을 명시하거나 조건부 표현 사용.
4. 주말/휴장일이면 "오늘 움직임"이 아니라 "지난 거래일 움직임 + 다음 주 관전포인트" 프레임.

════════════════════════════════════
[이중 목표 — 둘 다 달성 필수]
════════════════════════════════════

**깊이 (Goldman MD 수준)**: 인과 체인, 컨센서스 뒤집기, 2차 영향 추적은 타협 없음.
**가독성 (직장 5년차 출근길 수준)**: 전문용어는 괄호 풀이. 문장은 짧게. 전체 1200자 이내.

둘 중 하나라도 미달이면 실패. 깊이를 낮추지 말고, 표현만 쉽게 하라.

════════════════════════════════════
[규칙 1: 전문용어 괄호 풀이 강제]
════════════════════════════════════
금융/거시 용어 처음 등장 시 반드시 괄호 풀이:
❌ "BIS 비율 하락"
✅ "BIS 비율(은행 자기자본 건전성 지표) 하락"

❌ "PF 스프레드 30bp 확대"
✅ "PF(부동산 프로젝트 파이낸싱) 금리 가산폭 0.3%p 확대"

❌ "리파이낸싱 리스크"
✅ "리파이낸싱(기존 대출 연장·재조달) 리스크"

반도체 용어(팹, 수율, HBM, OSAT, 2nm 등)는 풀이 불필요 — 독자가 반도체 업계인.

════════════════════════════════════
[규칙 2: 숫자 출처 라벨링 필수 — 할루시네이션 방지]
════════════════════════════════════
모든 수치 뒤에 라벨 필수:
- `[#5]` 형식 = 뉴스 N번에 직접 인용된 숫자 (사실)
- `[추정]` 형식 = 분석적 추측 (당신이 계산한 숫자)
- `[업계관행]` 형식 = 일반적 업계 상식/통계

예:
✅ "유가 92달러 [#5], 1개월 전 78달러 대비 +18% [추정]"
✅ "SK하이닉스 HBM4 매출비중 40% 돌파 여부 [업계관행: 통상 실적발표 시 공개]"

뉴스에 없는 숫자를 [뉴스 인용]으로 위장하면 실패. 애매하면 [추정] 쓰고 그 근거를 1줄 설명.

════════════════════════════════════
[규칙 3: 액션 3단 계층]
════════════════════════════════════
각 TOP 이슈의 action은 3가지 버전 제공:
- `action_30s`: 지하철에서 30초. "X 뉴스 저장", "Y 종목 관찰 리스트 추가"
- `action_10m`: 점심시간 10분. "본인 퇴직연금 구성 확인", "포트폴리오 X비중 체크"
- `action_1h`: 주말 1시간. 선택적. "X 리서치 리포트 1개 읽기" 같은 학습형

비현실적 액션 금지:
❌ "CFO에게 서면 요청" (평사원이 못함)
❌ "사기업 정보공개청구" (공공기관 대상만 가능)
❌ "LinkedIn에서 TSMC 엔지니어 포스트 검색" (효용 대비 시간 낭비)

═��══════════════════════════════════
[규칙 4: 톤 캘리브레이션 — 매일 경보 금지]
════════════════════════════════════
오늘 뉴스의 실제 위험도를 판단하고 톤을 조정:
- **평상시(NORMAL)**: 서술적. "~로 보입니다", "변화가 관찰됩니다"
- **주시(WATCH)**: 경계적. "변곡 신호", "지표 확인 필요"
- **경보(ALERT)**: 강한 톤. "즉시 대응", "리스크 현실화". 이건 10일에 1번 수준

"연쇄 부도", "도미노", "붕괴", "스파이럴" 같은 격한 표현은 ALERT일 때만.
평상시에도 이런 단어 쓰면 독자가 둔감해짐 → 실패.

════════════════════════════════════
[규칙 5: 일반 직장인 다양한 직군 배려]
════════════════════════════════════
독자는 다양한 직군의 직장인. 특정 업계(반도체, 금융 등) 편향 금지.
공통 관심사로 접근: 연봉, 이직, 보너스, 퇴직연금, 주식/부동산/환율.
특정 업계 심층 분석이 필요한 경우에도 "해당 업계 종사자라면"처럼 조건부로 표현.

════════════════════════════════════
[규칙 6: 분량 엄수]
════════════════════════════════════
TOP 3 합쳐 800자, cross_insight 150자, contrarian 100자, quick_picks 150자.
전체 텔레그램 메시지가 1200자 이내가 되어야 함. 3분 출근길 기준.

════════════════════════════════════
[규칙 7: 각 이슈는 뉴스→분석→예측→액션 4단 구조]
════════════════════════════════════
각 TOP 이슈는 반드시 4개 필드를 모두 채운다:

(A) **news_fact**: 뉴스에서 실제로 벌어진 사건만. 2문장. 팩트만. 해석 금지.
    예: "9/25 호르무즈 해협 인근 유조선 2척 피격. 이란-이스라엘 긴장 고조로 해운사들 파나마 운하 우회 개시, 척당 통과료 59억원."

(B) **analysis**: 이게 무슨 뜻인지. 2-3문장. 용어 풀이 필수.
    - 시장 통념(consensus)과 놓친 부분(reality_check)을 대조
    예: "시장은 '단기 유가 스파이크'로만 본다. 하지만 실제는 로지스틱스(물류 비용 체계) 고정비 상승으로 전환되는 구조 변화다..."

(C) **prediction**: 앞으로 무슨 일이 생길지. 시간축별 예측. 반드시 확률/조건부 표현 사용.
    - 1개월 내: 확률 높은 것 (70%+)
    - 3개월 내: 가능성 있는 것 (40-60%)
    - 1년 내: 장기 시나리오 (조건부)
    예: "1개월 내: 컨테이너 운임 지수 SCFI 1,500p 돌파 확률 70%. 3개월 내: 부산항 처리량 전년 대비 -8%. 1년 내: 유가 80달러 이상 고착 시 PF(프로젝트파이낸싱) 금리 0.5%p 추가 상승."

(D) **action**: 실행. 30초/10분/1시간 3단계.

════════════════════════════════════
[출력 JSON 형식 — 마크다운 없이 순수 JSON만]
════════════════════════════════════
{{
  "tldr": {{
    "one_line": "오늘의 핵심을 20자 이내로 (실제 숫자 포함: 예 '나스닥 +6.8% vs 한국 수출 압박')",
    "career_impact": "HIGH/MED/LOW",
    "asset_impact": "HIGH/MED/LOW",
    "read_time": "30초 훑기 / 5분 정독 / 15분 심층 중 택1",
    "alert_level": "NORMAL / WATCH / ALERT 중 택1"
  }},
  "market_snapshot": {{
    "kor_summary": "한국 증시 한 줄 요약 (코스피/코스닥 값과 변동률 포함, 맥락 해석 1문장)",
    "us_summary": "미국 증시 한 줄 요약 (S&P/나스닥 값과 변동률, 맥락 1문장)",
    "fx_oil": "환율·유가 한 줄 (원/달러, WTI 변화와 의미)",
    "key_signal": "오늘 시장이 말하는 핵심 시그널 1문장"
  }},
  "headline": "숫자+동사 포함, 30자 이내",
  "mood": "RISK / OPPORTUNITY / MIXED 중 택1",
  "mood_reason": "한 문장, 수치 1개 포함, 어려운 용어 풀이 포함",
  "top3": [
    {{
      "rank": 1,
      "title": "20자 이내, 핵심 동사 포함",
      "news_fact": "뉴스에서 실제로 벌어진 사건만. 2문장. 해석/의견 금지. 팩트만.",
      "analysis": {{
        "consensus": "시장 통설 1문장 (쉽게)",
        "reality_check": "통설이 놓친 것 + 진짜 의미. 2문장. 용어 풀이 필수."
      }},
      "prediction": {{
        "short_term": "1개월 내 예상. 확률 높은 것 (70%+). 구체 수치/지표/날짜.",
        "mid_term": "3개월 내 예상. 가능성 있는 것 (40-60%). 조건부 표현.",
        "long_term": "1년 내 장기 시나리오. 조건부 ('~가 지속되면 ~').",
        "key_watch": "예측 검증할 1개 지표 (지표명/발표일/임계값)"
      }},
      "impact": "HIGH / MED",
      "sources": [뉴스번호],
      "why_it_matters": "독자에게 이게 왜 돈/커리어에 직결되는지. 1문장. 쉬운 말로.",
      "action_30s": "지하철에서 30초 안에 할 수 있는 것 1개",
      "action_10m": "점심에 10분이면 되는 것 1개",
      "action_1h": "주말에 1시간 투자할 가치 있는 것 1개 (선택)",
      "tags": ["태그1", "태그2"]
    }}
  ],
  "cross_insight": {{
    "chain": "A → B → C. 쉬운 말로 3문장. 용어는 괄호 풀이.",
    "hidden_risk": "이 체인을 놓치면 어떤 손실이 생기는지 1문장 (평이하게)"
  }},
  "contrarian_take": "오늘 '시장이 틀린 부분' 1개. 2문장. 근거는 주어진 뉴스.",
  "quick_picks": [
    {{
      "emoji": "이모지",
      "text": "팩트 + 숫자 + 의미. 숫자에 출처 라벨 필수.",
      "so_what": "한국 직장인에게 뜻하는 바 1문장",
      "sources": [뉴스번호]
    }}
  ],
  "closing": "오늘 독자가 '머리에 심어둘' 한 줄. 격려 X, 관점 제시 O. 2문장."
}}

════════════════════════════════════
[출력 전 자가 검증]
════════════════════════════════════
□ 각 TOP 이슈에 news_fact(뉴스 사실), analysis(분석), prediction(예측), action(실행) 4가지가 모두 있는가?
□ news_fact는 해석 없이 팩트만 담았는가?
□ prediction이 1개월/3개월/1년 세 시간축을 모두 포함하는가?
□ prediction에 확률 표현이나 조건부 표현이 있는가?
□ TL;DR이 20자 이내로 한 줄 요약되는가?
□ 독자가 "오늘 영향 있음/없음"을 3초 안에 판단 가능한가?
□ 금융 용어가 전부 괄호 풀이되었는가?
□ 각 이슈에 30s/10m/1h 액션이 있고 모두 현실적인가?
□ 톤이 오늘 실제 위험도에 맞는가? (매일 ALERT 아닌가?)
□ cross_insight가 A→B→C 인과 체인인가?

한 개라도 NO면 다시 써라. 순수 JSON만 출력."""

    raw = _call_claude(prompt)
    # 첫 '{'부터 raw_decode로 첫 완전한 JSON 객체만 파싱 (뒤에 여분 텍스트 있어도 OK)
    start = raw.find('{')
    if start == -1:
        raise ValueError("브리핑 JSON 파싱 실패 — 중괄호 없음")
    decoder = json.JSONDecoder()
    try:
        briefing, _ = decoder.raw_decode(raw[start:])
    except json.JSONDecodeError as e:
        # 코드블록이면 벗겨내고 재시도
        cleaned = re.sub(r'```(?:json)?\s*', '', raw[start:]).strip().rstrip('`')
        briefing, _ = decoder.raw_decode(cleaned)

    # 소스 매핑
    for item in briefing.get('top3', []):
        refs = []
        for si in item.get('sources', []):
            if si in news_index:
                a = news_index[si]
                refs.append({'title': a['title'], 'source': a['source_name'], 'link': a['link']})
        item['news_refs'] = refs

    for item in briefing.get('quick_picks', []):
        refs = []
        for si in item.get('sources', []):
            if si in news_index:
                a = news_index[si]
                refs.append({'title': a['title'], 'source': a['source_name'], 'link': a['link']})
        item['news_refs'] = refs

    briefing['date'] = date_str
    briefing['weekday'] = weekday
    briefing['news_count'] = total_count

    # 팩트 검증 — 미확인 기업 언급 시 quick_picks 제거 및 경고 추가
    briefing = _verify_claims(briefing, articles, stocks)
    if briefing.get('_warnings'):
        print("=== 검증 경고 ===")
        for w in briefing['_warnings']:
            print(f"  - {w}")

    # DB 저장
    now = datetime.now().isoformat()
    try:
        with _get_db() as conn:
            for item in briefing.get('top3', []):
                conn.execute('''
                    INSERT INTO issues(date,category,title,summary,impact,tags,short_term,mid_term,long_term,opportunities,risk,source,news_sources,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ''', (date_str, 'briefing', item['title'], item['summary'], item['impact'],
                      json.dumps(item.get('tags', []), ensure_ascii=False),
                      '', '', '',
                      json.dumps([], ensure_ascii=False),
                      '', 'briefing',
                      json.dumps(item.get('news_refs', []), ensure_ascii=False), now))
    except Exception:
        pass

    return briefing


def _clean_labels(text):
    """[#N], [추정], [업계관행] 등의 출처 라벨을 본문에서 제거."""
    if not text:
        return text
    # [# ... ] 어떤 형태든 (여러 번호, 공백, 쉼표, #기호 복수 모두 포함)
    text = re.sub(r'\s*\[#[^\[\]]*\]', '', text)
    # [추정], [업계관행], [예측], [분석], [출처...] 같은 메타 라벨
    text = re.sub(r'\s*\[(추정|업계관행|예측|분석|출처|마켓 데이터|실제 마켓 데이터)[^\]]*\]', '', text)
    # 괄호 안에 #숫자/번호만
    text = re.sub(r'\s*\(#[^\)]*\)', '', text)
    # "숫자 라벨: ... = ..." 같은 메타 안내 문구 제거
    text = re.sub(r'숫자 라벨:[^\n]*', '', text)
    # 중복 공백/줄바꿈 정리
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r' +([,.!?])', r'\1', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def _verify_claims(briefing, articles, stocks=None):
    """브리핑에 언급된 기업명/인명이 뉴스에 실제 존재하는지 검증.
    - 없는 기업명 언급 시 해당 필드에 ⚠️ 표시
    - quick_picks 중 팩트 검증 실패한 것은 제거
    """
    # 뉴스 제목 + 출처명 통합 텍스트
    news_corpus = ' '.join([a.get('title','') + ' ' + a.get('source_name','') for a in articles or []])

    # 시세 데이터에 있는 종목명
    stock_names = set((stocks or {}).keys())

    # 검증 대상 기업명 리스트 (한국 주요 기업들)
    entities = [
        '삼성전자','삼성전기','삼성바이오','삼성디스플레이',
        'SK하이닉스','SK이노베이션','SK텔레콤','SK에너지',
        '현대차','현대모비스','기아','LG에너지솔루션','LG전자','LG화학','LG디스플레이',
        '네이버','NAVER','카카오','두산로보틱스','두산','한화','포스코','POSCO',
        'HMM','HD현대','한진','대한항공','아시아나','CJ','신세계','롯데',
        'TSMC','엔비디아','NVIDIA','Apple','Google','Amazon','Meta','Microsoft',
        '애플','구글','아마존','메타','마이크로소프트','인텔','Intel',
        '이재명','트럼프','바이든','시진핑','푸틴','이준석','윤석열',
        '이란','이스라엘','중국','일본','미국','러시아','우크라이나',
        '호르무즈','파나마운하','수에즈',
    ]

    def _check_text(text):
        """텍스트에 언급된 entity 중 뉴스에 없는 것을 찾는다"""
        if not text or not isinstance(text, str):
            return []
        missing = []
        for ent in entities:
            if ent in text:
                # 주식 데이터나 뉴스 코퍼스에 있으면 OK
                if ent in news_corpus or ent in stock_names:
                    continue
                # 짧은 이름은 부분 매칭 확인
                if any(ent in a.get('title','') for a in articles or []):
                    continue
                missing.append(ent)
        return missing

    warnings = []

    # quick_picks 검증 — 팩트 못 찾으면 제거
    if briefing.get('quick_picks'):
        valid_picks = []
        for qp in briefing['quick_picks']:
            missing = _check_text(qp.get('text','')) + _check_text(qp.get('so_what',''))
            if missing:
                warnings.append(f"Quick pick에 미확인 기업: {missing} — 제거됨")
                continue
            valid_picks.append(qp)
        briefing['quick_picks'] = valid_picks

    # top3 검증 — 미확인 기업명 있으면 news_fact 시작에 경고
    for item in briefing.get('top3', []) or []:
        fields_to_check = [
            item.get('news_fact',''),
            item.get('why_it_matters',''),
            item.get('title',''),
        ]
        analysis = item.get('analysis', {})
        if isinstance(analysis, dict):
            fields_to_check += [analysis.get('consensus',''), analysis.get('reality_check','')]
        pred = item.get('prediction', {})
        if isinstance(pred, dict):
            fields_to_check += [pred.get('short_term',''), pred.get('mid_term',''), pred.get('long_term',''), pred.get('key_watch','')]

        all_missing = set()
        for f in fields_to_check:
            for m in _check_text(f):
                all_missing.add(m)
        if all_missing:
            warnings.append(f"Top {item.get('rank','?')}에 미확인 기업: {list(all_missing)}")
            item['_verification_warning'] = list(all_missing)

    # contrarian, closing 등도 확인
    for k in ['contrarian_take', 'closing']:
        if k in briefing:
            missing = _check_text(briefing[k])
            if missing:
                warnings.append(f"{k}에 미확인 기업: {missing}")

    briefing['_warnings'] = warnings
    return briefing


def _clean_briefing(b):
    """브리핑 객체의 모든 텍스트 필드에서 출처 라벨을 제거한 복사본 반환"""
    import copy
    b = copy.deepcopy(b)

    def clean_str(v):
        return _clean_labels(v) if isinstance(v, str) else v

    # 최상위 텍스트 필드
    for k in ['headline', 'mood_reason', 'contrarian_take', 'closing']:
        if k in b:
            b[k] = clean_str(b[k])

    # tldr
    if b.get('tldr'):
        for k in list(b['tldr'].keys()):
            b['tldr'][k] = clean_str(b['tldr'][k])

    # market_snapshot
    if b.get('market_snapshot'):
        for k in list(b['market_snapshot'].keys()):
            b['market_snapshot'][k] = clean_str(b['market_snapshot'][k])

    # top3
    for item in b.get('top3', []) or []:
        for k in ['title', 'news_fact', 'why_it_matters', 'action_30s', 'action_10m', 'action_1h', 'action']:
            if k in item:
                item[k] = clean_str(item[k])
        if isinstance(item.get('analysis'), dict):
            for k in list(item['analysis'].keys()):
                item['analysis'][k] = clean_str(item['analysis'][k])
        if isinstance(item.get('prediction'), dict):
            for k in list(item['prediction'].keys()):
                item['prediction'][k] = clean_str(item['prediction'][k])
        # legacy
        for k in ['consensus', 'reality_check', 'watch_this']:
            if k in item:
                item[k] = clean_str(item[k])

    # cross_insight
    if isinstance(b.get('cross_insight'), dict):
        for k in list(b['cross_insight'].keys()):
            b['cross_insight'][k] = clean_str(b['cross_insight'][k])
    elif isinstance(b.get('cross_insight'), str):
        b['cross_insight'] = clean_str(b['cross_insight'])

    # quick_picks
    for qp in b.get('quick_picks', []) or []:
        for k in ['text', 'so_what']:
            if k in qp:
                qp[k] = clean_str(qp[k])

    return b


def format_telegram(b):
    """텔레그램 메시지 포맷 — 깊이+가독성"""
    b = _clean_briefing(b)  # 라벨 제거
    mood_icon = {'RISK': '🔴', 'OPPORTUNITY': '🟢', 'MIXED': '🟡'}.get(b['mood'], '⚪')
    tldr = b.get('tldr', {}) or {}
    alert = tldr.get('alert_level', 'NORMAL')
    alert_icon = {'ALERT': '🚨', 'WATCH': '⚠️', 'NORMAL': '🔵'}.get(alert, '🔵')

    lines = []
    # 헤더
    lines.append(f"📡 {b['date']} ({b['weekday']}) 모닝 브리핑")
    lines.append(f"")

    # TL;DR 박스 (맨 위 핵심) - 있으면 표시, 없으면 fallback
    if tldr:
        lines.append(f"━ 오늘 한 줄 ━")
        lines.append(f"{alert_icon} {tldr.get('one_line', b['headline'])}")
        lines.append(f"")
        lines.append(f"• 커리어 영향: {tldr.get('career_impact','—')}")
        lines.append(f"• 자산 영향: {tldr.get('asset_impact','—')}")
        lines.append(f"• 권장 독서: {tldr.get('read_time','5분 정독')}")
        lines.append(f"• 오늘 상태: {alert}")
        lines.append(f"")
        lines.append(f"━━━━━━━━━━━━━━━━")
    else:
        lines.append(f"{mood_icon} {b['headline']}")
        lines.append(f"{b.get('mood_reason','')}")
        lines.append(f"")

    # 실시간 마켓 스냅샷
    ms = b.get('market_snapshot', {})
    if ms:
        lines.append(f"")
        lines.append(f"📊 오늘의 마켓")
        if ms.get('kor_summary'): lines.append(f"• 🇰🇷 {ms['kor_summary']}")
        if ms.get('us_summary'):  lines.append(f"• 🇺🇸 {ms['us_summary']}")
        if ms.get('fx_oil'):      lines.append(f"• 💱 {ms['fx_oil']}")
        if ms.get('key_signal'):
            lines.append(f"")
            lines.append(f"🎯 시그널: {ms['key_signal']}")
        lines.append(f"")
        lines.append(f"━━━━━━━━━━━━━━━━")

    # TOP 3
    lines.append(f"▣ 오늘의 핵심 3가지")
    lines.append(f"")

    for item in b.get('top3', []):
        imp = '🔴' if item['impact'] == 'HIGH' else '🟠'
        lines.append(f"{imp} {item['rank']}. {item['title']}")
        lines.append(f"")

        # 📰 뉴스 (팩트)
        if item.get('news_fact'):
            lines.append(f"📰 뉴스")
            lines.append(f"{item['news_fact']}")
            lines.append(f"")

        # 🔍 분석
        analysis = item.get('analysis', {})
        if isinstance(analysis, dict) and (analysis.get('consensus') or analysis.get('reality_check')):
            lines.append(f"🔍 분석")
            if analysis.get('consensus'):
                lines.append(f"• 시장 해석: {analysis['consensus']}")
            if analysis.get('reality_check'):
                lines.append(f"• 놓친 부분: {analysis['reality_check']}")
            lines.append(f"")
        elif item.get('consensus') or item.get('reality_check'):
            # legacy fallback
            lines.append(f"🔍 분석")
            if item.get('consensus'):
                lines.append(f"• 시장 해석: {item['consensus']}")
            if item.get('reality_check'):
                lines.append(f"• 놓친 부분: {item['reality_check']}")
            lines.append(f"")

        # 🔮 예측 — 시간축 레이블 깔끔하게 (중복 제거)
        pred = item.get('prediction', {})
        if isinstance(pred, dict):
            lines.append(f"🔮 예측")
            def _strip_prefix(txt, *prefixes):
                """'1개월 내:' 같은 중복 프리픽스 제거"""
                if not txt: return txt
                t = txt.strip()
                for p in prefixes:
                    if t.startswith(p):
                        t = t[len(p):].strip()
                        # 선두 콜론/구두점 추가 제거
                        t = t.lstrip(':·-— ').strip()
                        break
                return t
            if pred.get('short_term'):
                v = _strip_prefix(pred['short_term'], '1개월 내', '단기', '1개월')
                lines.append(f"▸ 1개월 │ {v}")
            if pred.get('mid_term'):
                v = _strip_prefix(pred['mid_term'], '3개월 내', '중기', '3개월')
                lines.append(f"▸ 3개월 │ {v}")
            if pred.get('long_term'):
                v = _strip_prefix(pred['long_term'], '1년 내', '장기', '1년')
                lines.append(f"▸ 1년    │ {v}")
            if pred.get('key_watch'):
                lines.append(f"")
                lines.append(f"👁 체크포인트 │ {pred['key_watch']}")
            lines.append(f"")
        elif item.get('watch_this'):
            lines.append(f"👁 지켜볼 것")
            lines.append(f"{item['watch_this']}")
            lines.append(f"")

        if item.get('why_it_matters'):
            lines.append(f"💡 나한테 왜 중요?")
            lines.append(f"{item['why_it_matters']}")
            lines.append(f"")

        # ✅ 액션 3단 — 시간대별 이모지로 구분
        a30 = item.get('action_30s') or ''
        a10 = item.get('action_10m') or ''
        a1h = item.get('action_1h') or ''
        if not (a30 or a10 or a1h) and item.get('action'):
            a10 = item['action']

        if a30 or a10 or a1h:
            lines.append(f"✅ 실행 옵션")
            if a30:
                lines.append(f"⚡ 지금 바로 (30초)")
                lines.append(f"   {a30}")
            if a10:
                lines.append(f"☕ 점심에 (10분)")
                lines.append(f"   {a10}")
            if a1h:
                lines.append(f"📚 주말에 (1시간)")
                lines.append(f"   {a1h}")
            lines.append(f"")

        refs = item.get('news_refs', [])
        if refs:
            src_text = f"{refs[0]['source']}" + (f" 외 {len(refs)-1}건" if len(refs) > 1 else "")
            lines.append(f"📎 {src_text}")
        lines.append(f"")
        lines.append(f"────────────")
        lines.append(f"")

    # Cross Insight
    lines.append(f"🔗 숨은 연결고리")
    ci = b.get('cross_insight', {})
    if isinstance(ci, dict):
        lines.append(f"{ci.get('chain', '')}")
        if ci.get('hidden_risk'):
            lines.append(f"")
            lines.append(f"⚠️ 놓치면: {ci['hidden_risk']}")
    else:
        lines.append(f"{ci}")
    lines.append(f"")

    # Contrarian
    if b.get('contrarian_take'):
        lines.append(f"━━━━━━━━━━━━━━━━")
        lines.append(f"🔥 다른 각도")
        lines.append(f"{b['contrarian_take']}")
        lines.append(f"")

    # Quick Picks
    if b.get('quick_picks'):
        lines.append(f"━━━━━━━━━━━━━━━━")
        lines.append(f"⚡ 짧게 체크")
        for qp in b.get('quick_picks', []):
            lines.append(f"{qp.get('emoji','')} {qp.get('text','')}")
            if qp.get('so_what'):
                lines.append(f"   → {qp['so_what']}")
        lines.append(f"")

    # 마무리
    lines.append(f"━━━━━━━━━━━━━━━━")
    if b.get('closing'):
        lines.append(f"💬 {b['closing']}")
        lines.append(f"")
    lines.append(f"📊 뉴스 {b['news_count']}건 + 실시간 마켓 지수 기반")

    return "\n".join(lines)


def _render_cross(ci):
    if isinstance(ci, dict):
        return ci.get('chain', '')
    return ci or ''

def _render_hidden_risk(ci):
    if isinstance(ci, dict) and ci.get('hidden_risk'):
        return f'<div style="background:#2a0d0d;border-left:3px solid #ff4444;border-radius:4px;padding:10px 14px;margin-top:10px"><div style="color:#ff6b6b;font-size:11px;letter-spacing:1px;margin-bottom:4px">⚠️ 놓치면 생기는 일</div><div style="color:#ffcccc;font-size:13px">{ci["hidden_risk"]}</div></div>'
    return ''

def _render_contrarian(ct):
    if not ct:
        return ''
    return f'''<div style="background:#1a0d1a;border:1px solid #ff4081;border-radius:8px;padding:20px;margin:20px 0">
      <div style="font-size:11px;letter-spacing:2px;color:#ff4081;text-transform:uppercase;margin-bottom:12px;font-weight:700">🔥 컨트라리안 뷰 (시장이 틀린 부분)</div>
      <p style="color:#ffc1e3;font-size:14px;line-height:1.8;margin:0">{ct}</p>
    </div>'''

def format_html_email(b):
    """이메일/웹용 HTML 포맷"""
    b = _clean_briefing(b)  # 라벨 제거
    mood_icon = {'RISK': '🔴', 'OPPORTUNITY': '🟢', 'MIXED': '🟡'}.get(b['mood'], '⚪')
    mood_color = {'RISK': '#ff4444', 'OPPORTUNITY': '#00c853', 'MIXED': '#ffab00'}.get(b['mood'], '#999')

    top3_html = ""
    for item in b.get('top3', []):
        imp_color = '#ff4444' if item['impact'] == 'HIGH' else '#ff9800'
        refs_html = ""
        for ref in item.get('news_refs', []):
            refs_html += f'<a href="{ref["link"]}" style="color:#64b5f6;text-decoration:none;font-size:12px">📰 {ref["title"]} — {ref["source"]}</a><br>'
        tags_html = " ".join([f'<span style="background:#1a2a3a;color:#90caf9;padding:2px 8px;border-radius:3px;font-size:11px">#{t}</span>' for t in item.get('tags', [])])

        consensus_block = ""
        if item.get('consensus'):
            consensus_block = f"""
            <div style="background:#161b22;border-left:3px solid #8b949e;border-radius:4px;padding:10px 14px;margin-bottom:8px">
              <div style="color:#8b949e;font-size:11px;letter-spacing:1px;margin-bottom:4px">📢 시장 해석 (CONSENSUS)</div>
              <div style="color:#c9d1d9;font-size:13px">{item['consensus']}</div>
            </div>"""

        reality_block = ""
        if item.get('reality_check'):
            reality_block = f"""
            <div style="background:#2a1a0d;border-left:3px solid #ff9800;border-radius:4px;padding:10px 14px;margin-bottom:8px">
              <div style="color:#ff9800;font-size:11px;letter-spacing:1px;margin-bottom:4px">🎯 리얼리티 체크 (놓친 부분)</div>
              <div style="color:#ffe0b2;font-size:13px;line-height:1.6">{item['reality_check']}</div>
            </div>"""

        watch_block = ""
        if item.get('watch_this'):
            watch_block = f"""
            <div style="background:#0d1f2a;border-left:3px solid #58a6ff;border-radius:4px;padding:10px 14px;margin-bottom:12px">
              <div style="color:#58a6ff;font-size:11px;letter-spacing:1px;margin-bottom:4px">👁 이번 주 지켜볼 것</div>
              <div style="color:#c9d1d9;font-size:13px">{item['watch_this']}</div>
            </div>"""

        top3_html += f"""
        <div style="background:#0d1117;border:1px solid #21262d;border-radius:8px;padding:20px;margin-bottom:20px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:14px">
            <span style="background:{imp_color};color:white;padding:2px 10px;border-radius:4px;font-size:11px;font-weight:700">#{item['rank']} {item['impact']}</span>
            <span style="font-size:16px;font-weight:600;color:#e6edf3">{item.get('title','')}</span>
          </div>
          {consensus_block}
          {reality_block}
          {watch_block}
          <div style="background:#161b22;border-radius:6px;padding:12px;margin-bottom:10px">
            <div style="color:#ffab00;font-size:12px;margin-bottom:4px">💡 <b>나한테 왜 중요?</b></div>
            <div style="color:#c9d1d9;font-size:13px">{item.get('why_it_matters','')}</div>
          </div>
          <div style="background:#0d2818;border:1px solid #1a5030;border-radius:6px;padding:12px;margin-bottom:10px">
            <div style="color:#3fb950;font-size:13px">✅ <b>7일 내 액션:</b> {item.get('action','')}</div>
          </div>
          <div style="margin-bottom:10px">{tags_html}</div>
          <div style="border-top:1px solid #21262d;padding-top:10px">{refs_html}</div>
        </div>"""

    qp_html = ""
    for qp in b.get('quick_picks', []):
        sw = qp.get('so_what', '')
        sw_html = f'<div style="margin-left:22px;margin-top:4px;color:#ffab00;font-size:12px">└ {sw}</div>' if sw else ''
        qp_html += f'''<div style="padding:10px 0;border-bottom:1px solid #21262d">
          <div style="color:#c9d1d9;font-size:13px;line-height:1.5">{qp.get("emoji","")} {qp.get("text","")}</div>
          {sw_html}
        </div>'''

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#010409;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif">
<div style="max-width:640px;margin:0 auto;padding:20px">
  <div style="text-align:center;padding:30px 0 20px">
    <div style="font-size:11px;letter-spacing:3px;color:#8b949e;text-transform:uppercase">Morning Intelligence Briefing</div>
    <div style="font-size:28px;font-weight:700;color:#e6edf3;margin:8px 0">{mood_icon} {b['headline']}</div>
    <div style="font-size:13px;color:#8b949e">{b['date']} ({b['weekday']}) · 뉴스 {b['news_count']}건 분석</div>
    <div style="display:inline-block;margin-top:10px;padding:4px 16px;border-radius:20px;border:1px solid {mood_color};color:{mood_color};font-size:12px">{b['mood']} — {b['mood_reason']}</div>
  </div>
  <div style="font-size:11px;letter-spacing:2px;color:#58a6ff;text-transform:uppercase;margin:30px 0 16px;font-weight:700">■ Today's Top 3</div>
  {top3_html}

  <!-- Cross Insight (인과 체인) -->
  <div style="background:#0d1117;border:1px solid #1d4ed8;border-radius:8px;padding:20px;margin:20px 0">
    <div style="font-size:11px;letter-spacing:2px;color:#58a6ff;text-transform:uppercase;margin-bottom:12px;font-weight:700">🔗 Cross Insight (인과 체인)</div>
    <p style="color:#c9d1d9;font-size:14px;line-height:1.8;margin:0 0 12px 0">{_render_cross(b.get('cross_insight',''))}</p>
    {_render_hidden_risk(b.get('cross_insight',''))}
  </div>

  <!-- Contrarian Take -->
  {_render_contrarian(b.get('contrarian_take',''))}

  <div style="font-size:11px;letter-spacing:2px;color:#58a6ff;text-transform:uppercase;margin:24px 0 12px;font-weight:700">⚡ Quick Picks</div>
  <div style="background:#0d1117;border:1px solid #21262d;border-radius:8px;padding:12px 16px">{qp_html}</div>
  <div style="text-align:center;padding:30px 0;color:#8b949e;font-size:13px;line-height:1.8">
    <div style="font-style:italic;color:#c9d1d9;font-size:14px;margin-bottom:16px">"{b.get('closing','')}"</div>
    <div style="width:40px;height:1px;background:#21262d;margin:16px auto"></div>
    <div style="font-size:11px;letter-spacing:1px;color:#484f58">GLOBAL INTELLIGENCE SYSTEM</div>
  </div>
</div>
</body></html>"""


if __name__ == '__main__':
    print("브리핑 생성 중...")
    b = generate_briefing()
    print(format_telegram(b))
    with open('briefing_preview.html', 'w', encoding='utf-8') as f:
        f.write(format_html_email(b))
    print("\nbriefing_preview.html 생성 완료")
