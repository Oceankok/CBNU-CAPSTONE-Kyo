"""Snapshots use synthetic bytes and isolated DB/storage, never development data."""
import io
import json
import os
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from backend.backup import media
from backend.db.event_repository import get_connection
from backend.tests.support import isolated_api
from deploy import snapshot


class MediaBackupTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(TemporaryDirectory(prefix='kyo-backup-test-'))
        self.root = Path(self.directory)
        self.processed = self.root/'storage/candidate_events/processed'
        self.processed.mkdir(parents=True)
        self.enterContext(patch('backend.media.paths.PROJECT_ROOT',self.root))
        self.enterContext(patch('backend.media.paths.STORAGE_ROOT',self.root/'storage'))
        self.enterContext(patch.object(media,'PROCESSED_ROOT',self.processed))
        self.enterContext(isolated_api())
        self.output = self.root/'backup'
        self.key = 'storage/candidate_events/processed/retained.jpg'
        self.file = self.root/self.key
        self.file.write_bytes(b'synthetic-redacted-image')
        with get_connection() as conn:
            camera = conn.execute("SELECT camera_id FROM candidate_event WHERE event_id='EVT_0001'").fetchone()[0]
            conn.execute("INSERT INTO event_media(media_id,event_id,camera_id,kind,role,storage_path,status,redaction_status,source_path,created_at) VALUES('backup_media','EVT_0001',?,'image','reference',?,'ready','complete','private-original.jpg',CURRENT_TIMESTAMP)",(camera,self.key))
            conn.execute("UPDATE event_retention SET decision='retain',consent_confirmed=1,consent_reference='test-consent',retain_until=? WHERE event_id='EVT_0001'",(time.time()+3600,))

    def rewrite_manifest(self,body):
        (self.output/'media.json').write_text(json.dumps(body),encoding='utf-8')

    def test_only_eligible_processed_media_exported(self):
        (self.root/'private-secret').write_bytes(b'do-not-export')
        result = media.export_media(self.output)
        self.assertEqual(result['archived_media'],1)
        manifest = media.verified_manifest(self.output)
        with tarfile.open(self.output/'media.tar') as archive:
            self.assertEqual(archive.getnames(),['files/backup_media'])
            self.assertEqual(archive.extractfile('files/backup_media').read(),self.file.read_bytes())
        self.assertLessEqual(manifest['expires_at'],time.time()+3600)
        with self.assertRaises(ValueError):
            media.export_media(self.output)
        for update in ("consent_confirmed=0", "retain_until=1", "decision='pending'"):
            with self.subTest(update=update), get_connection() as conn:
                conn.execute("UPDATE event_retention SET decision='retain',consent_confirmed=1,retain_until=? WHERE event_id='EVT_0001'",(time.time()+3600,))
                conn.execute("UPDATE event_retention SET "+update+" WHERE event_id='EVT_0001'")
            destination = self.root/('excluded-'+str(len(list(self.root.glob('excluded-*')))))
            self.assertEqual(media.export_media(destination)['archived_media'],0)

    def test_corruption_and_unsafe_archive_rejected_before_write(self):
        media.export_media(self.output)
        manifest = json.loads((self.output/'media.json').read_text())
        with (self.output/'media.tar').open('ab') as archive:
            archive.write(b'corrupt')
        with self.assertRaisesRegex(ValueError,'checksum'):
            media.verified_manifest(self.output)
        for name,kind in (('../escape',tarfile.REGTYPE),('files/backup_media',tarfile.SYMTYPE)):
            with self.subTest(name=name), tarfile.open(self.output/'media.tar','w') as archive:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.size = len(self.file.read_bytes()) if kind == tarfile.REGTYPE else 0
                archive.addfile(member,io.BytesIO(self.file.read_bytes()))
            manifest['archive_sha256'] = media.digest(self.output/'media.tar')
            self.rewrite_manifest(manifest)
            with self.assertRaisesRegex(ValueError,'Unexpected archive'):
                media.verified_manifest(self.output)
        self.assertFalse((self.root/'escape').exists())

    def test_expiry_and_destination_escape_rejected(self):
        media.export_media(self.output)
        original = json.loads((self.output/'media.json').read_text())
        for expiry in (1,float('nan'),float('inf'),time.time()+90000):
            body = dict(original,expires_at=expiry)
            self.rewrite_manifest(body)
            with self.assertRaises(ValueError):
                media.verified_manifest(self.output)
        original['files'][0]['storage_path'] = 'storage/../../escape.jpg'
        self.rewrite_manifest(original)
        with self.assertRaises(ValueError):
            media.verified_manifest(self.output)

    def test_restore_preserves_retained_bytes_and_disables_replay(self):
        with get_connection() as conn:
            conn.execute("INSERT INTO field_node(node_id,name,token_hash) VALUES('backup_node','test','test-hash')")
            conn.execute("INSERT INTO field_command(command_id,node_id,kind,payload,status,created_at,expires_at) VALUES('backup_command','backup_node','broadcast','{}','pending',0,9999999999)")
            conn.execute("INSERT INTO event_clip_request(request_id,event_id,camera_id,status,created_at,updated_at) SELECT 'backup_clip',event_id,camera_id,'pending',0,0 FROM candidate_event WHERE event_id='EVT_0001'")
            conn.execute("INSERT INTO event_media(media_id,event_id,camera_id,kind,role,storage_path,status,created_at) SELECT 'excluded',event_id,camera_id,'image','reference','','failed',CURRENT_TIMESTAMP FROM candidate_event WHERE event_id='EVT_0001'")
            version = conn.execute("SELECT auth_version FROM app_user WHERE user_id='admin02'").fetchone()[0]
        media.export_media(self.output)
        self.file.unlink()
        result = media.restore_media(self.output,test_fixture=True)
        self.assertEqual(result['restored_media'],1)
        self.assertEqual(self.file.read_bytes(),b'synthetic-redacted-image')
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT auth_version FROM app_user WHERE user_id='admin02'").fetchone()[0],version+1)
            self.assertEqual(conn.execute("SELECT status FROM field_command WHERE command_id='backup_command'").fetchone()[0],'skipped')
            self.assertEqual(conn.execute("SELECT is_active FROM field_node WHERE node_id='backup_node'").fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT status FROM event_clip_request WHERE request_id='backup_clip'").fetchone()[0],'unavailable')
            self.assertEqual(conn.execute("SELECT status FROM event_media WHERE media_id='excluded'").fetchone()[0],'missing')
            self.assertEqual(conn.execute("SELECT source_path FROM event_media WHERE media_id='backup_media'").fetchone()[0],'')

    def test_restore_guard_and_retention_mismatch_leave_no_files(self):
        media.export_media(self.output)
        self.file.unlink()
        with patch.dict(os.environ,{'PPE_DATABASE_URL':'postgresql://localhost/kyo'}), self.assertRaisesRegex(ValueError,'isolated'):
            media.restore_media(self.output)
        with get_connection() as conn:
            conn.execute("UPDATE event_retention SET decision='delete' WHERE event_id='EVT_0001'")
        with self.assertRaisesRegex(ValueError,'does not match'):
            media.restore_media(self.output,test_fixture=True)
        self.assertFalse(self.file.exists())


class SnapshotHostTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(TemporaryDirectory(prefix='kyo-host-backup-')))
        self.release = {'commit':'a'*40,'project':'kyo-test','api_image':'ghcr.io/test/api@sha256:'+'a'*64,
                        'web_image':'ghcr.io/test/web@sha256:'+'b'*64,'https':False,'lan':False}

    def bundle(self,folder=None):
        folder = folder or self.folder
        folder.mkdir(parents=True,exist_ok=True)
        for name in snapshot.FILES:
            (folder/name).write_bytes(b'synthetic')
        snapshot.write_json(folder/'bundle.json',{'format':1,'created_at':time.time()-1,'expires_at':time.time()+3600,
                            'release':self.release,'sha256':{name:snapshot.sha(folder/name) for name in snapshot.FILES}})
        return folder

    def test_bundle_hash_and_expiry(self):
        folder = self.bundle()
        self.assertTrue(snapshot.verify_bundle(folder))
        (folder/'postgres.dump').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'checksum'):
            snapshot.verify_bundle(folder)
        self.bundle()
        body = json.loads((folder/'bundle.json').read_text())
        body['expires_at'] = 1
        snapshot.write_json(folder/'bundle.json',body)
        with self.assertRaisesRegex(ValueError,'Expired'):
            snapshot.verify_bundle(folder)

    def test_failed_backup_restarts_original_services_and_releases_lock(self):
        snapshot.write_json(self.folder/'current-release.json',self.release)
        calls = []
        def docker(args,**kwargs):
            calls.append(args)
            if 'ps' in args: return b'api\npostgres\nmaintenance\nweb\n'
            if 'pg_dump' in args: raise RuntimeError('synthetic failure')
            return b''
        with patch.object(snapshot,'run',side_effect=docker), self.assertRaises(RuntimeError):
            snapshot.backup(self.folder,True)
        self.assertTrue(any('start' in args and 'api' in args and 'maintenance' in args for args in calls))
        self.assertFalse((self.folder/'release.lock').exists())
        self.assertEqual(list((self.folder/'backups').iterdir()),[])
        with patch.object(snapshot,'run') as run, self.assertRaises(ValueError):
            snapshot.backup(self.folder,False)
        run.assert_not_called()

    def test_restore_collision_never_deletes_existing_resources(self):
        folder = self.bundle()
        with patch.object(snapshot,'run',return_value=b'existing-container') as run, self.assertRaisesRegex(ValueError,'already exists'):
            snapshot.restore_check(folder)
        self.assertFalse(any('down' in call.args[0] for call in run.call_args_list))

    def test_prune_only_expired_owned_snapshots(self):
        folder = self.bundle(self.folder/'backups'/('snapshot-'+'a'*32))
        body = json.loads((folder/'bundle.json').read_text())
        body['expires_at'] = 1
        snapshot.write_json(folder/'bundle.json',body)
        self.assertEqual(snapshot.prune(self.folder,False)['expired_snapshots'],[folder.name])
        self.assertTrue(folder.exists())
        (folder/'unknown-file').write_text('keep')
        self.assertEqual(snapshot.prune(self.folder,True)['expired_snapshots'],[])
        (folder/'unknown-file').unlink()
        snapshot.prune(self.folder,True)
        self.assertFalse(folder.exists())
