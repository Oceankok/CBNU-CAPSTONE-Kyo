"""In-process evidence renewal. Never changes frame mappings or tracking state."""
import math
import time
from urllib.parse import urlsplit
import httpx
from clock_contract import ClockPolicy, VERSION, number, sample_interval, within_envelope, covers
from inference_worker.continuous_source_mux import SourceMuxError


class ClockLeaseError(SourceMuxError): pass


IDENTITY = {'source_node_id', 'camera_id', 'stream_id', 'capture_context_id',
            'capture_session_id', 'run_id', 'epoch'}


def validate_config(value, clocks):
    if not isinstance(value, dict) or set(value) != {'version', 'origin', 'server_epoch', 'policy', 'bindings', 'poll_seconds', 'max_host_rtt_seconds'}:
        raise ValueError('clock_proof_configuration_invalid')
    if type(value['version']) is not int or value['version'] != VERSION: raise ValueError('clock_proof_version')
    policy = ClockPolicy(**value['policy'])
    if not 0 < number(value['poll_seconds']) < policy.ttl_seconds/2 or not 0 < number(value['max_host_rtt_seconds']) < policy.ttl_seconds/2:
        raise ValueError('clock_proof_poll_policy')
    url = urlsplit(value['origin'])
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1', '::1')):
        raise ValueError('clock_proof_secure_origin_required')
    if url.username or url.password or url.query or url.fragment or url.path not in ('', '/') or not value['server_epoch']:
        raise ValueError('clock_proof_origin_invalid')
    if set(value['bindings']) != set(clocks): raise ValueError('clock_proof_camera_bindings')
    for camera, binding in value['bindings'].items():
        if set(binding) != IDENTITY or binding['camera_id'] != camera or any(not isinstance(v, str) or not v or len(v) > 160 for v in binding.values()):
            raise ValueError('clock_proof_identity_invalid')
        if not 0 <= number(clocks[camera]['error_bound']) <= policy.max_error_seconds:
            raise ValueError('clock_proof_envelope_exceeds_policy')
    return policy


