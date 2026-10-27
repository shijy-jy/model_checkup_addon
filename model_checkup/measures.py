"""测量层: 读取 Blender 数据并计算指标, 不做任何判定。

每个 check_* 接收一个对象, 返回纯数据字典; 结果判断由 rules 层完成。

参考资料
    Unity Manual: Mesh.indexFormat / Mesh data / Lightmap UVs / Quality Settings
    glTF 2.0: Skins / Node transform
    数学: 重心坐标、散度定理、并查集
"""
import math
import os

import bpy
import bmesh
from mathutils import Vector

try:
    import numpy as np
except Exception:
    np = None

from .constants import THRESHOLDS


def _ratio(a, b):
    """计算百分比 a/b*100, 保留两位; 分母为 0 时返回 0。"""
    return round(100.0 * a / b, 2) if b else 0.0


def world_matrix(obj):
    """构造对象的世界矩阵。

    实现: 沿父链左乘 M_parent @ M_parent_inverse @ M_basis
    说明: 部分启动方式(如 blender -b)下 matrix_world 在 open_mainfile 后可能保持单位矩阵,
          渲染却使用真实变换, 故不直接读取 matrix_world
    参考: 仿射变换的复合; Blender API, matrix_basis / matrix_parent_inverse
    返回: mathutils.Matrix
    调用方: _world_bbox(), 需要世界坐标的测量
    """
    m = obj.matrix_basis.copy()
    p = obj.parent
    while p is not None:
        m = p.matrix_basis @ p.matrix_parent_inverse @ m
        p = p.parent
    return m


def _world_bbox(obj, cap=200_000):
    """计算世界坐标包围盒。

    实现: 顶点数 <= cap 时逐点变换取极值; 超过 cap 时变换局部包围盒的 8 个角点
    参考: 仿射变换保持凸包, 8 角点变换后仍包围全部顶点
    返回: ((xmin, xmax, ymin, ymax, zmin, zmax), 是否近似)
    调用方: check_mesh()
    """
    me = obj.data
    mw = world_matrix(obj)
    n = len(me.vertices)
    if n == 0:
        return None, True
    if n <= cap:
        xs, ys, zs = [], [], []
        for v in me.vertices:
            w = mw @ v.co
            xs.append(w.x)
            ys.append(w.y)
            zs.append(w.z)
        return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)), False
    xs = [v.co.x for v in me.vertices]
    ys = [v.co.y for v in me.vertices]
    zs = [v.co.z for v in me.vertices]
    corners = [mw @ Vector((x, y, z))
               for x in (min(xs), max(xs)) for y in (min(ys), max(ys)) for z in (min(zs), max(zs))]
    return (min(c.x for c in corners), max(c.x for c in corners),
            min(c.y for c in corners), max(c.y for c in corners),
            min(c.z for c in corners), max(c.z for c in corners)), True


def check_mesh(obj):
    """统计网格拓扑与几何信息。

    输入: Mesh 顶点/边/面; 经 bmesh 取得边-面关联关系
    实现: 三角形数取 Blender 的三角化结果 mesh.loop_triangles; N 边形 = 顶点数 > 4 的面;
          松散顶点 = 无关联边; 非流形边 = 关联面数 > 2; 包围盒取世界坐标
    返回: vertices, edges, faces, tris, ngons, loose_verts, non_manifold_edges, dimensions
    参考:
        Unity Mesh.indexFormat: 16 位索引, 单网格 65535 顶点
    调用方: _rule_geometry(), _rule_budget(), 面板
    """
    me = obj.data
    me.calc_loop_triangles()
    r = {
        "object": obj.name,
        "vertices": len(me.vertices),
        "edges": len(me.edges),
        "faces": len(me.polygons),
        "tris": len(me.loop_triangles),
        "ngons": sum(1 for p in me.polygons if len(p.vertices) > 4),
    }

    bm = bmesh.new()
    bm.from_mesh(me)
    try:
        bm.verts.ensure_lookup_table()
        r["loose_verts"] = sum(1 for v in bm.verts if not v.link_edges)
        r["non_manifold_edges"] = sum(1 for e in bm.edges if len(e.link_faces) > 2)
    finally:
        bm.free()

    bbox, _approx = _world_bbox(obj)
    if bbox:
        x0, x1, y0, y1, z0, z1 = bbox
        r["dimensions"] = [round(x1 - x0, 4), round(y1 - y0, 4), round(z1 - z0, 4)]
    return r


