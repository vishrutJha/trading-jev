from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable
from .models import Candle

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS tracked_symbols (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  stock_code TEXT NOT NULL,
  cash_exchange TEXT NOT NULL DEFAULT 'NSE',
  option_exchange TEXT NOT NULL DEFAULT 'NFO',
  strike_step REAL NOT NULL,
  lot_size INTEGER NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS candles (
  instrument_key TEXT NOT NULL,
  interval TEXT NOT NULL,
  ts TEXT NOT NULL,
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  volume REAL NOT NULL DEFAULT 0,
  open_interest REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (instrument_key, interval, ts)
);

CREATE TABLE IF NOT EXISTS fetch_cache (
  cache_key TEXT PRIMARY KEY,
  fetched_at TEXT NOT NULL,
  rows INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jev_cache (
  cache_key TEXT PRIMARY KEY,
  engine TEXT NOT NULL,
  model TEXT NOT NULL,
  question_kind TEXT NOT NULL,
  state_hash TEXT NOT NULL,
  response_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS backtest_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  status TEXT NOT NULL,
  symbol_id INTEGER NOT NULL,
  engine TEXT NOT NULL,
  model TEXT NOT NULL,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  expiry TEXT NOT NULL,
  interval TEXT NOT NULL,
  config_json TEXT NOT NULL,
  summary_json TEXT,
  error TEXT,
  FOREIGN KEY(symbol_id) REFERENCES tracked_symbols(id)
);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  ts TEXT NOT NULL,
  state_hash TEXT NOT NULL,
  question_kind TEXT NOT NULL,
  action TEXT NOT NULL,
  probabilities_json TEXT NOT NULL,
  confidence REAL NOT NULL,
  engine TEXT NOT NULL,
  model TEXT NOT NULL,
  latency_ms REAL NOT NULL,
  cached INTEGER NOT NULL DEFAULT 0,
  state_json TEXT NOT NULL,
  reason TEXT,
  FOREIGN KEY(run_id) REFERENCES backtest_runs(id)
);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  decision_id INTEGER,
  ts TEXT NOT NULL,
  contract_key TEXT NOT NULL,
  side TEXT NOT NULL,
  quantity INTEGER NOT NULL,
  requested_price REAL,
  fill_price REAL,
  status TEXT NOT NULL,
  reason TEXT,
  FOREIGN KEY(run_id) REFERENCES backtest_runs(id),
  FOREIGN KEY(decision_id) REFERENCES decisions(id)
);

CREATE TABLE IF NOT EXISTS run_points (
  run_id INTEGER NOT NULL,
  ts TEXT NOT NULL,
  spot REAL,
  equity REAL NOT NULL,
  PRIMARY KEY(run_id, ts),
  FOREIGN KEY(run_id) REFERENCES backtest_runs(id)
);

CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  contract_key TEXT NOT NULL,
  right TEXT NOT NULL,
  strike REAL NOT NULL,
  quantity INTEGER NOT NULL,
  entry_ts TEXT NOT NULL,
  exit_ts TEXT NOT NULL,
  entry_price REAL NOT NULL,
  exit_price REAL NOT NULL,
  buy_value REAL NOT NULL,
  sell_value REAL NOT NULL,
  gross_pnl REAL NOT NULL,
  charges REAL NOT NULL,
  net_pnl REAL NOT NULL,
  exit_reason TEXT NOT NULL,
  entry_decision_id INTEGER,
  exit_decision_id INTEGER,
  FOREIGN KEY(run_id) REFERENCES backtest_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_decisions_run ON decisions(run_id, ts);
