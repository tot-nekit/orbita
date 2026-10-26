"""Configuration without third-party dependencies. Secrets never reach the client."""
import os, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
for line in (ROOT / '.env').read_text().splitlines() if os.getenv('ORBITA_SKIP_DOTENV')!='1' and (ROOT / '.env').exists() else []:
    if line.strip() and not line.lstrip().startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        value=v.strip()
        if value.startswith('"'):
            try:value=json.loads(value)
            except json.JSONDecodeError:raise ValueError('Некорректная строка в .env: '+k.strip()) from None
        elif len(value)>=2 and value.startswith("'") and value.endswith("'"):value=value[1:-1]
        os.environ.setdefault(k.strip(), value)
DB_PATH = os.getenv('DATABASE_PATH', str(ROOT / 'data' / 'orbita.sqlite3'))
API_KEY = os.getenv('OPENAI_API_KEY', '')
API_BASE = os.getenv('OPENAI_BASE_URL', 'https://vyceai.com/v1').rstrip('/')
MODEL = os.getenv('OPENAI_MODEL', 'claude-sonnet-4-6')
STT_MODEL = os.getenv('STT_MODEL', 'gpt-4o-mini-transcribe')
FALLBACK_MODEL = os.getenv('LLM_FALLBACK_MODEL', 'deepseek-v4-flash')
LLM_TIMEOUT = max(2, min(30, float(os.getenv('LLM_TIMEOUT', '16'))))
LLM_MAX_TOKENS = max(256, min(4096, int(os.getenv('LLM_MAX_TOKENS', '1200'))))
LLM_TEMPERATURE = max(0, min(1, float(os.getenv('LLM_TEMPERATURE', '0.1'))))
STT_API_BASE = os.getenv('STT_BASE_URL', API_BASE).rstrip('/')
STT_API_KEY = os.getenv('STT_API_KEY', API_KEY)
# Compatible chat endpoints do not necessarily support audio transcription.
STT_ENABLED = os.getenv('STT_ENABLED', '0' if 'vyceai.com' in STT_API_BASE else '1') == '1'