def check_uv(obj, size=512, max_tris=20000):
    """统计 UV 越界与重叠率。

    输入: 活动 UV 层、三角化后的 loop 数据
    实现: 逐 loop 判断 0-1 越界; UV 三角形按重心坐标光栅化到 N x N 网格并累加覆盖次数;
          覆盖次数 >= 2 的格计入重叠
    返回: layers, outside_ratio, overlap_ratio, raster, sampled_tris
    精度: 重叠率为网格采样估计, 误差取决于栅格尺寸和三角形采样上限, 不作为精确面积
    参考:
        Unity Lightmap UVs: 光照贴图 UV 不可重叠
        重心坐标判内点; 重叠率为网格积分估计, 误差 O(1/N)
    调用方: _rule_uv()
    """
    me = obj.data
    r = {"layers": [l.name for l in me.uv_layers], "raster": size, "sampled_tris": 0}
    if not me.uv_layers:
        return r
    if np is None:
        r["error"] = "numpy 不可用，跳过 UV 栅格"
        return r

    uv = me.uv_layers.active.data
    loops = len(uv)
    if loops != len(me.loops) or loops == 0:
        # 编辑模式或数据异常时 UV 层可能为空, 记录后跳过栅格统计
        r["error"] = "UV 数据长度异常（loops=%d, uv=%d），跳过栅格统计" % (len(me.loops), loops)
        return r
    outside = 0
    for i in range(loops):
        u, v = uv[i].uv
        if u < -1e-4 or u > 1 + 1e-4 or v < -1e-4 or v > 1 + 1e-4:
            outside += 1
    r["loops"] = loops
    r["outside_ratio"] = _ratio(outside, loops)

    me.calc_loop_triangles()
    tris = me.loop_triangles
    step = max(1, len(tris) // max_tris)
    grid = np.zeros((size, size), dtype=np.int16)
    sampled = 0
    for ti in range(0, len(tris), step):      # bpy 集合不支持带步长的切片，只能按索引取
        t = tris[ti]
        co = [uv[li].uv for li in t.loops]
        xs = [c[0] for c in co]
        ys = [c[1] for c in co]
        x0 = max(0, int(min(xs) * (size - 1)))
        x1 = min(size - 1, int(max(xs) * (size - 1)) + 1)
        y0 = max(0, int(min(ys) * (size - 1)))
        y1 = min(size - 1, int(max(ys) * (size - 1)) + 1)
        if x1 < x0 or y1 < y0:
            continue
        sampled += 1
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) / (size - 1), np.arange(y0, y1 + 1) / (size - 1))
        ax, ay = xs[0], ys[0]
        bx, by = xs[1], ys[1]
        cx, cy = xs[2], ys[2]
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-12:
            continue
        w0 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / den
        w1 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / den
        w2 = 1.0 - w0 - w1
        mask = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        grid[y0:y1 + 1, x0:x1 + 1] += mask.astype(np.int16)

    covered = int((grid > 0).sum())
    overlap = int((grid > 1).sum())
    r["sampled_tris"] = sampled
    r["overlap_ratio"] = round(overlap / float(covered), 4) if covered else 0.0
    return r


_TEXTURE_INDEX_CACHE = {}


def _texture_name_index(root, max_depth=3):
    """建立文件名到路径的索引, 深度上限 max_depth, 结果按目录缓存。

    参考: 按层剪枝 + 目录缓存, 控制扫描耗时
    """
    key = os.path.normcase(os.path.abspath(root))
    if key in _TEXTURE_INDEX_CACHE:
        return _TEXTURE_INDEX_CACHE[key]

    index = {}
    base_depth = os.path.abspath(root).rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath.count(os.sep) - base_depth >= max_depth:
            dirnames[:] = []
        for fn in filenames:
            index.setdefault(fn.lower(), os.path.join(dirpath, fn))
    _TEXTURE_INDEX_CACHE[key] = index
    return index


def _texture_availability(image):
    """判断贴图是否可用。

    返回: (可用, 是否按文件名找回)。GENERATED 图像视为可用;
          原路径不存在时在 .blend 所在目录内按文件名查找。
    参考: Blender API, bpy.path.abspath 的 `//` 指向 .blend 所在目录
    """
    if image.source == 'GENERATED':
        return True, False
    abs_path = bpy.path.abspath(image.filepath) if image.filepath else ""
    if abs_path and os.path.exists(abs_path):
        return True, False
    name = os.path.basename(abs_path).lower()
    if not name:
        return False, False
    root = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else os.getcwd()
    return (True, True) if _texture_name_index(root).get(name) else (False, False)


