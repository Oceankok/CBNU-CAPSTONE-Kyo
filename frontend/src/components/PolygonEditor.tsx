import { useRef, useState } from 'react';
import { clamp01 } from '../polygon';
import type { Point } from '../polygon';
import styles from './PolygonEditor.module.css';

const COLORS = [
  '#3182ce',
  '#dd6b20',
  '#38a169',
  '#805ad5',
  '#d69e2e',
  '#e53e3e',
];

export interface OtherArea {
  id: string;
  name: string;
  points: Point[];
}

// Draw and adjust one polygon on top of a camera image.
// Coordinates are 0..1 of the image; the SVG uses the image's pixel size so dots stay round.
export default function PolygonEditor({
  imageUrl,
  points,
  others,
  editing,
  onChange,
}: {
  imageUrl: string;
  points: Point[];
  others: OtherArea[];
  editing: boolean; // false: view only
  onChange: (next: Point[]) => void;
}) {
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [drag, setDrag] = useState<number | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const moved = useRef(false);

  const toPoint = (e: React.PointerEvent): Point => {
    const r = svg.current!.getBoundingClientRect();
    return [
      clamp01((e.clientX - r.left) / r.width),
      clamp01((e.clientY - r.top) / r.height),
    ];
  };

  const px = (p: Point) => (size ? `${p[0] * size.w},${p[1] * size.h}` : '');
  const radius = size ? size.w * 0.012 : 6;
  const font = size ? size.w * 0.022 : 12;

  return (
    <div className={styles.wrap}>
      <img
        className={styles.image}
        src={imageUrl}
        alt="카메라 기준 화면 (얼굴 가림 처리)"
        onLoad={(e) =>
          setSize({
            w: e.currentTarget.naturalWidth,
            h: e.currentTarget.naturalHeight,
          })
        }
      />
      {size && (
        <svg
          ref={svg}
          className={`${styles.svg} ${editing ? styles.editing : ''}`}
          viewBox={`0 0 ${size.w} ${size.h}`}
          onPointerDown={(e) => {
            if (!editing || drag !== null) return;
            moved.current = false;
            onChange([...points, toPoint(e)]);
          }}
          onPointerMove={(e) => {
            if (drag === null) return;
            moved.current = true;
            const next = points.slice();
            next[drag] = toPoint(e);
            onChange(next);
          }}
          onPointerUp={() => setDrag(null)}
          onPointerLeave={() => setDrag(null)}
        >
          {others.map((o, i) => (
            <g key={o.id} opacity={0.55}>
              <polygon
                points={o.points.map(px).join(' ')}
                fill={COLORS[i % COLORS.length]}
                fillOpacity={0.18}
                stroke={COLORS[i % COLORS.length]}
                strokeWidth={size.w * 0.003}
              />
              <text
                x={o.points[0][0] * size.w + radius}
                y={o.points[0][1] * size.h - radius}
                fontSize={font}
                fill="#fff"
                stroke="#000"
                strokeWidth={font * 0.12}
                paintOrder="stroke"
              >
                {o.name}
              </text>
            </g>
          ))}
          {points.length >= 3 && (
            <polygon
              points={points.map(px).join(' ')}
              fill="#e53e3e"
              fillOpacity={0.22}
              stroke="#e53e3e"
              strokeWidth={size.w * 0.004}
              pointerEvents="none"
            />
          )}
          {points.length >= 2 && points.length < 3 && (
            <polyline
              points={points.map(px).join(' ')}
              fill="none"
              stroke="#e53e3e"
              strokeWidth={size.w * 0.004}
              pointerEvents="none"
            />
          )}
          {points.map((p, i) => (
            <circle
              key={i}
              cx={p[0] * size.w}
              cy={p[1] * size.h}
              r={radius}
              fill={i === 0 ? '#fff' : '#e53e3e'}
              stroke="#e53e3e"
              strokeWidth={size.w * 0.003}
              className={editing ? styles.handle : undefined}
              onPointerDown={(e) => {
                if (!editing) return;
                e.stopPropagation();
                (e.target as Element).setPointerCapture?.(e.pointerId);
                setDrag(i);
              }}
            />
          ))}
        </svg>
      )}
    </div>
  );
}
