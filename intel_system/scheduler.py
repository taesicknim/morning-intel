"""
매일 아침 자동 브리핑 발송 스케줄러
- 매일 오전 7시에 브리핑 생성 + 텔레그램 채널 발송
- PC를 켜두기만 하면 동작
"""
import time, threading, schedule
from datetime import datetime

def run_daily_briefing():
    """브리핑 생성 + 발송"""
    print(f"\n{'='*40}")
    print(f"  자동 브리핑 시작: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*40}")
    try:
        from briefing import generate_briefing, format_telegram
        from telegram_bot import send_to_channel, TELEGRAM_TOKEN

        if not TELEGRAM_TOKEN:
            print("  텔레그램 토큰 미설정 — 건너뜀")
            return

        b = generate_briefing()
        text = format_telegram(b)
        result = send_to_channel(text)
        print(f"  발송 완료: 뉴스 {b['news_count']}건 분석")
    except Exception as e:
        print(f"  발송 실패: {e}")

def start_scheduler(send_time="07:00"):
    """스케줄러 시작 (백그라운드 스레드)"""
    schedule.every().day.at(send_time).do(run_daily_briefing)
    print(f"\n  스케줄러 등록: 매일 {send_time}에 자동 발송")
    print(f"  PC를 켜두면 자동으로 발송됩니다\n")

    def loop():
        while True:
            schedule.run_pending()
            time.sleep(30)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t

if __name__ == '__main__':
    # 직접 실행하면 즉시 발송 테스트
    print("즉시 발송 테스트...")
    run_daily_briefing()
