"""A generated document the size of a short film's: two rigs of 58 bones with skinned
bodies, 14 buildings with procedural materials, a 700-node group, 10 signs, a ground, a camera and a prop in
quaternion mode. Used by thin_tests.py.
build() returns the names it made; it never touches files.
"""

import bpy


def _rig(name, location):
    data = bpy.data.armatures.new(name)
    rig = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(rig)
    rig.scale, rig.location = (0.01,) * 3, location
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    previous = None
    for index in range(58):
        bone = data.edit_bones.new("B%02d" % index)
        bone.head, bone.tail = (0, 0, 10 * index), (0, 0, 10 * index + 10)
        if previous is not None:
            bone.parent = previous
        previous = bone
    for side, x in (("Arm.L", 20), ("Arm.R", -20)):
        arm = data.edit_bones.new(side)
        arm.head, arm.tail = (x, 0, 450), (x * 3, 0, 450)
        arm.parent = data.edit_bones["B45"]
    bpy.ops.object.mode_set(mode="OBJECT")
    mesh = bpy.data.meshes.new(name + "_Body")
    mesh.from_pydata([(0, 0, 0), (50, 0, 0), (50, 0, 580), (0, 0, 580)], [], [(0, 1, 2, 3)])
    body = bpy.data.objects.new(name + "_Body", mesh)
    bpy.context.scene.collection.objects.link(body)
    body.vertex_groups.new(name="B00").add([0, 1], 1.0, "REPLACE")
    body.vertex_groups.new(name="B57").add([2, 3], 1.0, "REPLACE")
    body.parent = rig
    body.modifiers.new("Armature", "ARMATURE").object = rig
    return rig.name


def build():
    scene = bpy.context.scene
    made = {"rigs": [_rig("OC_Film_Cowboy", (-3, 0, 0)), _rig("OC_Film_Outlaw", (3, 0, 0))]}
    town = bpy.data.collections.new("OC_Town")
    scene.collection.children.link(town)
    for index in range(14):
        mesh = bpy.data.meshes.new("OC_Building_%02d" % index)
        mesh.from_pydata([(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0), (0, 0, 5), (4, 0, 5), (4, 4, 5), (0, 4, 5)], [],
                         [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)])
        building = bpy.data.objects.new("OC_Building_%02d" % index, mesh)
        building.location = (index * 6 - 40, 12, 0)
        town.objects.link(building)
        material = bpy.data.materials.new("OC_Wood_%02d" % index)
        material.use_nodes = True
        for _ in range(25):
            material.node_tree.nodes.new("ShaderNodeMath")
        mesh.materials.append(material)
    group = bpy.data.node_groups.new("OC_HugeGroup", "ShaderNodeTree")
    for _ in range(700):
        group.nodes.new("ShaderNodeMath")
    for index in range(10):
        sign = bpy.data.objects.new("OC_Sign_%02d" % index, bpy.data.curves.new("OC_Sign_%02d" % index, "FONT"))
        town.objects.link(sign)
    ground = bpy.data.objects.new("OC_Ground", bpy.data.meshes.new("OC_Ground"))
    ground.data.from_pydata([(-100, -100, 0), (100, -100, 0), (100, 100, 0), (-100, 100, 0)], [], [(0, 1, 2, 3)])
    scene.collection.objects.link(ground)
    camera = bpy.data.objects.new("OC_Cam_Wide", bpy.data.cameras.new("OC_Cam_Wide"))
    camera.location = (0, -20, 3)
    scene.collection.objects.link(camera)
    scene.camera = camera
    # An import in quaternion mode, 7 m tall and half under the ground.
    prop = bpy.data.objects.new("OC_Import", bpy.data.meshes.new("OC_Import"))
    prop.data.from_pydata([(-1, -1, -3.5), (1, -1, -3.5), (1, 1, 3.5), (-1, 1, 3.5)], [], [(0, 1, 2, 3)])
    prop.rotation_mode = "QUATERNION"
    scene.collection.objects.link(prop)
    bpy.context.view_layer.update()
    made.update(objects=len(bpy.data.objects), node_groups=len(bpy.data.node_groups))
    return made
