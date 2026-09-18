# SPDX-FileCopyrightText: 2024 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

__all__ = (
    "classes",
)

import bpy
from bpy.types import Operator, FileHandler
from bpy.props import (
    StringProperty,
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    CollectionProperty,
)
from bpy_extras.io_utils import ImportHelper, poll_file_object_drop
from bpy_extras.object_utils import AddObjectHelper
from bpy_extras.image_utils import load_image

import bmesh

# -----------------------------------------------------------------------------
# Core logic (duplicated from addon for startup availability)
# These functions create 3D meshes from images


def _get_image_pixels(image):
    if image is None:
        return None
    if image.size[0] == 0 or image.size[1] == 0:
        return None
    try:
        if len(image.pixels) == 0:
            try:
                image.reload()
            except Exception:
                pass
            if len(image.pixels) == 0:
                return None
    except Exception:
        pass
    try:
        pixels = image.pixels[:]
    except Exception:
        pixels = image.pixels
    return pixels


def _compute_target_resolution(img_w, img_h, max_resolution, use_full_resolution):
    if use_full_resolution:
        return img_w, img_h
    max_res = max(16, min(max_resolution, 2048))
    aspect = img_w / img_h if img_h != 0 else 1.0
    if img_w >= img_h:
        res_x = max_res
        res_y = max(1, int(round(max_res / aspect)))
    else:
        res_y = max_res
        res_x = max(1, int(round(max_res * aspect)))
    res_x = max(2, res_x)
    res_y = max(2, res_y)
    return res_x, res_y


def _compute_mesh_size(img_w, img_h, mesh_size):
    aspect = img_w / img_h if img_h != 0 else 1.0
    if aspect >= 1.0:
        scale_x = mesh_size
        scale_y = mesh_size / aspect
    else:
        scale_y = mesh_size
        scale_x = mesh_size * aspect
    return scale_x, scale_y


def _sample_mask(pixels, img_w, img_h, x, y, threshold_source='ALPHA', threshold=0.5, invert=False):
    if x < 0:
        x = 0
    elif x >= img_w:
        x = img_w - 1
    if y < 0:
        y = 0
    elif y >= img_h:
        y = img_h - 1
    idx = (y * img_w + x) * 4
    try:
        r = pixels[idx]
        g = pixels[idx + 1]
        b = pixels[idx + 2]
        a = pixels[idx + 3]
    except IndexError:
        return False
    if threshold_source == 'ALPHA':
        val = a
    elif threshold_source == 'LUMINANCE':
        val = 0.2126 * r + 0.7152 * g + 0.0722 * b
    elif threshold_source == 'RED':
        val = r
    elif threshold_source == 'GREEN':
        val = g
    elif threshold_source == 'BLUE':
        val = b
    else:
        val = a
    if invert:
        val = 1.0 - val
    return val >= threshold


