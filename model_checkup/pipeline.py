"""编排层: 输入 -> 测量 -> 做判定, 返回结果"""
from .inputs import find_models
from .measures import measure_object
from .rules import unity_readiness


def inspect(names=None, limit=0, min_verts=0, progress=None):
    """执行检查并返回结果, 供插件调用。

    参数: names, limit(<= 0 不限), min_verts, progress 回调
    实现: find_models 选择对象; 每个对象执行采集, 再由 unity_readiness 判定;
    返回: [{name, checks, unity}]
    调用方: 插件的 CHECKUP_OT_run.execute()
    说明: 无缓存, 每次调用执行完整检查
    """
    found = find_models(names=names, limit=limit, min_verts=min_verts)
    total = len(found)
    out = []
    for i, (name, arm, mesh) in enumerate(found, 1):
        checks = measure_object(mesh, arm)
        out.append({"name": name, "checks": checks, "unity": unity_readiness(checks)})
        if progress is not None:
            progress(i, total, name)
    return out
