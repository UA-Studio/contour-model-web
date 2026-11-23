"""스케치업용 출력.

.skp 는 스케치업 프로그램(SDK) 없이는 직접 쓸 수 없어서, 스케치업이 그대로 읽는 COLLADA(.dae)로
레이어 구조를 담아 쓰고, 함께 주는 스케치업 확장(.rbz)이 그것을 불러와 태그·건물 정보를 붙인 뒤
.skp 로 저장한다.

- model_sketchup.dae       : 최상위 그룹 = Rhino 레이어 경로(예: '건물::주택'), 그 안에 건물별 그룹.
                             나무는 수관·줄기 컴포넌트 하나씩을 반복 배치(파일이 가볍다).
- model_sketchup_info.json : 건물 그룹 이름(B0001 …) → 수치지도 속성(명칭·용도·종류·층수 …)
- 스케치업_확장_설치파일(모델아님).rbz : 스케치업 확장. 확장 › 콘타 모델 불러오기 → .dae 선택 → 같은 폴더에 .skp 저장
"""
import json
import math
import os
import zipfile
from xml.sax.saxutils import escape

import numpy as np
import trimesh


def _ncname(s):
    """COLLADA 이름 규격(xs:NCName)에 맞게: 띄어쓰기·'::'·괄호 등은 '_' 로, 숫자로 시작하면 앞에 '_'.
    레이어 경로의 '::' 는 '__' 가 된다(스케치업 확장이 다시 '::' 로 읽는다)."""
    import re
    s = str(s).replace("::", "__")
    s = re.sub(r"[^\w.\-]", "_", s, flags=re.UNICODE)
    if not s or not (s[0].isalpha() or s[0] == "_"):
        s = "_" + s
    return s


def _f(a):
    return " ".join(map(str, np.round(np.ravel(a), 3).tolist()))


def _clean(mesh, tol=0.002):
    """스케치업이 못 만드는 면을 미리 정리: 2mm 안에 겹친 점은 하나로, 넓이가 거의 0인 삼각형은 뺀다.
    (스케치업은 아주 짧은 모서리의 면을 버려서 구멍이 생긴다)"""
    v = np.round(np.asarray(mesh.vertices, float), 3)    # 파일에 쓰는 정밀도(1mm)로 먼저 맞춘다
    f = np.asarray(mesh.faces, int)
    if not len(f):
        return v, f
    key = np.round(v / tol).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    v = v[first]
    f = inv.reshape(-1)[f]
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    area = np.linalg.norm(np.cross(b - a, c - a), axis=1) / 2
    keep = (f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2]) & (area > 1e-6)
    return v, f[keep]


