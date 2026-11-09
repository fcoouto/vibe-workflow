"""Qualification canary patch validation; moved unchanged from setup_observation."""
import re
from pathlib import Path

from runtime import onboarding as d3, startup


def _public_canary_patch(descriptor, repository_root, *, readback=None):
    """The qualification probe supports one unambiguous public-file update hunk."""
    d3._object(descriptor,('canary','patch','before_sha256'))
    canary=startup._canonical_path(descriptor['canary']);root=startup._canonical_path(str(repository_root))
    startup._canonical_hash(descriptor['before_sha256'])
    patch=descriptor['patch']
    d3._require(isinstance(patch,str) and 0<len(patch.encode('utf-8'))<=65536
        and not any(ord(character)<32 and character not in '\n\t' or 127<=ord(character)<=159 for character in patch),
        'Public canary patch contains unsupported bytes')
    lines=patch.removesuffix('\n').split('\n')
    d3._require(len(lines)>=6 and lines[0]=='*** Begin Patch' and lines[-1]=='*** End Patch'
        and lines[1].startswith('*** Update File: ') and lines[2]=='@@',
        'Public canary patch requires one Update File hunk')
    relative=lines[1][len('*** Update File: '):]
    # Codex trims the update header before interpreting its path.
    d3._require(relative and relative == relative.rstrip() and not Path(relative).is_absolute() and '\\' not in relative
        and all(part not in ('','.','..') for part in relative.split('/')) and root/relative==canary,
        'Public canary patch selected another file')
    hunk=lines[3:-1]
    d3._require(all(line and line[0] in ' +-' for line in hunk)
        and any(line.startswith('-') for line in hunk) and any(line.startswith('+') for line in hunk),
        'Public canary patch requires a nonempty single-file change')
    original=[line[1:] for line in hunk if line[0] in ' -']
    changed=[line[1:] for line in hunk if line[0] in ' +']
    d3._require(original!=changed and any(line[1:] for line in hunk if line[0] in '+-'),
        'Public canary patch does not change original content')
    if readback is not None:
        d3._require(isinstance(readback,str) and len(readback.encode('utf-8'))<=65536,'Public canary readback exceeds its bound')
        parts=re.split(r'(\r\n|\r|\n)',readback)
        current='\n'+'\n'.join(parts[::2]).removesuffix('\n')+'\n';needle='\n'+'\n'.join(original)+'\n'
        occurrence=current.find(needle)
        d3._require(occurrence>=0 and current.find(needle,occurrence+1)==-1,
            'Public canary patch does not match its original readback')
        # Codex preserves existing endings and uses the first ending for changed lines.
        ending=parts[1] if len(parts)>1 else '\n'
        cursor=2*current.count('\n',0,occurrence)
        updated=parts[:cursor]
        for line in hunk:
            if line[0]=='+':
                updated.extend((line[1:],ending))
            else:
                if line[0]==' ':
                    context_ending=parts[cursor+1] if cursor+1<len(parts) else ending
                    updated.extend((parts[cursor],context_ending))
                cursor+=2
        updated.extend(parts[cursor:])
        result=''.join(updated)
        return result if result.endswith(('\r','\n')) else result+ending
