"""Real Jev answers over labelled replay cases; no Rekordbox actuator is created."""
import json
from pathlib import Path
import time
import uuid
from .jev_client import JevClient
from .policy import resolve

ROOT = Path(__file__).resolve().parents[1]


async def run_audit(key):
    cases = json.loads((ROOT/'evidence/decision-audit-input.json').read_text())
    if not isinstance(cases, list) or not 1 <= len(cases) <= 24:
        raise ValueError('Expected 1–24 labelled replay cases.')
    output = ROOT/'evidence/decision-audits'/str(uuid.uuid4())
    output.mkdir(parents=True)
    (output/'cases.json').write_text(json.dumps(cases, ensure_ascii=False, allow_nan=False, indent=2))
    client = JevClient(key)
    results = []
    try:
        for case in cases:
            request = case['request']
            if case.get('physical_control') is not False:
                raise ValueError('Replay must be explicitly marked as no physical control.')
            started = time.monotonic()
            try:
                answer = await client.ask(request)
                decision = resolve(request, answer)
                checks = case.get('checks', {'transport':case.get('expected', [])})
                result = {'case':case['name'], 'seconds':time.monotonic()-started,
                          'answer':answer, 'decision':{k:v for k,v in decision.items() if k!='answers'},
                          'expected':case.get('expected'),
                          'checks':checks,
                          'passed':all(decision.get(field) in values for field,values in checks.items())}
            except Exception as error:
                result = {'case':case['name'], 'seconds':time.monotonic()-started,
                          'passed':False, 'error':str(error).replace(key,'[verborgen]')}
            results.append(result)
            with (output/'results.jsonl').open('a') as stream:
                stream.write(json.dumps(result, ensure_ascii=False, allow_nan=False)+'\n')
        report = {'physical_control':False, 'cases':len(results),
                  'passed':sum(r['passed'] for r in results), 'directory':str(output)}
        (output/'summary.json').write_text(json.dumps(report, indent=2))
        return report
    finally:
        await client.close()
