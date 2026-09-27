from __future__ import annotations
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from .breeze_source import BreezeHistoricalSource
from .db import Database
from .decision import HeuristicDecisionEngine, JevDecisionEngine
from .features import entry_state, exit_state, state_hash
from .models import Candle, Contract, PendingOrder, Position
from .risk import RiskConfig

IST=ZoneInfo('Asia/Kolkata')

class BacktestError(RuntimeError): pass

def _round_step(x,step): return round(x/step)*step

def _market_bounds(start_date:str,end_date:str):
    s=date.fromisoformat(start_date); e=date.fromisoformat(end_date)
    return datetime.combine(s,time(9,15),IST), datetime.combine(e,time(15,30),IST)

def _index(candles:list[Candle]): return {c.ts:c for c in candles}

class Backtester:
    def __init__(self,db:Database,source:BreezeHistoricalSource,jev_model='jev-latest'):
        self.db=db; self.source=source; self.jev_model=jev_model

    def run(self,p:dict)->int:
        symbol=self.db.symbol(int(p['symbol_id']))
        if not symbol: raise BacktestError('Unknown symbol')
        engine=JevDecisionEngine(self.db,self.jev_model) if p['engine']=='jev' else HeuristicDecisionEngine()
        run_id=self.db.start_run(p,engine.model)
        try:
            report=self._run(run_id,symbol,p,engine)
            self.db.finish_run(run_id,report['summary'])
            return run_id
        except Exception as e:
            self.db.fail_run(run_id,str(e)); raise

    def _run(self,run_id:int,symbol:dict,p:dict,engine):
        start,end=_market_bounds(p['start_date'],p['end_date'])
        if date.fromisoformat(p['end_date'])>date.fromisoformat(p['expiry']):
            raise BacktestError('end_date must not be after the selected option expiry')
        interval=p.get('interval','1minute')
        refresh=bool(p.get('refresh_data',False))
        spot=self.source.cash(symbol['stock_code'],symbol['cash_exchange'],interval,start,end,refresh)
        if len(spot)<20: raise BacktestError('Not enough underlying candles returned by Breeze')
        step=float(symbol['strike_step']); offsets=int(p.get('strike_offsets',0)); max_contracts=int(p.get('max_contracts',40))
        atms=sorted({_round_step(c.close,step) for c in spot})
        strikes=sorted({atm+step*o for atm in atms for o in range(-offsets,offsets+1)})
        if len(strikes)*2>max_contracts:
            raise BacktestError(f'Range requires {len(strikes)*2} option contracts; reduce date range/strike_offsets or raise max_contracts')
        contracts=[]; opt={}
        for strike in strikes:
            for right in ('call','put'):
                c=Contract(symbol['stock_code'],p['expiry'],float(strike),right,int(symbol['lot_size']))
                rows=self.source.option(c,symbol['option_exchange'],interval,start,end,refresh)
                contracts.append(c); opt[c.key]=_index(rows)
        spot_idx=_index(spot); timeline=sorted(spot_idx)
        warmup=int(p.get('warmup_bars',30)); cadence=max(1,int(p.get('decision_every_bars',1)))
        trigger=float(p.get('event_trigger_pct',0.0))/100.0
        slippage=float(p.get('slippage_bps',5.0))/10000.0
        fee_bps=float(p.get('fees_bps_per_side',3.0))/10000.0
        brokerage=float(p.get('brokerage_per_order',20.0))
        lots=int(p.get('lots',1)); qty=lots*int(symbol['lot_size'])
        risk=RiskConfig(
          min_confidence=float(p.get('min_confidence',0.62)),max_trades_per_day=int(p.get('max_trades_per_day',6)),
          max_capital_per_trade=float(p.get('max_capital_per_trade',100000)),stop_loss_pct=float(p.get('stop_loss_pct',12)),
          take_profit_pct=float(p.get('take_profit_pct',20)),max_hold_bars=int(p.get('max_hold_bars',12)),
          entry_cutoff=p.get('entry_cutoff','15:05'),square_off=p.get('square_off','15:20'))

        position=None; pending=None; trades_today=defaultdict(int); equity=0.0; points=[]
        histories={c.key:[] for c in contracts}; spot_hist=[]; last_entry_decision=None; last_exit_decision=None

        for i,ts in enumerate(timeline):
            sbar=spot_idx[ts]; spot_hist.append(sbar)
            atm=_round_step(sbar.close,step)
            candidates=[c for c in contracts if abs(c.strike-atm)<=step*offsets+1e-9 or (offsets==0 and c.strike==atm)]
            for c in contracts:
                bar=opt[c.key].get(ts)
                if bar: histories[c.key].append(bar)

            # Execute prior decision on this candle's option open: no same-candle look-ahead.
            if pending:
                bar=opt[pending.contract.key].get(ts)
                if bar and bar.open>0:
                    if pending.action=='BUY':
                        fill=bar.open*(1+slippage)
                        self.db.add_order(run_id,pending.decision_id,ts,pending.contract.key,'BUY',pending.quantity,bar.open,fill,'FILLED',pending.reason)
                        position=Position(pending.contract,pending.quantity,ts,fill,pending.decision_id,0,fill)
                        trades_today[ts.date()]+=1
                    elif pending.action=='SELL' and position:
                        fill=bar.open*(1-slippage)
                        self.db.add_order(run_id,pending.decision_id,ts,pending.contract.key,'SELL',pending.quantity,bar.open,fill,'FILLED',pending.reason)
                        buy=position.entry_price*position.quantity; sell=fill*position.quantity
                        gross=sell-buy; charges=buy*fee_bps+sell*fee_bps+2*brokerage; net=gross-charges; equity+=net
                        self.db.add_trade(run_id,{'contract_key':position.contract.key,'right':position.contract.right,'strike':position.contract.strike,'quantity':position.quantity,
                          'entry_ts':position.entry_ts.isoformat(),'exit_ts':ts.isoformat(),'entry_price':position.entry_price,'exit_price':fill,
                          'buy_value':buy,'sell_value':sell,'gross_pnl':gross,'charges':charges,'net_pnl':net,'exit_reason':pending.reason,
                          'entry_decision_id':position.entry_decision_id,'exit_decision_id':pending.decision_id})
                        position=None
                    pending=None

            if position:
                bar=opt[position.contract.key].get(ts)
                if bar:
                    position.bars_held+=1; position.peak_price=max(position.peak_price,bar.close)
                    forced=risk.forced_exit(ts,position.entry_price,bar.close,position.bars_held)
                    if forced:
                        pending=PendingOrder('SELL',position.contract,position.quantity,None,forced,ts)
                    elif i%cadence==0:
                        st=exit_state(symbol,position,bar); d=engine.exit(st); sh=state_hash(st)
                        did=self.db.add_decision(run_id,ts,sh,'exit',d,st); last_exit_decision=did
                        if d.action=='EXIT' and d.confidence>=risk.min_confidence:
                            pending=PendingOrder('SELL',position.contract,position.quantity,did,'model_exit',ts)
            elif i>=warmup and i%cadence==0:
                # Optional event gate to reduce Jev calls. 0 means evaluate every cadence bar.
                if trigger>0 and len(spot_hist)>1 and abs((spot_hist[-1].close-spot_hist[-2].close)/spot_hist[-2].close)<trigger:
                    points.append({'ts':ts.isoformat(),'spot':sbar.close,'equity':round(equity,2)}); continue
                calls=[]; puts=[]
                for c in candidates:
                    h=histories[c.key]
                    if len(h)<max(12,warmup//2): continue
                    (calls if c.right=='call' else puts).append((c,h))
                if calls and puts:
                    st=entry_state(symbol,ts,spot_hist,calls,puts); d=engine.entry(st); sh=state_hash(st)
                    did=self.db.add_decision(run_id,ts,sh,'entry',d,st); last_entry_decision=did
                    if d.action in ('BUY_CALL','BUY_PUT'):
                        right='call' if d.action=='BUY_CALL' else 'put'
                        rows=calls if right=='call' else puts
                        # Pick candidate with highest causal strength, then absolute lag edge.
                        def rank(ch):
                            c,h=ch; f=[x for x in st[right+'s'] if x['contract']==c.key][0]
                            return (f['causal']['strength'],abs(f['causal']['lag_edge']))
                        c,h=max(rows,key=rank); premium=h[-1].close
                        allowed,reason=risk.entry_allowed(ts,d.confidence,qty,premium,trades_today[ts.date()])
                        if allowed: pending=PendingOrder('BUY',c,qty,did,'model_entry',ts)
                        else: self.db.add_order(run_id,did,ts,c.key,'BUY',qty,premium,None,'REJECTED',reason)
            points.append({'ts':ts.isoformat(),'spot':sbar.close,'equity':round(equity,2)})

        # Final close if a position remained; use last available close as a conservative terminal fill.
        if position:
            rows=histories[position.contract.key]
            if rows:
                bar=rows[-1]; fill=bar.close*(1-slippage); ts=bar.ts
                self.db.add_order(run_id,None,ts,position.contract.key,'SELL',position.quantity,bar.close,fill,'FILLED','end_of_backtest')
                buy=position.entry_price*position.quantity; sell=fill*position.quantity; gross=sell-buy; charges=buy*fee_bps+sell*fee_bps+2*brokerage; net=gross-charges; equity+=net
                self.db.add_trade(run_id,{'contract_key':position.contract.key,'right':position.contract.right,'strike':position.contract.strike,'quantity':position.quantity,'entry_ts':position.entry_ts.isoformat(),'exit_ts':ts.isoformat(),'entry_price':position.entry_price,'exit_price':fill,'buy_value':buy,'sell_value':sell,'gross_pnl':gross,'charges':charges,'net_pnl':net,'exit_reason':'end_of_backtest','entry_decision_id':position.entry_decision_id,'exit_decision_id':None})
                points.append({'ts':ts.isoformat(),'spot':spot_idx.get(ts).close if spot_idx.get(ts) else None,'equity':round(equity,2)})

        self.db.add_points(run_id,points)
        trades=self.db.all('SELECT * FROM trades WHERE run_id=? ORDER BY exit_ts',(run_id,))
        summary=self._summary(trades)
        summary.update({'candles':len(spot),'option_contracts_loaded':len(contracts),'engine':engine.name,'model':engine.model,
                        'data_source':'ICICI Breeze get_historical_data_v2','fill_model':'decision at candle t; fill on next available option candle open'})
        return {'summary':summary}

    @staticmethod
    def _summary(trades:list[dict])->dict:
        if not trades: return {'trades':0,'wins':0,'losses':0,'win_rate_pct':0,'gross_pnl':0,'charges':0,'net_pnl':0,'max_drawdown':0,'profit_factor':0}
        wins=[t for t in trades if t['net_pnl']>0]; losses=[t for t in trades if t['net_pnl']<=0]
        eq=peak=dd=0.0
        for t in trades:
            eq+=t['net_pnl']; peak=max(peak,eq); dd=max(dd,peak-eq)
        gp=sum(max(0,t['net_pnl']) for t in trades); gl=abs(sum(min(0,t['net_pnl']) for t in trades))
        return {'trades':len(trades),'wins':len(wins),'losses':len(losses),'win_rate_pct':round(100*len(wins)/len(trades),2),
                'gross_pnl':round(sum(t['gross_pnl'] for t in trades),2),'charges':round(sum(t['charges'] for t in trades),2),
                'net_pnl':round(sum(t['net_pnl'] for t in trades),2),'max_drawdown':round(dd,2),
                'profit_factor':round(gp/gl,3) if gl else (999.0 if gp else 0.0)}
