from pathlib import Path
import shutil
import subprocess
repo=Path('/Users/flo/Developer/github/agent-economy')
stage=Path('/tmp/agent-economy-mp02-ui-baseline')
for rel in ('services/ui/src','services/ui/tests','services/ui/data/web'):
    shutil.copytree(repo/rel,stage/rel,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','reports'))
for rel in ('services/ui/pyproject.toml','services/ui/config.yaml','docs/specifications/schema.sql'):
    (stage/rel).parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(repo/rel,stage/rel)
# Read-only historical files isolate the pre-MP02 UI behavior; retain all newer
# disposable credential/config fixtures so unrelated setup cannot masquerade as red proof.
restore=['services/ui/src/ui_service/app.py']
restore += [str(p.relative_to(repo)) for p in (repo/'services/ui/data/web').rglob('*') if p.suffix in {'.js','.html'}]
for rel in restore:
    result=subprocess.run(['git','show',f'HEAD:{rel}'],cwd=repo,capture_output=True,check=True)
    (stage/rel).write_bytes(result.stdout)
print(stage)
