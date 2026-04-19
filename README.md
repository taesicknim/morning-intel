# 모닝 인텔리전스 브리핑

매일 아침 7시, AI가 50건의 글로벌 뉴스를 분석해 핵심 3가지와 실행 액션을 전달하는 서비스.

## 주요 기능

- 실시간 뉴스 수집 (Google News 섹션 + 이벤트 키워드)
- 실시간 마켓 지수 (Yahoo Finance — KOSPI, NASDAQ, 환율, 유가 등)
- 한국 주요 10개 대형주 실시간 시세
- Claude AI 심층 분석 (뉴스 → 분석 → 예측 → 액션 4단)
- 텔레그램 채널 자동 발송
- 팩트 검증 레이어 (허위 기업명 언급 자동 제거)

## 로컬 실행

```bash
cd intel_system
cp config.example.py config.py
# config.py에 API 키 입력
pip install -r requirements.txt
python app.py
```

## 배포 (Render)

1. 이 저장소를 GitHub에 업로드
2. Render 계정에서 "New Web Service" → GitHub 저장소 연결
3. 환경변수 설정:
   - `CLAUDE_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHANNEL`
4. 자동 배포 완료

## 비용

- Claude Sonnet 4.6: 브리핑 1회당 약 ₩80-100
- Render 무료 플랜: ₩0
- 합계: 월 ₩3,000 수준

## 기술 스택

- Python 3.11 + Flask
- Claude Sonnet 4.6 (Anthropic API)
- Yahoo Finance API (실시간 시세)
- Google News RSS (실시간 뉴스)
- Telegram Bot API (채널 발송)
- SQLite (로컬 DB)
