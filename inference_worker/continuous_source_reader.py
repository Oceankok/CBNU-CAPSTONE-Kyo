"""Opt-in bounded source reader using existing VideoClient contracts.

A stream discontinuity stops the session; the coordinator must journal interruption
before constructing a new reader. No track identities survive reconnects.
"""
import math
import re
import time
import httpx
from inference_worker.time_utils import frame_timestamp,is_continuous_chunk
from inference_worker.video_decoder import decode_mp4
from inference_worker.continuous_source_mux import SourceMuxError


class SourceUnavailableError(SourceMuxError):
    """Known source identity is unavailable; never implies a physical stop."""
    def __init__(self,reason,*,camera,stream,expected_sequence,availability_reason=None):
        self.reason=reason;self.camera=camera;self.stream=stream
        self.expected_sequence=expected_sequence
        self.availability_reason=availability_reason
        # Stable, bounded reason remains safe for durable journal failure fields.
        super().__init__(reason)


def bounded_chunk(client,chunk_id,maximum):
    if not isinstance(chunk_id,str) or not re.fullmatch('[A-Za-z0-9_-]{1,160}',chunk_id):
        raise SourceMuxError('invalid_chunk_identity')
    with client.client.stream('GET',f'/api/processing/video/chunks/{chunk_id}',
            headers={'Accept-Encoding':'identity'}) as response:
        if response.status_code in (401,403):response.raise_for_status()
        if response.status_code!=200:raise SourceMuxError('chunk_http_'+str(response.status_code))
        if response.headers.get('content-encoding','identity').lower()!='identity':
            raise SourceMuxError('compressed_chunk_forbidden')
        declared=response.headers.get('content-length')
        if declared is not None and (not declared.isdigit() or int(declared)>maximum):
            raise SourceMuxError('chunk_size_limit')
        data=bytearray()
        for chunk in response.iter_bytes(chunk_size=min(65536,maximum+1)):
            if len(data)+len(chunk)>maximum:raise SourceMuxError('chunk_size_limit')
            data.extend(chunk)
        return bytes(data)


