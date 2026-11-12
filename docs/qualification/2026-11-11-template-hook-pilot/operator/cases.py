"""Identical inputs and protected checks for the three comparison arms."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from runner import HERE, ROOT, STATE, TEMP, Runtime, initialize, save, tree

TEMPLATES = ['project/product.md','project/overview.md','project/local-preparation.md']
COMMON = '''This is a bounded documentation-adaptation trial, not onboarding or environment preparation.
Read the original instructions, source code and check.py. Preserve every original nonblank line,
all code and checks, nested instructions, filenames, client choices and local-only/manual-merge preferences.
Adapt only the selected product, overview and preparation responsibilities, combining existing owners.
Describe actual purpose, scope, essential behavior and acceptance, code map, standard-library prerequisites,
first-run/check command and what it proves, known limits, and one explicitly PROPOSED next task with an
observable acceptance criterion. Infer required runtime from code; do not invent a minimum version.
Link only existing canonical owners with destination-relative links. Do not create a nine-file layout,
setup record, database, integration, service, account or UI. Omit irrelevant template sections and all
template author instructions/placeholders. Preserve any original author-like wording. State missing
setup evidence honestly. The templates provide shapes, not extra task requirements.
Check preservation, factual accuracy, local links and residue before concluding. You may repair your
own output within this session. No network, shell, arbitrary code execution, delegation, Git or installs.
Coordinator supplied evidence: python3 -B check.py passed on the initial fixture; this is not your execution.
Stop with a compact handoff naming changed paths, actual/supplied checks, limits and next action.
Your output is reviewable, not independently accepted. Date: 2026-11-11.
'''


def preserved(root, initial, allowed, hashes):
    findings = []
    for name, original in initial.items():
        path = root/name
        actual = path.read_text() if path.is_file() else ''
        if name in allowed:
            if Counter(line for line in original.splitlines() if line.strip()) - Counter(actual.splitlines()):
                findings.append('original_missing:'+name)
        elif not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != hashes[name]:
            findings.append('protected_changed:'+name)
    for path in root.rglob('*'):
        if path.is_file() and str(path.relative_to(root)) not in initial:
            findings.append('unexpected_file:'+str(path.relative_to(root)))
    return findings


def package_copy(base):
    package = base/'package'
    names = ['runtime/template_tools.py','scripts/read_instructions.py','scripts/template_tools.py',
             'clients/instruction-map.json'] + ['templates/'+t for t in TEMPLATES]
    for name in names:
        target=package/name; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,target)
    return package


def freeze():
    assert not STATE.exists(), 'pilot state already exists; never overwrite'
    base=Path(tempfile.mkdtemp(prefix='template-hook-pilot-',dir=TEMP)); base.chmod(0o700)
    package=package_copy(base)
    rows=[]
    for case,arms in [('minimal',['baseline','helper','hook']),('customized',['hook','helper','baseline'])]:
        source=ROOT/'assets/harness-evaluation/phase-two'/case/'input'
        before=base/'before'/case
        shutil.copytree(source,before)
        done=subprocess.run([sys.executable,'-I','-B','-c',
                             "import runpy,sys;sys.path.insert(0,'.');runpy.run_path('check.py')"],
                            cwd=before,capture_output=True,text=True,timeout=10)
        assert done.returncode==0, 'fixture baseline failed'
        allowed=['README.md'] if case=='minimal' else ['README.md','handbook/overview.md','handbook/engineering.md']
        mapping=('Combine the three responsibilities in README.md.' if case=='minimal' else
                 'README.md stays the entry point; handbook/overview.md owns product, handbook/engineering.md owns preparation; handbook/ARCHITECTURE.md stays unchanged.')
        for arm in arms:
            name=f'{case}-{arm}'; runtime=Runtime(base/name)
            runtime.prepare(allowed,arm,package,before,TEMPLATES)
            shutil.copytree(source,runtime.project,dirs_exist_ok=True)
            for template in TEMPLATES:
                target=runtime.project/'.workflow/templates'/template; target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(ROOT/'templates'/template,target)
            instruction=('Read .workflow/templates/project/product.md, overview.md and local-preparation.md with native reads; adapt only their applicable sections.' if arm=='baseline' else
                         'Call template_prepare once to read the selected shapes and context hashes. Read consumer files separately. Call template_check after your document batch; resolve relevant findings. This check is mechanical, not semantic acceptance.')
            prompt=COMMON+'\n'+mapping+'\nWrite only '+', '.join(allowed)+'.\n'+instruction
            if arm=='hook': prompt+='\nAn advisory native after-edit hook also checks the same document selection; silent success and unchanged findings are suppressed.'
            (runtime.base/'prompt.txt').write_text(prompt)
            initial={str(p.relative_to(runtime.project)):p.read_text() for p in runtime.project.rglob('*') if p.is_file()}
            manifest=dict(name=name,case=case,arm=arm,allowed=allowed,inputs=tree(runtime.project),
                          prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
            save(runtime.base/'manifest.json',manifest); save(runtime.base/'initial.json',initial); rows.append(manifest)
    probe=Runtime(base/'opencode-probe')
    before=base/'before/probe'; before.mkdir()
    (before/'README.md').write_text('# Probe\n\nKeep this original line.\n')
    probe.prepare(['README.md'],'probe',package,before,TEMPLATES)
    shutil.copyfile(before/'README.md',probe.project/'README.md')
    (probe.project/'unrelated.txt').write_text('Unrelated read must not run a template check.\n')
    (probe.project/'AGENTS.md').write_text('Synthetic hook probe. Edit only README.md. No shell, network, delegation or configuration edits.\n')
    prompt='''Perform these steps in order, one tool call at a time. Read README.md. Append exactly [Probe link](missing.md) to README.md using patch or edit. Observe the advisory finding; leave the broken link for the next step. Call pilot_batch twice without editing. Remove only that added link using patch or edit. Call pilot_batch once. Read unrelated.txt. Stop and report whether the first edit feedback mentioned link_missing, whether repeated batch feedback was suppressed, and whether successful tool output remained visible. No extra files or tools. Do not repair early; this deliberate synthetic error is the probe input.'''
    (probe.base/'prompt.txt').write_text(prompt)
    save(probe.base/'manifest.json',dict(name='opencode-probe',inputs=tree(probe.project)))
    initialize(STATE,base)
    save(base/'frozen.json',dict(source=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                               package=tree(package),runs=rows,probe='opencode-probe',source_templates=TEMPLATES))
    print(json.dumps(dict(base=str(base),comparisons=len(rows),baseline_checks='passed',model_calls=0)))


if __name__=='__main__':
    freeze()
