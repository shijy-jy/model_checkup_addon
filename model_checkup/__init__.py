"""model_checkup —— Blender 网格资产导入前检查。

分层
    inputs     输入层: 选择对象、解析所属骨架
    measures   测量层: check_* 计算指标, 不做判定
    rules      判定层: 依据阈值给出结论, 不依赖 bpy
    pipeline   编排层: inspect() = 输入 + 测量 + 判定
    console    输出层: 结果排版为分层文本, 写入 Blender Console 与标准输出

对外 API
    inspect(names, limit, min_verts, progress)  检查并返回 [{name, checks, unity}]
    format_entries(models, elapsed)             结果排版为控制台条目
    write_console(entries)                      写入 Console 与标准输出
    unity_readiness(checks)                     判定

导入策略: 判定层与常量层在导入本包时即加载;
其余模块按属性访问惰性导入
"""
from importlib import import_module

from .constants import THRESHOLDS, TOOL, VERSION
from .rules import unity_readiness

# 属性名: (子模块, 模块内名称)
_LAZY = {
    "find_models": (".inputs", "find_models"),
    "check_mesh": (".measures", "check_mesh"),
    "check_uv": (".measures", "check_uv"),
    "check_materials": (".measures", "check_materials"),
    "check_transform": (".measures", "check_transform"),
    "check_normals": (".measures", "check_normals"),
    "check_armature": (".measures", "check_armature"),
    "measure_object": (".measures", "measure_object"),
    "world_matrix": (".measures", "world_matrix"),
    "inspect": (".pipeline", "inspect"),
    "format_entries": (".console", "format_entries"),
    "write_console": (".console", "write_console"),
}

__all__ = ["THRESHOLDS", "TOOL", "VERSION", "unity_readiness"] + sorted(_LAZY)


def __getattr__(name):
    """按需导入其余模块; 判定层与常量层不经过这里。"""
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    module = import_module(target[0], __name__)
    value = getattr(module, target[1])
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