class ClockLeaseSet:
    def __init__(self, client, configuration, clocks, *, primary_camera=None, wall=time.time, mono=time.monotonic):
        self.policy = validate_config(configuration, clocks)
        if client.server_url.rstrip('/') != configuration['origin'].rstrip('/'):
            raise ClockLeaseError('clock_proof_origin_changed')
        self.client, self.config = client, configuration
        self.clocks = {camera: dict(clock) for camera, clock in clocks.items()}
        self.primary=primary_camera or next(iter(clocks));self.failures={}
        if self.primary not in clocks:raise ValueError('clock_primary_invalid')
        self.wall, self.mono = wall, mono
        self.wall_start, self.mono_start = number(wall()), number(mono())
        self.last_wall=self.wall_start
        self.last_mono = self.mono_start
        self.deadlines = {}; self.latest = {}; self.cache = {}
        self.next_poll = self.mono_start; self.server_anchor = None
        self.server_boot_id=None

    def _time_guard(self):
        now = number(self.mono()); elapsed = now-self.last_mono
        allowance = self.policy.step_tolerance_seconds + max(0, elapsed)*self.policy.drift_ppm/1e6
        wall=number(self.wall())
        if now < self.last_mono or abs(wall-self.last_wall-elapsed) > allowance:
            raise ClockLeaseError('clock_processing_host_step')
        self.last_mono,self.last_wall=now,wall
        return now

    def _proof(self, camera, proof):
        try:
            if type(proof['version']) is not int or proof['version'] != VERSION or proof['policy'] != self.policy.public():
                raise ValueError()
            if proof['server_epoch'] != self.config['server_epoch'] or any(proof[k] != v for k, v in self.config['bindings'][camera].items()):
                raise ClockLeaseError('clock_proof_identity_changed')
            low, high = sample_interval(proof['server_sample'], proof['source_sent_at'], proof['source_received_at'],
                                        proof['monotonic_sent'], proof['monotonic_received'], self.policy)
            if (low != proof['offset_low'] or high != proof['offset_high']
                    or proof['valid_until'] != proof['server_sample']+self.policy.ttl_seconds
                    or type(proof['sample_sequence']) is not int or proof['sample_sequence'] <= 0):
                raise ValueError()
            clock = self.clocks[camera]
            if not within_envelope(proof, clock['offset'], clock['error_bound'], self.policy):
                raise ClockLeaseError('clock_proof_outside_envelope')
        except (KeyError, TypeError, ValueError): raise ClockLeaseError('clock_proof_invalid') from None
        return proof

    def _read(self, camera, proof_id=None):
        started = self._time_guard()
        response = self.client.client.get('/api/processing/video/streams/'+self.config['bindings'][camera]['stream_id']+'/clock-proofs',
            params={} if proof_id is None else {'proof_id': proof_id})
        if response.status_code==503:raise ClockLeaseError('clock_proof_service_rejected')
        if response.status_code==409:
            try:restarted=response.json().get('detail')=='clock_server_restarted'
            except (ValueError,AttributeError):restarted=False
            if restarted:raise ClockLeaseError('clock_server_epoch_changed')
        response.raise_for_status()
        ended = self._time_guard(); rtt = ended-started
        if rtt > self.config['max_host_rtt_seconds']: raise ClockLeaseError('clock_proof_host_rtt')
        result = response.json()
        try:
            server = number(result['server_time'])
            if type(result['version']) is not int or result['version'] != VERSION or result['server_epoch'] != self.config['server_epoch']:
                raise ClockLeaseError('clock_server_epoch_changed')
            proof = self._proof(camera, result['proof'])
            boot=result['server_boot_id']
            if (not isinstance(boot,str) or not boot or proof['server_boot_id']!=boot
                    or self.server_boot_id not in (None,boot)):
                raise ClockLeaseError('clock_server_epoch_changed')
            self.server_boot_id=boot
            if proof_id is not None and proof['proof_id'] != proof_id: raise ValueError()
        except (KeyError, ValueError, TypeError): raise ClockLeaseError('clock_proof_invalid') from None
        self.server_anchor = (server+rtt, ended)  # Conservative upper bound for lag checks.
        self.cache[(camera, proof['proof_id'])] = proof
        # Cache is bounded independently of stream duration; old chunk proofs can be reread.
        while len(self.cache) > 128: self.cache.pop(next(iter(self.cache)))
        return proof, ended+proof['valid_until']-server-rtt-self.policy.drift()

    def refresh(self, *, force=False):
        now = self._time_guard()
        if force or now >= self.next_poll:
            for camera in self.clocks:
                if camera in self.failures:continue
                try:
                    proof, deadline = self._read(camera)
                except httpx.HTTPStatusError as error:
                    if error.response.status_code in (401, 403):raise
                    if camera not in self.deadlines:
                        if camera==self.primary:raise
                        self.failures[camera]='clock_reference_proof_unavailable';continue
                    if error.response.status_code not in (404, 429, 500, 502, 504):
                        if error.response.status_code==503:
                            raise ClockLeaseError('clock_proof_service_rejected') from None
                        if camera==self.primary:raise ClockLeaseError('clock_proof_service_rejected') from None
                        self.failures[camera]='clock_reference_proof_rejected'
                    continue
                except httpx.TransportError:
                    if camera not in self.deadlines:
                        if camera==self.primary:raise
                        self.failures[camera]='clock_reference_proof_unavailable'
                    continue
                except ClockLeaseError as error:
                    if camera==self.primary or str(error) in ('clock_processing_host_step','clock_server_epoch_changed','clock_proof_service_rejected'):raise
                    self.failures[camera]='clock_reference_calibration_invalid';continue
                old = self.latest.get(camera)
                if old and (proof['sample_sequence'] < old['sample_sequence'] or
                            (proof['sample_sequence'] == old['sample_sequence'] and proof != old)):
                    if camera==self.primary:raise ClockLeaseError('clock_proof_replayed')
                    self.failures[camera]='clock_reference_proof_replayed';continue
                self.latest[camera] = proof; self.deadlines[camera] = deadline
            self.next_poll = self._time_guard()+self.config['poll_seconds']
        self.check()

    def check(self):
        now = self._time_guard()
        for camera in self.clocks:
            if camera in self.failures:continue
            if camera not in self.deadlines or now>=self.deadlines[camera]:
                if camera==self.primary:raise ClockLeaseError('clock_calibration_expired')
                self.failures[camera]='clock_reference_calibration_expired'

    def server_now(self):
        self.check()
        return self.server_anchor[0]+self.mono()-self.server_anchor[1]

    def validate_chunk(self, camera, chunk):
        self.check()
        try:self._validate_chunk(camera,chunk)
        except ClockLeaseError as error:
            if camera==self.primary or str(error) in ('clock_processing_host_step','clock_server_epoch_changed','clock_proof_service_rejected'):
                raise
            self.failures[camera]='clock_reference_calibration_invalid'
            raise ClockLeaseError(self.failures[camera]) from None

    def _validate_chunk(self,camera,chunk):
        if camera in self.failures:raise ClockLeaseError(self.failures[camera])
        ident = chunk.get('clock_proof_id')
        if not isinstance(ident, str): raise ClockLeaseError('clock_chunk_proof_required')
        proof = self.cache.get((camera, ident))
        if proof is None:
            try:proof, _ = self._read(camera, ident)
            except ClockLeaseError as error:
                if camera==self.primary or str(error) in ('clock_processing_host_step','clock_server_epoch_changed','clock_proof_service_rejected'):raise
                self.failures[camera]='clock_reference_calibration_invalid'
                raise ClockLeaseError(self.failures[camera]) from None
        from inference_worker.time_utils import parse_capture_time
        start = parse_capture_time(chunk['capture_start_at']).timestamp()
        if not covers(proof, start, start+chunk['duration_ms']/1000, self.policy):
            raise ClockLeaseError('clock_chunk_outside_proof')
        for key in ('capture_context_id', 'capture_session_id'):
            if chunk.get(key) != proof[key]: raise ClockLeaseError('clock_chunk_identity_changed')
        expected=dict(stream_id=proof['stream_id'],clock_proof_version=VERSION,clock_epoch=proof['epoch'],clock_run_id=proof['run_id'])
        if any(chunk.get(key)!=value for key,value in expected.items()):
            raise ClockLeaseError('clock_chunk_identity_changed')
        self.check()
