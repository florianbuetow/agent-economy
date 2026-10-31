from pathlib import Path
import hashlib
import json
import sys

repo = Path('/Users/flo/Developer/github/agent-economy')
prior = json.loads(Path('/private/tmp/agent-economy-mp07-ui-parent-final-before.json').read_text())
paths = set()
for directory in ('services/ui/src', 'services/ui/tests', 'libs/service-auth/src'):
    paths.update((repo / directory).rglob('*.py'))
paths.update((repo / 'services/ui/data/web/assets').glob('*.js'))
for name in ('services/ui/config.yaml', 'services/ui/pyproject.toml', 'services/ui/justfile'):
    paths.add(repo / name)
sources = {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
logs = {}
for root in prior['log_roots']:
    for path in sorted(Path(root).rglob('*')):
        if path.is_file():
            logs[str(path.relative_to(repo))] = hashlib.sha256(path.read_bytes()).hexdigest()
Path(sys.argv[1]).write_text(json.dumps({'sources': sources, 'logs': logs}, indent=2) + '\n')
print(len(sources), 'sources and', len(logs), 'ordinary logs recorded')
