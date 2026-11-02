"""Normalized simple polygons; boundary points belong to the work area."""
import math

EPS = 1e-10


def cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def on_segment(a, b, p):
    return abs(cross(a, b, p)) <= EPS and all(
        min(a[i], b[i])-EPS <= p[i] <= max(a[i], b[i])+EPS for i in (0, 1))


def intersects(a, b, c, d):
    values = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
    return (values[0]*values[1] < 0 and values[2]*values[3] < 0) or any((
        on_segment(a, b, c), on_segment(a, b, d), on_segment(c, d, a), on_segment(c, d, b)))


def validate_points(points):
    if not isinstance(points, list) or not 3 <= len(points) <= 32:
        raise ValueError('polygon_requires_3_to_32_vertices')
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2 or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or
                not math.isfinite(v) or not 0 <= v <= 1 for v in point):
            raise ValueError('polygon_coordinates_must_be_normalized')
    if len({tuple(point) for point in points}) != len(points):
        raise ValueError('polygon_duplicate_vertices')
    count = len(points)
    area = sum(cross((0, 0), points[i], points[(i+1) % count]) for i in range(count))
    if abs(area) <= EPS:
        raise ValueError('polygon_zero_area')
    for i in range(count):
        if abs(cross(points[i-1], points[i], points[(i+1) % count])) <= EPS:
            raise ValueError('polygon_collinear_vertices')
        for j in range(i+1, count):
            if j == i+1 or (i == 0 and j == count-1):
                continue
            if intersects(points[i], points[(i+1) % count], points[j], points[(j+1) % count]):
                raise ValueError('polygon_self_intersection')
    return [[float(v) for v in point] for point in points]


def contains(points, point):
    inside = False
    x, y = point
    for i, a in enumerate(points):
        b = points[(i+1) % len(points)]
        if on_segment(a, b, point):
            return True
        if (a[1] > y) != (b[1] > y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
            inside = not inside
    return inside