def check_materials(obj):
    """统计材质、贴图与材质槽使用情况。

    输入: Mesh.materials、材质节点树、Image.filepath 与文件系统
    实现: 按 material_index 统计面分布, 索引越界或空槽计为未指定材质;
          遍历节点树查找 TEX_IMAGE; 贴图先按原路径解析, 失败后在 .blend 目录 3 层内按文件名查找
    返回: count, face_share, textures, missing, unpacked, relinked, unassigned_faces,
          mmd_shader, blend_modes
    参考:
        Unity Mesh.subMeshCount: 材质槽对应子网格, 决定绘制调用数
        Unity Model Importer: 贴图按路径重绑, 失效则用默认材质
    调用方: _rule_textures(), _rule_uv(), _rule_budget()
    """
    me = obj.data
    mats = [m for m in me.materials if m]
    r = {"count": len(mats), "face_share": [], "textures": [], "unassigned_faces": 0,
         "mmd_shader": 0, "blend_modes": {}}

    used = {}
    for p in me.polygons:
        if p.material_index >= len(me.materials) or me.materials[p.material_index] is None:
            r["unassigned_faces"] += 1
            continue
        nm = me.materials[p.material_index].name
        used[nm] = used.get(nm, 0) + 1
    tot = max(1, len(me.polygons))
    for nm, c in sorted(used.items(), key=lambda kv: -kv[1]):
        r["face_share"].append([nm, c, round(100.0 * c / tot, 2)])

    seen = set()
    for m in mats:
        if not m.use_nodes or m.node_tree is None:
            continue
        for n in m.node_tree.nodes:
            if n.type == 'TEX_IMAGE' and n.image:
                im = n.image
                if im.name in seen:
                    continue
                seen.add(im.name)
                available, relinked = _texture_availability(im)
                r["textures"].append({
                    "material": m.name, "image": im.name,
                    "packed": im.packed_file is not None,
                    "file_exists": available,
                    "relinked": relinked,
                    "size": list(im.size),
                })
        if any(n.type == 'GROUP' and n.node_tree and "MMD" in n.node_tree.name for n in m.node_tree.nodes):
            r["mmd_shader"] += 1

    r["unpacked"] = sum(1 for i in r["textures"] if not i["packed"])
    r["missing"] = sum(1 for i in r["textures"] if not i["file_exists"])
    r["relinked"] = sum(1 for i in r["textures"] if i.get("relinked"))
    for m in mats:
        r["blend_modes"][m.name] = getattr(m, "blend_method", "?")
    return r


def check_transform(obj):
    """检查对象局部变换。

    输入: matrix_basis、parent、parent_type
    实现: 分解为平移/旋转/缩放; 判定缩放是否为 1、旋转是否为 0;
          负缩放使 det(S) < 0, 面绕序与法线方向反转
    返回: location, rotation_deg, scale, applied, negative_scale, parent_type, parent_is_bone
    精度: 缩放容差 1e-4, 角度容差 1e-3
    参考:
        glTF Node transform: 行列式为负时须反转绕序
        det(S) < 0 即镜像, 基底手性翻转导致法线反向
    调用方: _rule_transform()
    """
    loc, rot, scale = obj.matrix_basis.decompose()
    eul = [round(math.degrees(a), 2) for a in rot.to_euler()]
    scale_ok = all(abs(s - 1.0) < 1e-4 for s in scale)
    rot_ok = max(abs(a) for a in eul) < 1e-3
    return {
        "location": [round(v, 4) for v in loc],
        "rotation_deg": eul,
        "scale": [round(v, 4) for v in scale],
        "applied": bool(scale_ok and rot_ok),
        "negative_scale": any(s < 0 for s in scale),
        "parent_type": obj.parent.type if obj.parent else None,
        "parent_is_bone": obj.parent_type == 'BONE',
    }


def check_normals(obj):
    """检查面朝向: 绕序一致性、整体朝向、退化面。

    输入: 面绕序、三角形顶点坐标、面面积
    实现: 1) 二面边的两个面遍历方向应相反, 相同表示存在翻转面
          2) 封闭网格用散度定理求有符号体积 V = (1/6) * sum(a . (b x c)), V < 0 表示法线朝内
          3) 开放网格以面法线与(面中心 - 重心)的点积判断朝向, 为启发式
    返回: closed, inconsistent_edges, signed_volume, inward, degenerate_faces, outward_ratio
    精度: 绕序与体积为精确值; 开放网格朝向比为启发式, 凹形体可能误判
    参考:
        Unity Mesh data: 正面由绕序决定, 背面剔除依赖绕序一致
        散度定理离散化: V = (1/6) Σ a·(b×c), 符号即朝向
    调用方: _rule_normals()
    """
    me = obj.data
    r = {"closed": False, "inconsistent_edges": 0, "signed_volume": 0.0,
         "inward": False, "degenerate_faces": 0, "outward_ratio": None}
    if not me.polygons:
        return r

    bm = bmesh.new()
    bm.from_mesh(me)
    try:
        bm.verts.ensure_lookup_table()

        # 每条边的两个方向记为 +1 / -1；二面边两侧方向应当相反
        dirs = {}
        for f in bm.faces:
            if f.calc_area() < 1e-12:
                r["degenerate_faces"] += 1
            for loop in f.loops:
                a = loop.vert.index
                b = loop.link_loop_next.vert.index
                key = (a, b) if a < b else (b, a)
                dirs.setdefault(key, []).append(1 if a < b else -1)

        r["inconsistent_edges"] = sum(1 for d in dirs.values() if len(d) == 2 and d[0] == d[1])
        r["closed"] = all(len(d) == 2 for d in dirs.values())

        if r["closed"]:
            try:
                vol = bm.calc_volume(signed=True)
                r["signed_volume"] = round(vol, 8)
                r["inward"] = vol < 0
            except Exception:
                pass
        elif len(bm.faces) >= 32:
            # 开放网格（墙板、片状物件）没法用体积判朝向，退而用启发式：
            # 从模型重心指向面中心的方向，与面法线同向 = 朝外。
            # 凹形物体（例如做了内墙的房子）可能误判，所以只作警告、并标明是启发式。
            # 片状物件（树叶卡、单面墙纸）本身没有"内外"，跳过。
            xs = [v.co.x for v in bm.verts]
            ys = [v.co.y for v in bm.verts]
            zs = [v.co.z for v in bm.verts]
            dims = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
            if max(dims) > 0 and min(dims) / max(dims) >= 0.15:
                c0 = Vector((0, 0, 0))
                for v in bm.verts:
                    c0 += v.co
                c0 /= max(1, len(bm.verts))
                out = sum(1 for f in bm.faces if f.normal.dot(f.calc_center_median() - c0) > 0)
                r["outward_ratio"] = round(out / float(len(bm.faces)), 3)
    finally:
        bm.free()
    return r


