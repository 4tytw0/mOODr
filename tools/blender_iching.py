"""Blender: draws the m00Dr roller's live hexagram (m800Dr server.py GET /hexagram) into the scene.

The script of the Mac's ~/Documents/Blender/iChing.blend (its "iChing" text block), kept here.
Run it in Blender's Text Editor (Run Script), or open the file with --python-text iChing.
"""
import json
import urllib.request

import bmesh
import bpy

# The m00Dr roller's current hexagram, served by m800Dr's audio server.
URL = "http://192.168.1.17:8808/hexagram"
LIVE = True        # keep redrawing as the roller moves through its arc
EVERY = 2.0        # seconds between checks when LIVE

z = 0
whole = (0, 0, z)
brokenleft = (0, -2, z)
brokenright = (0, 2, z)
spacing = 3


def material(name, color):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.diffuse_color = color   # viewport
        mat.use_nodes = True
        mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = color   # render
    return mat


def cube_mesh(name, mat):
    """A 2x2x2 cube mesh, made once and shared by every block that uses mat."""
    mesh = bpy.data.meshes.get(name)
    if mesh is None:
        mesh = bpy.data.meshes.new(name)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=2)
        bm.to_mesh(mesh)
        bm.free()
        mesh.materials.append(mat)
    return mesh


def collection():
    coll = bpy.data.collections.get("Hexagram")
    if coll is None:
        coll = bpy.data.collections.new("Hexagram")
        bpy.context.scene.collection.children.link(coll)
    return coll


def block(coll, mesh, location, scale=(1, 1, 1)):
    obj = bpy.data.objects.new("Line", mesh)
    obj.location = location
    obj.scale = scale
    coll.objects.link(obj)


def fetch():
    try:
        with urllib.request.urlopen(URL, timeout=1.5) as response:
            return json.load(response)
    except (OSError, ValueError):
        return None   # rig off, or no Roll yet: keep what's drawn


def draw(hexagram):
    coll = collection()
    for obj in list(coll.objects):
        bpy.data.objects.remove(obj)
    plain = cube_mesh("HexCube", material("Material", (0.8, 0.8, 0.8, 1)))
    changing = cube_mesh("HexCubeChanging", material("Changing", (0.8, 0.15, 0.1, 1)))
    # Bottom line first, stacked upward, as the roller sends them.
    for i, (yang, turns) in enumerate(zip(hexagram["lines"], hexagram["changing"])):
        mesh = changing if turns else plain
        height = (0, 0, i * spacing)
        if yang:
            block(coll, mesh, tuple(a + b for a, b in zip(whole, height)), scale=(1, 3, 1))
        else:
            block(coll, mesh, tuple(a + b for a, b in zip(brokenleft, height)))
            block(coll, mesh, tuple(a + b for a, b in zip(brokenright, height)))
    coll["hexagram"] = json.dumps(hexagram)
    print(f"hexagram {hexagram['text']} ({hexagram['binary']}), section {hexagram['section']}/{hexagram['of']}")


def update():
    hexagram = fetch()
    if hexagram is not None and json.dumps(hexagram) != collection().get("hexagram"):
        draw(hexagram)
    return EVERY


# Running the script again replaces the old timer rather than adding a second one.
old = bpy.app.driver_namespace.get("iching_update")
if old and bpy.app.timers.is_registered(old):
    bpy.app.timers.unregister(old)
update()
if LIVE and not bpy.app.background:
    bpy.app.driver_namespace["iching_update"] = update
    bpy.app.timers.register(update, first_interval=EVERY)
