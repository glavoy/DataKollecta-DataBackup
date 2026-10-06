import json
import tempfile
import unittest
from pathlib import Path
from snapshot_integrity import write_manifest, verify_manifest

class SnapshotIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'database.dump').write_bytes(b'PGDMP synthetic fixture')
        self.manifest = write_manifest(self.root, {'database':'synthetic; recovery not established'})
    def test_unmodified_snapshot_verifies(self):
        self.assertEqual(verify_manifest(self.root), self.manifest)
    def test_corruption_is_refused(self):
        (self.root/'database.dump').write_bytes(b'corrupted')
        with self.assertRaises(ValueError): verify_manifest(self.root)
    def test_missing_or_unlisted_file_is_refused(self):
        (self.root/'extra.csv').write_text('unexpected')
        with self.assertRaises(ValueError): verify_manifest(self.root)
    def test_path_traversal_is_refused(self):
        self.manifest['files'][0]['path']='../outside.dump'
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        with self.assertRaises(ValueError): verify_manifest(self.root)
    def test_duplicate_manifest_entry_is_refused(self):
        self.manifest['files'].append(dict(self.manifest['files'][0]))
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        with self.assertRaises(ValueError): verify_manifest(self.root)
    def test_symbolic_link_is_refused(self):
        (self.root/'link.dump').symlink_to(self.root/'database.dump')
        with self.assertRaises(ValueError): write_manifest(self.root,{})
if __name__=='__main__': unittest.main()

class SnapshotRestoreSafetyTests(unittest.TestCase):
    def test_remote_restore_is_refused_before_any_command_runs(self):
        import os
        from unittest.mock import patch
        from backup_snapshot import restore_snapshot
        with patch.dict(os.environ, {'DATAKOLLECTA_RESTORE_DATABASE_URL':'postgresql://operator:secret@production.example/data'}):
            with self.assertRaises(ValueError): restore_snapshot('unused','data')
    def test_builtin_database_cannot_be_a_restore_destination(self):
        import os
        from unittest.mock import patch
        from backup_snapshot import restore_snapshot
        with patch.dict(os.environ, {'DATAKOLLECTA_RESTORE_DATABASE_URL':'postgresql://postgres:postgres@127.0.0.1:54322/postgres'}):
            with self.assertRaises(ValueError): restore_snapshot('unused','postgres')