class Dae:
    def __init__(self):
        self.mats, self.geoms, self.lib_nodes = {}, [], []
        self.tree = {}          # 레이어 경로 → [(이름, [geom_id…] 또는 하위 목록)]
        self.n = 0

    def material(self, rgb, name=None, alpha=1.0):
        key = (tuple(int(c) for c in rgb), name, round(alpha, 3))
        if key not in self.mats:
            self.mats[key] = f"mat{len(self.mats)}"
        return self.mats[key]

    def geometry(self, mesh, rgb, name=None, alpha=1.0):
        self.n += 1
        gid, mid = f"g{self.n}", self.material(rgb, name, alpha)
        v, f = _clean(mesh)
        if not len(f):
            return ""
        self.geoms.append(
            f'<geometry id="{gid}"><mesh>'
            f'<source id="{gid}p"><float_array id="{gid}pa" count="{v.size}">{_f(v)}</float_array>'
            f'<technique_common><accessor source="#{gid}pa" count="{len(v)}" stride="3">'
            f'<param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>'
            f'</accessor></technique_common></source>'
            f'<vertices id="{gid}v"><input semantic="POSITION" source="#{gid}p"/></vertices>'
            f'<triangles material="m" count="{len(f)}"><input semantic="VERTEX" source="#{gid}v" offset="0"/>'
            f'<p>{" ".join(map(str, f.ravel()))}</p></triangles></mesh></geometry>')
        return f'<instance_geometry url="#{gid}"><bind_material><technique_common>' \
               f'<instance_material symbol="m" target="#{mid}"/></technique_common></bind_material></instance_geometry>'

    def add(self, layer, name, meshes_rgb):
        """layer 그룹 안에 name 그룹 하나(여러 메시)를 넣는다."""
        body = "".join(self.geometry(*x) for x in meshes_rgb if len(x[0].faces))
        if body:
            self.tree.setdefault(layer, []).append(f'<node name="{_ncname(name)}">{body}</node>')

    def add_raw(self, layer, xml):
        self.tree.setdefault(layer, []).append(xml)

    def library_node(self, nid, name, mesh, rgb):
        self.lib_nodes.append(f'<node id="{nid}" name="{_ncname(name)}">{self.geometry(mesh, rgb)}</node>')

    def write(self, path):
        eff = "".join(
            f'<effect id="{m}e"><profile_COMMON><technique sid="c"><lambert><diffuse>'
            f'<color>{r / 255:.3f} {g / 255:.3f} {b / 255:.3f} 1</color></diffuse>'
            + (f'<transparent opaque="A_ONE"><color>1 1 1 {al:.3f}</color></transparent>'
               f'<transparency><float>1</float></transparency>' if al < 1 else '')
            + f'</lambert></technique></profile_COMMON></effect>' for ((r, g, b), nm, al), m in self.mats.items())
        mats = "".join(f'<material id="{m}" name="{_ncname(nm) if nm else m}"><instance_effect url="#{m}e"/></material>'
                       for (_, nm, _), m in self.mats.items())
        scene = "".join(f'<node name="{_ncname(layer)}">{"".join(items)}</node>' for layer, items in self.tree.items())
        import time
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('<?xml version="1.0" encoding="utf-8"?>\n'
                     '<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">'
                     '<asset><contributor><authoring_tool>contour_ai</authoring_tool></contributor>'
                     f'<created>{now}</created><modified>{now}</modified>'
                     '<unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>'
                     f'<library_effects>{eff}</library_effects><library_materials>{mats}</library_materials>'
                     f'<library_geometries>{"".join(self.geoms)}</library_geometries>'
                     + (f'<library_nodes>{"".join(self.lib_nodes)}</library_nodes>' if self.lib_nodes else '') +
                     f'<library_visual_scenes><visual_scene id="scene" name="contour">{scene}</visual_scene>'
                     '</library_visual_scenes><scene><instance_visual_scene url="#scene"/></scene></COLLADA>')


def _ellipsoid_unit(seg=12, rings=8):
    """반지름 1 의 낮은 해상도 구(스케치업에서 가볍게). 배치할 때 (r, r, rz) 로 늘인다."""
    return trimesh.creation.uv_sphere(radius=1.0, count=[rings, seg])


def _matrix(sx, sy, sz, tx, ty, tz):
    return f"{sx:.3f} 0 0 {tx:.3f} 0 {sy:.3f} 0 {ty:.3f} 0 0 {sz:.3f} {tz:.3f} 0 0 0 1"


