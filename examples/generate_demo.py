"""Generate demo layer data for ScanPathViewer.
生成演示用的逐层NC数据（同心环轮廓+往复填充线，层间半径渐变，模拟一个花瓶截面）。

Usage:  python generate_demo.py [layers]
Output: ./demo_data/1.nc ... N.nc
"""
import math, os, sys


def ring_points(cx, cy, r, n=90):
    return [(cx + r * math.cos(2 * math.pi * i / n),
             cy + r * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]


def gen_layer(ln, total, cx=100.0, cy=100.0):
    """One layer: outer ring + inner ring contours, hatch fill between them."""
    t = ln / total
    r_out = 60 + 25 * math.sin(math.pi * t)          # vase profile
    r_in = max(12.0, r_out - 18 - 8 * math.sin(3 * math.pi * t))
    lines = []

    # travel to contour start
    pts = ring_points(cx, cy, r_out)
    lines.append(f"G00 X{pts[0][0]:.3f} Y{pts[0][1]:.3f}")
    for x, y in pts[1:]:
        lines.append(f"G01 X{x:.3f} Y{y:.3f}")

    pts = ring_points(cx, cy, r_in)
    lines.append(f"G00 X{pts[0][0]:.3f} Y{pts[0][1]:.3f}")
    for x, y in pts[1:]:
        lines.append(f"G01 X{x:.3f} Y{y:.3f}")

    # serpentine hatch fill between the two rings
    # odd layers hatch along X, even layers along Y (alternating like real SLS)
    pitch = 2.0
    swap = (ln % 2 == 0)
    v = -r_out
    flip = False
    while v <= r_out:
        if abs(v) < r_out:
            half_out = math.sqrt(r_out * r_out - v * v)
            if abs(v) < r_in:
                half_in = math.sqrt(r_in * r_in - v * v)
                spans = [(-half_out, -half_in), (half_in, half_out)]
            else:
                spans = [(-half_out, half_out)]
            for u1, u2 in spans:
                if flip: u1, u2 = u2, u1
                if swap:
                    lines.append(f"G00 X{cx + v:.3f} Y{cy + u1:.3f}")
                    lines.append(f"G01 X{cx + v:.3f} Y{cy + u2:.3f}")
                else:
                    lines.append(f"G00 X{cx + u1:.3f} Y{cy + v:.3f}")
                    lines.append(f"G01 X{cx + u2:.3f} Y{cy + v:.3f}")
            flip = not flip
        v += pitch
    return "\n".join(lines) + "\n"


def main():
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_data")
    os.makedirs(out, exist_ok=True)
    for ln in range(1, total + 1):
        with open(os.path.join(out, f"{ln}.nc"), "w", encoding="utf-8") as f:
            f.write(gen_layer(ln, total))
    print(f"OK: {total} layers -> {out}")


if __name__ == "__main__":
    main()
