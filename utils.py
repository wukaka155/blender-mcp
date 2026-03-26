import bpy
from typing import cast
import os
import math
from mathutils import Vector


# from loadModel import load_asset_from_library
def generateModel(width: float, length: float, floor: int, modelName: str):
    """调用此函数可以快速生成一个建筑模型，风格为居民楼

    Args:
        width (float): 建筑宽度
        length (float): 建筑长度
        floor (int): 楼层数量
    """
    try:
        clean()
        load_asset_from_library(
            blend_filename="buildify_1.0.blend",
            asset_directory="Object",
            asset_name="BaseBuild",
            link=False,
        )
        # 查找类型为 'NODES' 的修改器
        if bpy.context.scene is not None:
            mesh_objects = [
                obj for obj in bpy.context.scene.objects if obj.type == "MESH"
            ]
            model_active: bpy.types.Object | None = None
            for obj in mesh_objects:
                if obj.name == "BaseBuild":
                    modifModel(obj, width, length, floor)
                    obj.name = modelName
                    model_active = obj
                else:
                    obj.hide_set(True)
            # `model_active` is a variable that stores the active object (building model) after it has been modified and renamed in the `generateModel` function. It is used to set up the camera and lighting for the modified model before saving it. The variable is then returned as the result of the function, providing the name of the modified model for further processing or reference.
            if model_active is None:
                raise RuntimeError("没有加入任何建筑资产")
            else:
                setup_camera_and_light(model_active)
                save_blend()
                return model_active.name
    except Exception as e:
        raise RuntimeError(f"生成失败{e}")


def modifModel(obj: bpy.types.Object, width: float, length: float, floor: int):
    if obj is not None:
        gn_mod = next((m for m in obj.modifiers if m.type == "NODES"), None)
        if gn_mod is not None:
            print(f"修改器名称: {gn_mod.name}")
            # 访问它关联的几何节点组
            if (
                isinstance(gn_mod, bpy.types.NodesModifier)
                and gn_mod.node_group is not None
            ):
                print(f"关联的节点组: {gn_mod.node_group.name}")
                if gn_mod.node_group.interface is not None:
                    for item in gn_mod.node_group.interface.items_tree:
                        input = cast(bpy.types.NodeTreeInterfaceSocket, item)
                        if input.socket_type != "NodeSocketGeometry":
                            if input.name == "Max number of floors":
                                gn_mod[input.identifier] = floor
                            if input.name == "Min number of floors":
                                gn_mod[input.identifier] = 4
                            if input.name == "width":
                                gn_mod[input.identifier] = width
                            if input.name == "length":
                                gn_mod[input.identifier] = length
                            print(
                                input.identifier,
                                input.name,
                                item.item_type,
                                input.socket_type,
                                gn_mod[input.identifier],
                            )
            obj.update_tag()  # type: ignore
            bpy.context.view_layer.update()  # pyright: ignore[reportOptionalMemberAccess]


def load_asset_from_library(blend_filename, asset_directory, asset_name, link=False):
    """
    从 Blender 的指定资产库中加载资产
    参数:
    blend_filename (str): 包含该资产的 .blend 文件名 (例如 'Trees.blend')
    asset_directory (str): 资产的数据类型目录 (常用: 'Object', 'Collection', 'Material', 'NodeTree')
    # The above code is defining a variable `asset_name` in Python.
    asset_name (str): 资产的实际名称
    link (bool): True 为链接资产(保持与源文件同步)，False 为追加资产(完全复制到当前文件)
    """
    # 1. 获取资产库的根路径
    preferences = bpy.context.preferences
    if preferences is not None:
        # 2. 构建目标 .blend 文件的绝对路径
        blend_filepath: str | None = None
        for library in preferences.filepaths.asset_libraries:
            library_path = library.path
            blend_filepath_tmp = os.path.join(library_path, blend_filename)
            if not os.path.exists(blend_filepath_tmp):
                continue
            else:
                blend_filepath = blend_filepath_tmp

        # 3. 构建 Blender 内部数据提取路径
        # Blender 的追加机制需要指定到具体的内部目录，例如 "C:/.../Trees.blend/Object/"
        if blend_filepath is None:
            raise f"没找到BaseBuild资产"
        inner_dir = os.path.join(blend_filepath, asset_directory) + "/"
        # 4. 执行追加或链接操作
        try:
            if link:
                bpy.ops.wm.link(
                    filepath=os.path.join(inner_dir, asset_name),
                    directory=inner_dir,
                    filename=asset_name,
                )
            else:
                bpy.ops.wm.append(
                    filepath=os.path.join(inner_dir, asset_name),
                    directory=inner_dir,
                    filename=asset_name,
                )
        except Exception as e:
            raise RuntimeError(e)
    else:
        raise RuntimeError(f"找不到 perfence")