class SkpJson:
    """스케치업 안에서 '진짜 면'으로 다시 그리기 위한 자료(model_sketchup_build.json).
    판·건물은 바닥 다각형(구멍 포함) + 높이로 적어 스케치업이 면을 만들고 밀어 올린다(삼각형으로 쪼개지지 않음).
    곡면(대지·지붕 장식·창)은 메시로, 나무·가로등은 컴포넌트 하나를 여러 번 놓는다."""
    def __init__(self, origin):
        self.origin = origin
        self.groups, self.defs, self.inst, self.lines = {}, {}, [], []

    @staticmethod
    def _loops(p):
        p = p.simplify(0.01, preserve_topology=True)
        if p.is_empty or p.geom_type != "Polygon" or p.area < 0.05:
            return None
        def ring(r):
            c = np.round(np.asarray(r.coords)[:-1, :2], 3)
            return c.ravel().tolist() if len(c) >= 3 else None
        out = [ring(p.exterior)] + [ring(h) for h in p.interiors if h.length > 0.05]
        return [r for r in out if r] if out[0] else None

    def _grp(self, layer, name, attrs=None):
        key = (layer, name)
        if key not in self.groups:
            self.groups[key] = dict(tag=layer, name=name, items=[], attrs=attrs or {})
        elif attrs:
            self.groups[key]["attrs"].update(attrs)
        return self.groups[key]["items"]

    def ext(self, layer, name, poly, z0, z1, rgb, mat=None, attrs=None):
        """poly: 이미 원점을 뺀 다각형(또는 여러 개)"""
        items = self._grp(layer, name, attrs)
        for g in getattr(poly, "geoms", [poly]):
            lp = self._loops(g)
            if lp and z1 - z0 > 0.005:
                items.append(dict(t="ext", loops=lp, z0=round(float(z0), 3), z1=round(float(z1), 3),
                                  rgb=[int(c) for c in rgb], mat=mat))

    def mesh(self, layer, name, mesh, rgb, mat=None, soft=False, attrs=None, alpha=1.0):
        v, f = _clean(mesh)
        if len(f):
            self._grp(layer, name, attrs).append(dict(t="mesh", v=np.round(v, 3).ravel().tolist(), f=f.ravel().tolist(),
                                                      rgb=[int(c) for c in rgb], mat=mat, soft=bool(soft),
                                                      alpha=round(float(alpha), 3)))

    def define(self, did, name, parts):
        if did in self.defs:
            return
        ms = []
        for mesh, rgb, mat in parts:
            v, f = _clean(mesh)
            if len(f):
                ms.append(dict(v=np.round(v, 3).ravel().tolist(), f=f.ravel().tolist(), rgb=[int(c) for c in rgb], mat=mat))
        self.defs[did] = dict(name=name, meshes=ms)

    def place(self, layer, did, m4):
        """m4: 4×4 행 순서 → 스케치업은 열 순서로 받는다"""
        self.inst.append([layer, did, [round(float(x), 4) for x in np.asarray(m4, float).T.ravel()]])

    def line(self, layer, name, xyz):
        self.lines.append(dict(tag=layer, name=name, p=np.round(np.asarray(xyz, float), 3).ravel().tolist()))

    def write(self, path):
        doc = dict(format="contour_ai sketchup build", version=1, unit="m", origin=[float(self.origin[0]), float(self.origin[1])],
                   groups=list(self.groups.values()), defs=self.defs, instances=self.inst, lines=self.lines)
        # json.dump(파일) 은 파이썬으로 한 조각씩 쓰느라 느리다(580m 에서 8초). dumps 는 C 로 한 번에 만든다 — 내용은 같다.
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
        return path


def write_sketchup(out_dir, regions, masses, origin, terrain_mesh=None, area_solids=(), inlays=(),
                   areas=(), trees=(), steps_too=False, floor_height=3.3, lights=(), structs=(),
                   tree_style="species", roadbits=(), site_lines=(), log=print, make_skp=True):
    """out_dir 에 model_sketchup.dae · model_sketchup_build.json · model_sketchup_info.json · 확장 설치파일(.rbz)
    · 여는 법(.txt) 을 쓰고, 이 PC 에 스케치업이 있으면 스케치업을 띄워 model_sketchup.skp(2021 형식)까지 만든다."""
    from .building_detail import building_parts
    from .contour_model import SOURCE_KO
    from .facade import MATERIALS, facade_meshes, material_of
    from .contour_model import ROLE_KO, USE_COLORS, _shift, as_polys, cut_regions
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
        battrs = {**{k: str(v) for k, v in at.items()}, "층수(모델)": str(m["floors"]),
                  "높이(m)": str(round(m["top"] - m["base"], 2)), "층수출처": SOURCE_KO.get(m["source"], "추정")}
        fc = m.get("facade") or {}
        fm = facade_meshes(m, floor_height, origin) if fc.get("windows") else None
        for part in building_parts(m, floor_height):
            if part[0] == "body" and fm:
                for mat, mesh in fm.items():
                    rgb, tr_ = MATERIALS[mat]
                    parts.append((mesh, rgb, mat, 1 - tr_))
                    J.mesh(f"건물::{use}", gname, mesh, rgb, mat, alpha=1 - tr_, attrs=battrs)
                continue
            if part[1] == "ext":
                if fc:
                    mat_ = material_of(m, part[0]); rgb_ = MATERIALS[mat_][0]
                else:
                    mat_, rgb_ = None, (tuple(int(c * 0.85) for c in color) if part[0] == "roof" else color)
                J.ext(f"건물::{use}", gname, _shift(part[2], origin), part[3], part[4], rgb_, mat_, attrs=battrs)
                mm = extrude(part[2], part[3], part[4])
            elif part[1] == "mesh":
                mm = part[2].copy(); mm.apply_translation([-origin[0], -origin[1], 0])
                if fc:
                    mat_ = material_of(m, part[0]); rgb_ = MATERIALS[mat_][0]
                else:
                    mat_, rgb_ = None, (tuple(int(c * 0.85) for c in color) if part[0] == "roof" else color)
                J.mesh(f"건물::{use}", gname, mm, rgb_, mat_, attrs=battrs)
            else:
                continue
            if mm is not None:
                if fc:
                    mat = material_of(m, part[0]); rgb, tr_ = MATERIALS[mat]
                    parts.append((mm, rgb, mat, 1 - tr_))
                else:
                    parts.append((mm, tuple(int(c * 0.85) for c in color) if part[0] == "roof" else color))
        if parts:
            d.add(f"건물::{use}", f"{key} {title} {m['floors']}F", parts)
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
    for name, xyz in site_lines:
        J.line("대지::경계", name, np.column_stack([np.asarray(xyz)[:, 0] - origin[0], np.asarray(xyz)[:, 1] - origin[1],
                                                 np.asarray(xyz)[:, 2]]))
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


