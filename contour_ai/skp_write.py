"""스케치업 파일 쓰기 write_sketchup(): 레이어별 그룹·건물·나무 컴포넌트를 .dae·JSON 으로."""
import json
import math
import os

import numpy as np
import trimesh
from .skp_dae import Dae, SkpJson, _ellipsoid_unit, _matrix, _ncname
from .skp_ruby import BUILDER_RB, HOWTO, RBZ_NAME, write_rbz
from .skp_app import make_skp_with_sketchup


def write_sketchup(out_dir, regions, masses, origin, terrain_mesh=None, area_solids=(), inlays=(),
                   areas=(), trees=(), steps_too=False, floor_height=3.3, lights=(), structs=(),
                   tree_style="species", roadbits=(), site_items=(), log=print, make_skp=True):
    """out_dir 에 model_sketchup.dae · model_sketchup_build.json · model_sketchup_info.json · 확장 설치파일(.rbz)
    · 여는 법(.txt) 을 쓰고, 이 PC 에 스케치업이 있으면 스케치업을 띄워 model_sketchup.skp(2021 형식)까지 만든다."""
    from .building_detail import building_parts
    from .buildings import SOURCE_KO
    from .facade import MATERIALS, facade_meshes, material_of
    from .buildings import ROLE_KO, USE_COLORS
    from .geom_util import _shift, as_polys, cut_regions
    from .paths_trees import PATH_STYLE, TREE_KIND, tree_dims
    d = Dae()
    J = SkpJson(origin)
    tr = trimesh.transformations.translation_matrix
    lv = list(regions)
    interval = lv[1] - lv[0] if len(lv) > 1 else 1.0
    with_steps = terrain_mesh is None or steps_too

    def extrude(p, z0, z1):
        try:
            m = trimesh.creation.extrude_polygon(_shift(p, origin), z1 - z0)
        except Exception:
            return None
        m.apply_transform(tr([0, 0, z0]))
        return m

    if with_steps:
        cut = cut_regions(regions, inlays)
        for i, L in enumerate(lv):
            bottom = L - interval if i else L - interval * 2
            ms = [m for m in (extrude(p, bottom, L) for p in as_polys(cut[L])) if m is not None]
            for p in as_polys(cut[L]):
                J.ext("지형::계단판", f"계단판 EL {L:g}", _shift(p, origin), bottom, L, (205, 205, 200), "계단판")
            if ms:
                d.add("지형::계단판", f"계단판 EL {L:g}", [(trimesh.util.concatenate(ms), (205, 205, 200))])
        by = {}
        for p, L, kind in inlays:
            m = extrude(p, L - interval, L)
            J.ext(f"길::계단판::{PATH_STYLE[kind][0]}", PATH_STYLE[kind][0], _shift(p, origin), L - interval, L,
                  PATH_STYLE[kind][1], PATH_STYLE[kind][0])
            if m is not None:
                by.setdefault(kind, []).append(m)
        for kind, ms in by.items():
            name, rgb = PATH_STYLE[kind][:2]
            d.add(f"길::계단판::{name}", name, [(trimesh.util.concatenate(ms), rgb)])
        by = {}
        for p, L, kind in areas:
            m = extrude(p, L, L + PATH_STYLE[kind][2])
            J.ext(f"길::계단판::{PATH_STYLE[kind][0]}", PATH_STYLE[kind][0], _shift(p, origin), L, L + PATH_STYLE[kind][2],
                  PATH_STYLE[kind][1], PATH_STYLE[kind][0])
            if m is not None:
                by.setdefault(kind, []).append(m)
        for kind, ms in by.items():
            name, rgb = PATH_STYLE[kind][:2]
            d.add(f"길::계단판::{name}", name, [(trimesh.util.concatenate(ms), rgb)])
    if terrain_mesh is not None:
        d.add("지형::경사매스" if steps_too else "지형::곡면대지", "경사 매스" if steps_too else "곡면 대지",
              [(terrain_mesh, (214, 212, 205))])
        J.mesh("지형::경사매스" if steps_too else "지형::곡면대지", "경사 매스" if steps_too else "곡면 대지",
               terrain_mesh, (214, 212, 205), "대지", soft=True)
    by = {}
    for m, kind in area_solids:
        by.setdefault(kind, []).append(m)
    for kind, ms in by.items():
        name, rgb = PATH_STYLE[kind][:2]
        d.add(f"길::곡면대지::{name}", name, [(trimesh.util.concatenate(ms), rgb)])
        J.mesh(f"길::곡면대지::{name}", name, trimesh.util.concatenate(ms), rgb, name, soft=True)

    info = {}
    for bi, m in enumerate(masses):
        at = m.get("attrs", {})
        use = m.get("use", "용도미상")
        color = USE_COLORS.get(use, (232, 232, 230))
        title = at.get("명칭") or at.get("주기") or m["layer"] or "건물"
        key = f"B{bi + 1:04d}"
        parts = []
        gname = f"{key} {title} {m['floors']}F"
        tag = f"건물::{int(m['floors']):02d}층"      # 스케치업 태그도 Rhino 처럼 층수별 (v2.2)
        battrs = {**{k: str(v) for k, v in at.items()}, "층수(모델)": str(m["floors"]),
                  "높이(m)": str(round(m["top"] - m["base"], 2)), "층수출처": SOURCE_KO.get(m["source"], "추정")}
        fc = m.get("facade") or {}
        fm = facade_meshes(m, floor_height, origin) if fc.get("windows") else None
        walls_done = False
        for part in building_parts(m, floor_height):
            if part[0] == "body" and fm and walls_done:
                continue   # 셋백 건물: 창 메시는 모든 단을 한 번에 담고 있다
            if part[0] == "body" and fm:
                walls_done = True
                for mat, mesh in fm.items():
                    rgb, tr_ = MATERIALS[mat]
                    parts.append((mesh, rgb, mat, 1 - tr_))
                    J.mesh(tag, gname, mesh, rgb, mat, alpha=1 - tr_, attrs=battrs)
                continue
            if part[1] == "ext":
                if fc:
                    mat_ = material_of(m, part[0]); rgb_ = MATERIALS[mat_][0]
                else:
                    mat_, rgb_ = None, (tuple(int(c * 0.85) for c in color) if part[0] == "roof" else color)
                J.ext(tag, gname, _shift(part[2], origin), part[3], part[4], rgb_, mat_, attrs=battrs)
                mm = extrude(part[2], part[3], part[4])
            elif part[1] == "mesh":
                mm = part[2].copy(); mm.apply_translation([-origin[0], -origin[1], 0])
                if fc:
                    mat_ = material_of(m, part[0]); rgb_ = MATERIALS[mat_][0]
                else:
                    mat_, rgb_ = None, (tuple(int(c * 0.85) for c in color) if part[0] == "roof" else color)
                J.mesh(tag, gname, mm, rgb_, mat_, attrs=battrs)
            else:
                continue
            if mm is not None:
                if fc:
                    mat = material_of(m, part[0]); rgb, tr_ = MATERIALS[mat]
                    parts.append((mm, rgb, mat, 1 - tr_))
                else:
                    parts.append((mm, tuple(int(c * 0.85) for c in color) if part[0] == "roof" else color))
        if parts:
            d.add(tag, f"{key} {title} {m['floors']}F", parts)
            info[key] = {**{k: str(v) for k, v in at.items()}, "층수(모델)": str(m["floors"]),
                         "높이(m)": str(round(m["top"] - m["base"], 2)),
                         "층수출처": SOURCE_KO.get(m["source"], "추정"),
                         **({"외벽재료": fc["wall"], "지붕재료": fc["roof"], "창형식": fc["window"],
                             "재료근거": fc["basis"]} if fc else {})}

    if trees and tree_style == "species":
        from .tree_models import FORMS, WOOD, pick
        ids, by = {}, {}
        for x, y, z, r, kind in trees:
            key, (w, lf), sp = pick(x, y, r, kind)
            if key not in ids:
                ids[key] = f"tree{len(ids)}"
                body = d.geometry(w, WOOD, "줄기") + d.geometry(lf, FORMS[key[0]][2], FORMS[key[0]][0])
                J.define(ids[key], FORMS[key[0]][0] + f" r{key[1]:g}", [(w, WOOD, "줄기"), (lf, FORMS[key[0]][2], FORMS[key[0]][0])])
                d.lib_nodes.append(f'<node id="{ids[key]}" name="{_ncname(FORMS[key[0]][0] + f" r{key[1]:g}")}">{body}</node>')
            by.setdefault(kind, []).append(
                f'<node><matrix>{_matrix(1, 1, 1, x - origin[0], y - origin[1], z)}</matrix>'
                f'<instance_node url="#{ids[key]}"/></node>')
            J.place(f"나무::{TREE_KIND[kind]}", ids[key], tr([x - origin[0], y - origin[1], z]))
        for kind, items in by.items():
            d.add_raw(f"나무::{TREE_KIND[kind]}", "".join(items))
    elif trees:
        J.define("crown", "수관", [(_ellipsoid_unit(), (214, 205, 180), "수관")])
        J.define("trunk", "줄기", [(trimesh.creation.cylinder(radius=1.0, height=1.0, sections=6, transform=tr([0, 0, 0.5])),
                                   (150, 130, 110), "줄기")])
        d.library_node("crown", "수관", _ellipsoid_unit(), (214, 205, 180))
        d.library_node("trunk", "줄기", trimesh.creation.cylinder(radius=1.0, height=1.0, sections=6,
                                                                  transform=tr([0, 0, 0.5])), (150, 130, 110))
        by = {}
        for x, y, z, r, kind in trees:
            cz, tr_, rz = tree_dims(r)
            x, y = x - origin[0], y - origin[1]
            by.setdefault(kind, []).append(
                f'<node><matrix>{_matrix(r, r, rz, x, y, z + cz)}</matrix><instance_node url="#crown"/></node>'
                f'<node><matrix>{_matrix(tr_, tr_, cz, x, y, z)}</matrix><instance_node url="#trunk"/></node>')
            J.place(f"나무::{TREE_KIND[kind]}", "crown",
                    trimesh.transformations.concatenate_matrices(tr([x, y, z + cz]), np.diag([r, r, rz, 1.0])))
            J.place(f"나무::{TREE_KIND[kind]}", "trunk",
                    trimesh.transformations.concatenate_matrices(tr([x, y, z]), np.diag([tr_, tr_, cz, 1.0])))
        for kind, items in by.items():
            d.add_raw(f"나무::{TREE_KIND[kind]}", "".join(items))
    if structs:
        from .structures import STRUCT_RGB
        for mesh, kind, name in structs:
            mm = mesh.copy(); mm.apply_translation([-origin[0], -origin[1], 0])
            d.add(f"구조물::{kind}", name, [(mm, STRUCT_RGB.get(kind, (176, 174, 168)))])
            J.mesh(f"구조물::{kind}", name, mm, STRUCT_RGB.get(kind, (176, 174, 168)), kind)
    for mesh, lay, rgb, name in roadbits:
        mm = mesh.copy(); mm.apply_translation([-origin[0], -origin[1], 0])
        d.add(lay, name, [(mm, rgb)])
        J.mesh(lay, name, mm, rgb, name)
    if lights:
        from .streetlights import light_mesh
        ids, by = {}, {}
        for x, y, z, h, ang, kind, src in lights:
            key = (kind, round(h, 1))
            if key not in ids:
                ids[key] = f"lamp{len(ids)}"
                d.library_node(ids[key], f"{kind} {h:g}m", light_mesh(h, kind), (96, 96, 100))
                J.define(ids[key], f"{kind} {h:g}m", [(light_mesh(h, kind), (96, 96, 100), "가로등")])
            c, sn = math.cos(ang), math.sin(ang)
            m = (f"{c:.4f} {-sn:.4f} 0 {x - origin[0]:.3f} {sn:.4f} {c:.4f} 0 {y - origin[1]:.3f} "
                 f"0 0 1 {z:.3f} 0 0 0 1")
            by.setdefault("추정" if src == "추정" else "자료", []).append(
                f'<node><matrix>{m}</matrix><instance_node url="#{ids[key]}"/></node>')
            J.place(f"가로등::{'추정' if src == '추정' else '자료'}", ids[key],
                    [[c, -sn, 0, x - origin[0]], [sn, c, 0, y - origin[1]], [0, 0, 1, z], [0, 0, 0, 1]])
        for grp, items in by.items():
            d.add_raw(f"가로등::{grp}", "".join(items))
    # 검토 대지 표시 (site_overlay.py): 선은 스케치업 모서리(태그 대지::…), 정북일조 사선은 반투명 면
    for it in site_items:
        if it["kind"] == "curve":
            xyz = np.asarray(it["xyz"], float)
            J.line(it["layer"], it["name"], np.column_stack([xyz[:, 0] - origin[0], xyz[:, 1] - origin[1], xyz[:, 2]]))
        else:
            m = it["mesh"].copy(); m.apply_translation([-origin[0], -origin[1], 0])
            J.mesh(it["layer"], it["name"], m, it["rgb"], alpha=0.4, attrs=it["attrs"])
            d.add(it["layer"], it["name"], [(m, it["rgb"], None, 0.4)])
    dae = os.path.join(out_dir, "model_sketchup.dae")
    d.write(dae)
    build = J.write(os.path.join(out_dir, "model_sketchup_build.json"))
    json.dump(info, open(os.path.join(out_dir, "model_sketchup_info.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)
    old = os.path.join(out_dir, "콘타모델_스케치업.rbz")
    if os.path.exists(old):
        os.remove(old)
    write_rbz(os.path.join(out_dir, RBZ_NAME))
    with open(os.path.join(out_dir, "스케치업에서_여는_법.txt"), "w", encoding="utf-8-sig") as fh:
        fh.write(HOWTO)
    with open(os.path.join(out_dir, "스케치업_만들기.rb"), "w", encoding="utf-8") as fh:
        fh.write(BUILDER_RB + f"\nContourAI.build_from({json.dumps(build)}, ENV['CONTOUR_AI_QUIT'] == '1')\n")
    if make_skp:
        make_skp_with_sketchup(out_dir, log)
    return dae
