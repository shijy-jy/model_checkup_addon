"""控制台输出: 把检查结果排版为分层文本写入 Blender Console
"""
import textwrap

import bpy

LEVEL_ZH = {"fail": "错误", "warn": "警告", "info": "提示"}
MAX_OBJECTS = 200
DETAIL_WIDTH = 92

# 条目类型 -> Blender Console 的显示类型
CONSOLE_TYPE = {
    "head": 'INFO',
    "section": 'INFO',
    "object": 'OUTPUT',
    "fail": 'ERROR',
    "warn": 'OUTPUT',
    "info": 'OUTPUT',
    "muted": 'OUTPUT',
}


def _console_area():
    """返回当前窗口中的 CONSOLE 编辑器区域, 不存在时返回 None。"""
    try:
        win = bpy.context.window
        if win and win.screen:
            for area in win.screen.areas:
                if area.type == 'CONSOLE':
                    return area
    except Exception:
        pass
    return None


def _wrap(text, width=DETAIL_WIDTH, indent="        "):
    """按控制台宽度折行, 续行使用 indent 缩进。"""
    return textwrap.wrap(text, width=width, subsequent_indent=indent) or [""]


def format_entries(models, elapsed=0.0):
    """把检查结果转换为控制台条目。

    参数: models 为 inspect() 的返回值, elapsed 为耗时(秒)
    实现: 统计各状态数量, 按 不可用/存在风险 分组, 逐对象输出问题与折行详情;
          问题对象数超过 MAX_OBJECTS 时截断
    返回: [(text, kind)], kind 取值 head/section/object/fail/warn/info/muted
    """
    n = {"fail": 0, "warn": 0, "ok": 0}
    for m in models:
        n[m["unity"]["status"]] = n.get(m["unity"]["status"], 0) + 1

    entries = [
        ("", "muted"),
        ("Unity 导入检查", "head"),
        ("对象 %d 个   不可用 %d   存在风险 %d   通过 %d   用时 %.2f s"
         % (len(models), n.get("fail", 0), n.get("warn", 0), n.get("ok", 0), elapsed), "head"),
    ]

    groups = [("fail", "不可用"), ("warn", "存在风险")]
    listed = 0
    for status, title in groups:
        items = [m for m in models if m["unity"]["status"] == status]
        if not items:
            continue
        entries.append(("", "muted"))
        entries.append(("── %s（%d 个）" % (title, len(items)), "section"))
        for m in items[:max(0, MAX_OBJECTS - listed)]:
            listed += 1
            mesh = m["checks"]["mesh"]
            entries.append(("", "muted"))
            entries.append(("■ %s    %d 顶点 / %d 面"
                            % (m["name"], mesh["vertices"], mesh["faces"]), "object"))
            for v in m["unity"]["issues"]:
                entries.append(("   [%s] %s · %s"
                                % (LEVEL_ZH[v["level"]], v.get("category", ""), v["item"]),
                                v["level"]))
                for line in _wrap(v["detail"]):
                    entries.append(("        " + line, "muted"))

    problems = n.get("fail", 0) + n.get("warn", 0)
    if problems > listed:
        entries.append(("", "muted"))
        entries.append(("……其余 %d 个有问题的对象从略" % (problems - listed), "muted"))

    entries.append(("", "muted"))
    if not problems:
        entries.append(("全部对象均通过检查。", "object"))
    else:
        entries.append(("其余 %d 个对象通过检查。" % n.get("ok", 0), "muted"))
    entries.append(("小结：不可用 %d · 存在风险 %d · 通过 %d"
                    % (n.get("fail", 0), n.get("warn", 0), n.get("ok", 0)), "head"))
    return entries


def write_console(entries, clear=True):
    """把条目写入 Console, 同时输出到标准输出。

    参数: entries 为 (text, kind) 列表
    实现: 先清屏, 再逐行 scrollback_append(整段写入不会产生换行);
          无 Console 编辑器时仅 print
    """
    print("\n".join(text for text, _ in entries))

    area = _console_area()
    if area is None:
        return
    try:
        with bpy.context.temp_override(area=area):
            if clear:
                bpy.ops.console.clear()
            for text, kind in entries:
                bpy.ops.console.scrollback_append(
                    text=text + "\n", type=CONSOLE_TYPE.get(kind, 'OUTPUT'))
    except Exception:
        pass