# ---------------------------------------------------------------- 스케치업 확장(.rbz)
RBZ_NAME = "스케치업_확장_설치파일(모델아님).rbz"
HOWTO = """스케치업에서 콘타 모델 여는 법

이 폴더의 .rbz 파일은 모델이 아니라 '불러오기 확장'을 설치하는 파일입니다(처음 한 번만).
스케치업 모델은 model_sketchup.dae 에 들어 있고, 확장으로 불러오면 같은 폴더에 model_sketchup.skp 가 저장됩니다.

[처음 한 번] 확장 설치
  스케치업 > 확장 프로그램(Extensions) > Extension Manager > Install Extension
  > 스케치업_확장_설치파일(모델아님).rbz 선택

[매번] 모델 불러오기
  확장 프로그램 > 콘타 모델 > 콘타 모델 불러오기 (.dae → .skp) > model_sketchup.dae 선택
  - 레이어 구조대로 태그(지형 / 길 / 건물 > 용도 / 나무)가 붙고, 건물마다 수치지도 정보가 들어갑니다.
  - 대지·경사면·곡면 위 길의 삼각형 모서리는 부드럽게 숨깁니다.
  - 같은 폴더에 model_sketchup.skp 로 저장됩니다.

확장 없이 열려면: 파일 > 가져오기(Import) > COLLADA(*.dae) > model_sketchup.dae
  (이때는 태그·건물 정보가 붙지 않고, 대지의 삼각형 모서리가 모두 선으로 보입니다)
"""

LOADER_RB = '''# encoding: UTF-8
require 'sketchup.rb'
require 'extensions.rb'

module ContourAI
  EXT = SketchupExtension.new('콘타 모델 불러오기', 'contour_ai_importer/main')
  EXT.description = 'contour_ai 로 만든 model_sketchup.dae 를 태그·건물 정보와 함께 불러와 .skp 로 저장'
  EXT.version = '1.0'
  EXT.creator = 'contour_ai'
  Sketchup.register_extension(EXT, true)
end
'''

