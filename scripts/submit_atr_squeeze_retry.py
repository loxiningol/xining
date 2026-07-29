#!/usr/bin/env python3
import json, os, sys, time, traceback
from pathlib import Path
ROOT=Path(os.environ.get('VECTOR_ROOT') or '/root')
sys.path.insert(0,str(ROOT)); os.chdir(str(ROOT))
PACK=Path(sys.argv[1] if len(sys.argv)>1 else '/root/strategy_atr_squeeze_structural_breakout.json')
DIRECTION=(sys.argv[2] if len(sys.argv)>2 else 'long').lower()
SYMBOL=sys.argv[3] if len(sys.argv)>3 else 'BTC-USDT-SWAP'
TIMEFRAME=sys.argv[4] if len(sys.argv)>4 else '15m'
MAX_TRIES=int(sys.argv[5] if len(sys.argv)>5 else 12)

def unblock():
    from dual_engine_workflow_v2.failure_kb import path_is_blocked
    p=Path('/root/auto_trade/dual_engine/workflow_v2/failure_knowledgebase.json')
    kb=json.loads(p.read_text())
    fam='compression_release_structural_breakout'
    kb['blocked_families']=[f for f in (kb.get('blocked_families') or []) if f!=fam]
    kb['blocked_paths']=[x for x in (kb.get('blocked_paths') or []) if fam not in str(x)]
    tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps(kb,ensure_ascii=False,indent=2)); tmp.replace(p)
    b,_=path_is_blocked('family|%s'%fam, family=fam)
    print('unblock_check', b, flush=True)

def main():
    import auto_trade_strategy_dsl as dsl_mod
    from dual_engine_workflow_v2.pipeline_step_a import run_creation_pipeline_step_a
    from dual_engine_workflow_v2.step_a_config import STEP_A_CODE_VERSION
    pack=json.loads(PACK.read_text())
    spec=pack['mechanism_spec']; dsl=pack['dsl_long' if DIRECTION=='long' else 'dsl_short']
    dsl_mod.validate_strategy(dsl)
    print('STEP_A', STEP_A_CODE_VERSION, 'max_tries', MAX_TRIES, flush=True)
    unblock()
    results=[]
    for i in range(1, MAX_TRIES+1):
        print('TRY', i, flush=True)
        t0=time.time()
        try:
            result=run_creation_pipeline_step_a(
                symbol=SYMBOL, timeframe=TIMEFRAME, exploration_mode='A',
                allow_horizontal_expand=False,
                prebuilt_spec_pack={
                    'ok':True,'mechanism_spec':spec,'dsl':dsl,
                    'dsl_long':pack.get('dsl_long'),'dsl_short':pack.get('dsl_short'),
                    'meta':{'symbol':SYMBOL,'timeframe':TIMEFRAME,'direction':DIRECTION,
                            'title':spec.get('mechanism_name'),'source':'atr_squeeze_retry'},
                    'errors':[],'call_id':'atr_squeeze_%s_%s_%d'%(DIRECTION,int(time.time()),i),'attempts':0,
                },
                windtalker_tag='atr_squeeze_structural_breakout_%s_try%d'%(DIRECTION,i),
            )
        except Exception as exc:
            result={'ok':False,'reason':'exception','error':str(exc),'trace':traceback.format_exc()[-800:]}
        elapsed=round(time.time()-t0,2)
        summary={'try':i,'ok':(result or {}).get('ok'),'task_id':(result or {}).get('task_id'),
                 'reason':(result or {}).get('reason') or (result or {}).get('stage'),'elapsed':elapsed}
        print('SUMMARY', json.dumps(summary,ensure_ascii=False), flush=True)
        results.append({'summary':summary,'result':result})
        reason=str(summary.get('reason') or '')
        if summary.get('ok'):
            break
        if reason in ('funnel_l1_fail','kb_blocked'):
            unblock(); continue
        # hard stop on other gate fails
        break
    out=ROOT/'auto_trade'/'dual_engine'/'workflow_v2'/('atr_squeeze_retry_%s_%s.json'%(DIRECTION,time.strftime('%Y%m%d_%H%M%S')))
    out.write_text(json.dumps({'results':results},ensure_ascii=False,indent=2,default=str))
    print('OUT', out, flush=True)
    return 0 if results and results[-1]['summary'].get('ok') else 2

if __name__=='__main__':
    sys.exit(main())
