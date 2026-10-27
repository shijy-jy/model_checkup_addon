"""判定层: 依据 checks 与阈值给出结论。

unity_readiness() 汇总规则函数, 输出带 category 的 issue 列表
"""
from .constants import THRESHOLDS


def _issue(level, category, item, detail):
    """构造一条 issue: 级别、类别、检查项、说明。"""
    return {"level": level, "category": category, "item": item, "detail": detail}


_LEVEL_RANK = {"fail": 0, "warn": 1, "info": 2}


def _rule_shell(checks):
    """空壳网格(全部面面积为 0): 终止性判定, 命中后不再执行其他规则。"""
    m = checks["mesh"]
    nm = checks.get("normals") or {}
    if m["faces"] and nm.get("degenerate_faces", 0) >= m["faces"]:
        return [_issue("warn", "网格", "空壳网格",
                       "全部 %d 个面面积近似为 0，渲染不出可见表面；多为占位或残留对象，建议删除。"
                       % m["faces"])]
    return []


def _rule_geometry(checks):
    """几何规则: 空网格 -> 错误; 松散顶点/非流形边/N 边形/零面积面 -> 提示。"""
    m = checks["mesh"]
    nm = checks.get("normals") or {}
    issues = []

    if m["faces"] == 0:
        issues.append(_issue("fail", "网格", "空网格", "面数为 0，导入后不可见。"))
    if m["loose_verts"]:
        issues.append(_issue("info", "网格", "松散顶点",
                             "%d / %d 个顶点未连接任何边，不参与渲染。"
                             % (m["loose_verts"], m["vertices"])))
    if m["non_manifold_edges"]:
        issues.append(_issue("info", "网格", "非流形边",
                             "%d / %d 条边被 3 个以上面共享，影响碰撞体生成与布尔运算。"
                             % (m["non_manifold_edges"], m["edges"])))
    if m["ngons"]:
        issues.append(_issue("info", "网格", "N 边形",
                             "%d / %d 个面为多边形面，导入时自动三角化。"
                             % (m["ngons"], m["faces"])))
    if nm.get("degenerate_faces"):
        issues.append(_issue("info", "网格", "零面积面",
                             "%d / %d 个面面积近似为 0，法线不可靠，可能出现黑斑。"
                             % (nm["degenerate_faces"], m["faces"])))
    return issues


def _rule_uv(checks):
    """UV 规则: 材质使用贴图但缺少 UV -> 错误; 越界、重叠 -> 提示。

    纯色材质不要求 UV。
    """
    uv = checks["uv"]
    mat = checks["materials"]
    issues = []

    if not uv["layers"]:
        if mat["textures"]:
            issues.append(_issue("fail", "UV", "缺少 UV",
                                 "材质使用 %d 张贴图，但对象没有 UV 层：贴图无法映射，只能显示纯色。"
                                 % len(mat["textures"])))
        return issues

    if uv.get("outside_ratio", 0) > 0:
        issues.append(_issue("info", "UV", "UV 越界",
                             "%.2f%% 的 UV 点落在 0–1 之外；贴图为 Clamp 模式时会被裁切，"
                             "Repeat 模式下属正常平铺。" % uv["outside_ratio"]))
    if uv.get("overlap_ratio", 0) > THRESHOLDS["uv_overlap_warn"]:
        issues.append(_issue("info", "UV", "UV 重叠",
                             "约 %.1f%% 的 UV 面积被重复覆盖（阈值 %.0f%%，栅格 %d²）；"
                             "对称或平铺 UV 属正常。"
                             % (uv["overlap_ratio"] * 100, THRESHOLDS["uv_overlap_warn"] * 100,
                                uv.get("raster", 0))))
    return issues