MAIN_RB = '''# encoding: UTF-8
require 'json'
require 'contour_ai_importer/builder'

module ContourAI
  def self.entity_name(e)
    n = e.name.to_s
    n = e.definition.name.to_s if n.empty? && e.respond_to?(:definition)
    n.sub(/#\\d+\\z/, '').gsub('__', '::')
  end

  def self.tag_for(model, path)
    parts = path.split('::')
    name = parts.join(' > ')
    tag = model.layers[name] || model.layers.add(name)
    if parts.size > 1 && model.layers.respond_to?(:add_folder)
      begin
        folder = model.layers.folders.find { |f| f.name == parts[0] } || model.layers.add_folder(parts[0])
        tag.folder = folder
      rescue StandardError
      end
    end
    tag
  end

  def self.children(e)
    ents = e.respond_to?(:definition) ? e.definition.entities : e.entities
    ents.select { |c| c.is_a?(Sketchup::Group) || c.is_a?(Sketchup::ComponentInstance) }
  end

  # 곡면(대지·경사매스·곡면 위 길·나무)의 삼각형 모서리를 부드럽게 숨긴다. 꺾인 모서리(20° 넘게)는 그대로.
  def self.soften(ents, depth = 0)
    return if depth > 4
    ents.each do |e|
      if e.is_a?(Sketchup::Edge)
        f = e.faces
        if f.size == 2 && f[0].normal.angle_between(f[1].normal) < 20.degrees
          e.soft = true
          e.smooth = true
        end
      elsif e.is_a?(Sketchup::Group)
        soften(e.entities, depth + 1)
      elsif e.is_a?(Sketchup::ComponentInstance)
        soften(e.definition.entities, depth + 1)
      end
    end
  end

  def self.import_model
    path = UI.openpanel('콘타 모델 선택 (model_sketchup.dae)', '', 'COLLADA|*.dae||')
    return unless path
    model = Sketchup.active_model
    info_path = File.join(File.dirname(path), 'model_sketchup_info.json')
    info = File.exist?(info_path) ? JSON.parse(File.read(info_path, encoding: 'UTF-8')) : {}
    model.start_operation('콘타 모델 불러오기', true)
    defn = model.definitions.import(path)
    unless defn
      model.abort_operation
      UI.messagebox('불러오지 못했습니다: ' + path)
      return
    end
    inst = model.active_entities.add_instance(defn, Geom::Transformation.new)
    tops = inst.explode.select { |e| e.is_a?(Sketchup::Group) || e.is_a?(Sketchup::ComponentInstance) }
    n_bld = 0
    tops.each do |top|
      layer_path = entity_name(top)
      next if layer_path.empty?
      top.name = layer_path.split('::').join(' > ')
      top.layer = tag_for(model, layer_path)
      if layer_path.start_with?('지형') || layer_path.include?('곡면대지') || layer_path.start_with?('나무')
        soften(top.respond_to?(:definition) ? top.definition.entities : top.entities)
      end
      next unless layer_path.start_with?('건물')
      children(top).each do |b|
        key = entity_name(b).split('_').first
        b.name = entity_name(b).tr('_', ' ').strip
        attrs = info[key]
        next unless attrs
        attrs.each { |k, v| b.set_attribute('수치지도', k, v) }
        n_bld += 1
      end
    end
    model.commit_operation
    skp = path.sub(/\\.dae\\z/i, '.skp')
    model.save(skp)
    UI.messagebox("불러왔습니다. 태그 #{tops.size}개, 건물 정보 #{n_bld}동.\\n저장: #{skp}\\n" \\
                  "건물을 선택하고 '엔터티 정보' 또는 확장의 속성 보기로 수치지도 정보를 볼 수 있습니다.")
  end

  def self.show_info
    e = Sketchup.active_model.selection.first
    return UI.messagebox('건물 하나를 선택하세요.') unless e
    d = e.attribute_dictionary('수치지도')
    return UI.messagebox('수치지도 정보가 없는 개체입니다.') unless d
    UI.messagebox(d.map { |k, v| "#{k}: #{v}" }.join("\\n"))
  end

  unless file_loaded?(__FILE__)
    menu = UI.menu('Extensions').add_submenu('콘타 모델')
    menu.add_item('콘타 모델 만들기 (.json → .skp, 편집용 면)') do
      path = UI.openpanel('model_sketchup_build.json 선택', '', 'JSON|*.json||')
      if path
        build_from(path, false)
        log = path.sub(/\\.json\\z/i, '.log')
        UI.messagebox(File.exist?(log) ? File.read(log, encoding: 'UTF-8') : '끝났습니다')
      end
    end
    menu.add_item('콘타 모델 불러오기 (.dae → .skp)') { import_model }
    menu.add_item('선택한 건물 정보 보기') { show_info }
    file_loaded(__FILE__)
  end
end
'''


