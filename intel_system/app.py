import sqlite3, json, requests, re, xml.etree.ElementTree as ET
from flask import Flask, render_template, request, jsonify
from datetime import datetime
from urllib.parse import quote
from config import CLAUDE_API_KEY, CRON_SECRET

app = Flask(__name__)
DB = 'intel.db'

# Claude Haiku 4.5 pricing (USD per token)
PRICE_INPUT  = 0.80 / 1_000_000   # $0.80 / 1M tokens
PRICE_OUTPUT = 4.00 / 1_000_000   # $4.00 / 1M tokens
api_usage = {'input_tokens': 0, 'output_tokens': 0, 'cost_usd': 0.0, 'calls': 0}

def get_db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                category TEXT,
                title TEXT,
                summary TEXT,
                impact TEXT,
                tags TEXT,
                short_term TEXT,
                mid_term TEXT,
                long_term TEXT,
                opportunities TEXT,
                risk TEXT,
                source TEXT DEFAULT 'ai',
                news_sources TEXT DEFAULT '[]',
                memo TEXT DEFAULT '',
                starred INTEGER DEFAULT 0,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                category TEXT,
                issue_count INTEGER,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT,
                count INTEGER DEFAULT 1,
                last_seen TEXT,
                UNIQUE(keyword)
            );
            CREATE TABLE IF NOT EXISTS briefings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                headline TEXT,
                payload TEXT,
                rating INTEGER DEFAULT 0,
                feedback TEXT DEFAULT '',
                grade TEXT DEFAULT '',
                created_at TEXT
            );
        ''')

def call_claude(prompt):
    url = 'https://api.anthropic.com/v1/messages'
    headers = {
        'x-api-key': CLAUDE_API_KEY,
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json'
    }
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}]
    }
    r = requests.post(url, json=body, headers=headers, timeout=60)
    r.raise_for_status()
    data = r.json()
    # Track usage
    usage = data.get('usage', {})
    inp = usage.get('input_tokens', 0)
    out = usage.get('output_tokens', 0)
    cost = inp * PRICE_INPUT + out * PRICE_OUTPUT
    api_usage['input_tokens'] += inp
    api_usage['output_tokens'] += out
    api_usage['cost_usd'] += cost
    api_usage['calls'] += 1
    return data['content'][0]['text']

NEWS_QUERIES = {
    'economy': ['글로벌 경제', '금융시장 중앙은행', '환율 인플레이션 무역'],
    'geo':     ['지정학 국제분쟁', '외교 군사 안보', '미중 관계'],
    'tech':    ['AI 반도체 기술', '에너지전환 디지털', '바이오테크 우주'],
    'society': ['인구구조 노동시장', '소비트렌드 사회변화', 'MZ세대 트렌드'],
    'invest':  ['글로벌 투자 트렌드', '주식시장 유망섹터', '스타트업 자산배분']
}

def fetch_news(category, max_articles=15):
    """Google News RSS에서 실제 뉴스 헤드라인을 수집 (무료)"""
    queries = NEWS_QUERIES.get(category, ['글로벌 경제'])
    articles = []
    seen_titles = set()
    for q in queries:
        try:
            url = f'https://news.google.com/rss/search?q={quote(q)}&hl=ko&gl=KR&ceid=KR:ko'
            r = requests.get(url, timeout=10)
            r.encoding = 'utf-8'
            root = ET.fromstring(r.content)
            for item in root.findall('.//item'):
                title = item.find('title').text or ''
                link = item.find('link').text or ''
                pub = item.find('pubDate').text or ''
                # 중복 제거
                if title not in seen_titles:
                    seen_titles.add(title)
                    # 제목에서 " - 매체명" 분리
                    parts = title.rsplit(' - ', 1)
                    headline = parts[0].strip()
                    source_name = parts[1].strip() if len(parts) > 1 else ''
                    articles.append({
                        'title': headline,
                        'source_name': source_name,
                        'link': link,
                        'pubDate': pub
                    })
        except Exception:
            continue
    return articles[:max_articles]

def extract_keywords(issues_list):
    for issue in issues_list:
        tags = issue.get('tags', [])
        for tag in tags:
            with get_db() as conn:
                conn.execute('''
                    INSERT INTO keywords(keyword, count, last_seen)
                    VALUES(?,1,?) ON CONFLICT(keyword)
                    DO UPDATE SET count=count+1, last_seen=?
                ''', (tag, datetime.now().isoformat(), datetime.now().isoformat()))

CATS = {
    'economy': '글로벌 경제, 금융시장, 중앙은행 정책, 무역, 인플레이션, 환율, 원자재',
    'geo':     '지정학적 긴장, 국제 외교, 군사 안보, 지역 분쟁, 강대국 관계',
    'tech':    'AI, 반도체, 에너지전환, 우주, 바이오테크, 디지털 전환',
    'society': '인구구조, 노동시장, 소비트렌드, 사회문제, MZ세대',
    'invest':  '글로벌 투자 트렌드, 유망 섹터, 한국 주식시장, 스타트업, 자산배분'
}
CAT_LABELS = {
    'economy':'글로벌 경제', 'geo':'지정학', 'tech':'기술혁신',
    'society':'사회변화', 'invest':'투자기회'
}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/landing')
def landing():
    """랜딩 페이지 — 샘플 브리핑을 서버에서 직접 주입"""
    import os
    sample_json = '{}'
    try:
        sample_path = os.path.join(os.path.dirname(__file__), 'sample_briefing.json')
        if os.path.exists(sample_path):
            with open(sample_path, 'r', encoding='utf-8') as f:
                d = json.load(f)
                sample_json = d.get('payload', '{}')
    except Exception:
        pass
    return render_template('landing.html', sample_briefing_json=sample_json)

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.json
    cat = data.get('category', 'economy')
    today = datetime.now().strftime('%Y-%m-%d')

    # 1단계: 실제 뉴스 수집 (무료)
    articles = fetch_news(cat)
    if not articles:
        return jsonify({'error': '뉴스를 수집할 수 없습니다. 네트워크를 확인하세요.'}), 500

    # 뉴스 목록을 텍스트로 정리
    news_text = ""
    for i, a in enumerate(articles, 1):
        news_text += f"{i}. [{a['source_name']}] {a['title']}\n   링크: {a['link']}\n   날짜: {a['pubDate']}\n\n"

    # 2단계: Claude에 실제 뉴스 기반 분석 요청
    prompt = f"""당신은 세계 최고의 경제·지정학 전문가입니다. 기준일: {today}

아래는 "{CAT_LABELS.get(cat,'')}" 분야의 실제 최신 뉴스 헤드라인입니다:
===
{news_text}
===

위 실제 뉴스를 바탕으로 가장 중요한 이슈 5개를 선별하고 분석해주세요.
반드시 위 뉴스에 근거한 분석만 하세요. 뉴스에 없는 내용을 지어내지 마세요.
각 이슈에 근거가 되는 뉴스 번호(sources)를 반드시 포함하세요.

순수 JSON 배열만 출력 (마크다운, 코드블록 없이):
[{{"title":"이슈 제목(40자 이내)","summary":"2문장 핵심 요약 (근거 뉴스 내용 기반)","impact":"HIGH 또는 MED 또는 LOW","tags":["태그1","태그2","태그3"],"sources":[1,3],"short_term":"단기 영향(1-3개월)","mid_term":"중기 영향(3-12개월)","long_term":"장기 영향(1-3년)","opportunities":["기회1","기회2","기회3"],"risk":"핵심 리스크 한 문장"}}]"""
    try:
        raw = call_claude(prompt)
        m = re.search(r'\[[\s\S]*\]', raw)
        if not m:
            return jsonify({'error': 'JSON 파싱 실패'}), 500
        issues = json.loads(m.group())
        now = datetime.now().isoformat()
        saved = []
        with get_db() as conn:
            for iss in issues:
                # 근거 뉴스 소스 매핑
                source_indices = iss.get('sources', [])
                news_refs = []
                for idx in source_indices:
                    if 1 <= idx <= len(articles):
                        a = articles[idx - 1]
                        news_refs.append({'title': a['title'], 'source': a['source_name'], 'link': a['link']})
                iss['news_sources'] = news_refs

                cur = conn.execute('''
                    INSERT INTO issues(date,category,title,summary,impact,tags,short_term,mid_term,long_term,opportunities,risk,source,news_sources,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ''', (today, cat, iss['title'], iss['summary'], iss['impact'],
                      json.dumps(iss.get('tags',[]), ensure_ascii=False),
                      iss.get('short_term',''), iss.get('mid_term',''), iss.get('long_term',''),
                      json.dumps(iss.get('opportunities',[]), ensure_ascii=False),
                      iss.get('risk',''), 'news',
                      json.dumps(news_refs, ensure_ascii=False), now))
                iss['id'] = cur.lastrowid
                saved.append(iss)
            conn.execute('INSERT INTO sessions(date,category,issue_count,created_at) VALUES(?,?,?,?)',
                         (today, cat, len(issues), now))
        extract_keywords(issues)
        return jsonify({'issues': saved, 'news_count': len(articles)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/paste', methods=['POST'])
def paste_analyze():
    data = request.json
    text = data.get('text', '')[:4000]
    cat = data.get('category', 'economy')
    today = datetime.now().strftime('%Y-%m-%d')
    prompt = f"""당신은 세계 최고의 경제·지정학 전문가입니다.

다음 뉴스/기사를 분석하여 핵심 이슈와 인사이트를 추출하세요:
---
{text}
---

순수 JSON 배열만 출력 (마크다운, 코드블록 없이):
[{{"title":"이슈 제목(40자 이내)","summary":"2문장 핵심 요약","impact":"HIGH 또는 MED 또는 LOW","tags":["태그1","태그2","태그3"],"short_term":"단기 영향(1-3개월)","mid_term":"중기 영향(3-12개월)","long_term":"장기 영향(1-3년)","opportunities":["기회1","기회2","기회3"],"risk":"핵심 리스크 한 문장"}}]"""
    try:
        raw = call_claude(prompt)
        m = re.search(r'\[[\s\S]*\]', raw)
        if not m:
            return jsonify({'error': '파싱 실패'}), 500
        issues = json.loads(m.group())
        now = datetime.now().isoformat()
        with get_db() as conn:
            for iss in issues:
                cur = conn.execute('''
                    INSERT INTO issues(date,category,title,summary,impact,tags,short_term,mid_term,long_term,opportunities,risk,source,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ''', (today, cat, iss['title'], iss['summary'], iss['impact'],
                      json.dumps(iss.get('tags',[]), ensure_ascii=False),
                      iss.get('short_term',''), iss.get('mid_term',''), iss.get('long_term',''),
                      json.dumps(iss.get('opportunities',[]), ensure_ascii=False),
                      iss.get('risk',''), 'paste', now))
                iss['id'] = cur.lastrowid
        extract_keywords(issues)
        return jsonify({'issues': issues})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/cron/health')
def cron_health():
    """UptimeRobot / cron-job.org 헬스 체크용 (슬립 방지)"""
    return jsonify({'ok': True, 'time': datetime.now().isoformat()})

def _generate_and_send_briefing_async(reuse_hours=6):
    """브리핑 생성 + 텔레그램 발송 (백그라운드 실행용).
    reuse_hours: 최근 N시간 내 브리핑 있으면 재사용 (비용 절감)"""
    try:
        from briefing import generate_briefing, format_telegram
        from telegram_bot import send_to_channel, TELEGRAM_TOKEN

        # 최근 브리핑 재사용 가능 여부
        b = None
        reused = False
        try:
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(hours=reuse_hours)).isoformat()
            with get_db() as conn:
                row = conn.execute('''
                    SELECT payload FROM briefings
                    WHERE created_at > ? ORDER BY created_at DESC LIMIT 1
                ''', (cutoff,)).fetchone()
                if row:
                    b = json.loads(row['payload'])
                    reused = True
                    print(f"[cron] 최근 {reuse_hours}h 내 브리핑 재사용 (비용 절감)")
        except Exception as e:
            print(f"[cron] 캐시 확인 실패: {e}")

        # 재사용 못하면 새로 생성
        if not b:
            print("[cron] 새 브리핑 생성 중...")
            b = generate_briefing()

        telegram_text = format_telegram(b)

        if TELEGRAM_TOKEN:
            send_to_channel(telegram_text)
            print(f"[cron] 텔레그램 발송 완료 (reused={reused})")

        # 새로 생성한 경우만 DB에 저장
        if not reused:
            now = datetime.now().isoformat()
            with get_db() as conn:
                conn.execute('''
                    INSERT INTO briefings(date, headline, payload, created_at)
                    VALUES(?,?,?,?)
                ''', (b.get('date',''), b.get('headline',''), json.dumps(b, ensure_ascii=False), now))
    except Exception as e:
        import traceback
        print(f"[cron] 오류: {e}\n{traceback.format_exc()}")


@app.route('/api/cron/daily-briefing', methods=['GET', 'POST'])
def cron_daily_briefing():
    """매일 오전 7시 외부 크론에서 호출 — 동기 실행.
    cron-job.org는 30초 타임아웃되지만 Render는 100초까지 작업 완료함.
    캐시 있으면 빠름(~2초), 없으면 100초.
    Usage: /api/cron/daily-briefing?token=<CRON_SECRET>"""
    token = request.args.get('token', '') or request.headers.get('X-Cron-Token', '')
    if not CRON_SECRET or token != CRON_SECRET:
        return jsonify({'error': 'unauthorized'}), 401

    if request.args.get('test') == '1':
        return jsonify({'ok': True, 'mode': 'test', 'message': '인증 확인됨'})

    reuse_hours = int(request.args.get('reuse_hours', '6'))

    try:
        from briefing import generate_briefing, format_telegram
        from telegram_bot import send_to_channel, TELEGRAM_TOKEN

        # 캐시 확인
        b = None
        reused = False
        try:
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(hours=reuse_hours)).isoformat()
            with get_db() as conn:
                row = conn.execute('''
                    SELECT payload FROM briefings
                    WHERE created_at > ? ORDER BY created_at DESC LIMIT 1
                ''', (cutoff,)).fetchone()
                if row:
                    b = json.loads(row['payload'])
                    reused = True
        except Exception:
            pass

        # 새로 생성
        if not b:
            b = generate_briefing()
            # DB 저장
            now = datetime.now().isoformat()
            with get_db() as conn:
                conn.execute('''
                    INSERT INTO briefings(date, headline, payload, created_at)
                    VALUES(?,?,?,?)
                ''', (b.get('date',''), b.get('headline',''), json.dumps(b, ensure_ascii=False), now))

        telegram_text = format_telegram(b)

        telegram_sent = False
        if TELEGRAM_TOKEN:
            result = send_to_channel(telegram_text)
            telegram_sent = bool(result)

        return jsonify({
            'ok': True,
            'reused': reused,
            'headline': b.get('headline',''),
            'telegram_sent': telegram_sent
        })
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

@app.route('/api/briefing', methods=['POST'])
def api_briefing():
    """프리미엄 모닝 브리핑 생성 + DB 저장"""
    try:
        from briefing import generate_briefing, format_telegram, format_html_email
        b = generate_briefing()
        telegram_text = format_telegram(b)
        html_text = format_html_email(b)

        # 브리핑 DB 저장 (평가용)
        now = datetime.now().isoformat()
        with get_db() as conn:
            cur = conn.execute('''
                INSERT INTO briefings(date, headline, payload, created_at)
                VALUES(?,?,?,?)
            ''', (b.get('date',''), b.get('headline',''), json.dumps(b, ensure_ascii=False), now))
            briefing_id = cur.lastrowid

        return jsonify({
            'briefing_id': briefing_id,
            'briefing': b,
            'telegram_text': telegram_text,
            'html': html_text
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/briefing/rate', methods=['POST'])
def rate_briefing():
    """브리핑 품질 평가 저장 (학습 데이터로 누적)"""
    data = request.json
    bid = data.get('briefing_id')
    rating = int(data.get('rating', 0))  # 1-5
    feedback = data.get('feedback', '')
    grade = data.get('grade', '')  # 'S', 'A', 'B', 'C'
    with get_db() as conn:
        conn.execute('UPDATE briefings SET rating=?, feedback=?, grade=? WHERE id=?',
                     (rating, feedback, grade, bid))
    return jsonify({'ok': True})

@app.route('/api/briefing/latest', methods=['GET'])
def briefing_latest():
    """가장 최근 저장된 브리핑 반환 (랜딩 페이지 샘플용 — 빠름)
    DB에 없으면 정적 sample_briefing.json fallback"""
    try:
        with get_db() as conn:
            row = conn.execute('''
                SELECT id, date, headline, payload, created_at
                FROM briefings ORDER BY created_at DESC LIMIT 1
            ''').fetchone()
        if row:
            d = dict(row)
            d['briefing'] = json.loads(d['payload'])
        else:
            # 정적 샘플 fallback
            import os
            sample_path = os.path.join(os.path.dirname(__file__), 'sample_briefing.json')
            if os.path.exists(sample_path):
                with open(sample_path, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                d['briefing'] = json.loads(d['payload'])
            else:
                return jsonify({'error': 'no_briefing_yet'}), 404

        try:
            from briefing import format_telegram, format_html_email
            d['telegram_text'] = format_telegram(d['briefing'])
            d['html'] = format_html_email(d['briefing'])
        except Exception:
            pass
        return jsonify(d)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/briefings/list', methods=['GET'])
def briefings_list():
    """저장된 전체 브리핑 목록"""
    with get_db() as conn:
        rows = conn.execute('''
            SELECT id, date, headline, rating, grade, created_at
            FROM briefings ORDER BY created_at DESC LIMIT 60
        ''').fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/briefings/<int:bid>', methods=['GET'])
def briefing_get(bid):
    """특정 브리핑 전체 내용"""
    with get_db() as conn:
        row = conn.execute('SELECT * FROM briefings WHERE id=?', (bid,)).fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404
    d = dict(row)
    try:
        d['briefing'] = json.loads(d['payload'])
    except Exception:
        d['briefing'] = None
    # telegram/html 포맷 재생성
    try:
        from briefing import format_telegram, format_html_email
        if d['briefing']:
            d['telegram_text'] = format_telegram(d['briefing'])
            d['html'] = format_html_email(d['briefing'])
    except Exception:
        pass
    return jsonify(d)

@app.route('/api/briefing/best', methods=['GET'])
def best_briefings():
    """평가 높은 브리핑 목록 (few-shot 학습용)"""
    with get_db() as conn:
        rows = conn.execute('''
            SELECT id, date, headline, grade, rating, feedback
            FROM briefings WHERE rating >= 4 OR grade IN ('S','A')
            ORDER BY rating DESC, created_at DESC LIMIT 20
        ''').fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/briefing/send', methods=['POST'])
def send_briefing():
    """생성된 브리핑을 텔레그램 채널로 발송"""
    try:
        data = request.json
        telegram_text = data.get('telegram_text', '')
        if not telegram_text:
            return jsonify({'error': '발송할 브리핑이 없습니다'}), 400
        from telegram_bot import send_to_channel, TELEGRAM_TOKEN
        if not TELEGRAM_TOKEN:
            return jsonify({'error': '텔레그램 봇 토큰이 설정되지 않았습니다'}), 400
        result = send_to_channel(telegram_text)
        return jsonify({'ok': True, 'result': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/telegram/setup', methods=['POST'])
def telegram_setup():
    """텔레그램 봇 토큰 및 채널 설정"""
    data = request.json
    token = data.get('token', '')
    channel = data.get('channel', '')
    from telegram_bot import set_token, set_channel, get_bot_info
    if token:
        set_token(token)
    if channel:
        set_channel(channel)
    try:
        info = get_bot_info()
        return jsonify({'ok': True, 'bot': info})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/history')
def history():
    cat = request.args.get('cat', '')
    impact = request.args.get('impact', '')
    limit = int(request.args.get('limit', 50))
    q = 'SELECT * FROM issues WHERE 1=1'
    params = []
    if cat:
        q += ' AND category=?'; params.append(cat)
    if impact:
        q += ' AND impact=?'; params.append(impact)
    q += ' ORDER BY created_at DESC LIMIT ?'; params.append(limit)
    with get_db() as conn:
        rows = conn.execute(q, params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['tags'] = json.loads(d['tags']) if d['tags'] else []
        d['opportunities'] = json.loads(d['opportunities']) if d['opportunities'] else []
        d['news_sources'] = json.loads(d.get('news_sources') or '[]')
        result.append(d)
    return jsonify(result)

@app.route('/api/trends')
def trends():
    with get_db() as conn:
        keywords = conn.execute('SELECT keyword, count, last_seen FROM keywords ORDER BY count DESC LIMIT 30').fetchall()
        daily = conn.execute('''
            SELECT date, category, COUNT(*) as cnt,
            SUM(CASE WHEN impact="HIGH" THEN 1 ELSE 0 END) as high_cnt
            FROM issues GROUP BY date, category ORDER BY date DESC LIMIT 60
        ''').fetchall()
        total = conn.execute('SELECT COUNT(*) as cnt FROM issues').fetchone()['cnt']
        high_total = conn.execute('SELECT COUNT(*) as cnt FROM issues WHERE impact="HIGH"').fetchone()['cnt']
    return jsonify({
        'keywords': [dict(k) for k in keywords],
        'daily': [dict(d) for d in daily],
        'total': total,
        'high_total': high_total
    })

@app.route('/api/memo', methods=['POST'])
def save_memo():
    data = request.json
    with get_db() as conn:
        conn.execute('UPDATE issues SET memo=?, starred=? WHERE id=?',
                     (data.get('memo',''), data.get('starred',0), data.get('id')))
    return jsonify({'ok': True})

@app.route('/api/stats')
def stats():
    with get_db() as conn:
        total = conn.execute('SELECT COUNT(*) as c FROM issues').fetchone()['c']
        high = conn.execute('SELECT COUNT(*) as c FROM issues WHERE impact="HIGH"').fetchone()['c']
        sessions = conn.execute('SELECT COUNT(*) as c FROM sessions').fetchone()['c']
        by_cat = conn.execute('SELECT category, COUNT(*) as c FROM issues GROUP BY category').fetchall()
    return jsonify({'total':total,'high':high,'sessions':sessions,'by_cat':[dict(r) for r in by_cat]})

@app.route('/api/usage')
def usage():
    usd = api_usage['cost_usd']
    krw = usd * 1450  # approximate USD→KRW
    return jsonify({
        'input_tokens': api_usage['input_tokens'],
        'output_tokens': api_usage['output_tokens'],
        'cost_usd': round(usd, 4),
        'cost_krw': round(krw, 1),
        'calls': api_usage['calls'],
        'model': 'claude-haiku-4-5'
    })

# 모듈 import 시점에 DB 초기화 (gunicorn/로컬 둘 다 대응)
def _ensure_db_ready():
    try:
        init_db()
        # 기존 DB에 news_sources 컬럼이 없으면 추가
        try:
            with get_db() as conn:
                conn.execute("SELECT news_sources FROM issues LIMIT 1")
        except Exception:
            with get_db() as conn:
                conn.execute("ALTER TABLE issues ADD COLUMN news_sources TEXT DEFAULT '[]'")
    except Exception as e:
        print(f"[init_db warning] {e}")

_ensure_db_ready()

if __name__ == '__main__':
    # 로컬 전용: 스케줄러 시작 (Render는 외부 cron-job.org 사용)
    try:
        from scheduler import start_scheduler
        start_scheduler("07:00")
    except Exception as e:
        print(f"  스케줄러 로드 실패 (무시): {e}")

    print('\n' + '='*50)
    print(' 글로벌 인텔리전스 시스템 v2.0')
    print(' Powered by Claude API + Real-time News')
    print(' ')
    print(' 대시보드: http://localhost:9999')
    print(' 랜딩페이지: http://localhost:9999/landing')
    print(' 자동발송: 매일 07:00 (텔레그램 설정 필요)')
    print('='*50 + '\n')
    app.run(debug=False, port=9999, host='0.0.0.0')
