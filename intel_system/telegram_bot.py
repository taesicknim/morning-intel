"""
텔레그램 봇 — 채널 발송 + 구독자 관리
"""
import requests, json, time, threading
try:
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL
    TELEGRAM_TOKEN = TELEGRAM_BOT_TOKEN
    CHANNEL_ID = TELEGRAM_CHANNEL
except ImportError:
    TELEGRAM_TOKEN = ""
    CHANNEL_ID = ""

def set_token(token):
    global TELEGRAM_TOKEN
    TELEGRAM_TOKEN = token

def set_channel(channel_id):
    global CHANNEL_ID
    CHANNEL_ID = channel_id

def send_message(chat_id, text, parse_mode=None):
    """텔레그램 메시지 발송"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    r = requests.post(url, json=payload, timeout=30)
    return r.json()

def send_to_channel(text):
    """채널에 메시지 발송"""
    if not CHANNEL_ID:
        print("채널 ID가 설정되지 않았습니다")
        return None
    # 텔레그램 메시지 길이 제한 4096자
    if len(text) > 4096:
        # 분할 발송
        parts = split_message(text, 4096)
        results = []
        for part in parts:
            r = send_message(CHANNEL_ID, part)
            results.append(r)
            time.sleep(0.5)
        return results
    return send_message(CHANNEL_ID, text)

def split_message(text, max_len=4096):
    """긴 메시지를 분할"""
    lines = text.split('\n')
    parts = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > max_len:
            parts.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        parts.append(current)
    return parts

def send_briefing_to_channel(briefing_text):
    """브리핑을 채널에 발송"""
    print(f"📡 텔레그램 채널 발송 중...")
    result = send_to_channel(briefing_text)
    if result:
        print(f"✅ 발송 완료")
    return result

def get_bot_info():
    """봇 정보 확인"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe"
    r = requests.get(url, timeout=10)
    return r.json()