def create_heightmap_mesh(image, name="ImageMesh", mesh_size=2.0, height_scale=0.3,
                          resolution=256, use_image_resolution=False,
                          height_source='LUMINANCE', invert_height=False,
                          triangulate=False):
    if image is None:
        return None
    img_w, img_h = image.size
    if img_w == 0 or img_h == 0:
        return None
    pixels = _get_image_pixels(image)
    if pixels is None or len(pixels) == 0:
        return None
    res_x, res_y = _compute_target_resolution(img_w, img_h, resolution, use_image_resolution)
    scale_x, scale_y = _compute_mesh_size(img_w, img_h, mesh_size)
    bm = bmesh.new()
    verts = []
    for iy in range(res_y):
        v = iy / (res_y - 1) if res_y > 1 else 0.5
        src_y = int(round(v * (img_h - 1)))
        if src_y < 0:
            src_y = 0
        elif src_y >= img_h:
            src_y = img_h - 1
        y_pos = (v - 0.5) * scale_y
        for ix in range(res_x):
            u = ix / (res_x - 1) if res_x > 1 else 0.5
            src_x = int(round(u * (img_w - 1)))
            if src_x < 0:
                src_x = 0
            elif src_x >= img_w:
                src_x = img_w - 1
            idx = (src_y * img_w + src_x) * 4
            try:
                r = pixels[idx]
                g = pixels[idx + 1]
                b = pixels[idx + 2]
                a = pixels[idx + 3]
            except IndexError:
                r = g = b = a = 0.0
            if height_source == 'LUMINANCE':
                h = 0.2126 * r + 0.7152 * g + 0.0722 * b
            elif height_source == 'RED':
                h = r
            elif height_source == 'GREEN':
                h = g
            elif height_source == 'BLUE':
                h = b
            elif height_source == 'ALPHA':
                h = a
            else:
                h = 0.2126 * r + 0.7152 * g + 0.0722 * b
            if invert_height:
                h = 1.0 - h
            x_pos = (u - 0.5) * scale_x
            z_pos = h * height_scale
            vert = bm.verts.new((x_pos, y_pos, z_pos))
            verts.append(vert)
    bm.verts.ensure_lookup_table()
    uv_layer = bm.loops.layers.uv.new("UVMap")
    for iy in range(res_y - 1):
        for ix in range(res_x - 1):
            i0 = iy * res_x + ix
            i1 = iy * res_x + ix + 1
            i2 = (iy + 1) * res_x + ix + 1
            i3 = (iy + 1) * res_x + ix
            if i0 >= len(verts) or i1 >= len(verts) or i2 >= len(verts) or i3 >= len(verts):
                continue
            try:
                face = bm.faces.new([verts[i0], verts[i1], verts[i2], verts[i3]])
            except ValueError:
                continue
            u0 = ix / (res_x - 1) if res_x > 1 else 0.0
            v0 = iy / (res_y - 1) if res_y > 1 else 0.0
            u1 = (ix + 1) / (res_x - 1) if res_x > 1 else 1.0
            v1 = iy / (res_y - 1) if res_y > 1 else 0.0
            u2 = (ix + 1) / (res_x - 1) if res_x > 1 else 1.0
            v2 = (iy + 1) / (res_y - 1) if res_y > 1 else 1.0
            u3 = ix / (res_x - 1) if res_x > 1 else 0.0
            v3 = (iy + 1) / (res_y - 1) if res_y > 1 else 1.0
            if len(face.loops) == 4:
                face.loops[0][uv_layer].uv = (u0, v0)
                face.loops[1][uv_layer].uv = (u1, v1)
                face.loops[2][uv_layer].uv = (u2, v2)
                face.loops[3][uv_layer].uv = (u3, v3)
    if triangulate:
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
    mesh = bpy.data.meshes.new(name + "_Mesh")
    bm.to_mesh(mesh)
    bm.free()
    mesh.validate()
    mesh.update()
    return mesh


