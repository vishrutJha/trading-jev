from __future__ import annotations
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from .auth import activate, status
from .backtest import Backtester
from .breeze_source import BreezeHistoricalSource
from .config import Settings
from .db import Database

settings=Settings.from_env(); db=Database(settings.db_path); source=BreezeHistoricalSource(db); backtester=Backtester(db,source,settings.jev_model)
app=FastAPI(title='Breeze + Jev Backtester',version='0.1.0')

class SymbolIn(BaseModel):
    name:str; stock_code:str; strike_step:float=Field(gt=0); lot_size:int=Field(gt=0); cash_exchange:str='NSE'; option_exchange:str='NFO'
class SessionIn(BaseModel): token:str
class BacktestIn(BaseModel):
    symbol_id:int; start_date:str; end_date:str; expiry:str; interval:str='1minute'; engine:str='heuristic'
    strike_offsets:int=0; warmup_bars:int=30; decision_every_bars:int=1; event_trigger_pct:float=0.0; lots:int=1
    min_confidence:float=0.62; max_trades_per_day:int=6; max_capital_per_trade:float=100000
    stop_loss_pct:float=12; take_profit_pct:float=20; max_hold_bars:int=12; entry_cutoff:str='15:05'; square_off:str='15:20'
    slippage_bps:float=5; fees_bps_per_side:float=3; brokerage_per_order:float=20; max_contracts:int=40; refresh_data:bool=False

@app.get('/',response_class=HTMLResponse)
def root(): return (Path(__file__).parent/'static'/'index.html').read_text()
@app.get('/api/health')
def health(): return {'ok':True,'db':settings.db_path,'jev_model':settings.jev_model}
@app.get('/api/auth')
def auth_status(): return status()
@app.post('/api/auth/session')
def auth_session(body:SessionIn):
    try: activate(body.token); return status()
    except Exception as e: raise HTTPException(400,str(e))
@app.get('/api/symbols')
def symbols(): return db.symbols()
@app.post('/api/symbols')
def add_symbol(body:SymbolIn): return {'id':db.add_symbol(body.model_dump())}
@app.patch('/api/symbols/{symbol_id}/{active}')
def active(symbol_id:int,active:int): db.set_symbol_active(symbol_id,bool(active)); return {'ok':True}
@app.delete('/api/symbols/{symbol_id}')
def delete_symbol(symbol_id:int): db.delete_symbol(symbol_id); return {'ok':True}
@app.post('/api/backtests')
def run_backtest(body:BacktestIn):
    p=body.model_dump()
    if p['engine'] not in ('heuristic','jev'): raise HTTPException(400,'engine must be heuristic or jev')
    try:
        rid=backtester.run(p); return db.run_report(rid)
    except Exception as e: raise HTTPException(400,str(e))
@app.get('/api/backtests')
def runs(): return db.runs()
@app.get('/api/backtests/{run_id}')
def run(run_id:int):
    r=db.run_report(run_id)
    if not r: raise HTTPException(404,'run not found')
    return r
