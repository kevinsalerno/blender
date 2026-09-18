# SPDX-FileCopyrightText: 2024 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

bl_info = {
    "name": "Image to 3D Mesh",
    "author": "Blender Foundation",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "File > Import > Image to 3D Mesh, Add > Mesh > Image as 3D",
    "description": "Upload an image and immediately create a 3D mesh representation using heightmap displacement or extruded silhouette",
    "warning": "",
    "doc_url": "https://docs.blender.org/manual/en/latest/addons/import_export/image_to_3d.html",
    "support": 'OFFICIAL',
    "category": "Import-Export",
}

# To support reload properly, try to access a package var,
# if it's there, reload everything
if "bpy" in locals():
    import importlib
    if "image_to_mesh" in locals():
        importlib.reload(image_to_mesh)

import bpy
from bpy.types import Operator
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

COMPATIBLE_ENGINES = {'CYCLES', 'BLENDER_EEVEE', 'BLENDER_WORKBENCH'}


class IMPORT_MESH_OT_image_to_3d(Operator, ImportHelper, AddObjectHelper):
    """Upload an image and create a 3D mesh representation"""
    bl_idname = "import_mesh.image_to_3d"
    bl_label = "Image to 3D Mesh"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ""
    filter_glob: StringProperty(
        default="*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.tga;*.tiff;*.exr;*.hdr",
        options={'HIDDEN'},
    )

    # ImportHelper properties
    directory: StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE', 'HIDDEN'})
    files: CollectionProperty(
        name="File Path",
        type=bpy.types.OperatorFileListElement,
    )

    # Method
    method: EnumProperty(
        name="Method",
        description="How to convert image to 3D mesh",
        items=(
            ('HEIGHTMAP', "Heightmap (Relief)", "Create a displaced plane where brightness controls height, giving a 3D relief of the image"),
            ('EXTRUDE', "Extruded Silhouette", "Create an extruded 3D shape from the image silhouette using alpha or luminance threshold"),
        ),
        default='HEIGHTMAP',
    )

    # Common properties
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

    # Heightmap specific
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
        description="Triangulate mesh faces (useful for heightmap to avoid non-planar quads)",
        default=False,
    )

    # Extrude specific
    threshold: FloatProperty(
        name="Threshold",
        description="Threshold for silhouette detection (0-1). Pixels above threshold become solid",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
    )

    threshold_source: EnumProperty(
        name="Threshold Source",
        description="Which channel to use for thresholding",
        items=(
            ('ALPHA', "Alpha", "Use alpha channel (transparency) for silhouette"),
            ('LUMINANCE', "Luminance", "Use luminance (brightness) for silhouette"),
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

        # Method
        layout.prop(self, "method")

        # Common
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
        from . import image_to_mesh

        import os

        # Collect files
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
                # Skip if not a file (might be directory)
                continue

            # Load image and create mesh based on method
            # We will use the high-level API from image_to_mesh module
            # To avoid duplicating logic, we call the functions directly

            # Load image
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

            # Create mesh
            if self.method == 'HEIGHTMAP':
                mesh = image_to_mesh.create_heightmap_mesh(
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
            else:  # EXTRUDE
                mesh = image_to_mesh.create_extruded_mesh(
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
                # Remove image if it was newly loaded and not used? We keep it for material
                continue

            # Create material if requested
            mat = None
            if self.add_material:
                mat = image_to_mesh.create_material_with_image(
                    image,
                    name=name + "_Material",
                    use_alpha=True,
                )
                if mat:
                    mesh.materials.append(mat)

            # Create object
            obj = bpy.data.objects.new(name + "_3D", mesh)

            # Link object to scene
            # Use AddObjectHelper to place at cursor?
            # We will use object_data_add which respects AddObjectHelper (cursor location, etc.)
            from bpy_extras import object_utils
            # We need to set self.location etc. from AddObjectHelper? The AddObjectHelper provides location, rotation
            # We can use object_utils.object_data_add
            # But our mesh is already created, and object is created. We need to add object to collection and set transform.

            # Use object_data_add's behavior: it handles collection and cursor
            # However we already have mesh, we can use object_utils.object_data_add with our mesh but we already have object
            # Let's use the standard way: link to active collection

            # Get active collection
            collection = context.view_layer.active_layer_collection.collection
            collection.objects.link(obj)

            # Set location from AddObjectHelper if available (cursor)
            # AddObjectHelper provides self.location, but IMPORT operator may not have it? It inherits AddObjectHelper
            # So we can check
            try:
                # If AddObjectHelper provided location, use it
                if hasattr(self, 'location'):
                    obj.location = self.location
                else:
                    # Place at cursor
                    obj.location = context.scene.cursor.location
            except Exception:
                obj.location = context.scene.cursor.location

            # Select and make active
            obj.select_set(True)
            context.view_layer.objects.active = obj

            created_objects.append(obj)

            # For extruded mesh, we might want to add smooth shading?
            # For heightmap, set shade smooth
            if self.method == 'HEIGHTMAP':
                # Shade smooth for better relief appearance
                # Need to set in object mode
                # Use mesh polygons shade smooth
                # We need to be in object mode or use bmesh? We can set via mesh
                # Blender 4.x: mesh shade smooth is via attribute? Simpler: use operator after creation
                # We can set shade smooth via bpy.ops.object.shade_smooth but need to be active
                # Let's try to set via mesh
                # In Blender 4.0+, shade smooth is a boolean per face, but we can use mesh's shade_smooth? Actually
                # The simplest: select all faces and shade smooth
                # We can do via bpy context with temp override? For now, just set via mesh's use_auto_smooth? That's deprecated.
                # Instead, we can call shade smooth operator if we are in object mode
                # We'll do it after the loop for each object
                pass

        if not created_objects:
            self.report({'ERROR'}, "No meshes created")
            return {'CANCELLED'}

        # Apply shade smooth for heightmap meshes
        # We need to ensure we are in object mode
        # Use temp override to set active object for operator

        # Deselect all first, then select created objects for shading
        # But we already selected them

        # For heightmap, shade smooth
        if self.method == 'HEIGHTMAP':
            # Use bpy.ops.object.shade_smooth with selected objects
            # Need to ensure we are in object mode
            # We can try to call the operator with context
            # Since we are inside execute, context is available
            # We can use context.temp_override to set active object one by one?

            # Simplest: for each object, set shade smooth via mesh polygons
            for obj in created_objects:
                mesh = obj.data
                # In Blender 4.x, shade smooth is per face: mesh.polygons[i].use_smooth = True
                # But for newer versions, it's an attribute? Let's try both
                try:
                    for poly in mesh.polygons:
                        poly.use_smooth = True
                except Exception:
                    pass
                mesh.update()

        self.report({'INFO'}, f"Created {len(created_objects)} 3D mesh(es) from image(s)")

        return {'FINISHED'}

    def invoke(self, context, event):
        # If filepath is already set (e.g., from file handler drop), execute directly
        if self.properties.is_property_set("filepath"):
            return self.execute(context)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class IO_FH_image_to_3d(bpy.types.FileHandler):
    bl_idname = "IO_FH_image_to_3d"
    bl_label = "Image to 3D Mesh"
    bl_import_operator = "import_mesh.image_to_3d"
    bl_file_extensions = ".png;.jpg;.jpeg;.webp;.bmp;.tga;.tiff;.exr;.hdr"

    @classmethod
    def poll_drop(cls, context):
        return poll_file_object_drop(context)


class VIEW3D_MT_image_to_3d_add(bpy.types.Menu):
    """Menu for adding Image to 3D"""
    bl_label = "Image to 3D"

    def draw(self, context):
        self.layout.operator(IMPORT_MESH_OT_image_to_3d.bl_idname, text="Image as Heightmap Relief")
        # Could add separate entries for methods with preset
        op = self.layout.operator(IMPORT_MESH_OT_image_to_3d.bl_idname, text="Image as Extruded Silhouette")
        op.method = 'EXTRUDE'


class VIEW3D_PT_image_to_3d_panel(bpy.types.Panel):
    """Panel for quick access to Image to 3D"""
    bl_label = "Image to 3D"
    bl_idname = "VIEW3D_PT_image_to_3d"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Create"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.label(text="Create 3D from Image", icon='IMAGE_DATA')
        layout.operator(IMPORT_MESH_OT_image_to_3d.bl_idname, text="Upload Image to 3D", icon='IMAGE_DATA')
        layout.separator()
        layout.label(text="Method:", icon='MESH_GRID')
        # These operators will open file dialog with preset method
        op = layout.operator(IMPORT_MESH_OT_image_to_3d.bl_idname, text="Heightmap Relief", icon='MOD_DISPLACE')
        op.method = 'HEIGHTMAP'
        op = layout.operator(IMPORT_MESH_OT_image_to_3d.bl_idname, text="Extruded Silhouette", icon='MOD_SOLIDIFY')
        op.method = 'EXTRUDE'


# Addon preferences (optional)
class ImageTo3DPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    default_method: EnumProperty(
        name="Default Method",
        items=(
            ('HEIGHTMAP', "Heightmap", "Default to heightmap relief"),
            ('EXTRUDE', "Extrude", "Default to extruded silhouette"),
        ),
        default='HEIGHTMAP',
    )

    default_resolution: IntProperty(
        name="Default Resolution",
        default=256,
        min=16,
        max=2048,
    )

    default_height_scale: FloatProperty(
        name="Default Height Scale",
        default=0.3,
        min=0.0,
        max=10.0,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "default_method")
        layout.prop(self, "default_resolution")
        layout.prop(self, "default_height_scale")


# Menus
def menu_func_import(self, context):
    self.layout.operator(IMPORT_MESH_OT_image_to_3d.bl_idname, text="Image to 3D Mesh (.png, .jpg, .webp...)")


def menu_func_add_mesh(self, context):
    self.layout.menu(VIEW3D_MT_image_to_3d_add.bl_idname, icon='IMAGE_DATA')
    # Also direct operator for quick access
    # self.layout.operator(IMPORT_MESH_OT_image_to_3d.bl_idname, text="Image as 3D Mesh", icon='IMAGE_DATA')


classes = (
    IMPORT_MESH_OT_image_to_3d,
    IO_FH_image_to_3d,
    VIEW3D_MT_image_to_3d_add,
    VIEW3D_PT_image_to_3d_panel,
    ImageTo3DPreferences,
)


def register():
    # Register classes, but gracefully handle duplicates if operator already registered
    # from startup (bl_operators/image_to_3d.py). The startup version provides
    # immediate capability without needing addon, while addon adds extra UI.
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            # If class already registered (e.g., operator from startup), skip
            # Check if error mentions already registered
            err_str = str(e).lower()
            if "already" in err_str or "exists" in err_str or "register" in err_str:
                # If it's the main operator, it means startup already provides it
                # We can still proceed to register remaining UI classes
                # For operator duplicate, we should not fail entire addon registration
                # Try to check if bl_idname already exists in bpy.types
                # If operator already exists, skip it and continue
                # For other classes, we should also skip if duplicate
                continue
            else:
                raise

    # UI integration - these may already be registered by startup? No, startup doesn't add these menus,
    # so we should add them. But if duplicate, we handle via try/except as well
    try:
        bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    except Exception:
        pass
    try:
        bpy.types.VIEW3D_MT_mesh_add.append(menu_func_add_mesh)
    except Exception:
        pass


def unregister():
    try:
        bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    except Exception:
        pass
    try:
        bpy.types.VIEW3D_MT_mesh_add.remove(menu_func_add_mesh)
    except Exception:
        pass

    for cls in reversed(classes):
        try:
            if hasattr(cls, 'is_registered') and cls.is_registered:
                bpy.utils.unregister_class(cls)
            else:
                # Try to unregister anyway, but ignore if not registered
                try:
                    bpy.utils.unregister_class(cls)
                except Exception:
                    pass
        except Exception:
            pass


if __name__ == "__main__":
    register()