CREATE INDEX IF NOT EXISTS idx_orders_run ON orders(run_id, ts);
CREATE INDEX IF NOT EXISTS idx_trades_run ON trades(run_id, exit_ts);
CREATE INDEX IF NOT EXISTS idx_points_run ON run_points(run_id, ts);
CREATE INDEX IF NOT EXISTS idx_candles_lookup ON candles(instrument_key, interval, ts);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def all(self, sql: str, params=()):
        with self.connect() as con:
            return [dict(r) for r in con.execute(sql, params).fetchall()]

    def one(self, sql: str, params=()):
        with self.connect() as con:
            r = con.execute(sql, params).fetchone()
            return dict(r) if r else None

    def execute(self, sql: str, params=()) -> int:
        with self.connect() as con:
            cur = con.execute(sql, params)
            return int(cur.lastrowid or 0)

    def add_symbol(self, data: dict) -> int:
        return self.execute(
            "INSERT INTO tracked_symbols(name,stock_code,cash_exchange,option_exchange,strike_step,lot_size) VALUES(?,?,?,?,?,?)",
            (data["name"], data["stock_code"].upper(), data.get("cash_exchange","NSE").upper(),
             data.get("option_exchange","NFO").upper(), float(data["strike_step"]), int(data["lot_size"]))
        )

    def symbols(self):
        return self.all("SELECT * FROM tracked_symbols ORDER BY active DESC, name")

    def symbol(self, symbol_id: int):
        return self.one("SELECT * FROM tracked_symbols WHERE id=?", (symbol_id,))

    def set_symbol_active(self, symbol_id: int, active: bool):
        self.execute("UPDATE tracked_symbols SET active=? WHERE id=?", (1 if active else 0, symbol_id))

    def delete_symbol(self, symbol_id: int):
        self.execute("DELETE FROM tracked_symbols WHERE id=?", (symbol_id,))

    def upsert_candles(self, instrument_key: str, interval: str, candles: Iterable[Candle]):
        rows=[(instrument_key,interval,c.ts.isoformat(),c.open,c.high,c.low,c.close,c.volume,c.open_interest) for c in candles]
        if not rows: return
        with self.connect() as con:
            con.executemany("""
              INSERT INTO candles(instrument_key,interval,ts,open,high,low,close,volume,open_interest)
              VALUES(?,?,?,?,?,?,?,?,?)
              ON CONFLICT(instrument_key,interval,ts) DO UPDATE SET
              open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
              volume=excluded.volume,open_interest=excluded.open_interest
            """, rows)

    def read_candles(self, instrument_key: str, interval: str, start: datetime, end: datetime) -> list[Candle]:
        rows=self.all("""SELECT * FROM candles WHERE instrument_key=? AND interval=? AND ts>=? AND ts<=? ORDER BY ts""",
                      (instrument_key,interval,start.isoformat(),end.isoformat()))
        return [Candle(datetime.fromisoformat(r['ts']),r['open'],r['high'],r['low'],r['close'],r['volume'],r['open_interest']) for r in rows]

    def mark_fetch(self, cache_key: str, rows: int):
        self.execute("INSERT OR REPLACE INTO fetch_cache(cache_key,fetched_at,rows) VALUES(?,?,?)",
                     (cache_key, datetime.now().isoformat(), rows))

    def fetched(self, cache_key: str) -> bool:
        return self.one("SELECT cache_key FROM fetch_cache WHERE cache_key=?", (cache_key,)) is not None

    def cache_get(self, key: str):
        r=self.one("SELECT response_json FROM jev_cache WHERE cache_key=?",(key,))
        return json.loads(r['response_json']) if r else None

    def cache_put(self, key: str, engine: str, model: str, kind: str, state_hash: str, response: dict):
        self.execute("""INSERT OR REPLACE INTO jev_cache(cache_key,engine,model,question_kind,state_hash,response_json)
                        VALUES(?,?,?,?,?,?)""",
                     (key,engine,model,kind,state_hash,json.dumps(response,separators=(',',':'))))

    def start_run(self, p: dict, model: str) -> int:
        return self.execute("""INSERT INTO backtest_runs(status,symbol_id,engine,model,start_date,end_date,expiry,interval,config_json)
          VALUES('running',?,?,?,?,?,?,?,?)""",
          (p['symbol_id'],p['engine'],model,p['start_date'],p['end_date'],p['expiry'],p['interval'],json.dumps(p)))

    def finish_run(self, run_id: int, summary: dict):
        self.execute("UPDATE backtest_runs SET status='complete', summary_json=? WHERE id=?",(json.dumps(summary),run_id))

    def fail_run(self, run_id: int, error: str):
        self.execute("UPDATE backtest_runs SET status='failed', error=? WHERE id=?",(error,run_id))

    def add_decision(self, run_id: int, ts: datetime, state_hash: str, kind: str, decision, state: dict) -> int:
        return self.execute("""INSERT INTO decisions(run_id,ts,state_hash,question_kind,action,probabilities_json,confidence,engine,model,latency_ms,cached,state_json,reason)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (run_id,ts.isoformat(),state_hash,kind,decision.action,json.dumps(decision.probabilities),decision.confidence,
           decision.engine,decision.model,decision.latency_ms,1 if decision.cached else 0,json.dumps(state,separators=(',',':')),decision.reason))

    def add_order(self, run_id:int, decision_id:int|None, ts:datetime, contract_key:str, side:str, qty:int, requested:float|None, fill:float|None, status:str, reason:str) -> int:
        return self.execute("""INSERT INTO orders(run_id,decision_id,ts,contract_key,side,quantity,requested_price,fill_price,status,reason)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",(run_id,decision_id,ts.isoformat(),contract_key,side,qty,requested,fill,status,reason))

    def add_trade(self, run_id:int, row:dict) -> int:
        cols=['contract_key','right','strike','quantity','entry_ts','exit_ts','entry_price','exit_price','buy_value','sell_value','gross_pnl','charges','net_pnl','exit_reason','entry_decision_id','exit_decision_id']
        return self.execute("INSERT INTO trades(run_id,"+','.join(cols)+") VALUES(?"+",?"*len(cols)+")",
                            [run_id]+[row.get(c) for c in cols])


    def add_points(self, run_id:int, points:list[dict]):
        if not points: return
        rows=[(run_id,p['ts'],p.get('spot'),p['equity']) for p in points]
        with self.connect() as con:
            con.executemany("INSERT OR REPLACE INTO run_points(run_id,ts,spot,equity) VALUES(?,?,?,?)", rows)

    def runs(self):
        rows=self.all("SELECT r.*, s.name symbol_name FROM backtest_runs r JOIN tracked_symbols s ON s.id=r.symbol_id ORDER BY r.id DESC LIMIT 100")
        for r in rows:
            if r.get('summary_json'):
                r['summary']=json.loads(r['summary_json'])
        return rows

    def run_report(self, run_id:int):
        run=self.one("SELECT r.*, s.name symbol_name,s.stock_code FROM backtest_runs r JOIN tracked_symbols s ON s.id=r.symbol_id WHERE r.id=?",(run_id,))
        if not run: return None
        run['config']=json.loads(run['config_json'])
        run['summary']=json.loads(run['summary_json']) if run.get('summary_json') else None
        run['decisions']=self.all("SELECT * FROM decisions WHERE run_id=? ORDER BY ts",(run_id,))
        for d in run['decisions']:
            d['probabilities']=json.loads(d['probabilities_json'])
            d.pop('probabilities_json',None); d.pop('state_json',None)
        run['orders']=self.all("SELECT * FROM orders WHERE run_id=? ORDER BY ts,id",(run_id,))
        run['trades']=self.all("SELECT * FROM trades WHERE run_id=? ORDER BY exit_ts,id",(run_id,))
        pts=self.all("SELECT ts,spot,equity FROM run_points WHERE run_id=? ORDER BY ts",(run_id,))
        if len(pts)>5000:
            step=max(1,len(pts)//5000); pts=pts[::step]
        run['series']={
          'equity_curve':[{'ts':x['ts'],'equity':x['equity']} for x in pts],
          'spot_curve':[{'ts':x['ts'],'spot':x['spot']} for x in pts if x['spot'] is not None],
        }
        return run
