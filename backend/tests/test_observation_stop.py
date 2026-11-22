import json,time,unittest
from datetime import datetime,timezone
from uuid import uuid4
from backend.tests import test_clock_proofs as fixtures
from backend.db.event_repository import get_connection

class ObservationStopTests(unittest.TestCase):
    setUp=fixtures.ClockProofApiTests.setUp
    challenge=fixtures.ClockProofApiTests.challenge
    publish=fixtures.ClockProofApiTests.publish
    proof=fixtures.ClockProofApiTests.proof
    upload=fixtures.ClockProofApiTests.upload

    def marker(self,proof,**changes):
        value=dict(stream_id=self.stream,run_id=self.run_id,sequence=0,frame_index=3,fps=15,proof_id=proof['proof_id'])
        value.update(changes);return value
    def post(self,body,headers=None,context=None):
        return self.client.post('/api/video/capture-sessions/'+(context or self.context['context_id'])+'/observation-stop',
                                headers=headers or self.nodes['source'],json=body)
    def test_auth_binding_strict_payload_and_projection(self):
        _,proof=self.proof();body=self.marker(proof)
        self.assertEqual(self.post(body,headers=self.nodes['other']).status_code,403)
        self.assertEqual(self.post(body,headers=self.admin).status_code,401)
        for patch in ({'sequence':True},{'frame_index':15},{'fps':0},{'extra':1}):
            with self.subTest(patch=patch):self.assertEqual(self.post(body|patch).status_code,422)
        self.assertEqual(self.post(body|{'run_id':str(uuid4())}).status_code,409)
        self.assertEqual(self.post(body|{'proof_id':str(uuid4())}).status_code,422)
        response=self.post(body);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json(),{'observation_stop':body})
        self.assertEqual(self.post(body).json(),response.json())
        self.assertEqual(self.post(body|{'frame_index':4}).status_code,409)
        streams=self.client.get('/api/processing/video/streams',headers=self.nodes['processor']).json()['items']
        self.assertEqual(next(x for x in streams if x['stream_id']==self.stream)['observation_stop'],body)
        available=self.client.get(f'/api/processing/video/streams/{self.stream}/availability?after_sequence=-1',headers=self.nodes['processor'])
        self.assertEqual(available.status_code,200,available.text)
        self.assertEqual(available.json()['observation_stop'],body)
    def test_foreign_context_and_unknown_stream_rejected(self):
        _,proof=self.proof();body=self.marker(proof)
        self.client.put('/api/cameras/CAM_002/node',headers=self.admin,
            json={'source_node_id':'source','output_node_id':'source'}).raise_for_status()
        other=self.client.post('/api/video/capture-sessions',headers=self.nodes['source'],
            json={'camera_id':'CAM_002','session_id':str(uuid4())})
        self.assertEqual(other.status_code,201,other.text)
        self.assertEqual(self.post(body,context=other.json()['context_id']).status_code,403)
        self.assertEqual(self.post(body|{'stream_id':str(uuid4())}).status_code,404)
        self.assertEqual(self.post(body,context=str(uuid4())).status_code,404)

    def test_chunk_proof_matches_marker_and_replay_after_retirement(self):
        _,proof=self.proof();body=self.marker(proof);self.assertEqual(self.post(body).status_code,200)
        self.captured=datetime.fromtimestamp(proof['source_received_at']+.02,timezone.utc).isoformat()
        time.sleep(1.1)
        headers={**self.nodes['source'],'Content-Type':'video/mp4','X-Capture-Start':self.captured,'X-Duration-Ms':'1000','X-Clock-Proof-Id':str(uuid4())}
        wrong=self.client.post(f'/api/video/streams/{self.stream}/chunks?sequence=0',headers=headers,content=self.data)
        self.assertEqual(wrong.status_code,409,wrong.text)
        self.assertEqual(self.upload(proof).status_code,202)
        chunks=self.client.get(f'/api/processing/video/streams/{self.stream}/chunks?after_sequence=-1',headers=self.nodes['processor']).json()['items']
        self.assertEqual(chunks[0]['observation_stop'],body)
        stopped=self.client.post('/api/video/capture-sessions/'+self.context['context_id']+'/stop',headers=self.nodes['source'],
                                 json={'capture_stopped':True,'final_sequences':{self.stream:0}})
        self.assertEqual(stopped.status_code,200,stopped.text)
        self.assertEqual(self.post(body).json(),{'observation_stop':body})
        self.assertEqual(self.post(body|{'sequence':1}).status_code,409)
    def test_schema_37_to_38_preserves_every_existing_table(self):
        from backend.db.connection import is_postgres,schema_version
        from backend.db.migrations import apply_migrations,LATEST_VERSION
        _,proof=self.proof()
        with get_connection() as conn:
            postgres=is_postgres()
            conn.execute('DROP TABLE capture_observation_stop')
            if postgres:
                conn.execute('UPDATE app_schema_version SET version=37 WHERE singleton=1')
                tables=[r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE' AND table_name!='app_schema_version'")]
            else:
                conn.execute('PRAGMA user_version=37')
                tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            import re
            self.assertTrue(all(re.fullmatch('[a-z_]+',name) for name in tables))
            before={name:sorted(repr(tuple(row)) for row in conn.execute('SELECT * FROM '+name)) for name in tables}
        if postgres:
            from backend.db.postgres import initialize
            initialize(seed=False)
        else:
            with get_connection() as conn:apply_migrations(conn)
        with get_connection() as conn:
            self.assertEqual(schema_version(conn),LATEST_VERSION)
            for name in tables:self.assertEqual(sorted(repr(tuple(row)) for row in conn.execute('SELECT * FROM '+name)),before[name],name)
            self.assertEqual(conn.execute('SELECT count(*) FROM capture_observation_stop').fetchone()[0],0)

    def test_cannot_install_after_boundary_upload(self):
        _,proof=self.proof();self.captured=datetime.fromtimestamp(proof['source_received_at']+.02,timezone.utc).isoformat()
        time.sleep(1.1);self.assertEqual(self.upload(proof).status_code,202)
        self.assertEqual(self.post(self.marker(proof)).status_code,409)

if __name__=='__main__':unittest.main()