def clean():
    # 1. 确保处于物体模式
    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode="OBJECT")
    # 2. 遍历所有物体并移除其修改器
    for obj in bpy.data.objects:
        # 只针对模型（Mesh）或曲线（Curve）等拥有修改器的类型
        if hasattr(obj, "modifiers"):
            # 倒序遍历删除修改器（防止索引错位）
            for mod in reversed(obj.modifiers):
                obj.modifiers.remove(mod)
    # 3. 删除场景中所有物体
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    # 4. 删除所有自定义集合（保留根集合）
    for col in bpy.data.collections:
        bpy.data.collections.remove(col)
    # 5. 深度清理：移除所有孤立的数据块（材质、网格、修改器缓存等）
    # 这一步会递归清理，确保没有任何“幽灵数据”残留
    bpy.ops.outliner.orphans_purge(
        do_local_ids=True, do_linked_ids=True, do_recursive=True
    )
    print("清理完成：已移除所有修改器、物体及集合。")


def export_model_base(target_name: str, export_path: str):
    target_path = f"{export_path}/{target_name}.glb"
    target_obj = bpy.data.objects.get(target_name)
    if target_obj:
        # 3. 选中该物体
        bpy.ops.object.select_all(action="DESELECT")
        target_obj.select_set(True)
        if bpy.context.view_layer is not None:
            bpy.context.view_layer.objects.active = target_obj
            bpy.ops.export_scene.gltf(
                use_selection=True,
                filepath=target_path,
                export_format="GLB",
                export_apply=True,  # 应用修改器（如表面细分）
                export_animations=True,  # 如果有动画则导出
                export_tangents=False,  # 多数引擎不需要切线，可减小体积
            )
            return f"GLB 成功导出至: {target_path}"
    else:
        raise RuntimeError(f"找不到名为 '{target_name}' 的物体")


def get_world_bbox(objects):
    """计算多个对象合并后的世界坐标包围盒"""
    inf = float("inf")
    min_v = Vector((inf, inf, inf))
    max_v = Vector((-inf, -inf, -inf))

    for obj in objects:
        if obj.type != "MESH":
            continue

        for corner in obj.bound_box:
            world_corner = obj.matrix_world @ Vector(corner)

            min_v.x = min(min_v.x, world_corner.x)
            min_v.y = min(min_v.y, world_corner.y)
            min_v.z = min(min_v.z, world_corner.z)

            max_v.x = max(max_v.x, world_corner.x)
            max_v.y = max(max_v.y, world_corner.y)
            max_v.z = max(max_v.z, world_corner.z)

    if min_v.x == inf:
        raise ValueError("传入对象中没有可用的 MESH")

    return min_v, max_v


def look_at(obj, target):
    """让对象朝向目标点"""
    direction = target - obj.location
    rot_quat = direction.to_track_quat("-Z", "Y")
    obj.rotation_euler = rot_quat.to_euler()


