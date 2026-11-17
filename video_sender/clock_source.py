"""Opt-in source measurements for a fixed actual-monotonic capture epoch."""
import json
import os
import math
from pathlib import Path
import time
from uuid import uuid4
import requests
from clock_contract import AnchoredClock, ClockPolicy, VERSION, covers, number, sample_interval


def configuration():
    if os.environ.get('VIDEO_CLOCK_PROOF_ENABLED', '').lower() != 'true': return None
    try:
        policy = ClockPolicy(**json.loads(os.environ['VIDEO_CLOCK_POLICY_JSON']))
        epoch, node = os.environ['VIDEO_CLOCK_SERVER_EPOCH'], os.environ['VIDEO_CLOCK_SOURCE_NODE_ID']
        refresh = float(os.environ['VIDEO_CLOCK_REFRESH_SECONDS'])
        if not epoch or not node or not 0 < refresh < policy.ttl_seconds/2: raise ValueError()
    except (KeyError, TypeError, ValueError): raise ValueError('source_clock_configuration_required') from None
    return policy, epoch, node, refresh


class FreshSampleUnavailable(ValueError):
    pass


class ClockPublisher:
    def __init__(self, runner, configuration, *, wall=time.time, mono=time.monotonic, sleep=time.sleep):
        self.runner, self.client = runner, runner.client
        self.policy, self.server_epoch, self.node, self.interval = configuration
        self.mono, self.sleep = mono, sleep
        state = runner.state
        if 'clock' not in state:
            # Two adjacent actual observations, never frame-count synthesized time.
            anchor_mono, anchor_wall = mono(), wall()
            state['clock'] = dict(version=VERSION, anchor={'utc': anchor_wall, 'monotonic': anchor_mono},
                                  epoch=str(uuid4()), sample_sequence=0, proof=None)
            runner.save()
        self.state = state['clock']
        if self.state['version'] != VERSION: raise ValueError('source_clock_version')
        self.clock = AnchoredClock(self.state['anchor'], self.policy, wall=wall, mono=mono)
        self.path = runner.root/'.clock-current.json'; self.next_refresh = mono()

    def refresh(self, *, force=False):
        if not force and self.mono() < self.next_refresh:
            self.check(); return
        old_proof=self.state.get('proof')
        if not force:self.check()  # An expired lease cannot be revived by a new sample.
        try:
            self._refresh_exchange()
        except (FreshSampleUnavailable, requests.exceptions.Timeout,
                requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as error:
            if force:raise
            if isinstance(error,requests.exceptions.HTTPError) and getattr(error.response,'status_code',None) not in (429,500,502,503,504):
                raise
            # The exact prior on-disk proof is the only fallback. A recovered
            # publication may advance the sequence, but cannot renew the lease.
            try:published=json.loads(self.path.read_text(encoding='utf-8'))
            except (OSError,ValueError):raise error
            if (old_proof is None or published != dict(anchor=self.state['anchor'],
                    policy=self.policy.public(),proof=old_proof)):
                raise error
            stamp=self.clock.now()
            if stamp+old_proof['offset_high']+self.policy.drift() >= old_proof['valid_until']:
                raise ValueError('source_clock_proof_expired') from error
            if self.state.get('proof') != old_proof:
                self.state['proof']=old_proof;self.runner.save()
            self.next_refresh=self.mono()+1
            return

    def _refresh_exchange(self):
        recovering=self.state.get('pending') is not None
        # A recovered receipt settles sequence ownership, never masquerades as
        # a new live measurement. Follow it with an actual fresh exchange.
        for attempt in range(2):
            if self.state.get('pending') is None:
                self.state['pending']=dict(request=dict(version=VERSION,run_id=self.runner.state['run_id'],
                    epoch=self.state['epoch'],sample_sequence=self.state['sample_sequence']+1,
                    source_sent_at=self.clock.now(),monotonic_sent=self.mono()),challenge=None,publication=None)
                self.runner.save()  # Before the request can consume a server sequence.
            proof=self._exchange()
            if proof is None or (recovering and attempt==0):continue
            from video_sender.context_sender import atomic_json
            atomic_json(self.path,dict(anchor=self.state['anchor'],policy=self.policy.public(),proof=proof))
            self.next_refresh=self.mono()+self.interval
            self.check();return
        raise FreshSampleUnavailable('source_clock_fresh_sample_required')

    def _abandon(self):
        # Sequence was positively acknowledged by the challenge response.
        # Its unusable measurement is never published or substituted for media.
        self.state.pop('pending',None);self.runner.save()
        return None

    def _exchange(self):
        pending=self.state['pending'];request=pending['request'];sequence=request['sample_sequence']
        stream = self.runner.state['stream_id']
        if pending['challenge'] is None:
            challenge=self.client.post('/api/video/streams/'+stream+'/clock-challenges',request)
            received_mono=self.mono();received_stamp=self.clock.now()
            pending['challenge']=challenge
            pending['publication']=dict(version=VERSION,source_received_at=received_stamp,monotonic_received=received_mono)
            self.state['sample_sequence']=sequence
            self.runner.save()  # Exact response and actual receive samples before publication IO.
        challenge=pending['challenge'];publication=pending['publication']
        if challenge['version'] != VERSION or challenge['server_epoch'] != self.server_epoch or challenge['policy'] != self.policy.public():
            raise ValueError('source_clock_server_contract_changed')
        if (not isinstance(challenge.get('server_boot_id'),str) or not challenge['server_boot_id']
                or self.state.get('server_boot_id',challenge['server_boot_id'])!=challenge['server_boot_id']):
            raise ValueError('source_clock_server_restarted')
        try:
            low,high=sample_interval(challenge['server_time'],request['source_sent_at'],publication['source_received_at'],
                request['monotonic_sent'],publication['monotonic_received'],self.policy)
        except ValueError as error:
            if (publication['monotonic_received']-request['monotonic_sent']>self.policy.max_rtt_seconds
                    or str(error)=='clock_sample_uncertain'):return self._abandon()
            raise
        try:
            proof=self.client.post('/api/video/streams/'+stream+'/clock-proofs/'+challenge['proof_id'],publication)
        except Exception as error:
            response=getattr(error,'response',None)
            if getattr(response,'status_code',None)==409:
                try:expired=response.json().get('detail') in ('clock_challenge_expired','clock_challenge_superseded')
                except (ValueError,AttributeError):expired=False
                if expired:return self._abandon()
            raise  # Ambiguous acceptance retains the exact durable publication.
        bindings = dict(source_node_id=self.node, camera_id=self.runner.state['camera_id'], stream_id=stream,
            capture_context_id=self.runner.state['context_id'], capture_session_id=self.runner.state['session_id'],
            run_id=self.runner.state['run_id'], epoch=self.state['epoch'], server_epoch=self.server_epoch,
            server_boot_id=challenge['server_boot_id'])
        if (any(proof.get(k) != v for k,v in bindings.items()) or proof.get('proof_id') != challenge['proof_id']
                or proof.get('version') != VERSION or proof.get('sample_sequence') != sequence
                or proof.get('policy') != self.policy.public() or proof.get('offset_low') != low or proof.get('offset_high') != high
                or proof.get('source_received_at') != publication['source_received_at']
                or proof.get('valid_until') != challenge['server_time']+self.policy.ttl_seconds):
            raise ValueError('source_clock_proof_binding_invalid')
        self.state.update(sample_sequence=sequence,proof=proof,server_boot_id=challenge['server_boot_id'])
        self.state.pop('pending',None);self.runner.save()
        return proof

    def check(self):
        stamp = self.clock.now(); proof = self.state['proof']
        if proof is None or stamp+proof['offset_high']+self.policy.drift() >= proof['valid_until']:
            raise ValueError('source_clock_proof_expired')
        return stamp

    def wait(self, *, valid_from=None, raw_end=None):
        """Keep both raw backend gates strict; no timestamp rewriting."""
        until = self.mono()+self.policy.ttl_seconds
        while True:
            self.refresh(); now = self.check(); proof = self.state['proof']
            low = proof['offset_low']-self.policy.drift()
            guard = self.policy.sample_error_seconds+.002  # Millisecond header quantization.
            admitted = (now >= max(valid_from+max(0,-low)+guard,proof['source_received_at']+guard) if valid_from is not None
                        else now+low >= raw_end+guard)
            if admitted: return
            if self.mono() >= until: raise TimeoutError('source_clock_gate_wait_expired')
            self.sleep(.02)


class HistoricalClockDrain:
    """Current server observations admit original media; no live lease renewal."""
    def __init__(self,runner,*,mono=time.monotonic,sleep=time.sleep):
        self.runner,self.client,self.mono,self.sleep=runner,runner.client,mono,sleep
        state=runner.state['clock']
        if state['version']!=VERSION or not state.get('proof'):raise ValueError('source_clock_historical_proof_required')
        self.original=state['proof'];self.policy=ClockPolicy(**self.original['policy'])

    def wait(self,*,raw_end):
        until=self.mono()+self.policy.ttl_seconds;previous=self.mono()
        identity={key:self.original[key] for key in ('source_node_id','camera_id','stream_id','capture_context_id',
                                                     'capture_session_id','run_id','epoch')}
        while True:
            started=self.mono()
            observation=self.client.get('/api/video/streams/'+self.runner.state['stream_id']+'/clock-observation')
            finished=self.mono()
            current_policy=ClockPolicy(**observation['policy'])
            if (finished<previous or finished-started>current_policy.max_rtt_seconds
                    or observation.get('version')!=VERSION or any(observation.get(k)!=v for k,v in identity.items())):
                raise ValueError('source_clock_historical_observation_invalid')
            previous=finished
            # The returned server sample is already a lower bound at receipt.
            # No old anchor, wall UTC, proof publication or boot equivalence is needed.
            if number(observation['server_time'])>=number(raw_end)+current_policy.sample_error_seconds+.002:return
            if finished>=until:raise TimeoutError('source_clock_gate_wait_expired')
            self.sleep(.1)


def capture_stamp(path, *, clock=None, wall=time.time, mono=time.monotonic, observed_monotonic=None):
    """Timestamp an actual chunk-first-frame observation, retaining actual gaps."""
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    policy = ClockPolicy(**data['policy'])
    if clock is None:clock = AnchoredClock(data['anchor'], policy, wall=wall, mono=mono)
    elif data['anchor']!={'utc':clock.utc,'monotonic':clock.monotonic} or policy!=clock.policy:
        raise ValueError('source_clock_anchor_changed')
    stamp = clock.now()
    if observed_monotonic is not None:
        observed = number(observed_monotonic)
        if observed < clock.monotonic or observed > clock.last:
            raise ValueError('source_clock_observation_invalid')
        stamp = clock.utc + observed - clock.monotonic
    wire_stamp=math.floor(stamp*1000)/1000
    proof=data['proof']
    if not covers(proof,wire_stamp,wire_stamp+3,policy):
        previous=getattr(clock,'capture_proof',None)
        if previous is None or not covers(previous,wire_stamp,wire_stamp+3,policy):
            raise ValueError('source_clock_chunk_proof_expired')
        proof=previous
    clock.capture_proof=proof
    if not covers(proof, wire_stamp, wire_stamp+3, policy):
        raise ValueError('source_clock_chunk_proof_expired')
    return stamp, proof['proof_id']
