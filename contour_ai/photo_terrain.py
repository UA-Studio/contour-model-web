"""항공사진 지형 (v2.2): 항공사진을 입힌 지형 면을 OBJ(+MTL+JPG)와 GLB로.

렌더 프로그램(Blender·KeyShot·Twinmotion·Rhino 등)에 바로 넣어 쓰는 '사진 지형'. 높이는 곡면 지형(hz)을 그대로,
범위는 모델 범위(네모 또는 원). 항공사진이 없으면 만들지 않는다.
좌표: m, 모델 왼쪽 아래(origin) 기준. OBJ 는 관례대로 Y 가 위(x, z, -y), GLB 도 Y 가 위.
"""
import os

import numpy as np
import trimesh

OBJ_NAME, GLB_NAME, JPG_NAME = "terrain_photo.obj", "terrain_photo.glb", "terrain_photo.jpg"


def photo_mesh(clip, hz, origin, aerial, cell=None):
    """(메시, UV) — 메시 좌표는 origin 기준 m, Z 위."""
    from .terrain_zones import zone_mesh
    side = max(clip.bounds[2] - clip.bounds[0], clip.bounds[3] - clip.bounds[1])
    cell = cell or (2.0 if side <= 600 else 3.0 if side <= 1000 else 4.0)
    m = zone_mesh(clip, hz, origin, cell)
    if m is None:
        return None, None
    rgb, ax0, ay0, acell = aerial
    h, w = rgb.shape[:2]
    X = m.vertices[:, 0] + origin[0]
    Y = m.vertices[:, 1] + origin[1]
    uv = np.column_stack([(X - ax0) / (w * acell), (Y - ay0) / (h * acell)]).clip(0, 1)
    return m, uv


def _save_jpg(rgb, path, max_px=4096):
    from PIL import Image
    im = Image.fromarray(np.asarray(rgb, np.uint8))
    if max(im.size) > max_px:
        k = max_px / max(im.size)
        im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
    im.save(path, quality=88)
    return im


def write_obj(path, m, uv, jpg_name):
    """Y 위 OBJ + 같은 이름 MTL. 꼭짓점과 UV 번호가 같다."""
    base = os.path.splitext(path)[0]
    mtl = base + ".mtl"
    with open(mtl, "w", encoding="utf-8", newline="\n") as f:
        f.write("# 콘타 모델 항공사진 지형\nnewmtl aerial\nKa 1 1 1\nKd 1 1 1\nKs 0 0 0\nillum 1\n"
                f"map_Kd {jpg_name}\n")
    v = m.vertices
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"# 콘타 모델 항공사진 지형 · 단위 m · Y 위\nmtllib {os.path.basename(mtl)}\no terrain_photo\n")
        f.write("".join(f"v {x:.3f} {z:.3f} {-y:.3f}\n" for x, y, z in v))
        f.write("".join(f"vt {a:.6f} {b:.6f}\n" for a, b in uv))
        f.write("usemtl aerial\n")
        F = m.faces + 1
        f.write("".join(f"f {a}/{a} {b}/{b} {c}/{c}\n" for a, b, c in F))
    return path


def write_glb(path, m, uv, image):
    mat = trimesh.visual.material.PBRMaterial(name="aerial", baseColorTexture=image, metallicFactor=0.0,
                                              roughnessFactor=1.0)
    g = trimesh.Trimesh(m.vertices.copy(), m.faces.copy(), process=False,
                        visual=trimesh.visual.TextureVisuals(uv=uv, material=mat))
    g.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))   # Z 위 → Y 위
    trimesh.Scene({"terrain_photo": g}).export(path)
    return path


def write_photo_terrain(out_dir, clip, hz, origin, aerial, log=print):
    """항공사진 지형 OBJ·MTL·JPG·GLB 를 쓴다. 쓴 파일 이름 목록."""
    if aerial is None or hz is None:
        return []
    m, uv = photo_mesh(clip, hz, origin, aerial)
    if m is None:
        return []
    jpg = os.path.join(out_dir, JPG_NAME)
    im = _save_jpg(aerial[0], jpg)
    write_obj(os.path.join(out_dir, OBJ_NAME), m, uv, JPG_NAME)
    write_glb(os.path.join(out_dir, GLB_NAME), m, uv, im)
    log(f"   항공사진 지형: 면 {len(m.faces):,}개 (OBJ·GLB)")
    return [OBJ_NAME, os.path.splitext(OBJ_NAME)[0] + ".mtl", JPG_NAME, GLB_NAME]