def create_extruded_mesh(image, name="ImageMesh", mesh_size=2.0,
                         resolution=256, use_image_resolution=False,
                         threshold=0.5, threshold_source='ALPHA', invert_mask=False,
                         extrude_depth=0.3, triangulate=False):
    if image is None:
        return None
    img_w, img_h = image.size
    if img_w == 0 or img_h == 0:
        return None
    pixels = _get_image_pixels(image)
    if pixels is None or len(pixels) == 0:
        return None
    res_x, res_y = _compute_target_resolution(img_w, img_h, resolution, use_image_resolution)
    scale_x, scale_y = _compute_mesh_size(img_w, img_h, mesh_size)
    bm = bmesh.new()
    top_verts = []
    for iy in range(res_y):
        v = iy / (res_y - 1) if res_y > 1 else 0.5
        y_pos = (v - 0.5) * scale_y
        for ix in range(res_x):
            u = ix / (res_x - 1) if res_x > 1 else 0.5
            x_pos = (u - 0.5) * scale_x
            vert = bm.verts.new((x_pos, y_pos, 0.0))
            top_verts.append(vert)
    bm.verts.ensure_lookup_table()
    uv_layer = bm.loops.layers.uv.new("UVMap")
    top_faces = []
    for iy in range(res_y - 1):
        for ix in range(res_x - 1):
            u_center = (ix + 0.5) / (res_x - 1) if res_x > 1 else 0.5
            v_center = (iy + 0.5) / (res_y - 1) if res_y > 1 else 0.5
            src_x = int(round(u_center * (img_w - 1)))
            src_y = int(round(v_center * (img_h - 1)))
            if src_x < 0:
                src_x = 0
            elif src_x >= img_w:
                src_x = img_w - 1
            if src_y < 0:
                src_y = 0
            elif src_y >= img_h:
                src_y = img_h - 1
            passes = _sample_mask(pixels, img_w, img_h, src_x, src_y,
                                  threshold_source=threshold_source,
                                  threshold=threshold,
                                  invert=invert_mask)
            if not passes:
                continue
            i0 = iy * res_x + ix
            i1 = iy * res_x + ix + 1
            i2 = (iy + 1) * res_x + ix + 1
            i3 = (iy + 1) * res_x + ix
            if i0 >= len(top_verts) or i1 >= len(top_verts) or i2 >= len(top_verts) or i3 >= len(top_verts):
                continue
            try:
                face = bm.faces.new([top_verts[i0], top_verts[i1], top_verts[i2], top_verts[i3]])
            except ValueError:
                continue
            u0 = ix / (res_x - 1) if res_x > 1 else 0.0
            v0 = iy / (res_y - 1) if res_y > 1 else 0.0
            u1 = (ix + 1) / (res_x - 1) if res_x > 1 else 1.0
            v1 = iy / (res_y - 1) if res_y > 1 else 0.0
            u2 = (ix + 1) / (res_x - 1) if res_x > 1 else 1.0
            v2 = (iy + 1) / (res_y - 1) if res_y > 1 else 1.0
            u3 = ix / (res_x - 1) if res_x > 1 else 0.0
            v3 = (iy + 1) / (res_y - 1) if res_y > 1 else 1.0
            if len(face.loops) == 4:
                face.loops[0][uv_layer].uv = (u0, v0)
                face.loops[1][uv_layer].uv = (u1, v1)
                face.loops[2][uv_layer].uv = (u2, v2)
                face.loops[3][uv_layer].uv = (u3, v3)
            top_faces.append(face)
    top_to_bottom = {}
    bottom_verts = []
    for i, tv in enumerate(top_verts):
        bv = bm.verts.new((tv.co.x, tv.co.y, tv.co.z - extrude_depth))
        bottom_verts.append(bv)
        top_to_bottom[tv] = bv
    bm.verts.ensure_lookup_table()
    bottom_faces = []
    for tf in top_faces:
        verts_top = [l.vert for l in tf.loops]
        try:
            bv0 = top_to_bottom.get(verts_top[0])
            bv1 = top_to_bottom.get(verts_top[1])
            bv2 = top_to_bottom.get(verts_top[2])
            bv3 = top_to_bottom.get(verts_top[3])
            if bv0 is None or bv1 is None or bv2 is None or bv3 is None:
                continue
            bf = bm.faces.new([bv0, bv3, bv2, bv1])
            bottom_faces.append(bf)
            if len(bf.loops) == 4 and len(tf.loops) == 4:
                bf.loops[0][uv_layer].uv = tf.loops[0][uv_layer].uv
                bf.loops[1][uv_layer].uv = tf.loops[3][uv_layer].uv
                bf.loops[2][uv_layer].uv = tf.loops[2][uv_layer].uv
                bf.loops[3][uv_layer].uv = tf.loops[1][uv_layer].uv
        except ValueError:
            continue
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    for tf in top_faces:
        for loop in tf.loops:
            edge = loop.edge
            if len(edge.link_faces) != 1:
                continue
            v_start_top = loop.vert
            v_end_top = loop.link_loop_next.vert
            v_start_bottom = top_to_bottom.get(v_start_top)
            v_end_bottom = top_to_bottom.get(v_end_top)
            if v_start_bottom is None or v_end_bottom is None:
                continue
            try:
                sf = bm.faces.new([v_end_top, v_start_top, v_start_bottom, v_end_bottom])
                if len(sf.loops) == 4:
                    sf.loops[0][uv_layer].uv = (0.0, 0.0)
                    sf.loops[1][uv_layer].uv = (1.0, 0.0)
                    sf.loops[2][uv_layer].uv = (1.0, 1.0)
                    sf.loops[3][uv_layer].uv = (0.0, 1.0)
            except ValueError:
                continue
    if triangulate:
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
    mesh = bpy.data.meshes.new(name + "_Mesh")
    bm.to_mesh(mesh)
    bm.free()
    mesh.validate()
    mesh.update()
    return mesh


