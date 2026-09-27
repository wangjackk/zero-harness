import json, time, urllib.request

def call(name, kwargs=None, timeout=20):
    body = json.dumps({'name': name, 'kwargs': kwargs or {}}).encode()
    req = urllib.request.Request('http://127.0.0.1:7781/run_routine', data=body,
                                  headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as exc:
        return {'error': f'{type(exc).__name__}: {exc}'}

t0 = time.time()
r = call('routine/reload', {'path': 'routines/user/dynamic'})
print(f'reload escape hatch ({time.time()-t0:.1f}s):', json.dumps(r, ensure_ascii=False)[:200])
