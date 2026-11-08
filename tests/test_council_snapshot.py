"""Reviewed bytes stay bounded, private and independent of later checkout edits."""
import importlib
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

from runtime import onboarding
from tests.council_fixtures import CouncilCase


class SnapshotTests(CouncilCase):
    def setUp(self):
        super().setUp()
        self.assertTrue((Path(__file__).resolve().parents[1] / 'runtime/council_snapshot.py').exists(),
                        'Council snapshot helper must exist')
        self.api = importlib.import_module('runtime.council_snapshot')
        self.destination = self.root / 'evidence'

    def write(self, name, raw=b'reviewed evidence\n'):
        target = self.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return target

    def test_captures_committed_dirty_untracked_and_valid_dotfiles(self):
        self.write('committed.txt', b'committed\n')
        self.write('dirty.txt', b'old\n')
        self.git('add', 'committed.txt', 'dirty.txt')
        self.git('commit', '-q', '-m', 'fixture')
        self.write('dirty.txt', b'dirty\n')
        self.write('untracked.txt', b'untracked\n')
        self.write('.github/workflows/check.yml', b'name: check\n')
        self.write('.gitignore', b'cache/\n')
        names = ['committed.txt', 'dirty.txt', 'untracked.txt', '.github/workflows/check.yml', '.gitignore']
        approval = self.approved(names)
        result = self.api.capture(self.repo, self.destination, approval)
        self.assertEqual(result['files'], approval)
        self.assertEqual(result['version'], 1)
        self.assertEqual(result['coverage']['captured'], 5)
        self.assertTrue(result['coverage']['gaps'])
        for name in names:
            self.assertEqual((self.destination / name).read_bytes(), (self.repo / name).read_bytes())
            self.assertEqual((self.destination / name).stat().st_mode & 0o777, 0o400)
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o500)
        self.api.verify(self.destination, result)
        self.write('dirty.txt', b'later source edit\n')
        self.assertEqual((self.destination / 'dirty.txt').read_bytes(), b'dirty\n')
        self.api.verify(self.destination, result)

    def test_empty_snapshot_requires_explicit_text_mode_opt_in(self):
        with self.assertRaisesRegex(ValueError, 'invalid_council_approved_files'):
            self.api.capture(self.repo, self.destination, {})
        self.assertFalse(self.destination.exists())
        manifest = self.api.capture(self.repo, self.destination, {}, allow_empty=True)
        self.assertEqual(manifest['files'], {})
        self.assertEqual(manifest['coverage']['captured'], 0)
        self.api.verify(self.destination, manifest)

    def test_unapproved_files_are_never_copied(self):
        self.write('approved.txt')
        self.write('private.txt', b'not reviewed\n')
        result = self.api.capture(self.repo, self.destination, self.approved(['approved.txt']))
        self.assertFalse((self.destination / 'private.txt').exists())
        self.assertEqual(set(result['files']), {'approved.txt'})

    def test_stale_review_does_not_publish(self):
        self.write('source.txt', b'before\n')
        approved = self.approved(['source.txt'])
        self.write('source.txt', b'after!\n')
        with self.assertRaises(ValueError):
            self.api.capture(self.repo, self.destination, approved)
        self.assertFalse(self.destination.exists())

    def test_source_replacement_during_capture_does_not_publish(self):
        path = self.write('source.txt')
        approved = self.approved(['source.txt'])
        original = onboarding._read_file
        def replace_after_read(location, *args, **kwargs):
            raw = original(location, *args, **kwargs)
            if location == path:
                replacement = self.write('replacement.txt', raw)
                replacement.replace(path)
            return raw
        with patch.object(onboarding, '_read_file', replace_after_read), self.assertRaises(ValueError):
            self.api.capture(self.repo, self.destination, approved)
        self.assertFalse(self.destination.exists())

    def test_traversal_and_malformed_reviews_fail_without_publication(self):
        self.write('source.txt')
        record = self.approved(['source.txt'])['source.txt']
        for approved in ({'../outside': record}, {'/absolute': record}, {'a//b': record},
                         {'./source.txt': record}, {'a/../source.txt': record}, {'a\n.txt': record},
                         {'source.txt': {'sha256': '0' * 64, 'size': True}},
                         {'source.txt': dict(record, extra='field')}, {}, []):
            with self.subTest(approved=approved), self.assertRaises(ValueError):
                self.api.capture(self.repo, self.destination, approved)
            self.assertFalse(self.destination.exists())

    def test_links_hardlinks_directories_and_special_files_fail(self):
        self.write('real.txt')
        (self.repo / 'link.txt').symlink_to('real.txt')
        os.link(self.repo / 'real.txt', self.repo / 'hard.txt')
        (self.repo / 'directory').mkdir()
        os.mkfifo(self.repo / 'pipe')
        record = dict(sha256='0' * 64, size=0)
        for name in ('link.txt', 'hard.txt', 'real.txt', 'directory', 'pipe'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.api.capture(self.repo, self.destination, {name: record})
            self.assertFalse(self.destination.exists())
        (self.repo / 'linked-directory').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.api.capture(self.repo, self.destination, {'linked-directory/file': record})

    def test_credentials_git_runtime_and_nested_repositories_are_excluded(self):
        names = ['safe.txt', '.env', '.env.example', 'cert.pem', 'id_rsa', '.netrc', '.npmrc',
                 '.aws/config', 'auth.json', 'credentials.json', '.git/config',
                 '.vibe-workflow/project.json', '.codex/history.jsonl',
                 'nested/.git/HEAD', 'nested/source.txt']
        for name in names:
            if name != '.vibe-workflow/project.json': self.write(name)
        result = self.api.capture(self.repo, self.destination, self.approved(names))
        self.assertEqual(set(result['files']), {'safe.txt'})
        self.assertEqual({row['path'] for row in result['exclusions']}, set(names) - {'safe.txt'})
        self.assertFalse((self.destination / '.env').exists())
        self.assertFalse((self.destination / '.git').exists())
        self.api.verify(self.destination, result)

    def test_invalid_utf8_and_unsafe_source_permissions_fail(self):
        path = self.write('source.txt', b'\xff\xfe')
        with self.assertRaises(ValueError):
            self.api.capture(self.repo, self.destination, self.approved(['source.txt']))
        path.write_text('safe\n')
        for mode in (0o666, 0o4644):
            path.chmod(mode)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.api.capture(self.repo, self.destination, self.approved(['source.txt']))
        path.chmod(0o644)
        self.repo.chmod(0o777)
        with self.assertRaises(ValueError):
            self.api.capture(self.repo, self.destination, self.approved(['source.txt']))
        self.repo.chmod(0o755)
        self.assertFalse(self.destination.exists())

    def test_size_count_total_and_free_space_bounds_fail(self):
        self.write('source.txt', b'12345')
        approved = self.approved(['source.txt'])
        for bound in ('MAX_FILE', 'MAX_TOTAL'):
            with self.subTest(bound=bound), patch.object(self.api, bound, 4), self.assertRaises(ValueError):
                self.api.capture(self.repo, self.destination, approved)
        self.write('other.txt', b'67890')
        with patch.object(self.api, 'MAX_PATHS', 1), self.assertRaises(ValueError):
            self.api.capture(self.repo, self.destination, self.approved(['source.txt', 'other.txt']))
        with patch.object(self.api.shutil, 'disk_usage', return_value=(1, 1, 0)), self.assertRaises(ValueError):
            self.api.capture(self.repo, self.destination, approved)
        self.assertFalse(self.destination.exists())

    def test_file_size_ceiling_accepts_512_kib_and_rejects_next_byte(self):
        self.write('source.txt', b'a' * (512 * 1024))
        manifest = self.api.capture(self.repo, self.destination, self.approved(['source.txt']))
        self.assertEqual((self.destination / 'source.txt').stat().st_size, 524288)
        self.api.verify(self.destination, manifest)
        self.write('source.txt', b'a' * (512 * 1024 + 1))
        with self.assertRaises(ValueError):
            self.api.capture(self.repo, self.root / 'overflow', self.approved(['source.txt']))

    def test_path_count_ceiling_accepts_2000_and_rejects_next_path(self):
        # Excluded paths still consume the review ceiling without requiring 2001 source files.
        empty = dict(sha256=hashlib.sha256(b'').hexdigest(), size=0)
        approved = {f'.env.{index}': dict(empty) for index in range(2000)}
        manifest = self.api.capture(self.repo, self.destination, approved)
        self.assertEqual(manifest['coverage']['excluded'], 2000)
        approved['.env.overflow'] = empty
        with self.assertRaises(ValueError):
            self.api.capture(self.repo, self.root / 'overflow', approved)

    def test_total_size_ceiling_accepts_32_mib_and_rejects_next_byte(self):
        names = [f'part-{index}.txt' for index in range(64)]
        for name in names:
            self.write(name, b'a' * (512 * 1024))
        manifest = self.api.capture(self.repo, self.destination, self.approved(names))
        self.assertEqual(sum(item['size'] for item in manifest['files'].values()), 33554432)
        self.write('overflow.txt', b'x')
        with self.assertRaises(ValueError):
            self.api.capture(self.repo, self.root / 'overflow', self.approved([*names, 'overflow.txt']))

    def test_destination_must_be_absent_private_and_outside_checkout(self):
        self.write('source.txt')
        approved = self.approved(['source.txt'])
        self.destination.mkdir(mode=0o700)
        marker = self.destination / 'retained.txt'
        marker.write_text('retained')
        for destination in (self.destination, self.repo / 'snapshot', Path('relative-snapshot')):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.api.capture(self.repo, destination, approved)
        self.assertEqual(marker.read_text(), 'retained')
        self.root.chmod(0o755)
        with self.assertRaises(ValueError):
            self.api.capture(self.repo, self.root / 'new', approved)
        self.root.chmod(0o700)
        (self.temporary / 'link').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.api.capture(self.repo, self.temporary / 'link' / 'new', approved)

    def test_destination_cannot_be_inside_a_bare_repository(self):
        self.write('source.txt')
        bare = self.temporary / 'bare'
        bare.mkdir(mode=0o700)
        (bare / 'HEAD').write_text('ref: refs/heads/main\n')
        (bare / 'objects').mkdir()
        (bare / 'refs').mkdir()
        with self.assertRaises(ValueError):
            self.api.capture(self.repo, bare / 'snapshot', self.approved(['source.txt']))

    def test_verify_rejects_changed_missing_extra_linked_bytes_or_manifest(self):
        self.write('source.txt')
        manifest = self.api.capture(self.repo, self.destination, self.approved(['source.txt']))
        path = self.destination / 'source.txt'
        original = path.read_bytes()
        path.chmod(0o600)
        path.write_bytes(b'changed')
        path.chmod(0o400)
        with self.assertRaises(ValueError): self.api.verify(self.destination, manifest)
        path.chmod(0o600)
        path.write_bytes(original)
        path.chmod(0o400)
        self.destination.chmod(0o700)
        extra = self.destination / 'extra.txt'
        extra.write_text('extra')
        with self.assertRaises(ValueError): self.api.verify(self.destination, manifest)
        extra.unlink()
        path.unlink()
        with self.assertRaises(ValueError): self.api.verify(self.destination, manifest)
        path.symlink_to(self.repo / 'source.txt')
        with self.assertRaises(ValueError): self.api.verify(self.destination, manifest)
        path.unlink()
        path.write_bytes(original)
        path.chmod(0o400)
        self.destination.chmod(0o500)
        changed = dict(manifest, digest='0' * 64)
        with self.assertRaises(ValueError): self.api.verify(self.destination, changed)
        self.api.verify(self.destination, manifest)

    def test_failed_atomic_write_cannot_leave_complete_snapshot(self):
        self.write('source.txt')
        with patch.object(self.api.changes, '_atomic', side_effect=OSError('injected')), self.assertRaises(ValueError):
            self.api.capture(self.repo, self.destination, self.approved(['source.txt']))
        self.assertFalse(self.destination.exists())

    def test_nested_repository_introduced_during_final_read_fails(self):
        path = self.write('nested/source.txt')
        approved = self.approved(['nested/source.txt'])
        original = onboarding._read_file
        reads = 0
        def add_repository(location, *args, **kwargs):
            nonlocal reads
            raw = original(location, *args, **kwargs)
            if location == path:
                reads += 1
                if reads == 2: (path.parent / '.git').mkdir()
            return raw
        with patch.object(onboarding, '_read_file', add_repository), self.assertRaises(ValueError):
            self.api.capture(self.repo, self.destination, approved)
        self.assertFalse((self.destination / self.api.MARKER).exists())
        if self.destination.exists():
            self.assertEqual(self.destination.stat().st_mode & 0o777, 0o700)

    def test_nested_bare_repository_is_excluded(self):
        self.write('safe.txt')
        self.write('bare/HEAD', b'ref: refs/heads/main\n')
        (self.repo / 'bare/objects').mkdir()
        (self.repo / 'bare/refs').mkdir()
        self.write('bare/config', b'[core]\n bare = true\n')
        manifest = self.api.capture(self.repo, self.destination, self.approved(['safe.txt', 'bare/config']))
        self.assertEqual(set(manifest['files']), {'safe.txt'})
        self.assertEqual(manifest['exclusions'], [{'path': 'bare/config', 'reason': 'nested_repository'}])

    def test_verify_rejects_marker_replacement_during_read(self):
        self.write('source.txt')
        manifest = self.api.capture(self.repo, self.destination, self.approved(['source.txt']))
        marker = self.destination / self.api.MARKER
        original = onboarding._read_file
        def replace_marker(location, *args, **kwargs):
            raw = original(location, *args, **kwargs)
            if location == marker:
                marker.chmod(0o600)
                marker.write_text('corrupted manifest')
                marker.chmod(0o400)
            return raw
        with patch.object(onboarding, '_read_file', replace_marker), self.assertRaises(ValueError):
            self.api.verify(self.destination, manifest)

    def test_verify_rejects_incomplete_or_malformed_manifest(self):
        self.write('source.txt')
        manifest = self.api.capture(self.repo, self.destination, self.approved(['source.txt']))
        for key, value in (('files', {'../escape': {'sha256': '0' * 64, 'size': 0}}),
                           ('exclusions', ['malformed']), ('coverage', {})):
            changed = dict(manifest, **{key: value})
            changed['digest'] = hashlib.sha256(json.dumps({name: item for name, item in changed.items()
                if name != 'digest'}, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
            self.destination.chmod(0o700)
            marker = self.destination / self.api.MARKER
            marker.chmod(0o600)
            marker.write_text(json.dumps(changed, sort_keys=True, separators=(',', ':'), ensure_ascii=True))
            marker.chmod(0o400)
            self.destination.chmod(0o500)
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.api.verify(self.destination, changed)
        self.destination.chmod(0o700)
        marker.unlink()
        self.destination.chmod(0o500)
        with self.assertRaises(ValueError): self.api.verify(self.destination, manifest)
