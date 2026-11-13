"""Validate collector JSONL and summarize complete-window diagnostic observations."""
import argparse
import json
import math
import os
from pathlib import Path
from inference_worker.ppe_absence_evidence import summarize_absence


MAX_SYNTHETIC_OBSERVATIONS = 2_000_000


def summarize_records(records, *, max_sample_gap_seconds):
    if not isinstance(records, list) or not 4 <= len(records) <= 100002:
        raise ValueError('complete header, >=2 frames and footer required')
    if any(not isinstance(row, dict) for row in records):
        raise ValueError('records must be objects')
    header, footer = records[0], records[-1]
    if header.get('type') != 'scenario_header' or header.get('version') != 1:
        raise ValueError('unsupported collector header')
    if footer.get('type') != 'scenario_footer' or footer.get('completed') is not True:
        raise ValueError('incomplete scenario')
    if header.get('automatic_confirmation_allowed') is not False or footer.get('automatic_confirmation_allowed') is not False:
        raise ValueError('invalid diagnostic-only contract')
    for field in ('scenario_id','camera_id','session_id','model_version'):
        if not isinstance(header.get(field), str) or not header[field].strip():
            raise ValueError('missing scope metadata')
    labels = header.get('scenario_labels')
    if (not isinstance(labels, dict) or set(labels) != {'helmet','vest'}
            or any(v not in ('worn','not_worn','ambiguous') for v in labels.values())):
        raise ValueError('invalid scenario labels')
    if (isinstance(max_sample_gap_seconds, bool) or not isinstance(max_sample_gap_seconds,(float,int))
            or not math.isfinite(max_sample_gap_seconds) or max_sample_gap_seconds <= 0):
        raise ValueError('explicit positive sample gap required')
    frames = records[1:-1]; previous = None; tracks = set(); empty = 0
    normalized = []
    for index, row in enumerate(frames):
        if row.get('type') != 'frame' or type(row.get('index')) is not int or row['index'] != index:
            raise ValueError('frame order/count mismatch')
        timestamp = row.get('timestamp')
        if (isinstance(timestamp,bool) or not isinstance(timestamp,(int,float)) or not math.isfinite(timestamp)
                or timestamp < 0 or (previous is not None and timestamp <= previous)
                or (previous is None and timestamp != 0)):
            raise ValueError('invalid frame timestamp')
        previous = timestamp
        people = row.get('people')
        if not isinstance(people,list) or len(people) > 1000:
            raise ValueError('invalid people list')
        by_track = {}
        for person in people:
            if not isinstance(person,dict) or set(person) != {'tracking_id','ppe'}:
                raise ValueError('unexpected person fields')
            track = person['tracking_id']; ppe = person['ppe']
            if not isinstance(track,str) or not track or track in by_track:
                raise ValueError('invalid/duplicate track')
            if not isinstance(ppe,dict) or set(ppe) != {'helmet','vest'}:
                raise ValueError('invalid PPE fields')
            for kind,status in ppe.items():
                if status not in (kind,'suspected_no_'+kind,'unknown'):
                    raise ValueError('invalid PPE status')
            by_track[track] = ppe; tracks.add(track)
        if not by_track: empty += 1
        normalized.append((timestamp, by_track))
    if (type(footer.get('frame_count')) is not int or footer['frame_count'] != len(frames)
            or footer.get('last_frame_time') != previous
            or footer.get('empty_frame_count') != empty
            or footer.get('distinct_track_count') != len(tracks)):
        raise ValueError('footer does not match frames')
    if 2 * len(frames) * len(tracks) > MAX_SYNTHETIC_OBSERVATIONS:
        raise ValueError('too many frame-track combinations; inspect tracking churn without dropping frames')
    reports = []
    for track in sorted(tracks):
        for kind in ('helmet','vest'):
            observations = [dict(camera_id=header['camera_id'], session_id=header['session_id'],
                model_version=header['model_version'], tracking_id=track, ppe_type=kind,
                timestamp=t, status=people.get(track,{}).get(kind,'unknown')) for t,people in normalized]
            reports.append(summarize_absence(camera_id=header['camera_id'], session_id=header['session_id'],
                model_version=header['model_version'], tracking_id=track, ppe_type=kind,
                start_time=0, end_time=previous, observations=observations,
                max_sample_gap_seconds=max_sample_gap_seconds))
    return {'version':1, 'mode':'complete_scenario_diagnostics',
            'scenario_id':header['scenario_id'], 'camera_id':header['camera_id'],
            'model_version':header['model_version'], 'timing_basis':header.get('timing_basis'),
            'scenario_labels_unverified':labels, 'frame_count':len(frames),
            'empty_frame_count':empty, 'distinct_track_count':len(tracks),
            'track_ppe_diagnostics':reports, 'automatic_confirmation_allowed':False,
            'limitations':['missing_track_at_any_frame_is_unknown_not_dropped',
                'scenario_labels_not_assigned_to_individual_tracks',
                'no_cross_track_identity_merging', 'not_accuracy_evaluation_or_event_creation',
                'no_model_calibrated_violation_confidence', 'no_person_detected_is_not_proof_of_compliance']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--max-sample-gap-seconds',type=float,required=True)
    args=parser.parse_args()
    source=Path(args.input)
    if source.stat().st_size > 32*1024*1024:
        parser.error('input exceeds 32 MiB; inspect scenario limits')
    with source.open(encoding='utf-8') as stream:
        rows=[json.loads(line) for line in stream]
    report=summarize_records(rows,max_sample_gap_seconds=args.max_sample_gap_seconds)
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=True,allow_nan=False,indent=2)
        stream.write('\n')


if __name__ == '__main__':main()