def check_armature(arm, mesh_obj):
    """检查骨骼结构与蒙皮权重。

    输入: Armature.bones、pose.bones、Mesh.vertex_groups、顶点权重组
    实现: 统计骨骼数量/根/叶/层级/约束; 逐顶点累加骨骼权重, 统计未分配权重、
          权重和不为 1、影响骨骼数超过上限的顶点;
          名称不在骨骼集合中的组记为伪组, 不计入权重统计
    返回: bones, roots, leaves, non_deform, max_depth, constraints, ik_constraints,
          vertex_groups, fake_groups, unweighted_verts, weight_sum_not_1,
          over_max_influences, influences
    调用方: _rule_skinning(); 影响数上限取 THRESHOLDS["infl_max"]
    参考:
        Unity Quality Settings: Skin Weights 默认 4 Bones, 超出部分被丢弃
        glTF Skins: JOINTS_n / WEIGHTS_n 为 vec4, 权重和为 1
    """
    bones = arm.data.bones
    r = {
        "bones": len(bones),
        "roots": [b.name for b in bones if b.parent is None],
        "leaves": sum(1 for b in bones if not b.children),
        "non_deform": sum(1 for b in bones if not b.use_deform),
        "constraints": sum(len(pb.constraints) for pb in arm.pose.bones),
        "ik_constraints": sum(1 for pb in arm.pose.bones for c in pb.constraints if c.type == 'IK'),
    }

    def depth(b):
        """骨骼到根节点的层级深度。"""
        d = 0
        while b.parent:
            b = b.parent
            d += 1
        return d

    r["max_depth"] = max((depth(b) for b in bones), default=0)

    me = mesh_obj.data
    gname = {g.index: g.name for g in mesh_obj.vertex_groups}
    bone_names = {b.name for b in bones}
    fake = {g.name for g in mesh_obj.vertex_groups if g.name not in bone_names}
    fake_used = {}
    infl = {}
    unweighted = 0
    bad_sum = 0
    for v in me.vertices:
        n = 0
        s = 0.0
        for vg in v.groups:
            nm = gname.get(vg.group, "?")
            if nm in fake:
                fake_used[nm] = fake_used.get(nm, 0) + 1
                continue
            n += 1
            s += vg.weight
        infl[n] = infl.get(n, 0) + 1
        if n == 0:
            unweighted += 1
        elif abs(s - 1.0) > 1e-4:
            bad_sum += 1

    r["vertex_groups"] = len(mesh_obj.vertex_groups)
    r["fake_groups"] = fake_used
    r["unweighted_verts"] = unweighted
    r["weight_sum_not_1"] = bad_sum
    r["influences"] = dict(sorted(infl.items()))
    r["over_max_influences"] = sum(c for k, c in infl.items() if k > THRESHOLDS["infl_max"])
    return r


def measure_object(obj, arm=None):
    """对单个对象执行采集, 返回 checks 字典。

    返回: {mesh, uv, materials, transform, normals, armature}
    调用方: pipeline.inspect()
    """
    return {
        "mesh": check_mesh(obj),
        "uv": check_uv(obj, size=128, max_tris=1500),
        "materials": check_materials(obj),
        "transform": check_transform(obj),
        "normals": check_normals(obj),
        "armature": check_armature(arm, obj) if arm else None,
    }