def setup_camera_and_light(
    target,
    camera_name="AutoCamera",
    sun_name="AutoSun",
    fill_name="AutoFill",
    fov_degrees=50.0,
    margin=1.15,
    view_dir=Vector((1.0, -1.0, 0.75)),
    set_scene_camera=True,
    link_to_scene=True,
):
    """
    为目标模型创建相机和灯光，使其刚好进入画面

    参数:
        target:
            bpy.types.Object 或 list[bpy.types.Object]
        camera_name:
            相机名称
        sun_name:
            太阳灯名称
        fill_name:
            补光名称
        fov_degrees:
            相机水平视角
        margin:
            额外边距，1.0 为刚好，建议 1.05~1.2
        view_dir:
            观察方向
        set_scene_camera:
            是否设为当前场景相机
        link_to_scene:
            是否链接到当前 scene collection

    返回:
        {
            "camera": cam,
            "sun": sun,
            "fill": fill,
            "center": center,
            "bbox_min": bbox_min,
            "bbox_max": bbox_max,
            "distance": distance,
        }
    """
    scene = bpy.context.scene

    # 统一成列表
    if isinstance(target, bpy.types.Object):
        objects = [target]
    else:
        objects = list(target)

    if not objects:
        raise ValueError("target 不能为空")

    bbox_min, bbox_max = get_world_bbox(objects)
    center = (bbox_min + bbox_max) * 0.5
    size = bbox_max - bbox_min

    width = size.x
    height = size.z
    depth = size.y
    max_dim = max(width, height, depth, 0.001)

    # 创建相机
    cam_data = bpy.data.cameras.new(camera_name)
    cam = bpy.data.objects.new(camera_name, cam_data)

    if link_to_scene:
        scene.collection.objects.link(cam)

    cam_data.lens_unit = "FOV"
    cam_data.angle = math.radians(fov_degrees)
    cam_data.clip_start = 0.01
    cam_data.clip_end = max(1000.0, max_dim * 100.0)

    render = scene.render
    aspect = (
        render.resolution_x / render.resolution_y if render.resolution_y != 0 else 1.0
    )

    fov_x = cam_data.angle
    fov_y = 2 * math.atan(math.tan(fov_x / 2.0) / aspect)

    dist_x = (width * 0.5) / math.tan(fov_x * 0.5) if width > 0 else 0.0
    dist_y = (height * 0.5) / math.tan(fov_y * 0.5) if height > 0 else 0.0
    distance = max(dist_x, dist_y) * margin

    direction = view_dir.normalized()
    cam.location = center + direction * distance
    look_at(cam, center)

    if set_scene_camera:
        scene.camera = cam

    # 创建太阳灯
    sun_data = bpy.data.lights.new(name=sun_name, type="SUN")
    sun_data.energy = 3.0
    sun = bpy.data.objects.new(name=sun_name, object_data=sun_data)

    if link_to_scene:
        scene.collection.objects.link(sun)

    sun.location = center + Vector((max_dim * 2.0, -max_dim * 2.0, max_dim * 3.0))
    look_at(sun, center)

    # 创建补光
    fill_data = bpy.data.lights.new(name=fill_name, type="POINT")
    fill_data.energy = max_dim * 300.0 if max_dim > 1 else 1000.0
    fill = bpy.data.objects.new(name=fill_name, object_data=fill_data)

    if link_to_scene:
        scene.collection.objects.link(fill)

    fill.location = center + Vector((-max_dim * 1.5, max_dim * 1.5, max_dim * 1.2))

    return {
        "camera": cam,
        "sun": sun,
        "fill": fill,
        "center": center,
        "bbox_min": bbox_min,
        "bbox_max": bbox_max,
        "distance": distance,
    }


def save_blend(filepath=None, overwrite=True, ensure_dir=True):
    """
    保存当前 Blender 工程

    参数:
        filepath: 保存路径（None = 保存到当前文件）
        overwrite: 是否覆盖
        ensure_dir: 自动创建目录

    返回:
        实际保存路径
    """

    # 如果没传路径 → 直接保存当前文件
    if filepath is None:
        bpy.ops.wm.save_mainfile()
        return bpy.data.filepath

    # 转绝对路径
    filepath = bpy.path.abspath(filepath)

    # 创建目录
    if ensure_dir:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

    # 保存
    bpy.ops.wm.save_as_mainfile(filepath=filepath, check_existing=not overwrite)

    return filepath

