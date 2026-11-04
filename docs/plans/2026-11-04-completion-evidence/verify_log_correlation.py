import json
from pathlib import Path
import sys

import httpx
import yaml

sys.path.insert(0, str(Path.cwd()))
from scripts.isolated_test_runtime import IsolatedRuntime

root = Path.cwd()
runtime = IsolatedRuntime.create(repo_root=root, artifacts_root=Path('/tmp/agent-economy-goal-2026-11-04/telemetry'))
trace_id = '1234567890abcdef1234567890abcdef'
result = {'trace_id': trace_id, 'runtime': str(runtime.run_dir)}
try:
    for service in ('task-board', 'db-gateway'):
        config_path = runtime.config_paths[service]
        config = yaml.safe_load(config_path.read_text())
        config['telemetry']['enabled'] = True
        config['telemetry']['sample_ratio'] = 1.0
        config_path.write_text(yaml.safe_dump(config))
    runtime.start()
    health = {}
    for service in runtime.services:
        response = httpx.get(runtime.service_url(service) + '/health', timeout=5)
        response.raise_for_status()
        health[service] = response.json()['status']
    result['health'] = health
    response = httpx.get(
        runtime.service_url('task-board') + '/tasks/t-00000000-0000-4000-8000-000000000999',
        headers={'traceparent': f'00-{trace_id}-1234567890abcdef-01'},
        timeout=5,
    )
    result['request_status'] = response.status_code
    assert response.status_code == 404
finally:
    runtime.stop()
records = []
for path in (runtime.run_dir / 'logs').glob('*.log'):
    for line in path.read_text(errors='replace').splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get('trace_id') == trace_id:
            records.append({key: record.get(key) for key in ('logger', 'message', 'trace_id', 'span_id')})
result['correlated_logs'] = records
result['viewer_verified'] = False
result['scope'] = 'Real HTTP propagation and structured file-log correlation; no collector/viewer is installed or claimed.'
output = root / 'docs/plans/2026-11-04-completion-evidence/telemetry-local.json'
output.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
assert any('task-board' in row['logger'] for row in records)
assert any('db-gateway' in row['logger'] for row in records)
assert all(value == 'ok' for value in result['health'].values())
