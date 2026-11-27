"""스케치업 출력 바탕: COLLADA(.dae) 쓰기(Dae)와 스케치업 확장이 읽는 건물 JSON(SkpJson)."""
import json

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
        with open(path, "w", encoding="utf-8") as fh:
            # json.dump 는 파이썬으로 한 조각씩 써서 1500m 모델(약 80MB)에서 40초 걸렸다. dumps 는 C 로 한 번에 만든다 (내용 같음)
            fh.write(json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
        return path
