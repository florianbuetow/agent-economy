from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
from threading import Thread
import httpx

REPO=Path('/Users/flo/Developer/github/agent-economy')
sys.path.insert(0,str(REPO))
from scripts.isolated_test_runtime import IsolatedRuntime, SERVICE_ORDER

class Sentinel(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'sentinel alive')
    def log_message(self,*args):
        pass

artifacts=Path(tempfile.mkdtemp(prefix='agent-economy-dual-task-proof-'))
sentinel=ThreadingHTTPServer(('127.0.0.1',0),Sentinel)
thread=Thread(target=sentinel.serve_forever,daemon=True)
thread.start()
first=IsolatedRuntime.create(repo_root=REPO,artifacts_root=artifacts,services=SERVICE_ORDER)
second=IsolatedRuntime.create(repo_root=REPO,artifacts_root=artifacts,services=SERVICE_ORDER)
print(f'Artifacts: {artifacts}',flush=True)

def start(runtime):
    runtime.start()
    assert runtime.provision()==0

def post(runtime,label,foreign_task=None):
    output=artifacts/f'{label}.json'
    command=['uv','run','--no-sync','python','/tmp/agent-economy-dual-task-client.py',str(output)]
    if foreign_task is not None: command.append(foreign_task)
    assert runtime.run_command(command,cwd=REPO/'agents')==0
    return json.loads(output.read_text())

try:
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs=[pool.submit(start,runtime) for runtime in (first,second)]
        for job in jobs: job.result()
    with ThreadPoolExecutor(max_workers=2) as pool:
        left=pool.submit(post,first,'first')
        right=pool.submit(post,second,'second')
        first_result=left.result()
        second_result=right.result()
    assert first_result['agent_id']!=second_result['agent_id']
    assert first_result['task_id']!=second_result['task_id']
    assert first_result['status']==second_result['status']=='open'
    first.stop()
    assert not first.processes
    assert httpx.get(f'http://127.0.0.1:{sentinel.server_port}/',timeout=2).text=='sentinel alive'
    assert httpx.get(second.service_url('task-board')+'/tasks/'+second_result['task_id'],timeout=2).json()['status']=='open'
    second_after=post(second,'second-after-first-stop',first_result['task_id'])
    assert second_after['agent_id']==second_result['agent_id']
    assert second_after['balance']['balance']==second_result['balance']['balance']-10
    assert second_after['foreign_task_status']==404
    report={'first':first_result,'second':second_result,'second_after_first_stop':second_after,'sentinel_survived':True,'runtime_dirs':[str(first.run_dir),str(second.run_dir)]}
    (artifacts/'proof.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)
finally:
    first.stop()
    second.stop()
    sentinel.shutdown()
    sentinel.server_close()
    thread.join(timeout=5)
    assert not first.processes and not second.processes and not thread.is_alive()
print('PASS: separate identities/tasks/data, surviving peer and sentinel, complete owned cleanup',flush=True)
