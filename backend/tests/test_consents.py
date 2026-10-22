import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from pypdf import PdfWriter

from backend.consents import service
from backend.db.connection import schema_version, is_postgres
from backend.db.event_repository import get_connection
from backend.tests.support import isolated_api, login


def pdf_bytes(encrypted=False):
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    if encrypted:
        writer.encrypt('test-only-password')
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class ConsentTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(isolated_api())
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='kyo-consent-test-')))
        self.documents = self.root / 'consents'
        self.enterContext(patch.object(service, 'CONSENT_ROOT', self.documents))
        self.headers = login(self.client)

    def upload(self, site='SITE_A', data=None, **overrides):
        metadata = dict(site_code=site, scope_description='시연 이벤트의 모델 개선용 이용 동의',
                        form_version='2026-01', consent_confirmed=True)
        metadata.update(overrides)
        return self.client.post('/api/consent-records', headers=self.headers,
                                data={'metadata': json.dumps(metadata)},
                                files={'file': ('consent.pdf', pdf_bytes() if data is None else data, 'application/pdf')})

    def final_event(self, event_id='EVT_0001'):
        with get_connection() as conn:
            conn.execute("UPDATE candidate_event SET event_status='confirmed' WHERE event_id=?", (event_id,))
            conn.execute('DELETE FROM event_review WHERE event_id=?', (event_id,))
            conn.execute("INSERT INTO event_review(review_id,event_id,reviewer_id,review_result,second_review_needed) VALUES(?,?,?,'confirmed',0)",
                         (str(uuid4()), event_id, 'admin02'))

    def retain(self, record_id, event_id='EVT_0001', **overrides):
        body = dict(decision='retain', expected_version=0, consent_confirmed=True, consent_record_id=record_id,
                    retain_until='2099-01-01T00:00:00Z')
        body.update(overrides)
        return self.client.put(f'/api/events/{event_id}/retention', headers=self.headers, json=body)

    def test_roles_private_download_site_filter_and_authenticated_actor(self):
        first = self.upload()
        self.assertEqual(first.status_code, 201, first.text)
        record = first.json()
        self.assertEqual(record['confirmed_by'], 'admin02')
        self.assertNotIn('file_key', record)
        self.assertEqual(record['page_count'], 1)
        self.assertEqual(self.upload(site='SITE_B').status_code, 201)
        listed = self.client.get('/api/consent-records?site_code=SITE_A', headers=self.headers).json()['items']
        self.assertEqual([row['record_id'] for row in listed], [record['record_id']])
        paths = ['/api/consent-records', '/api/consent-records/'+record['record_id'],
                 '/api/consent-records/'+record['record_id']+'/file']
        for path in paths:
            self.assertEqual(self.client.get(path).status_code, 401)
            self.assertEqual(self.client.get(path, headers=login(self.client, 'worker01')).status_code, 403)
        download = self.client.get(paths[-1], headers=self.headers)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, pdf_bytes())
        self.assertEqual(download.headers['cache-control'], 'private, no-store')
        self.assertIn('attachment', download.headers['content-disposition'])
        login(self.client)  # /storage authenticates with its media cookie.
        self.assertEqual(self.client.get('/storage/consents/'+record['record_id']+'.pdf', headers=self.headers).status_code, 404)
        self.assertEqual(self.client.post('/api/consent-records/'+record['record_id']+'/revoke', headers=login(self.client, 'worker01'),
                                        json={'expected_version': 0, 'reason': '철회'}).status_code, 403)

    def test_invalid_encrypted_empty_oversized_and_unconfirmed_uploads_leave_no_files(self):
        for content in (b'', b'not a PDF', b'%PDF-1.7\ncorrupt', pdf_bytes(encrypted=True)):
            with self.subTest(content=content[:12]):
                self.assertEqual(self.upload(data=content).status_code, 400)
        self.assertEqual(self.upload(consent_confirmed=False).status_code, 400)
        self.assertEqual(self.upload(site='../escape').status_code, 422)
        with patch.object(service, 'MAX_BYTES', 10):
            self.assertEqual(self.upload().status_code, 413)
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM consent_record').fetchone()[0], 0)
        self.assertEqual(list(self.documents.iterdir()), [])

    def test_file_publication_failure_rolls_back_record_and_removes_stage(self):
        with patch.object(service.os, 'replace', side_effect=OSError('synthetic publication failure')):
            with self.assertRaises(OSError):
                self.upload()
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM consent_record').fetchone()[0], 0)
        self.assertEqual(list(self.documents.iterdir()), [])

    def test_retention_links_atomically_and_uses_generated_reference(self):
        record = self.upload().json()
        record_id = record['record_id']
        self.assertEqual(self.retain(record_id).status_code, 409)  # Final review is still required.
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_consent').fetchone()[0], 0)
        self.final_event()
        self.assertEqual(self.retain(record_id, retain_until=None).status_code, 400)
        self.assertEqual(self.retain(record_id, expected_version=88).status_code, 409)
        saved = self.retain(record_id, consent_reference='untrusted-reference', reviewer_id='someone-else')
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()['consent_reference'], record_id)
        self.assertEqual(saved.json()['consent_record_id'], record_id)
        with get_connection() as conn:
            link = conn.execute('SELECT * FROM event_consent').fetchone()
            self.assertEqual(link['linked_by'], 'admin02')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_retention_history WHERE event_id=?', ('EVT_0001',)).fetchone()[0], 1)
        legacy = self.client.put('/api/events/EVT_0001/retention', headers=self.headers,
                                 json={'decision': 'retain', 'expected_version': saved.json()['version'],
                                       'consent_confirmed': True, 'consent_reference': 'replace without document'})
        self.assertEqual(legacy.status_code, 409)

    def test_missing_or_changed_document_cannot_authorize_retention(self):
        record = self.upload().json()
        self.final_event()
        path = self.documents / (record['record_id']+'.pdf')
        path.write_bytes(b'tampered')
        self.assertEqual(self.retain(record['record_id']).status_code, 409)
        path.unlink()
        self.assertEqual(self.retain(record['record_id']).status_code, 404)
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_consent').fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT decision FROM event_retention WHERE event_id='EVT_0001'").fetchone()[0], 'pending')

    def test_revoke_deletes_all_linked_retained_media_but_keeps_audit_and_other_events(self):
        record = self.upload().json()
        record_id = record['record_id']
        files = {}
        for event_id in ('EVT_0001', 'EVT_0002'):
            self.final_event(event_id)
            self.assertEqual(self.retain(record_id, event_id).status_code, 200)
            file = self.root / (event_id+'.jpg')
            file.write_bytes(b'synthetic-redacted-media')
            files['storage/'+file.name] = file
            with get_connection() as conn:
                camera = conn.execute('SELECT camera_id FROM candidate_event WHERE event_id=?', (event_id,)).fetchone()[0]
                conn.execute("INSERT INTO event_media(media_id,event_id,camera_id,kind,role,storage_path,status,redaction_status,created_at) VALUES(?,?,?,'image','reference',?,'ready','complete',CURRENT_TIMESTAMP)",
                             (str(uuid4()), event_id, camera, 'storage/'+file.name))
        other = self.upload(site='SITE_B').json()
        url = '/api/consent-records/'+record_id+'/revoke'
        self.assertEqual(self.client.post(url, headers=self.headers, json={'expected_version': 2, 'reason': '철회 요청'}).status_code, 409)
        with patch('backend.media.service.owned_path', side_effect=lambda key: files[key]):
            revoked = self.client.post(url, headers=self.headers, json={'expected_version': 0, 'reason': '철회 요청'})
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(revoked.json()['revoked_by'], 'admin02')
        self.assertTrue(all(not file.exists() for file in files.values()))
        self.assertTrue((self.documents / (record_id+'.pdf')).exists())
        self.assertEqual(self.client.post(url, headers=self.headers, json={'expected_version': 0, 'reason': '재전송'}).status_code, 200)
        self.assertEqual(self.retain(record_id).status_code, 409)
        self.assertEqual(self.client.get('/api/consent-records/'+other['record_id'], headers=self.headers).json()['status'], 'active')
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM candidate_event WHERE event_id IN ('EVT_0001','EVT_0002')").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM event_retention WHERE decision='delete' AND event_id IN ('EVT_0001','EVT_0002')").fetchone()[0], 2)

    def test_v10_upgrade_preserves_existing_data_and_is_repeatable(self):
        from backend.db.init_db import init_database
        with get_connection() as conn:
            before = conn.execute('SELECT COUNT(*) FROM app_user').fetchone()[0]
            conn.execute('DROP TABLE event_consent')
            conn.execute('DROP TABLE consent_record')
            conn.execute('UPDATE app_schema_version SET version=10 WHERE singleton=1' if is_postgres() else 'PRAGMA user_version=10')
        init_database()
        init_database()
        with get_connection() as conn:
            self.assertEqual(schema_version(conn), 11)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM app_user').fetchone()[0], before)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM consent_record').fetchone()[0], 0)

    def test_backup_restores_private_consent_evidence_with_database_checks(self):
        from backend.backup import media as backup
        record = self.upload().json()
        processed = self.root / 'processed'
        processed.mkdir()
        output = self.root / 'backup'
        with patch.object(backup, 'PROCESSED_ROOT', processed):
            result = backup.export_media(output)
            self.assertEqual(result['archived_consents'], 1)
            body = backup.verified_manifest(output)
            self.assertEqual(body['consents'][0]['record_id'], record['record_id'])
            restored = self.root / 'restored-consents'
            with patch.object(service, 'CONSENT_ROOT', restored):
                backup.restore_media(output, test_fixture=True)
                self.assertEqual((restored / (record['record_id']+'.pdf')).read_bytes(), pdf_bytes())
            body['consents'][0]['file_key'] = '../escape.pdf'
            (output / 'media.json').write_text(json.dumps(body), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Unsafe consent'):
                backup.verified_manifest(output)

    def test_cleanup_removes_only_abandoned_documents_and_upload_stages(self):
        record = self.upload().json()
        registered = self.documents / (record['record_id']+'.pdf')
        orphan = self.documents / ('CONSENT_'+uuid4().hex+'.pdf')
        stage = self.documents / 'consent_abandoned.tmp'
        unrelated = self.documents / 'unrelated-file'
        for path in (orphan, stage, unrelated):
            path.write_bytes(b'fixture')
        for path in (registered, orphan, stage, unrelated):
            os.utime(path, (1, 1))
        self.assertEqual(service.cleanup(), 2)
        self.assertTrue(registered.exists())
        self.assertTrue(unrelated.exists())

    def test_revocation_delete_failure_stays_blocked_until_file_retry_succeeds(self):
        from backend.media.service import delete_event_media
        record = self.upload().json()
        self.final_event()
        self.assertEqual(self.retain(record['record_id']).status_code, 200)
        file = self.root / 'retained.jpg'
        file.write_bytes(b'synthetic-redacted-media')
        with get_connection() as conn:
            camera = conn.execute("SELECT camera_id FROM candidate_event WHERE event_id='EVT_0001'").fetchone()[0]
            conn.execute("INSERT INTO event_media(media_id,event_id,camera_id,kind,role,storage_path,status,redaction_status,created_at) VALUES('delete-retry','EVT_0001',?,'image','reference','storage/retained.jpg','ready','complete',CURRENT_TIMESTAMP)", (camera,))
        with patch('backend.media.service.owned_path', side_effect=PermissionError('synthetic locked file')):
            response = self.client.post('/api/consent-records/'+record['record_id']+'/revoke', headers=self.headers,
                                        json={'expected_version': 0, 'reason': '동의 철회'})
        self.assertEqual(response.status_code, 200, response.text)
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM event_media WHERE media_id='delete-retry'").fetchone()[0], 'delete_pending')
            self.assertIsNone(conn.execute("SELECT deleted_at FROM event_retention WHERE event_id='EVT_0001'").fetchone()[0])
        self.assertTrue(file.exists())
        with patch('backend.media.service.owned_path', return_value=file):
            result = delete_event_media('EVT_0001')
        self.assertEqual(result['pending_media'], 0)
        self.assertFalse(file.exists())
