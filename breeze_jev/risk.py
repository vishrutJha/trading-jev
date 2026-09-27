from __future__ import annotations
from dataclasses import dataclass
from datetime import time

@dataclass(frozen=True)
class RiskConfig:
    min_confidence: float=0.62
    max_trades_per_day: int=6
    max_capital_per_trade: float=100000.0
    stop_loss_pct: float=12.0
    take_profit_pct: float=20.0
    max_hold_bars: int=12
    entry_cutoff: str='15:05'
    square_off: str='15:20'

    def entry_allowed(self, ts, confidence:float, qty:int, premium:float, trades_today:int)->tuple[bool,str]:
        if confidence<self.min_confidence: return False,'confidence'
        if trades_today>=self.max_trades_per_day: return False,'daily_trade_limit'
        if qty*premium>self.max_capital_per_trade: return False,'capital_limit'
        if ts.time()>=_time(self.entry_cutoff): return False,'entry_cutoff'
        return True,'allowed'

    def forced_exit(self, ts, entry:float, current:float, bars:int)->str|None:
        ret=(current-entry)/entry*100 if entry else 0
        if ret<=-self.stop_loss_pct: return 'stop_loss'
        if ret>=self.take_profit_pct: return 'take_profit'
        if bars>=self.max_hold_bars: return 'max_hold'
        if ts.time()>=_time(self.square_off): return 'square_off'
        return None

def _time(s:str):
    h,m=map(int,s.split(':')); return time(h,m)
