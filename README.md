# Breeze + Jev Options Backtester

Fresh reference implementation of a short-horizon NSE options research loop inspired by the architecture of `jarrodwatts/jev-trader`, but designed around **ICICI Breeze historical data and deterministic replay** rather than Kuru/Monad market making.

## What it does

1. Pulls underlying and option OHLCV/OI through `BreezeConnect.get_historical_data_v2`.
2. Caches candles in SQLite so subsequent runs do not repeatedly download the same exact range.
3. Builds lagged spot→option predictive features using only observations available at each replay timestamp.
4. Produces either:
   - a deterministic causal/momentum baseline, or
   - a TypeSafe **Jev** structured decision: `BUY_CALL`, `BUY_PUT`, `NO_TRADE`, plus `HOLD`/`EXIT` while a position is open.
5. Applies deterministic risk limits outside the model.
6. Executes simulated orders on the **next available option candle open**, with configurable slippage and fees.
7. Stores decisions, probabilities, orders and realised trades in SQLite.
8. Exposes everything through a browser UI.

The causal diagnostic is a lagged predictive contribution measure, not proof of economic causation. It compares an autoregressive option-return model against the same model with lagged underlying returns added and records incremental R², slope, best lag and directional consistency.

## Install

```bash
python -m venv .venv
source .venv/bin/activate                 # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
```

Set:

```text
BREEZE_API_KEY=...
BREEZE_API_SECRET=...
TYPESAFE_API_KEY=...       # only required for Jev runs
```

Start:

```bash
python -m breeze_jev
```

Open `http://127.0.0.1:8000`.

## Breeze session

A Breeze `API_Session` is normally generated for the trading day from the ICICI API login page. The UI shows the correct login URL using your `BREEZE_API_KEY`. Paste the resulting session token into **Breeze authentication** and activate it. The token is cached locally in `data/breeze_session.json` for that day.

You can alternatively set `BREEZE_SESSION_TOKEN` in `.env`.

## Ideal UI workflow

### 1. Add the underlying

Use **Tracked symbols** once per underlying. Example values:

```text
Display name: NIFTY
Breeze stock code: NIFTY
Strike step: 50
Lot size: <current contract lot size>
Cash exchange: NSE
Option exchange: NFO
```

For stocks, the Breeze stock code can differ from the exchange display symbol. Use the stock code accepted by your Breeze account/API.

### 2. Choose a historical experiment

In **Backtest** select:

- symbol;
- start/end dates;
- a fixed option expiry;
- Breeze interval (`1second`, `1minute`, `5minute`, `30minute`, `1day`);
- decision engine (`heuristic` or `jev`).

The first version intentionally uses one fixed expiry per run. `end_date` must not be later than the expiry.

`Strike offsets = 0` loads only the dynamically changing ATM strike for each timestamp. `1` adds one strike above and below each encountered ATM. Wider ranges can cause many Breeze historical requests, so `max_contracts` is a guardrail.

### 3. Start with the heuristic baseline

Run the same dates first with `heuristic`. This establishes the backtester, data, cost model and causal feature behavior without an external decision model.

Inspect:

- net P&L and max drawdown;
- realised trade ledger;
- simulated order history;
- entry/exit decision probabilities;
- model confidence;
- charges and slippage assumptions.

### 4. Run Jev on the identical period

Switch the engine to `jev`. The Jev state contains the spot path, volatility, each candidate option's premium momentum, volume/OI, and causal diagnostics.

Every Jev result is cached by:

```text
model + question type + exact state hash
```

Therefore rerunning an unchanged experiment reuses the same decisions rather than paying for duplicate inference.

### 5. Compare, do not optimize on one run

Compare heuristic and Jev over the same dates. Then repeat on a separate out-of-sample date range. Useful breakdowns include confidence bucket, time of day, expiry distance, option side, causal strength, and lag edge.

Do not tune `min_confidence`, stop/target, or the feature window solely against one backtest period and treat the result as evidence of live expectancy.

## Replay semantics

A decision produced from candle `t` can never fill on candle `t`. The pending order is filled at the next available option candle's open:

```text
candle t closes
→ state constructed
→ causal/Jev decision
→ deterministic risk gate
→ candle t+1 option open
→ simulated fill (+/- slippage)
```

This prevents same-candle look-ahead.

For an open position, stop-loss/take-profit/max-hold/square-off are deterministic. Jev can additionally choose `HOLD` or `EXIT`; the actual sell still happens on the next available candle.

## SQLite records

Default database: `data/backtester.sqlite`.

Main tables:

- `tracked_symbols`
- `candles`
- `fetch_cache`
- `jev_cache`
- `backtest_runs`
- `decisions`
- `orders`
- `trades`

The realised ledger links entry and exit decisions to the simulated order history.

## Cost model

Historical Breeze OHLC does not provide a historical bid/ask spread for each candle. This implementation therefore uses an explicit simulation model:

- entry: next option open × `(1 + slippage_bps)`;
- exit: next option open × `(1 - slippage_bps)`;
- charges: configurable turnover basis points per side + flat brokerage per order.

Defaults are research placeholders, not an exact Indian statutory-charge calculator. Put your actual all-in fee/slippage assumptions in the UI before interpreting P&L.

## Breeze API considerations

The official Breeze V2 historical endpoint supports `1second`, `1minute`, `5minute`, `30minute`, and `1day` intervals and limits a single request to 1000 candle intervals. The source adapter automatically chunks larger requests and caches the results. Breeze's documented examples use exchange wall-clock timestamps such as `09:20:00.000Z` and return the matching `09:20` candle; the adapter therefore preserves the market wall-clock time rather than applying a UTC shift. Option expiry is normalized to Breeze's REST form (`YYYY-MM-DDT06:00:00.000Z`).

Historical data is not corporate-action adjusted according to the Breeze SDK documentation.

## Jev integration

Uses the current Python SDK:

```python
from typesafe_sdk import Choice, TypeSafeClient

response = client.system_one(
    state=state,
    questions={"decision": Choice(...)},
    model="jev-latest",
)
```

Jev is never allowed to bypass risk limits or directly place orders. The model estimates desirability; normal code decides whether the action is permitted.

## Tests

```bash
pytest -q
```

The tests are offline. Live Breeze/Jev calls require credentials and are deliberately not run by the test suite.
