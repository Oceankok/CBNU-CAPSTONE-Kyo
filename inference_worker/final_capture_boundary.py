"""Trim only unadvertised provisional evidence; no historical event recovery."""
import math
import time
import cv2


def frame_overlap(segment, start, end):
    """Conservative whole source-frame interval, with no padding/resampling."""
    first=max(0, math.ceil((start-segment.capture_start)*segment.fps))
    stop=min(segment.frame_count, math.floor((end-segment.capture_start)*segment.fps))
    return first, max(first, stop)


def trim_segment(spool, segment, first, stop, *, timeout, max_pixels):
    path=spool.path_for_upload(segment.segment_id)
    decoder=cv2.VideoCapture(str(path))
    begun=False
    deadline=time.monotonic()+timeout
    try:
        if (not decoder.isOpened() or int(decoder.get(cv2.CAP_PROP_FRAME_COUNT))!=segment.frame_count
                or abs(decoder.get(cv2.CAP_PROP_FPS)-segment.fps)>.001):
            raise ValueError('final_boundary_source_contract')
        for index in range(stop):
            if time.monotonic()>=deadline:raise TimeoutError('final_boundary_trim_timeout')
            ok,frame=decoder.read()
            if not ok:raise ValueError('final_boundary_source_truncated')
            if index<first:continue
            if not begun:
                height,width=frame.shape[:2]
                if height*width>max_pixels:raise ValueError('final_boundary_frame_limit')
                encoder=spool.begin(segment.capture_start+first/segment.fps,
                    segment.fps,width,height,encoder_timeout=timeout)
                begun=True
            encoder.write(frame)
        if not begun:raise ValueError('final_boundary_empty_trim')
        return spool.finalize(stop-first)
    except BaseException:
        if begun:spool.abort_active(reason='final_boundary_trim_failed')
        raise
    finally:
        decoder.release()
