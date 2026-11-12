"""CLI fallback for a local Codex workflow; input JSON on stdin, never arguments."""
import argparse,json,sys
from store import Store,Capture
p=argparse.ArgumentParser();p.add_argument('operation',choices=['map','search','capture','revise','archive','undo','export']);p.add_argument('--db');a=p.parse_args();s=Store(a.db)
try:
    payload={} if a.operation in ('map','export') else json.load(sys.stdin)
    if a.operation in ('map','export'):out=s.view(include_archived=a.operation=='export')
    elif a.operation=='capture':out=s.capture(Capture.model_validate(payload))
    elif a.operation=='search':out=s.search(**payload)
    else:out=getattr(s,a.operation)(**payload)
    print(json.dumps(out,ensure_ascii=False,indent=2))
except Exception as e:
    print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);sys.exit(1)
