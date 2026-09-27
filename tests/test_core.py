from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from breeze_jev.causal import rolling_causal_features
from breeze_jev.decision import HeuristicDecisionEngine

IST=ZoneInfo('Asia/Kolkata')

def test_causal_detects_positive_lag():
    spot=[100.0]
    for i in range(1,100): spot.append(spot[-1]*(1+(0.002 if i%5<3 else -0.001)))
    opt=[10.0,10.0]
    for i in range(2,100):
        sr=(spot[i-1]-spot[i-2])/spot[i-2]
        opt.append(opt[-1]*(1+2.0*sr))
    f=rolling_causal_features(spot,opt,1,window=60,max_lag=3)
    assert f['ready']
    assert f['slope']>0

def test_heuristic_returns_distribution():
    state={'calls':[{'right':'call','return_1':.01,'return_3':.02,'causal':{'strength':.8,'lag_edge':.01}}],
           'puts':[{'right':'put','return_1':-.01,'return_3':-.02,'causal':{'strength':.1,'lag_edge':0}}]}
    d=HeuristicDecisionEngine().entry(state)
    assert abs(sum(d.probabilities.values())-1)<1e-9
    assert d.action in d.probabilities
