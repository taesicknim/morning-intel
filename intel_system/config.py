# =============================================
#  API 키 설정 — 환경변수 우선, 로컬 파일 fallback
#  이 파일은 안전하게 GitHub에 커밋됨 (하드코딩 키 없음)
# =============================================
import os

# 1차: 환경변수 (프로덕션 Render)
CLAUDE_API_KEY     = os.environ.get('CLAUDE_API_KEY', '')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHANNEL   = os.environ.get('TELEGRAM_CHANNEL', '')
CRON_SECRET        = os.environ.get('CRON_SECRET', '')  # 크론 호출 인증용

# 2차: 환경변수가 없으면 로컬 dev 파일 시도 (config_local.py, gitignored)
if not CLAUDE_API_KEY:
    try:
        from config_local import (  # type: ignore
            CLAUDE_API_KEY as _KEY,
            TELEGRAM_BOT_TOKEN as _BOT,
            TELEGRAM_CHANNEL as _CH,
        )
        CLAUDE_API_KEY = _KEY
        TELEGRAM_BOT_TOKEN = _BOT
        TELEGRAM_CHANNEL = _CH
        try:
            from config_local import CRON_SECRET as _CRON  # type: ignore
            CRON_SECRET = _CRON
        except ImportError:
            pass
    except ImportError:
        pass