def _rule_textures(checks):
    """材质规则: 贴图失效 -> 错误; 面未指定材质 -> 警告; 未打包、需重链、MMD 着色器 -> 提示。"""
    mat = checks["materials"]
    issues = []

    if mat["missing"]:
        issues.append(_issue("fail", "材质", "贴图文件失效",
                             "%d / %d 张贴图的原路径与文件名均无法解析，导入后显示为默认纯色（粉色）。"
                             % (mat["missing"], len(mat["textures"]))))
    if mat.get("relinked"):
        issues.append(_issue("info", "材质", "贴图路径需重链",
                             "%d 张贴图原路径失效，已按文件名在工程目录找到。" % mat["relinked"]))
    if mat["unpacked"]:
        issues.append(_issue("info", "材质", "贴图未打包",
                             "%d / %d 张贴图未打包进 .blend，迁移时容易丢失。"
                             % (mat["unpacked"], len(mat["textures"]))))
    if mat["unassigned_faces"]:
        issues.append(_issue("warn", "材质", "面未指定材质",
                             "%d / %d 个面未指定材质，导入后显示为默认纯色（粉色）。"
                             % (mat["unassigned_faces"], checks["mesh"]["faces"])))
    if mat["mmd_shader"]:
        issues.append(_issue("info", "材质", "MMD 着色器",
                             "%d 个材质使用 MMD 节点组，导出前需替换为 PBR 材质。" % mat["mmd_shader"]))
    return issues


def _rule_transform(checks):
    """变换规则: 负缩放 -> 错误; 未应用变换时按父级区分。

    根级对象 -> 警告(变换会写入 Transform); 有父级 -> 提示(变换随层级导入)。
    """
    tr = checks["transform"]
    issues = []

    if tr["negative_scale"]:
        issues.append(_issue("fail", "变换", "负缩放",
                             "scale=%s；负缩放会使面法线反转，导入后可能被背面剔除。"
                             % (tr["scale"],)))
    if not tr["applied"]:
        detail = "scale=%s，rotation=%s°。" % (tr["scale"], tr["rotation_deg"])
        parent_type = tr.get("parent_type")
        if parent_type is not None or tr.get("parent_is_bone"):
            # 有父级的对象：变换相对于父级，导出时随层级带入，不属于缺陷
            issues.append(_issue("info", "变换", "变换随层级保留",
                                 detail + "父级为%s，变换属于层级结构，导出时随父级带入。"
                                 % ("骨架" if parent_type == 'ARMATURE' else "物体")))
        else:
            issues.append(_issue("warn", "变换", "变换未应用",
                                 detail + "根级对象应先应用变换，否则导入后尺寸与碰撞体不匹配。"))
    return issues


def _rule_normals(checks):
    """法线规则: 绕序不一致、整体朝内 -> 错误; 开放网格朝向比偏低 -> 警告(启发式)。"""
    nm = checks.get("normals") or {}
    issues = []

    if nm.get("inconsistent_edges"):
        issues.append(_issue("fail", "法线", "绕序不一致",
                             "%d / %d 条边两侧面的绕序相同，存在翻转面；开启背面剔除后显示为黑面或不可见。"
                             % (nm["inconsistent_edges"], checks["mesh"]["edges"])))
    if nm.get("inward"):
        issues.append(_issue("fail", "法线", "法线整体朝内",
                             "封闭网格的有符号体积为 %.6f（< 0），法线指向内部，从外部不可见。"
                             % nm.get("signed_volume", 0.0)))
    elif nm.get("outward_ratio") is not None and nm["outward_ratio"] < 0.35:
        issues.append(_issue("warn", "法线", "法线可能朝内",
                             "开放网格中仅 %.0f%% 的面法线朝外（启发式判定），需人工确认。"
                             % (nm["outward_ratio"] * 100)))
    return issues


