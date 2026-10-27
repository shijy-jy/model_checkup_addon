# -*- coding: utf-8 -*-
"""Model Checkup —— 对场景网格执行 Unity 导入前检查。

不写文件; 结果输出到 Blender 控制台、N 面板与状态栏。

数据流
    CheckupSettings ─┐
    选中对象/场景数据 ┴─> CHECKUP_OT_run.execute ─> core.inspect()
                                                       │
                        ┌──────────────────────────────┤
                        ▼                              ▼
              scene.checkup_results          core.write_console()
              (面板, draw 读取)
                        │
                        └─ self.report() 写状态栏

检查规则位于 model_checkup.unity_readiness(); 本模块只负责收集输入与呈现结果。
安装: Preferences -> Get Extensions -> Install from Disk; 使用: 3D 视图 N 面板 -> Checkup。
"""

bl_info = {
    "name": "Model Checkup (Unity)",
    "author": "sjy",
    "version": (0, 11, 2),
    "blender": (4, 2, 0),
    "location": "3D 视图 → N 面板 → Checkup",
    "description": "批量检查场景中所有网格能否直接导入 Unity：UV、材质、变换、蒙皮、法线",
    "category": "Object",
}

import time
import traceback

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, IntProperty,
                       PointerProperty, StringProperty)
from bpy.types import Operator, Panel, PropertyGroup

from . import model_checkup as core

ADDON_TITLE = "Unity 导入检查"
STATUS = {
    "fail": ("不可用", 'CANCEL'),
    "warn": ("存在风险", 'ERROR'),
    "ok": ("通过", 'CHECKMARK'),
}
MAX_PANEL_ROWS = 80



# 数据: 界面设置与面板状态

class CheckupResult(PropertyGroup):
    """面板列表的一行, 由 execute 写入, draw 读取。"""
    obj_name: StringProperty(name="对象")
    status: StringProperty(name="结论")     # fail / warn / ok
    fails: IntProperty(name="错误", default=0)
    warns: IntProperty(name="警告", default=0)
    infos: IntProperty(name="提示", default=0)
    verts: IntProperty(name="顶点", default=0)
    faces: IntProperty(name="面", default=0)


class CheckupSettings(PropertyGroup):
    """界面设置, 存于 Scene, 随 .blend 保存。

    scope     决定待检查对象范围
    min_verts 传递给 find_models 过滤小对象
    show_all  影响面板显示
    """
    scope: EnumProperty(
        name="范围",
        items=[('SCENE', "整个场景", "检查场景中所有网格对象，不限数量"),
               ('SELECTED', "选中对象", "仅检查当前选中的网格对象")],
        default='SCENE',
    )
    min_verts: IntProperty(name="最小顶点数", default=0, min=0,
                           description="顶点数低于此值的对象跳过；0 表示不跳过")
    show_all: BoolProperty(name="显示全部对象", default=False,
                           description="面板中是否也列出通过检查的对象")



# 输出

def _write_output(models, elapsed):
    """把结果写入 Blender Console 与标准输出。"""
    core.write_console(core.format_entries(models, elapsed))



# 操作符

class CHECKUP_OT_run(Operator):
    """执行检查: 读取设置与选择, 调用 core.inspect(), 输出到面板与控制台。"""
    bl_idname = "checkup.run"
    bl_label = "运行导入检查"
    bl_description = "检查选中对象或场景中全部网格；每次运行都是全量重查"
    bl_options = {'REGISTER'}

    def execute(self, context):
        """收集检查范围, 调用核心检查, 写入面板状态并输出控制台。"""
        # 检查范围与过滤条件: scope 决定 names, min_verts 交给核心过滤
        st = context.scene.checkup_settings
        names = None
        if st.scope == 'SELECTED':
            names = [o.name for o in context.selected_objects if o.type == 'MESH']
            if not names:
                # 对象位于隐藏集合时不会出现在 context.selected_objects, 改为读取选中标记
                try:
                    names = [o.name for o in bpy.data.objects
                             if o.type == 'MESH' and o.select_get()]
                except Exception:
                    names = []
            if not names:
                self.report({'ERROR'}, "未选择网格对象。")
                return {'CANCELLED'}

        t0 = time.time()

        def progress(i, total, name):
            """每 100 个对象输出一次进度。"""
            if total >= 100 and i % 100 == 0:
                print("[检查] %d/%d …" % (i, total))

        # 执行检查, limit=0 表示不限数量
        try:
            models = core.inspect(names=names, limit=0, min_verts=st.min_verts,
                                  progress=progress)
        except Exception as e:
            traceback.print_exc()
            self.report({'ERROR'}, "检查失败：%s" % e)
            return {'CANCELLED'}

        elapsed = time.time() - t0
        if not models:
            self.report({'WARNING'}, "没有可检查的网格对象, 可将最小顶点数调为 0。")
            return {'CANCELLED'}

        # 结果写入面板状态
        results = context.scene.checkup_results
        results.clear()
        for m in models:
            u = m["unity"]
            r = results.add()
            r.obj_name = m["name"]
            r.status = u["status"]
            r.fails = u["counts"]["fail"]
            r.warns = u["counts"]["warn"]
            r.infos = u["counts"]["info"]
            r.verts = m["checks"]["mesh"]["vertices"]
            r.faces = m["checks"]["mesh"]["faces"]

        # 输出到 Console
        _write_output(models, elapsed)

        bad = sum(1 for m in models if m["unity"]["status"] == "fail")
        warn = sum(1 for m in models if m["unity"]["status"] == "warn")
        # 用 WARNING 而非 ERROR：检查本身执行成功，结论严重并不等于操作失败
        if bad:
            self.report({'WARNING'}, "检查完成：%d 个对象不可用，%d 个存在风险，详见控制台。"
                        % (bad, warn))
        elif warn:
            self.report({'WARNING'}, "检查完成：%d 个对象存在风险，详见控制台。" % warn)
        else:
            self.report({'INFO'}, "检查完成：%d 个对象均通过。" % len(models))
        return {'FINISHED'}


