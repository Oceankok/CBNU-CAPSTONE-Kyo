"""Trim only unadvertised provisional evidence; no historical event recovery."""
from bisect import bisect_left, bisect_right
import time
import cv2


def frame_overlap(segment, start, end):
    """Conservative whole source-frame interval, with no padding/resampling."""
    # Compare grid edges directly. Subtracting UTC timestamps can shift an
    # exact frame boundary to the wrong side of an integer.
    def edge(index):
        return segment.capture_start + index / segment.fps

    first=bisect_left(range(segment.frame_count),start,key=edge)
    stop=bisect_right(range(segment.frame_count+1),end,key=edge)-1
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