def _rule_skinning(checks):
    """蒙皮规则: 未指派权重 -> 错误; 影响数超过上限、权重和不为 1 -> 警告; 非骨骼顶点组 -> 提示。"""
    arm = checks.get("armature")
    if not arm:
        return []
    m = checks["mesh"]
    issues = []

    if arm["unweighted_verts"]:
        issues.append(_issue("fail", "蒙皮", "未指派权重的顶点",
                             "%d / %d 个顶点没有骨骼权重，动画时不随骨骼运动。"
                             % (arm["unweighted_verts"], m["vertices"])))
    if arm["over_max_influences"]:
        issues.append(_issue("warn", "蒙皮", "顶点受超过 4 根骨骼影响",
                             "%d / %d 个顶点受影响骨骼数超过 %d，Unity 仅保留 4 根。"
                             % (arm["over_max_influences"], m["vertices"], THRESHOLDS["infl_max"])))
    if arm["weight_sum_not_1"]:
        issues.append(_issue("warn", "蒙皮", "权重和不为 1",
                             "%d / %d 个顶点权重和不为 1，动画可能出现抖动或形变异常。"
                             % (arm["weight_sum_not_1"], m["vertices"])))
    if arm.get("fake_groups"):
        issues.append(_issue("info", "蒙皮", "非骨骼顶点组",
                             "存在被顶点引用但非骨骼的组：%s。" % list(arm["fake_groups"])[:5]))
    return issues


def _rule_scale(checks):
    """尺寸规则: 最长边 > 1000 m 或 < 1 mm 时告警(Unity 中 1 单位 = 1 米)。"""
    dims = checks["mesh"].get("dimensions") or []
    longest = max(dims) if dims else 0
    if longest and (longest > 1000 or longest < 0.001):
        return [_issue("warn", "尺寸", "尺寸超出常规范围",
                       "最长边 %.3f（尺寸 %s，Unity 中 1 单位 = 1 米）。" % (longest, dims))]
    return []


def _rule_budget(checks):
    """预算规则: 三角面数、材质槽数超过阈值时给出提示。"""
    m = checks["mesh"]
    mat = checks["materials"]
    issues = []

    if m["tris"] > THRESHOLDS["tri_warn"]:
        issues.append(_issue("info", "网格", "面数偏高",
                             "三角面 %d（阈值 %d）。" % (m["tris"], THRESHOLDS["tri_warn"])))
    if mat["count"] > THRESHOLDS["material_warn"]:
        issues.append(_issue("info", "材质", "材质数量偏多",
                             "材质槽 %d 个（阈值 %d），数量约等于绘制调用数。"
                             % (mat["count"], THRESHOLDS["material_warn"])))
    return issues


UNITY_RULES = (
    _rule_geometry,
    _rule_uv,
    _rule_textures,
    _rule_transform,
    _rule_normals,
    _rule_skinning,
    _rule_scale,
    _rule_budget,
)


def unity_readiness(checks):
    """汇总 Unity 导入可用性判定。

    输入: checks
    实现: 先执行终止性规则 _rule_shell; 未命中则按 UNITY_RULES 顺序执行全部规则;
          结果按 错误、警告、提示 排序
    返回: {status: fail|warn|ok, issues: [...], counts: {fail, warn, info}}
    调用方: 插件(_format_entries、面板)

    判定口径仅覆盖能否直接导入 Unity 正常使用:
        错误：空网格、有贴图但缺 UV、贴图失效、负缩放、绕序不一致、法线朝内、未蒙皮顶点
        警告：面未指定材质、独立物件变换未应用、开放网格法线可能朝内、
              超过 4 根骨骼影响、权重和不为 1、尺寸异常
        提示：松散顶点、非流形边、N 边形、零面积面、UV 越界/重叠、
              贴图未打包、绑定部件变换保留、面数、材质数

    空壳网格为终止性判定：命中后只返回该条，避免占位对象叠加噪声。
    """
    shell = _rule_shell(checks)
    issues = shell if shell else [i for rule in UNITY_RULES for i in rule(checks)]
    issues.sort(key=lambda i: _LEVEL_RANK[i["level"]])      # 输出顺序: 错误、警告、提示

    counts = {lv: sum(1 for i in issues if i["level"] == lv) for lv in ("fail", "warn", "info")}
    status = "fail" if counts["fail"] else ("warn" if counts["warn"] else "ok")
    return {"status": status, "issues": issues, "counts": counts}