class CHECKUP_OT_focus(Operator):
    """选中指定对象并将其框选至视图中心。"""
    bl_idname = "checkup.focus"
    bl_label = "定位对象"
    bl_description = "选中该对象，并将视图框选至该对象"

    obj_name: StringProperty()

    def execute(self, context):
        """选中目标对象并调用 view_selected() 调整视图。"""
        ob = bpy.data.objects.get(self.obj_name)
        if ob is None:
            self.report({'ERROR'}, "对象不存在：%s" % self.obj_name)
            return {'CANCELLED'}
        for o in context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        try:
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    with context.temp_override(area=area):
                        bpy.ops.view3d.view_selected()
                    break
        except Exception:
            pass
        return {'FINISHED'}


class CHECKUP_OT_clear(Operator):
    """清空面板中的检查结果。"""
    bl_idname = "checkup.clear"
    bl_label = "清空结果"

    def execute(self, context):
        """清空 checkup_results 集合。"""
        context.scene.checkup_results.clear()
        return {'FINISHED'}


# --------------------------------------------------------------------------- #
# 面板
# --------------------------------------------------------------------------- #
class VIEW3D_PT_checkup(Panel):
    """3D 视图 N 面板: 范围、运行按钮、结果摘要与对象列表。"""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Checkup"
    bl_label = ADDON_TITLE

    def draw(self, context):
        """按当前设置与结果显示面板; 问题对象超过 MAX_PANEL_ROWS 时截断。"""
        # 面板控件与设置的绑定
        layout = self.layout
        st = context.scene.checkup_settings

        col = layout.column(align=True)
        col.prop(st, "scope", expand=True)
        col.operator("checkup.run", icon='PLAY', text="运行检查")

        box = layout.box()
        box.prop(st, "min_verts")

        results = context.scene.checkup_results
        if not results:
            layout.label(text="选择范围后运行检查。", icon='INFO')
            return

        n_bad = sum(1 for r in results if r.status == "fail")
        n_warn = sum(1 for r in results if r.status == "warn")
        n_ok = sum(1 for r in results if r.status == "ok")

        box = layout.box()
        box.row().label(text="已检查 %d 个对象" % len(results),
                        icon='CANCEL' if n_bad else ('ERROR' if n_warn else 'CHECKMARK'))
        sub = box.row(align=True)
        if n_bad:
            sub.label(text="不可用 %d" % n_bad, icon='CANCEL')
        if n_warn:
            sub.label(text="存在风险 %d" % n_warn, icon='ERROR')
        if n_ok:
            sub.label(text="通过 %d" % n_ok, icon='CHECKMARK')

        box.prop(st, "show_all")

        rows = 0
        for r in results:
            if r.status == "ok" and not st.show_all:
                continue
            if rows >= MAX_PANEL_ROWS:
                box.label(text="其余 %d 个对象请查看控制台。" % (len(results) - rows))
                break
            row = box.row(align=True)
            row.label(text=r.obj_name, icon=STATUS.get(r.status, ("", 'DOT'))[1])
            sub = row.row(align=True)
            sub.alignment = 'RIGHT'
            if r.fails:
                sub.label(text="%d 错误" % r.fails)
            if r.warns:
                sub.label(text="%d 警告" % r.warns)
            if not r.fails and not r.warns:
                sub.label(text="通过")
            op = row.operator("checkup.focus", text="", icon='VIEWZOOM')
            op.obj_name = r.obj_name
            rows += 1

        if rows == 0:
            box.label(text="全部对象均通过检查。", icon='CHECKMARK')

        layout.operator("checkup.clear", icon='TRASH', text="清空结果")



# 注册

classes = (
    CheckupResult,
    CheckupSettings,
    CHECKUP_OT_run,
    CHECKUP_OT_focus,
    CHECKUP_OT_clear,
    VIEW3D_PT_checkup,
)


def register():
    """注册面板、操作符与属性组, 并把设置与结果挂到 Scene 上。"""
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.checkup_settings = PointerProperty(type=CheckupSettings)
    bpy.types.Scene.checkup_results = CollectionProperty(type=CheckupResult)


def unregister():
    """注销全部类并移除 Scene 属性。"""
    for attr in ("checkup_settings", "checkup_results"):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
