"""시험 결과로 확인 그림을 만듭니다 (Actions 산출물 · PR 에 붙이는 그림).

사용: python tests/render_previews.py [시험결과 폴더]
만드는 것: 시험결과/확인그림/ 아래
  - 3D_기본모델.png, 3D_대지모델.png : 3D 프린트 STL 을 비스듬히 본 그림
  - 평면_기본모델.png, 평면_대지모델.png : 프로그램이 그린 평면 확인 그림
  - 레이저판_기본모델.png : 레이저 재단 판 전체
  - 모음.png : 위 그림을 한 장에
"""
import os
import shutil
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[1]
FONT = ROOT / "contour_ai" / "fonts" / "Pretendard-Regular.ttf"
if FONT.exists():
    font_manager.fontManager.addfont(str(FONT))
    plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(FONT)).get_name()


def render_stl(stl, png, title, W=1400, H=1000):
    """STL 을 비스듬히 본 그림. 깊이 버퍼로 직접 그려 가려진 면이 비치지 않게 한다."""
    import trimesh
    from PIL import Image, ImageDraw, ImageFont
    m = trimesh.load(stl)
    v = m.vertices - m.bounds.mean(0)
    az, el = np.radians(-35), np.radians(38)          # 남서쪽 위에서
    rz = np.array([[np.cos(az), -np.sin(az), 0], [np.sin(az), np.cos(az), 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, np.sin(el), np.cos(el)], [0, -np.cos(el), np.sin(el)]])
    p = v @ rz.T @ rx.T                                # x 오른쪽, y 위, z 보는 쪽(클수록 가까움)
    span = max(np.ptp(p[:, 0]) / (W * 0.9), np.ptp(p[:, 1]) / (H * 0.85))
    sx = (p[:, 0] - p[:, 0].min()) / span + (W - np.ptp(p[:, 0]) / span) / 2
    sy = (p[:, 1].max() - p[:, 1]) / span + (H - np.ptp(p[:, 1]) / span) / 2 + 30
    sz = p[:, 2]
    n = m.face_normals @ rz.T @ rx.T
    light = np.array([-0.35, 0.55, 0.75]); light /= np.linalg.norm(light)
    shade = np.clip(n @ light, 0, 1) * 0.6 + 0.35
    img = np.ones((H, W)); zb = np.full((H, W), -np.inf)
    F = m.faces[n[:, 2] > 0]                           # 뒤를 보는 면은 그리지 않음
    sh = shade[n[:, 2] > 0]
    for f, c in zip(F, sh):
        x, y, z = sx[f], sy[f], sz[f]
        x0, x1 = int(max(x.min(), 0)), int(min(np.ceil(x.max()), W - 1))
        y0, y1 = int(max(y.min(), 0)), int(min(np.ceil(y.max()), H - 1))
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        d = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
        if abs(d) < 1e-9:
            continue
        a = ((y[1] - y[2]) * (gx - x[2]) + (x[2] - x[1]) * (gy - y[2])) / d
        b = ((y[2] - y[0]) * (gx - x[2]) + (x[0] - x[2]) * (gy - y[2])) / d
        g = 1 - a - b
        inside = (a >= -1e-6) & (b >= -1e-6) & (g >= -1e-6)
        zz = a * z[0] + b * z[1] + g * z[2]
        sub = zb[y0:y1 + 1, x0:x1 + 1]
        win = inside & (zz > sub)
        sub[win] = zz[win]
        img[y0:y1 + 1, x0:x1 + 1][win] = c
    im = Image.fromarray((img * 255).astype(np.uint8)).convert("RGB")
    dr = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype(str(FONT), 30)
    except Exception:
        font = None
    dr.text((30, 24), title, fill=(0, 0, 0), font=font)
    im.save(png)


def render_laser(folder, png, title):
    import ezdxf
    sheets = sorted(Path(folder).glob("*.dxf"))
    n = len(sheets)
    cols = min(n, 4); rows = (n + cols - 1) // cols
    fig, axs = plt.subplots(rows, cols, figsize=(3 * cols, 3 * rows), dpi=100, squeeze=False)
    for ax in axs.flat:
        ax.set_axis_off()
    for ax, p in zip(axs.flat, sheets):
        doc = ezdxf.readfile(p)
        for e in doc.modelspace():
            try:
                pts = np.array([v[:2] for v in e.flattening(0.5)]) if e.dxftype() != "LWPOLYLINE" else \
                    np.array([v[:2] for v in e.get_points()] + ([e.get_points()[0][:2]] if e.closed else []))
            except Exception:
                continue
            if len(pts) > 1:
                ax.plot(pts[:, 0], pts[:, 1], lw=0.3, color="#0050d0" if "CUT" in e.dxf.layer.upper() else "black")
        ax.set_aspect("equal"); ax.set_title(p.stem, fontsize=8)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(png, facecolor="white")
    plt.close(fig)


def contact_sheet(pngs, out):
    imgs = [plt.imread(p) for p in pngs]
    cols = 2; rows = (len(imgs) + 1) // 2
    fig, axs = plt.subplots(rows, cols, figsize=(12, 5 * rows), dpi=90, squeeze=False)
    for ax in axs.flat:
        ax.set_axis_off()
    for ax, im in zip(axs.flat, imgs):
        ax.imshow(im)
    fig.tight_layout()
    fig.savefig(out, facecolor="white")
    plt.close(fig)


def main():
    res = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CONTOUR_TEST_OUT") or ROOT / "시험결과")
    dst = res / "확인그림"
    dst.mkdir(parents=True, exist_ok=True)
    made = []
    for name in ("기본모델", "대지모델"):
        d = res / name
        if not d.exists():
            continue
        if (d / "model.stl").exists():
            render_stl(d / "model.stl", dst / f"3D_{name}.png", f"{name} · 3D (가짜 도엽)")
            made.append(dst / f"3D_{name}.png")
        if (d / "preview_plan.png").exists():
            shutil.copy(d / "preview_plan.png", dst / f"평면_{name}.png")
            made.append(dst / f"평면_{name}.png")
    if (res / "기본모델" / "laser").exists():
        render_laser(res / "기본모델" / "laser", dst / "레이저판_기본모델.png", "레이저 재단 판 (기본모델)")
        made.append(dst / "레이저판_기본모델.png")
    if made:
        contact_sheet(made, dst / "모음.png")
    print("확인 그림:", ", ".join(p.name for p in made))


if __name__ == "__main__":
    main()
