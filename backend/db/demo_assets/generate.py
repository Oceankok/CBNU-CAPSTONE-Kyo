"""Small original graphics containing no people, faces, or external copyrighted media."""
from pathlib import Path
import cv2
import imageio_ffmpeg
import numpy as np
import subprocess
import tempfile

root = Path(__file__).resolve().parent
frame = np.full((180, 320, 3), (45, 35, 25), dtype=np.uint8)
cv2.rectangle(frame, (25, 65), (120, 150), (170, 120, 40), -1)
cv2.rectangle(frame, (160, 75), (295, 150), (50, 140, 170), -1)
cv2.putText(frame, 'DEMO - NO PERSONAL IMAGE', (12, 25), cv2.FONT_HERSHEY_SIMPLEX, .48, (255, 255, 255), 1)
cv2.putText(frame, 'Illustration / not AI evidence', (12, 49), cv2.FONT_HERSHEY_SIMPLEX, .42, (210, 210, 210), 1)
cv2.imwrite(str(root / 'sample.jpg'), frame)
with tempfile.TemporaryDirectory(prefix='kyo-demo-asset-') as folder:
    intermediate = Path(folder) / 'sample.mp4'
    writer = cv2.VideoWriter(str(intermediate), cv2.VideoWriter_fourcc(*'mp4v'), 12, (320,180))
    if not writer.isOpened():
        raise RuntimeError('Cannot generate demo clip')
    for _ in range(12):
        writer.write(frame)
    writer.release()
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-i', str(intermediate), '-an', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(root / 'sample.mp4')],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