def create_material_with_image(image, name="ImageMaterial", use_alpha=True):
    if image is None:
        return None
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    node_tree = mat.node_tree
    nodes = node_tree.nodes
    links = node_tree.links
    nodes.clear()
    output = nodes.new(type='ShaderNodeOutputMaterial')
    output.location = (300, 0)
    principled = nodes.new(type='ShaderNodeBsdfPrincipled')
    principled.location = (0, 0)
    tex_image = nodes.new(type='ShaderNodeTexImage')
    tex_image.location = (-400, 0)
    tex_image.image = image
    tex_image.interpolation = 'Linear'
    tex_image.extension = 'CLIP'
    links.new(principled.inputs['Base Color'], tex_image.outputs['Color'])
    if use_alpha:
        try:
            if 'Alpha' in principled.inputs:
                links.new(principled.inputs['Alpha'], tex_image.outputs['Alpha'])
                mat.blend_method = 'CLIP'
                if hasattr(mat, 'shadow_method'):
                    mat.shadow_method = 'CLIP'
        except Exception:
            pass
    links.new(output.inputs['Surface'], principled.outputs['BSDF'])
    return mat


# -----------------------------------------------------------------------------
# Operator - provides upload UI and immediate 3D generation


class IMPORT_MESH_OT_image_to_3d(Operator, ImportHelper, AddObjectHelper):
    """Create a 3D mesh from an image - upload image for immediate 3D representation"""
    bl_idname = "import_mesh.image_to_3d"
    bl_label = "Image to 3D Mesh"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ""
    filter_glob: StringProperty(
        default="*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.tga;*.tiff;*.exr;*.hdr",
        options={'HIDDEN'},
    )

    directory: StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE', 'HIDDEN'})
    files: CollectionProperty(
        name="File Path",
        type=bpy.types.OperatorFileListElement,
    )

    method: EnumProperty(
        name="Method",
        description="How to convert image to 3D mesh",
        items=(
            ('HEIGHTMAP', "Heightmap (Relief)", "Create a displaced plane where brightness controls height, giving a 3D relief of the image"),
            ('EXTRUDE', "Extruded Silhouette", "Create an extruded 3D shape from the image silhouette using alpha or luminance threshold"),
        ),
        default='HEIGHTMAP',
    )

    mesh_size: FloatProperty(
        name="Mesh Size",
        description="Size of the longest side of the mesh in Blender units",
        default=2.0,
        min=0.01,
        max=100.0,
        unit='LENGTH',
    )

    resolution: IntProperty(
        name="Resolution",
        description="Maximum resolution (longest side) of the generated mesh. Higher values give more detail but more vertices",
        default=256,
        min=16,
        max=2048,
    )

    use_image_resolution: BoolProperty(
        name="Use Image Resolution",
        description="Use the exact image resolution for mesh (may create many vertices for large images)",
        default=False,
    )

    add_material: BoolProperty(
        name="Add Material",
        description="Create a material using the image as texture",
        default=True,
    )

    height_scale: FloatProperty(
        name="Height Scale",
        description="How much the brightness displaces the mesh in Z direction",
        default=0.3,
        min=0.0,
        max=10.0,
        unit='LENGTH',
    )

    height_source: EnumProperty(
        name="Height Source",
        description="Which channel to use for height displacement",
        items=(
            ('LUMINANCE', "Luminance", "Use luminance (brightness) for height"),
            ('RED', "Red", "Use red channel for height"),
            ('GREEN', "Green", "Use green channel for height"),
            ('BLUE', "Blue", "Use blue channel for height"),
            ('ALPHA', "Alpha", "Use alpha channel for height"),
        ),
        default='LUMINANCE',
    )

    invert_height: BoolProperty(
        name="Invert Height",
        description="Invert the height displacement (dark becomes high)",
        default=False,
    )

    triangulate: BoolProperty(
        name="Triangulate",
        description="Triangulate mesh faces",
        default=False,
    )

    threshold: FloatProperty(
        name="Threshold",
        description="Threshold for silhouette detection (0-1)",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
    )

    threshold_source: EnumProperty(
        name="Threshold Source",
        description="Which channel to use for thresholding",
        items=(
            ('ALPHA', "Alpha", "Use alpha channel for silhouette"),
            ('LUMINANCE', "Luminance", "Use luminance for silhouette"),
            ('RED', "Red", "Use red channel"),
            ('GREEN', "Green", "Use green channel"),
            ('BLUE', "Blue", "Use blue channel"),
        ),
        default='ALPHA',
    )

    invert_mask: BoolProperty(
        name="Invert Mask",
        description="Invert the threshold mask",
        default=False,
    )

    extrude_depth: FloatProperty(
        name="Extrude Depth",
        description="How deep to extrude the silhouette",
        default=0.3,
        min=0.01,
        max=10.0,
        unit='LENGTH',
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "method")
        box = layout.box()
        box.label(text="Mesh Settings", icon='MESH_GRID')
        box.prop(self, "mesh_size")
        box.prop(self, "resolution")
        box.prop(self, "use_image_resolution")
        box.prop(self, "add_material")
        box.prop(self, "triangulate")
        if self.method == 'HEIGHTMAP':
            box = layout.box()
            box.label(text="Heightmap Settings", icon='IMAGE_DATA')
            box.prop(self, "height_scale")
            box.prop(self, "height_source")
            box.prop(self, "invert_height")
        elif self.method == 'EXTRUDE':
            box = layout.box()
            box.label(text="Extrude Settings", icon='MOD_SOLIDIFY')
            box.prop(self, "threshold_source")
            box.prop(self, "threshold")
            box.prop(self, "invert_mask")
            box.prop(self, "extrude_depth")

    def execute(self, context):
        import os
        files = []
        if self.files:
            for f in self.files:
                files.append(os.path.join(self.directory, f.name))
        else:
            files.append(self.filepath)
        if not files:
            self.report({'ERROR'}, "No files selected")
            return {'CANCELLED'}
        created_objects = []
        for filepath in files:
            if not os.path.isfile(filepath):
                continue
            try:
                image = bpy.data.images.load(filepath, check_existing=True)
            except RuntimeError as e:
                self.report({'WARNING'}, f"Failed to load image {filepath}: {e}")
                continue
            if image.size[0] == 0 or image.size[1] == 0:
                try:
                    image.reload()
                except Exception:
                    pass
                if image.size[0] == 0 or image.size[1] == 0:
                    self.report({'WARNING'}, f"Image {filepath} has zero size")
                    continue
            name = os.path.splitext(os.path.basename(filepath))[0]
            if self.method == 'HEIGHTMAP':
                mesh = create_heightmap_mesh(
                    image,
                    name=name,
                    mesh_size=self.mesh_size,
                    height_scale=self.height_scale,
                    resolution=self.resolution,
                    use_image_resolution=self.use_image_resolution,
                    height_source=self.height_source,
                    invert_height=self.invert_height,
                    triangulate=self.triangulate,
                )
            else:
                mesh = create_extruded_mesh(
                    image,
                    name=name,
                    mesh_size=self.mesh_size,
                    resolution=self.resolution,
                    use_image_resolution=self.use_image_resolution,
                    threshold=self.threshold,
                    threshold_source=self.threshold_source,
                    invert_mask=self.invert_mask,
                    extrude_depth=self.extrude_depth,
                    triangulate=self.triangulate,
                )
            if mesh is None:
                self.report({'WARNING'}, f"Failed to create mesh for {filepath}")
                continue
            mat = None
            if self.add_material:
                mat = create_material_with_image(
                    image,
                    name=name + "_Material",
                    use_alpha=True,
                )
                if mat:
                    mesh.materials.append(mat)
            obj = bpy.data.objects.new(name + "_3D", mesh)
            collection = context.view_layer.active_layer_collection.collection
            collection.objects.link(obj)
            try:
                if hasattr(self, 'location'):
                    obj.location = self.location
                else:
                    obj.location = context.scene.cursor.location
            except Exception:
                obj.location = context.scene.cursor.location
            obj.select_set(True)
            context.view_layer.objects.active = obj
            created_objects.append(obj)
        if not created_objects:
            self.report({'ERROR'}, "No meshes created")
            return {'CANCELLED'}
        if self.method == 'HEIGHTMAP':
            for obj in created_objects:
                mesh = obj.data
                try:
                    for poly in mesh.polygons:
                        poly.use_smooth = True
                except Exception:
                    pass
                mesh.update()
        self.report({'INFO'}, f"Created {len(created_objects)} 3D mesh(es) from image(s)")
        return {'FINISHED'}

    def invoke(self, context, event):
        if self.properties.is_property_set("filepath"):
            return self.execute(context)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class IO_FH_image_to_3d(FileHandler):
    bl_idname = "IO_FH_image_to_3d"
    bl_label = "Image to 3D Mesh"
    bl_import_operator = "import_mesh.image_to_3d"
    bl_file_extensions = ".png;.jpg;.jpeg;.webp;.bmp;.tga;.tiff;.exr;.hdr"

    @classmethod
    def poll_drop(cls, context):
        return poll_file_object_drop(context)


classes = (
    IMPORT_MESH_OT_image_to_3d,
    IO_FH_image_to_3d,
)
