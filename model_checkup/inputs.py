"""输入层: 从当前场景中选出待检查的对象, 并解析其所属骨架。
读取 bpy.data
"""
import bpy


def find_models(names=None, min_verts=200, limit=6):
    """选择本次检查的对象。

    参数: names 为对象名列表, None 表示自动选择; min_verts 为顶点数下限; limit <= 0 表示不限
    规则: 显式 names 优先, 其次 MMD ROOT 层级, 最后取场景全部网格; 自动模式按顶点数降序
    返回: [(显示名, 骨架对象或 None, 网格对象)]
    调用方: pipeline.inspect()
    """
    if names:
        out = []
        for n in names:
            o = bpy.data.objects.get(n)
            if o is None:
                print("[skip] 找不到对象: %s" % n)
                continue
            if o.type != 'MESH':
                print("[skip] 不是网格: %s (%s)" % (n, o.type))
                continue
            out.append((n, _parent_armature(o), o))
        return out

    out = []
    for root in bpy.data.objects:
        if getattr(root, "mmd_type", None) == "ROOT":
            arm = mesh = None
            for c in root.children_recursive:
                if c.type == 'ARMATURE' and arm is None:
                    arm = c
                elif c.type == 'MESH' and mesh is None:
                    mesh = c
            if mesh:
                out.append((root.name, arm, mesh))
    if out:
        return out

    for o in bpy.data.objects:
        if o.type == 'MESH' and len(o.data.vertices) >= min_verts:
            out.append((o.name, _parent_armature(o), o))
    out.sort(key=lambda t: -len(t[2].data.vertices))
    return out[:limit] if limit and limit > 0 else out


def _parent_armature(obj):
    """沿 parent 链向上查找第一个 ARMATURE 对象, 未找到返回 None。"""
    p = obj.parent
    while p is not None and p.type != 'ARMATURE':
        p = p.parent
    return p