def write_rbz(path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("contour_ai_importer.rb", LOADER_RB.encode("utf-8"))
        z.writestr("contour_ai_importer/main.rb", MAIN_RB.encode("utf-8"))
        z.writestr("contour_ai_importer/builder.rb", BUILDER_RB.encode("utf-8"))
    return path

# ---------------------------------------------------------------- 스케치업 안에서 진짜 면으로 만들기
BUILDER_RB = '''# encoding: UTF-8
# contour_ai: model_sketchup_build.json 을 읽어 스케치업 안에서 '진짜 면'으로 모델을 만들고 .skp(2021 형식)로 저장한다.
# 판·건물은 바닥 면을 그리고 밀어 올려(Push/Pull) 만든다 → 삼각형으로 쪼개지지 않아 그대로 편집할 수 있다.
require 'json'

module ContourAI
  M = 39.37007874015748   # m → 스케치업 내부 단위(inch)

  def self.tag_for(model, path)
    @tags ||= {}
    return @tags[path] if @tags[path] && @tags[path].valid?
    parts = path.split('::')
    tag = model.layers[parts.join(' > ')] || model.layers.add(parts.join(' > '))
    if parts.size > 1 && model.layers.respond_to?(:add_folder)
      begin
        folder = model.layers.folders.find { |f| f.name == parts[0] } || model.layers.add_folder(parts[0])
        tag.folder = folder
      rescue StandardError
      end
    end
    @tags[path] = tag
  end

  def self.mat_for(model, rgb, name, alpha = 1.0)
    key = (name && !name.to_s.empty?) ? name.to_s : "색 #{rgb.join(',')}"
    key = "#{key} #{(alpha * 100).round}%" if alpha && alpha < 0.999
    @mats ||= {}
    return @mats[key] if @mats[key] && @mats[key].valid?
    m = model.materials[key]
    unless m
      m = model.materials.add(key)
      m.color = Sketchup::Color.new(*rgb)
      m.alpha = alpha if alpha && alpha < 0.999
    end
    @mats[key] = m
  end

  def self.pts(flat, z)
    flat.each_slice(2).map { |x, y| Geom::Point3d.new(x * M, y * M, z * M) }
  end

  # 바닥 다각형(구멍 포함) → 면 → 밀어 올리기. 그룹 하나로.
  def self.add_ext(ents, it, mat)
    g = ents.add_group
    e = g.entities
    loops = it['loops']
    face = e.add_face(pts(loops[0], it['z0']))
    unless face
      g.erase!
      @fails += 1
      return nil
    end
    loops[1..-1].each do |h|
      begin
        hf = e.add_face(pts(h, it['z0']))
        hf.erase! if hf && hf.valid? && hf != face
      rescue ArgumentError
      end
    end
    face = e.grep(Sketchup::Face).max_by(&:area) unless face.valid?
    face.reverse! if face.normal.z < 0
    face.pushpull((it['z1'] - it['z0']) * M)
    g.material = mat
    @n_ext += 1
    g
  rescue ArgumentError, TypeError
    @fails += 1
    g.erase! if g && g.valid?
    nil
  end

  def self.fill_mesh(ents, it, mat)
    v = it['v']
    f = it['f']
    pm = Geom::PolygonMesh.new(v.size / 3, f.size / 3)
    idx = []
    v.each_slice(3) { |x, y, z| idx << pm.add_point(Geom::Point3d.new(x * M, y * M, z * M)) }
    f.each_slice(3) do |a, b, c|
      next if idx[a] == idx[b] || idx[b] == idx[c] || idx[a] == idx[c]
      begin
        pm.add_polygon(idx[a], idx[b], idx[c])
      rescue ArgumentError
      end
    end
    flags = it['soft'] ? 12 : 0   # 곡면: 모서리 부드럽게(AUTO_SOFTEN + SMOOTH_SOFT_EDGES)
    if ents.respond_to?(:fill_from_mesh)
      ents.fill_from_mesh(pm, true, flags, mat, mat)
    else
      ents.add_faces_from_mesh(pm, flags, mat, mat)
    end
    merge_coplanar(ents) unless it['soft']
  end

  def self.add_mesh(ents, it, mat)
    g = ents.add_group
    fill_mesh(g.entities, it, mat)
    @n_mesh += 1
    g
  end

  # 같은 평면·같은 재료로 이어진 삼각형 사이 선을 지워 한 면으로 (편집하기 쉽게)
  def self.merge_coplanar(ents)
    ents.grep(Sketchup::Edge).each do |e|
      next unless e.valid?
      fs = e.faces
      next unless fs.size == 2
      n0 = fs[0].normal
      n1 = fs[1].normal
      next unless n0.samedirection?(n1)
      next unless fs[0].material == fs[1].material
      e.erase!
    end
  end

  def self.build_from(json_path, quit = false)
    t0 = Time.now
    log_path = json_path.sub(/\\.json\\z/i, '.log')
    skp = File.join(File.dirname(json_path), 'model_sketchup.skp')
    @fails = 0
    @n_ext = 0
    @n_mesh = 0
    @tags = {}
    @mats = {}
    begin
      data = JSON.parse(File.read(json_path, encoding: 'UTF-8'))
      model = Sketchup.active_model
      model.start_operation('콘타 모델 만들기', true)
      begin
        model.options['UnitsOptions']['LengthUnit'] = 4      # 미터
        model.options['UnitsOptions']['LengthPrecision'] = 3
      rescue StandardError
      end
      tops = {}
      top_for = lambda do |tag|
        tops[tag] ||= begin
          t = model.entities.add_group
          t.name = tag.split('::').join(' > ')
          t.layer = tag_for(model, tag)
          t
        end
      end
      n_grp = 0
      data['groups'].each do |gd|
        top = top_for.call(gd['tag'])
        g = top.entities.add_group
        g.name = gd['name'].to_s
        (gd['attrs'] || {}).each { |k, v| g.set_attribute('수치지도', k, v) }
        gd['items'].each do |it|
          mat = mat_for(model, it['rgb'], it['mat'], it['alpha'] || 1.0)
          it['t'] == 'ext' ? add_ext(g.entities, it, mat) : add_mesh(g.entities, it, mat)
        end
        g.erase! if g.entities.length == 0
        n_grp += 1
      end
      defs = {}
      (data['defs'] || {}).each do |id, d|
        cd = model.definitions.add(d['name'].to_s)
        d['meshes'].each { |mm| fill_mesh(cd.entities, mm.merge('soft' => true), mat_for(model, mm['rgb'], mm['mat'])) }
        defs[id] = cd
      end
      n_inst = 0
      (data['instances'] || []).each do |tag, id, m|
        cd = defs[id]
        next unless cd
        a = m.dup
        a[12] *= M
        a[13] *= M
        a[14] *= M
        top_for.call(tag).entities.add_instance(cd, Geom::Transformation.new(a))
        n_inst += 1
      end
      (data['lines'] || []).each do |ln|
        p = ln['p'].each_slice(3).map { |x, y, z| Geom::Point3d.new(x * M, y * M, z * M) }
        top_for.call(ln['tag']).entities.add_curve(p) if p.size >= 2
      end
      model.commit_operation
      Sketchup.active_model.active_view.zoom_extents rescue nil
      ver = nil
      %w[VERSION_2021 VERSION_2020 VERSION_2019 VERSION_2018 VERSION_2017].each do |c|
        if Sketchup::Model.const_defined?(c)
          ver = Sketchup::Model.const_get(c)
          break
        end
      end
      ok = ver ? model.save(skp, ver) : model.save(skp)
      File.write(log_path, "#{ok ? 'ok' : 'save-failed'}\\t#{skp}\\tgroups=#{n_grp}\\text=#{@n_ext}\\tmesh=#{@n_mesh}\\t" \\
                           "instances=#{n_inst}\\tfails=#{@fails}\\tseconds=#{(Time.now - t0).round}\\n")
    rescue StandardError => ex
      File.write(log_path, "error\\t#{ex.class}: #{ex.message}\\n#{(ex.backtrace || [])[0, 8].join("\\n")}\\n") rescue nil
      UI.messagebox("콘타 모델을 만들지 못했습니다: #{ex.message}") unless quit
    end
    Sketchup.quit if quit
  end
end
'''


def find_sketchup():
    """이 PC 의 SketchUp.exe (가장 새 판). 환경 변수 CONTOUR_SKETCHUP 로 직접 지정할 수도 있다."""
    import glob
    env = os.environ.get("CONTOUR_SKETCHUP")
    if env and os.path.isfile(env):
        return env
    found = []
    for base in {os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles"), r"C:\Program Files"}:
        if not base:
            continue
        found += glob.glob(os.path.join(base, "SketchUp", "SketchUp 20*", "SketchUp.exe"))
        found += glob.glob(os.path.join(base, "SketchUp", "SketchUp.exe"))
    return sorted(found)[-1] if found else None


def make_skp_with_sketchup(out_dir, log=print, timeout=3600):
    """이 PC 의 스케치업을 띄워 model_sketchup_build.json → model_sketchup.skp(2021 형식, 2021~2022 이하에서 열림)."""
    import subprocess
    import time
    build = os.path.join(out_dir, "model_sketchup_build.json")
    if os.name != "nt":
        log("   스케치업 .skp: 윈도우에서 스케치업이 깔려 있으면 자동으로 만듭니다 (지금은 건너뜀)")
        return None
    exe = find_sketchup()
    if not exe:
        log("   스케치업 .skp: 이 PC 에서 스케치업을 찾지 못했습니다. 스케치업을 연 뒤 확장 › 콘타 모델 › "
            "'콘타 모델 만들기 (.json → .skp)'로 model_sketchup_build.json 을 고르면 됩니다")
        return None
    skp = os.path.join(out_dir, "model_sketchup.skp")
    status = os.path.splitext(build)[0] + ".log"
    for f in (skp, status):
        if os.path.exists(f):
            os.remove(f)
    # 한글 경로를 스케치업 시작 인자로 넘기지 않도록, 실행 스크립트는 영문 경로(공용 폴더)에 둔다
    run_dir = os.path.join(os.environ.get("PUBLIC") or os.environ.get("TEMP") or out_dir, "contour_ai")
    os.makedirs(run_dir, exist_ok=True)
    run_rb = os.path.join(run_dir, "build_skp.rb")
    with open(run_rb, "w", encoding="utf-8") as fh:
        fh.write(BUILDER_RB + f"\nUI.start_timer(2, false) {{ ContourAI.build_from({json.dumps(build)}, true) }}\n")
    log(f"   스케치업으로 .skp 만드는 중 ({os.path.basename(os.path.dirname(exe))}). "
        "스케치업 창이 잠깐 열렸다 닫힙니다. 시작 화면이 뜨면 닫아 주세요")
    t0 = time.time()
    p = subprocess.Popen([exe, "-RubyStartup", run_rb])
    while time.time() - t0 < timeout:
        if os.path.exists(status):
            break
        if p.poll() is not None:
            break
        time.sleep(2)
    if not os.path.exists(status):
        log("   스케치업 .skp: 스케치업이 끝났는데 결과가 없습니다. 스케치업에서 확장 › 콘타 모델 › "
            "'콘타 모델 만들기 (.json → .skp)'로 model_sketchup_build.json 을 직접 열어 주세요")
        return None
    res = open(status, encoding="utf-8", errors="replace").read().strip()
    if res.startswith("ok") and os.path.exists(skp):
        kv = dict(x.split("=", 1) for x in res.split("\t")[2:] if "=" in x)
        log(f"   스케치업 파일을 만들었습니다: model_sketchup.skp (2021 형식 · 그룹 {kv.get('groups')} · 밀어 올린 면 {kv.get('ext')}"
            f" · 나무·가로등 {kv.get('instances')}개 · {kv.get('seconds')}초)")
        try:
            p.wait(60)
        except Exception:
            pass
        return skp
    log(f"   스케치업 .skp 를 만들지 못했습니다: {res[:300]}")
    return None
