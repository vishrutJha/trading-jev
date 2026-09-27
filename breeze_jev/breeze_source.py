from __future__ import annotations
import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .auth import get_client
from .db import Database
from .models import Candle, Contract

IST=ZoneInfo('Asia/Kolkata')
INTERVAL_SECONDS={'1second':1,'1minute':60,'5minute':300,'30minute':1800,'1day':86400}

class BreezeDataError(RuntimeError): pass


def _breeze_iso(dt:datetime)->str:
    # Breeze historical examples treat the timestamp as exchange wall-clock time
    # even though the wire format ends in Z (09:20 request -> 09:20 candle).
    # Preserve the India-market clock instead of shifting 09:15 IST to 03:45 UTC.
    if dt.tzinfo is not None:
        dt=dt.astimezone(IST).replace(tzinfo=None)
    return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')

def _rest_expiry(raw:str)->str:
    d=datetime.fromisoformat(raw[:10])
    return d.strftime('%Y-%m-%dT06:00:00.000Z')


def _parse_dt(s:str)->datetime:
    # Breeze historical responses are exchange-local wall-clock strings.
    dt=datetime.fromisoformat(str(s).replace('Z','+00:00'))
    if dt.tzinfo is None: dt=dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def _rows(resp:dict)->list[dict]:
    if not isinstance(resp,dict): raise BreezeDataError(f'Unexpected Breeze response: {type(resp).__name__}')
    if resp.get('Error'): raise BreezeDataError(str(resp['Error']))
    return resp.get('Success') or []

class BreezeHistoricalSource:
    def __init__(self, db:Database): self.db=db

    def cash(self, stock_code:str, exchange:str, interval:str, start:datetime, end:datetime, refresh=False)->list[Candle]:
        key=f'CASH:{exchange}:{stock_code}'
        return self._fetch(key,interval,start,end,refresh,stock_code=stock_code,exchange_code=exchange,product_type='cash')

    def option(self, contract:Contract, exchange:str, interval:str, start:datetime, end:datetime, refresh=False)->list[Candle]:
        key='OPT:'+exchange+':'+contract.key
        return self._fetch(key,interval,start,end,refresh,
            stock_code=contract.stock_code,exchange_code=exchange,product_type='options',
            expiry_date=_rest_expiry(contract.expiry),right=contract.right,strike_price=f'{contract.strike:g}')

    def _fetch(self, instrument_key:str, interval:str, start:datetime, end:datetime, refresh:bool, **kwargs)->list[Candle]:
        if interval not in INTERVAL_SECONDS: raise ValueError('Unsupported interval '+interval)
        cache_key=hashlib.sha256(f'{instrument_key}|{interval}|{start.isoformat()}|{end.isoformat()}'.encode()).hexdigest()
        if not refresh and self.db.fetched(cache_key):
            return self.db.read_candles(instrument_key,interval,start,end)
        b=get_client()
        step=INTERVAL_SECONDS[interval]
        # Official V2 limit: max 1000 candles/request. Use 900 theoretical intervals to leave headroom.
        chunk=timedelta(seconds=step*900)
        cur=start; all_rows=[]
        while cur<=end:
            stop=min(end,cur+chunk)
            params=dict(interval=interval,from_date=_breeze_iso(cur),to_date=_breeze_iso(stop),**kwargs)
            try: resp=b.get_historical_data_v2(**params)
            except Exception as e: raise BreezeDataError(f'Breeze historical request failed: {e}') from e
            for r in _rows(resp):
                try:
                    all_rows.append(Candle(
                      ts=_parse_dt(r['datetime']),open=float(r.get('open') or 0),high=float(r.get('high') or 0),
                      low=float(r.get('low') or 0),close=float(r.get('close') or 0),volume=float(r.get('volume') or 0),
                      open_interest=float(r.get('open_interest') or 0)))
                except (KeyError,TypeError,ValueError):
                    continue
            cur=stop+timedelta(seconds=step)
        unique={c.ts:c for c in all_rows if c.close>0}
        candles=[unique[k] for k in sorted(unique)]
        self.db.upsert_candles(instrument_key,interval,candles)
        self.db.mark_fetch(cache_key,len(candles))
        return self.db.read_candles(instrument_key,interval,start,end)
