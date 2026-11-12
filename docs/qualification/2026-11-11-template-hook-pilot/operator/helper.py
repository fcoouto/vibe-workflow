"""Frozen selection boundary: tool input cannot choose files or execute commands."""
import json
from pathlib import Path
import sys

selection = json.loads(Path(sys.argv[1]).read_text())
sys.path.insert(0, selection['package'])
from runtime.template_tools import check, notice, prepare

if sys.argv[2] == 'prepare':
    result = prepare(selection['project'], selection['templates'], ['AGENTS.md'])
else:
    result = check(selection['project'], selection['documents'], selection['templates'], before=selection['before'])
    result['notice'] = notice(result)
print(json.dumps(result))
