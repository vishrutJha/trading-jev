from __future__ import annotations
import json, os, urllib.parse
from datetime import date
from pathlib import Path
from .config import load_dotenv

TOKEN_CACHE = Path("data/breeze_session.json")
_CLIENT = None

class BreezeAuthError(RuntimeError): pass


def login_url() -> str:
    load_dotenv()
    key=os.getenv('BREEZE_API_KEY','')
    return 'https://api.icicidirect.com/apiuser/login?api_key='+urllib.parse.quote_plus(key)


def _token() -> str|None:
    load_dotenv()
    if os.getenv('BREEZE_SESSION_TOKEN'): return os.environ['BREEZE_SESSION_TOKEN'].strip()
    try:
        d=json.loads(TOKEN_CACHE.read_text())
        if d.get('date')==date.today().isoformat(): return d.get('token')
    except Exception: pass
    return None


def activate(token:str):
    global _CLIENT
    load_dotenv()
    key=os.getenv('BREEZE_API_KEY'); secret=os.getenv('BREEZE_API_SECRET')
    if not key or not secret: raise BreezeAuthError('Set BREEZE_API_KEY and BREEZE_API_SECRET in .env')
    from breeze_connect import BreezeConnect
    b=BreezeConnect(api_key=key)
    try:
        b.generate_session(api_secret=secret, session_token=token.strip())
    except Exception as e:
        raise BreezeAuthError(f'Breeze session activation failed: {e}') from e
    TOKEN_CACHE.parent.mkdir(parents=True,exist_ok=True)
    TOKEN_CACHE.write_text(json.dumps({'date':date.today().isoformat(),'token':token.strip()}))
    try: TOKEN_CACHE.chmod(0o600)
    except OSError: pass
    _CLIENT=b
    return b


def get_client():
    global _CLIENT
    if _CLIENT is not None: return _CLIENT
    token=_token()
    if not token: raise BreezeAuthError('No Breeze session token for today. Use the UI Auth section or set BREEZE_SESSION_TOKEN.')
    return activate(token)


def status() -> dict:
    load_dotenv()
    return {
      'credentials_configured': bool(os.getenv('BREEZE_API_KEY') and os.getenv('BREEZE_API_SECRET')),
      'session_token_available': bool(_token()),
      'client_active': _CLIENT is not None,
      'login_url': login_url(),
    }
