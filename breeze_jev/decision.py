from __future__ import annotations
import hashlib, json, math, os, time
from .db import Database
from .models import Decision
from .features import state_hash

ENTRY_ACTIONS=['BUY_CALL','BUY_PUT','NO_TRADE']
EXIT_ACTIONS=['HOLD','EXIT']

class DecisionEngine:
    name='base'; model='none'
    def entry(self,state:dict)->Decision: raise NotImplementedError
    def exit(self,state:dict)->Decision: raise NotImplementedError


def _softmax(scores:dict[str,float])->dict[str,float]:
    m=max(scores.values()); ex={k:math.exp(v-m) for k,v in scores.items()}; z=sum(ex.values())
    return {k:v/z for k,v in ex.items()}

class HeuristicDecisionEngine(DecisionEngine):
    name='heuristic'; model='causal-heuristic-v1'
    def entry(self,state:dict)->Decision:
        t=time.perf_counter()
        def best(rows):
            vals=[]
            for x in rows:
                c=x['causal']; edge=c['lag_edge']; expected_edge=edge if x['right']=='call' else edge
                vals.append(3.2*c['strength'] + 25*max(0.0,expected_edge) + 1.2*max(0.0,x['return_3']) + 0.4*max(0.0,x['return_1']))
            return max(vals,default=-2.0)
        cs=best(state['calls']); ps=best(state['puts'])
        probs=_softmax({'BUY_CALL':cs,'BUY_PUT':ps,'NO_TRADE':0.85})
        action=max(probs,key=probs.get)
        return Decision(action,probs,probs[action],(time.perf_counter()-t)*1000,self.name,self.model,reason='deterministic causal/momentum baseline')
    def exit(self,state:dict)->Decision:
        t=time.perf_counter(); p=state['position']; r=p['return_since_entry']; dd=p['drawdown_from_peak']; held=p['bars_held']
        score_exit=max(0.0,-r*25)+max(0.0,-dd*18)+held/20
        probs=_softmax({'HOLD':1.0,'EXIT':score_exit})
        action=max(probs,key=probs.get)
        return Decision(action,probs,probs[action],(time.perf_counter()-t)*1000,self.name,self.model,reason='deterministic exit baseline')

class JevDecisionEngine(DecisionEngine):
    name='jev'
    def __init__(self,db:Database,model:str='jev-latest'):
        self.db=db; self.model=model

    def _call(self,state:dict,kind:str)->Decision:
        sh=state_hash(state); ck=hashlib.sha256(f'{self.name}|{self.model}|{kind}|{sh}'.encode()).hexdigest()
        cached=self.db.cache_get(ck)
        if cached:
            return Decision(cached['action'],cached['probabilities'],cached['confidence'],cached.get('latency_ms',0),self.name,self.model,True,cached.get('reason','cached'))
        if not os.getenv('TYPESAFE_API_KEY'):
            raise RuntimeError('TYPESAFE_API_KEY is required when engine=jev')
        from typesafe_sdk import Choice, TypeSafeClient
        if kind=='entry':
            instructions=(
              'Choose the best action for the next short option-trading horizon. Use only the supplied historical state. '
              'The trade, if any, will be entered on the next available option candle, so a move must be large enough to justify execution friction. '
              'Causal fields are lagged predictive diagnostics, not proof. Prefer NO_TRADE when evidence is weak, conflicting, stale, or already reflected in premium.'
            )
            criteria={
              'BUY_CALL':'A call option candidate has the clearest positive short-horizon edge after costs.',
              'BUY_PUT':'A put option candidate has the clearest positive short-horizon edge after costs.',
              'NO_TRADE':'No candidate has sufficiently strong and coherent edge.'}
        else:
            instructions=(
              'Decide whether an existing long option position should be held for another bar or exited on the next available candle. '
              'Use current return, drawdown from peak, holding duration, and only the supplied state. Prefer EXIT when the original short-horizon edge appears spent.'
            )
            criteria={'HOLD':'Expected benefit of holding another bar outweighs exit now.','EXIT':'The short-horizon edge has weakened or downside risk dominates.'}
        t=time.perf_counter()
        with TypeSafeClient() as client:
            resp=client.system_one(state=state,questions={'decision':Choice(instructions=instructions,criteria=criteria)},model=self.model)
        ans=(getattr(resp,'choices',{}) or {}).get('decision')
        if ans is None: ans=resp.answers['decision']
        probs={str(k):float(v) for k,v in ans.probabilities.items()}
        action=str(ans.choice); conf=float(ans.confidence)
        result={'action':action,'probabilities':probs,'confidence':conf,'latency_ms':(time.perf_counter()-t)*1000,'reason':'TypeSafe Jev structured decision'}
        self.db.cache_put(ck,self.name,self.model,kind,sh,result)
        return Decision(action,probs,conf,result['latency_ms'],self.name,self.model,False,result['reason'])

    def entry(self,state): return self._call(state,'entry')
    def exit(self,state): return self._call(state,'exit')