class ContinuousSourceReader:
    def __init__(self,client,mux,*,offsets,decoder=decode_mp4,max_chunk_bytes,max_chunk_gap_seconds,downloader=None,require_capture_context=False,degrade_references=False,tolerate_source_gaps=False,clock_verifier=None,allow_unverified_clock_proofs=False,reserve_chunk_frames=None,reserve_chunk_bytes=None):
        if set(offsets)!=set(mux.cameras) or any(not math.isfinite(v) for v in offsets.values()):raise ValueError('camera clock mappings')
        if not math.isfinite(max_chunk_gap_seconds) or max_chunk_gap_seconds<0:raise ValueError('chunk gap policy')
        if type(degrade_references) is not bool:raise ValueError('reference failure mode')
        self.degrade_references=degrade_references;self.reference_failures={};self.reference_wait_since={}
        if (reserve_chunk_frames is None)!=(reserve_chunk_bytes is None):raise ValueError('incomplete chunk reserve')
        if reserve_chunk_frames is not None and (type(reserve_chunk_frames) is not int or reserve_chunk_frames<=0 or type(reserve_chunk_bytes) is not int or reserve_chunk_bytes<=0):raise ValueError('invalid chunk reserve')
        self.reserve_chunk_frames,self.reserve_chunk_bytes=reserve_chunk_frames,reserve_chunk_bytes
        self.gap=max_chunk_gap_seconds
        self.tolerate_source_gaps=tolerate_source_gaps
        self.arrival_by_camera={}
        if type(max_chunk_bytes) is not int or max_chunk_bytes<=0:raise ValueError('chunk byte cap')
        self.client,self.mux,self.offsets=client,mux,dict(offsets)
        self.decoder,self.maximum=decoder,max_chunk_bytes
        self.downloader=downloader or (lambda ident:bounded_chunk(client,ident,max_chunk_bytes))
        if type(require_capture_context) is not bool:raise ValueError('capture context mode')
        self.require_capture_context=require_capture_context
        self.contexts={};self.finals={};self.cutoffs={};self.end_reasons={}
        self.streams={};self.sequence={};self.previous={}
        self.pending_decode=None
        if clock_verifier is not None and not callable(clock_verifier):raise ValueError('clock verifier required')
        self.clock_verifier=clock_verifier
        if type(allow_unverified_clock_proofs) is not bool:raise ValueError('invalid unverified proof mode')
        self.allow_unverified_clock_proofs=allow_unverified_clock_proofs

    def poll(self):
        """At most one new chunk per camera; caller handles empty polls/stall TTL."""
        if len(self.mux.ended)==len(self.mux.cameras) and not self.mux.queue:
            raise SourceMuxError('primary_source_ended' if self.cutoffs else 'session_stopped')
        active=list(self.client.list_streams())
        # Recover bound stream metadata before EOS/cutoff decisions, never by
        # choosing a newer stream for the same camera.
        for camera,stream in self.streams.items():
            if camera in self.mux.ended:continue
            try:
                if not any(row['camera_id']==camera and row['stream_id']==stream for row in active):
                    row=self._availability(camera)
                    if row is None:
                        raise SourceUnavailableError('source_stream_unavailable',camera=camera,
                            stream=stream,expected_sequence=self.sequence[camera]+1)
                    active.append(row)
            except (SourceMuxError,httpx.HTTPError,ValueError) as error:
                if not self._degrade(camera,error):raise
        # Primary EOS freezes finite local reference drain boundaries. These are
        # not backend stop acknowledgments and must never authorize zone changes.
        primary_rows=[row for row in active if row['camera_id']==self.mux.primary
                      and row['stream_id']==self.streams.get(self.mux.primary)]
        if primary_rows and primary_rows[0].get('capture_stopped') is True:
            for camera in self.mux.cameras:
                if camera==self.mux.primary or camera in self.mux.ended or camera in self.cutoffs:continue
                rows=[row for row in active if row['camera_id']==camera
                      and row['stream_id']==self.streams.get(camera)]
                if len(rows)==1 and rows[0].get('capture_stopped',False) is False:
                    boundary=rows[0].get('last_sequence')
                    if type(boundary) is not int or boundary<self.sequence[camera]:
                        raise SourceMuxError('invalid_reference_drain_boundary')
                    self.cutoffs[camera]=boundary
        selected={}
        # Resolve all cameras before decoding: a terminal source must not discard
        # frames already decoded for another camera in this same poll.
        for camera in self.mux.cameras:
            if camera in self.mux.ended:continue
            try:
                matches=[s for s in active if s['camera_id']==camera]
                if camera in self.streams:
                    matches=[s for s in matches if s['stream_id']==self.streams[camera]]
                else:
                    matches=[s for s in matches if not s.get('capture_stopped',False)]
                if not matches and camera in self.streams:
                    available=self._availability(camera)
                    if available is not None:matches=[available]
                    else:raise SourceUnavailableError('source_stream_unavailable',camera=camera,
                        stream=self.streams[camera],expected_sequence=self.sequence[camera]+1)
                if not matches and camera not in self.streams and self.tolerate_source_gaps:
                    continue
                if len(matches)!=1:raise SourceMuxError('camera_missing_or_ambiguous')
                row=matches[0]
                if self.clock_verifier is None and not self.allow_unverified_clock_proofs and row.get('clock_proof_version') is not None:
                    raise SourceMuxError('clock_proof_profile_required')
                stream=row['stream_id']
                context=self._context(row)
                if camera in self.contexts and self.contexts[camera]!=context:
                    raise SourceMuxError('camera_capture_context_changed')
                stopped=row.get('capture_stopped',False)
                if type(stopped) is not bool:raise SourceMuxError('invalid_capture_stop')
                final=None
                if camera in self.finals and not stopped:
                    raise SourceMuxError('capture_stop_reverted')
                if stopped:
                    final=row.get('final_sequence')
                    if (type(final) is not int or final < -1 or
                            type(row.get('last_sequence')) is not int or row['last_sequence']!=final):
                        raise SourceMuxError('invalid_capture_final_sequence')
                    if camera in self.finals and self.finals[camera]!=final:
                        raise SourceMuxError('capture_final_sequence_changed')
                    self.finals[camera]=final
                    if self.sequence[camera]>=final:
                        if self.sequence[camera]!=final:raise SourceMuxError('capture_final_sequence_regressed')
                        self._end(camera,'source_ended')
                        continue
                if camera in self.cutoffs:
                    final=self.cutoffs[camera] if final is None else min(final,self.cutoffs[camera])
                    if self.sequence[camera]==final:
                        self._end(camera,'reference_drain_cutoff');continue
                selected[camera]=(row,stream,context,final)
            except (SourceMuxError,httpx.HTTPError,ValueError) as error:
                if not self._degrade(camera,error):raise
        # Return already buffered frames before decoding another whole chunk.
        # Only proven watermark blockers are failed; queued reference frames
        # remain chronological and the failure keeps its evidence incomplete.
        if self.pending_decode is not None:
            camera=self.pending_decode['camera']
            try:
                maintain=getattr(self.decoder,'maintain',None)
                if maintain is not None:maintain()
                return self._advance_decode(selected)
            except (SourceMuxError, ValueError, RuntimeError, OSError, httpx.HTTPError) as error:
                if self.pending_decode is not None:
                    frames=self.pending_decode['frames'];self.pending_decode=None
                    if hasattr(frames,'close'):frames.close()
                if not self._degrade(camera,error):raise
                return self.mux.pop_ready()
        cameras=self.mux.cameras
        if self.degrade_references and not self.tolerate_source_gaps:
            cameras=tuple(sorted(cameras,key=lambda c:(self.mux.latest.get(c,float('-inf')),c==self.mux.primary)))
            blockers=self.mux.pressure_blockers()
            # Give blocking sources first opportunity to catch up. Never label
            # a source stalled while its advertised next chunk is available.
            available=[c for c in blockers if c in selected and c in self.sequence
                       and selected[c][0].get('last_sequence',-1)>self.sequence[c]]
            if available:
                cameras=tuple(available)
            elif self.mux.primary in blockers:
                raise SourceMuxError('primary_source_pressure_stalled')
            elif blockers:
                if any(c not in self.sequence for c in blockers):
                    raise SourceMuxError('reference_pressure_before_first_binding')
                for camera in blockers:
                    self._degrade(camera,SourceMuxError('reference_watermark_pressure'),preserve_queued=True)
                return self.mux.pop_ready()
        for camera in cameras:
            if camera not in selected:continue
            # A previous camera may have consumed most of the reserve in this
            # same poll. Re-evaluate watermark ownership before each chunk.
            if self.reserve_chunk_frames is not None:
                blockers=self.mux.pressure_blockers()
                if blockers:
                    available=[c for c in blockers if c in selected and c in self.sequence
                               and selected[c][0].get('last_sequence',-1)>self.sequence[c]]
                    if available and camera not in available:
                        return self.mux.pop_ready()
                    if not available:
                        if self.mux.primary in blockers:raise SourceMuxError('primary_source_pressure_stalled')
                        if any(c not in self.sequence for c in blockers):raise SourceMuxError('reference_pressure_before_first_binding')
                        for c in blockers:self._degrade(c,SourceMuxError('reference_watermark_pressure'),preserve_queued=True)
                        return self.mux.pop_ready()
            try:
                row,stream,context,final=selected[camera]
                self.contexts[camera]=context
                self.streams[camera]=stream
                if camera not in self.sequence:
                    boundary=row.get('last_sequence')
                    if type(boundary) is not int or boundary < -1:raise SourceMuxError('invalid_live_boundary')
                    self.sequence[camera]=boundary
                boundary=row.get('last_sequence')
                if type(boundary) is not int or boundary < -1:
                    raise SourceMuxError('invalid_live_boundary')
                expected=self.sequence[camera]+1
                chunks=sorted(self.client.list_chunks(stream,self.sequence[camera]),key=lambda c:c['sequence'])
                if not chunks:
                    if boundary>=expected:
                        self._availability(camera)
                        raise SourceUnavailableError('source_chunk_unavailable',camera=camera,
                            stream=stream,expected_sequence=expected)
                    continue
                if not self.tolerate_source_gaps and type(chunks[0]['sequence']) is int and chunks[0]['sequence']>expected:
                    raise SourceUnavailableError('source_chunk_unavailable',camera=camera,
                        stream=stream,expected_sequence=expected)
                chunk=chunks[0]
                if self.clock_verifier is None and not self.allow_unverified_clock_proofs and chunk.get('clock_proof_id') is not None:
                    raise SourceMuxError('clock_proof_profile_required')
                if type(chunk['sequence']) is not int or (chunk['sequence']!=self.sequence[camera]+1 and not self.tolerate_source_gaps):
                    raise SourceMuxError('camera_chunk_discontinuity')
                if final is not None and chunk['sequence']>final:
                    raise SourceMuxError('chunk_beyond_capture_final_sequence')
                if self._context(chunk)!=context:
                    raise SourceMuxError('chunk_capture_context_mismatch')
                if self.clock_verifier is not None:self.clock_verifier(camera,chunk)
                if camera in self.previous:
                    previous=self.previous[camera]
                    if not self.tolerate_source_gaps and (chunk['sequence']!=self.sequence[camera]+1 or not is_continuous_chunk(
                            previous['capture_start_at'],previous['duration_ms'],chunk['capture_start_at'],
                            max_gap_seconds=self.gap)):
                        raise SourceMuxError('camera_chunk_discontinuity')
                if self.reserve_chunk_frames is not None and (len(self.mux.queue)+self.reserve_chunk_frames>self.mux.max_frames or self.mux.bytes+self.reserve_chunk_bytes>self.mux.max_bytes):
                    ready=self.mux.pop_ready()
                    if ready:return ready
                    raise SourceMuxError('chunk_reserve_unavailable')
                try:
                    raw=self.downloader(chunk['chunk_id'])
                except SourceMuxError as error:
                    if str(error) not in ('chunk_http_404','chunk_http_410'):raise
                    raise SourceUnavailableError('source_chunk_unavailable',camera=camera,
                        stream=stream,expected_sequence=expected) from error
                if not raw or len(raw)>self.maximum:raise SourceMuxError('chunk_size_limit')
                frames=iter(self.decoder(raw,with_timing=True,duration_ms=chunk['duration_ms']))
                self.pending_decode=dict(camera=camera,chunk=chunk,final=final,frames=frames,count=0,frame=None)
                batch=self._advance_decode(selected)
                if self.pending_decode is not None:return batch
                # Keep released frames in the mux until all selected sources
                # are handled, except when pressure requires an early return.
                if batch:return batch
            except (SourceMuxError, ValueError, RuntimeError, OSError, httpx.HTTPError) as error:
                if not self._degrade(camera,error):raise
        return self.mux.pop_ready()

    def _advance_decode(self,selected):
        pending=self.pending_decode;frames=pending['frames'];camera=pending['camera'];chunk=pending['chunk']
        try:
            while True:
                if pending['frame'] is None:
                    try:pending['frame']=next(frames)
                    except StopIteration:
                        if not pending['count']:raise SourceMuxError('empty_video_chunk')
                        self.previous[camera]=dict(chunk);self.sequence[camera]=chunk['sequence']
                        self.arrival_by_camera[camera]=time.monotonic()
                        if pending['final'] is not None and chunk['sequence']==pending['final']:
                            self._end(camera,'reference_drain_cutoff' if camera in self.cutoffs else 'source_ended')
                        self.pending_decode=None
                        return self.mux.pop_ready()
                index,fps,frame=pending['frame']
                stamp=frame_timestamp(chunk['capture_start_at'],index,fps).timestamp()+self.offsets[camera]
                if self.mux.would_overflow(frame):
                    ready=self.mux.pop_ready()
                    if ready:return ready
                    blockers=self.mux.pressure_blockers()
                    if not self.degrade_references or not blockers or self.mux.primary in blockers:
                        raise SourceMuxError('primary_source_pressure_stalled' if self.mux.primary in blockers else 'mux_budget_exhausted')
                    # An advertised unread blocker must be handled fairly; one
                    # active decoder cannot safely switch chunks mid-frame.
                    if any(c not in self.sequence or c not in selected or
                           selected[c][0].get('last_sequence',-1)>self.sequence[c] for c in blockers):
                        raise SourceMuxError('reference_pressure_pending_chunk')
                    for c in blockers:
                        self._degrade(c,SourceMuxError('reference_watermark_pressure'),preserve_queued=True)
                    batch=self.mux.pop_ready()
                    if not batch:raise SourceMuxError('mux_budget_exhausted')
                    return batch
                self.mux.push(camera,stamp,frame);pending['count']+=1;pending['frame']=None
        except BaseException:
            self.pending_decode=None
            if hasattr(frames,'close'):frames.close()
            raise

    def _degrade(self,camera,error,*,preserve_queued=False):
        if str(error).startswith('clock_') and not str(error).startswith('clock_reference_'):return False
        if isinstance(error,httpx.HTTPStatusError) and error.response.status_code in (401,403):
            return False
        if (not self.degrade_references or camera==self.mux.primary
                or str(error) in ("mux_budget_exhausted","chunk_reserve_unavailable","chunk_http_401","chunk_http_403")):
            return False
        metadata=dict(reason="reference_source_failed",camera=camera,
            stream=self.streams.get(camera,"unbound_"+camera),
            expected_sequence=max(0,self.sequence.get(camera,-1)+1),
            availability_reason=getattr(error,"availability_reason",None),
            failure_code=str(error) if re.fullmatch("[a-z0-9_]{1,80}",str(error)) else type(error).__name__)
        self.reference_failures[camera]=metadata
        self.end_reasons[camera]="source_failed"
        self.mux.fail_reference(camera,str(error),preserve_queued=preserve_queued)
        return True

    def expire_reference_stalls(self,*,now,max_lag_seconds):
        if not self.degrade_references:return
        if self.tolerate_source_gaps:
            if self.mux.primary not in self.streams:return
            now=time.monotonic()
            for camera in self.mux.cameras:
                if camera==self.mux.primary or camera in self.mux.ended:continue
                since=self.arrival_by_camera.get(camera,self.reference_wait_since.setdefault(camera,now))
                if now-since>=max_lag_seconds:self._degrade(camera,SourceMuxError('reference_arrival_timeout'))
            return
        for camera in self.mux.cameras:
            if camera==self.mux.primary or camera in self.mux.ended:continue
            last=self.mux.latest.get(camera)
            if last is None:
                since=self.reference_wait_since.setdefault(camera,now)
                if now-since>=max_lag_seconds:
                    self._degrade(camera,SourceMuxError("reference_initial_frame_timeout"))
            elif now-last>max_lag_seconds:
                self._degrade(camera,SourceMuxError("reference_stalled"))

    def fail_clock_references(self,failures):
        for camera,reason in failures.items():
            if camera not in self.mux.ended:
                if not self._degrade(camera,SourceMuxError(reason)):raise SourceMuxError(reason)

    def waiting_first_references(self):
        return tuple(c for c in self.mux.cameras if c!=self.mux.primary
                     and c not in self.mux.ended and c not in self.mux.latest)


    def _availability(self,camera):
        lookup=getattr(self.client,'stream_availability',None)
        if lookup is None:return None
        row=lookup(self.streams[camera],self.sequence[camera])
        if row is None:return None
        expected=self.sequence[camera]+1
        if (not isinstance(row,dict) or row.get('stream_id')!=self.streams[camera]
                or row.get('camera_id')!=camera or type(row.get('next_sequence')) is not int or row.get('next_sequence')!=expected
                or type(row.get('last_sequence')) is not int or row['last_sequence']<self.sequence[camera]
                or self._context(row)!=self.contexts[camera]
                or row.get('next_chunk_state') not in ('available','unavailable','not_received')):
            raise SourceMuxError('invalid_source_availability')
        if ((row['next_chunk_state']=='not_received') != (expected>row['last_sequence'])):
            raise SourceMuxError('invalid_source_availability')
        if row['next_chunk_state']=='unavailable':
            reason=row.get('reason')
            if reason not in ('raw_expired','raw_missing','raw_unavailable','receipt_missing'):reason=None
            raise SourceUnavailableError('source_chunk_unavailable',camera=camera,
                stream=self.streams[camera],expected_sequence=expected,availability_reason=reason)
        return row

    def _end(self,camera,reason):
        self.end_reasons[camera]=reason
        self.mux.end_source(camera)

    def _context(self,row):
        fields=('capture_context_id','capture_session_id','assignment_version')
        values=tuple(row.get(k) for k in fields)
        if all(v is None for v in values):
            if self.require_capture_context:raise SourceMuxError('capture_context_required')
            return None
        if (not all(isinstance(v,str) and re.fullmatch('[A-Za-z0-9_-]{1,100}',v) for v in values[:2])
                or type(values[2]) is not int or values[2]<1):
            raise SourceMuxError('invalid_capture_context')
        return values

    def primary_capture_context(self):
        value=self.contexts.get(self.mux.primary)
        return value[0] if value else None
