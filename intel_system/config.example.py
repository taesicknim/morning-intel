# =============================================
#  API 키 설정 템플릿
#  이 파일을 config.py로 복사한 뒤 키를 입력하세요
#  cp config.example.py config.py
#
#  또는 환경변수로 설정 가능:
#    CLAUDE_API_KEY=sk-ant-... python app.py
# =============================================
import os

CLAUDE_API_KEY     = os.environ.get('CLAUDE_API_KEY',     "여기에_Anthropic_API_키_입력")
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', "여기에_텔레그램_봇_토큰_입력")
TELEGRAM_CHANNEL   = os.environ.get('TELEGRAM_CHANNEL',   "@채널이름")
