from __future__ import annotations
import hashlib, json, math
from statistics import pstdev
from .causal import rolling_causal_features
from .models import Candle, Contract, Position


def pct(a:float,b:float)->float:
    return 0.0 if not a else (b-a)/a


def state_hash(state:dict)->str:
    raw=json.dumps(state,sort_keys=True,separators=(',',':')).encode()
    return hashlib.sha256(raw).hexdigest()


def option_features(contract:Contract, spot_hist:list[Candle], opt_hist:list[Candle])->dict:
    # Pair by timestamp so missing option candles cannot silently shift the lag model.
    smap={c.ts:c.close for c in spot_hist}
    paired=[(smap[c.ts],c.close) for c in opt_hist if c.ts in smap]
    spot=[x for x,_ in paired]
    closes=[y for _,y in paired]
    expected=1 if contract.right=='call' else -1
    causal=rolling_causal_features(spot,closes,expected)
    def r(k): return pct(closes[-1-k],closes[-1]) if len(closes)>k else 0.0
    return {
      'contract':contract.key,'right':contract.right,'strike':contract.strike,
      'premium':round(closes[-1],4) if closes else 0.0,
      'return_1':round(r(1),6),'return_3':round(r(3),6),'return_5':round(r(5),6),
      'volume':round(opt_hist[-1].volume,2) if opt_hist else 0.0,
      'open_interest':round(opt_hist[-1].open_interest,2) if opt_hist else 0.0,
      'causal':causal,
    }


def entry_state(symbol:dict, ts, spot_hist:list[Candle], calls:list[tuple[Contract,list[Candle]]], puts:list[tuple[Contract,list[Candle]]])->dict:
    sc=[c.close for c in spot_hist]
    returns=[pct(sc[i-1],sc[i]) for i in range(1,len(sc))]
    def sr(k): return pct(sc[-1-k],sc[-1]) if len(sc)>k else 0.0
    vol=pstdev(returns[-20:]) if len(returns)>=3 else 0.0
    return {
      'timestamp':ts.isoformat(),'symbol':symbol['name'],'stock_code':symbol['stock_code'],
      'spot':round(sc[-1],4),'spot_return_1':round(sr(1),6),'spot_return_3':round(sr(3),6),'spot_return_5':round(sr(5),6),
      'realised_volatility_20':round(vol,6),
      'calls':[option_features(c,spot_hist,h) for c,h in calls if h],
      'puts':[option_features(c,spot_hist,h) for c,h in puts if h],
    }


def exit_state(base:dict, position:Position, current:Candle)->dict:
    pnl=pct(position.entry_price,current.close)
    drawdown=pct(position.peak_price,current.close) if position.peak_price else 0.0
    return {
      'timestamp':current.ts.isoformat(),'symbol':base['name'],'stock_code':base['stock_code'],
      'position':{'contract':position.contract.key,'right':position.contract.right,'strike':position.contract.strike,
                  'entry_price':round(position.entry_price,4),'current_price':round(current.close,4),
                  'return_since_entry':round(pnl,6),'drawdown_from_peak':round(drawdown,6),'bars_held':position.bars_held}
    }
