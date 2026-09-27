from __future__ import annotations
import math
import numpy as np


def _ret(xs:list[float])->np.ndarray:
    a=np.asarray(xs,dtype=float)
    if len(a)<2: return np.array([],dtype=float)
    prev=a[:-1]; cur=a[1:]
    return np.divide(cur-prev,prev,out=np.zeros_like(cur),where=prev!=0)


def _r2(y:np.ndarray, X:np.ndarray)->float:
    if len(y)<4: return 0.0
    X=np.column_stack([np.ones(len(X)),X])
    try: pred=X@np.linalg.lstsq(X,y,rcond=None)[0]
    except Exception: return 0.0
    ss=float(np.sum((y-y.mean())**2))
    if ss<=1e-15: return 0.0
    return max(0.0,min(1.0,1-float(np.sum((y-pred)**2))/ss))


def rolling_causal_features(spot_closes:list[float], option_closes:list[float], expected_sign:int, window:int=40, max_lag:int=4)->dict:
    """Lagged predictive contribution, not proof of economic causation.

    For each lag L, compare an autoregressive option-return model against the
    same model plus lagged underlying return. The incremental R² is a compact
    measure of whether spot history contributes predictive information beyond
    the option's own previous return. Only past observations are used.
    """
    n=min(len(spot_closes),len(option_closes))
    if n < max(window//2, 12):
        return {'ready':False,'strength':0.0,'lag_bars':0,'slope':0.0,'incremental_r2':0.0,'lag_consistency':0.0,'predicted_return':0.0,'lag_edge':0.0}
    s=_ret(spot_closes[-(window+max_lag+2):])
    o=_ret(option_closes[-(window+max_lag+2):])
    n=min(len(s),len(o)); s=s[-n:]; o=o[-n:]
    best=None
    for lag in range(1,max_lag+1):
        if n-lag-1 < 8: continue
        y=o[lag:]
        x=s[:-lag]
        prev=o[lag-1:-1]
        m=min(len(y),len(x),len(prev)); y=y[-m:]; x=x[-m:]; prev=prev[-m:]
        base=_r2(y,prev.reshape(-1,1))
        full=_r2(y,np.column_stack([prev,x]))
        inc=max(0.0,full-base)
        vx=float(np.var(x))
        slope=float(np.cov(x,y,bias=True)[0,1]/vx) if vx>1e-15 else 0.0
        directional=max(0.0, expected_sign*slope)
        score=inc*min(2.0,abs(slope)+0.25)* (1.0 if directional>0 else 0.15)
        row=(score,lag,slope,inc)
        if best is None or row[0]>best[0]: best=row
    if not best:
        return {'ready':False,'strength':0.0,'lag_bars':0,'slope':0.0,'incremental_r2':0.0,'lag_consistency':0.0,'predicted_return':0.0,'lag_edge':0.0}
    _,lag,slope,inc=best
    recent_spot=float(s[-lag:].sum()) if len(s)>=lag else 0.0
    predicted=slope*recent_spot
    observed=float(o[-1]) if len(o) else 0.0
    edge=predicted-observed
    # Consistency: same sign of slope in both halves at selected lag.
    y=o[lag:]; x=s[:-lag]; m=min(len(y),len(x)); y=y[-m:]; x=x[-m:]
    half=max(4,m//2)
    slopes=[]
    for xx,yy in [(x[:half],y[:half]),(x[-half:],y[-half:])]:
        vx=float(np.var(xx)); slopes.append(float(np.cov(xx,yy,bias=True)[0,1]/vx) if vx>1e-15 else 0.0)
    consistency=sum(1 for z in slopes if expected_sign*z>0)/len(slopes)
    strength=max(0.0,min(1.0,inc*4.0))*consistency
    return {'ready':True,'strength':round(strength,6),'lag_bars':lag,'slope':round(slope,6),
            'incremental_r2':round(inc,6),'lag_consistency':round(consistency,3),
            'predicted_return':round(predicted,6),'lag_edge':round(edge,6)}
