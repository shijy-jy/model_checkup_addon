"""常量: 判定阈值。
"""

TOOL = "model_checkup"
VERSION = "0.3.0"

THRESHOLDS = {
    # spec: glTF 的 JOINTS_0/WEIGHTS_0 为 vec4, Unity 默认 4 Bones
    "infl_max": 4,
    # spec + tolerance: 光照贴图 UV 要求零重叠; 5% 覆盖栅格估算误差与对称 UV
    "uv_overlap_warn": 0.05,
    # budget: 单帧约 300 万三角面, 单件按 5% 分配
    "tri_warn": 150_000,
    # budget: 单帧约 300 次绘制调用, 单件按 2.5% 分配
    "material_warn": 8,
}
