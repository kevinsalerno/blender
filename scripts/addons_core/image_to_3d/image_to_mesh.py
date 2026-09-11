# SPDX-FileCopyrightText: 2024 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Image to 3D Mesh - Core conversion logic

Provides functions to create 3D meshes from images using:
- Heightmap displacement (relief)
- Extruded silhouette (threshold + extrusion)
"""

import bpy
import bmesh
from mathutils import Vector


def _get_image_pixels(image):
    """
    Return image pixels as flat list of floats (RGBA).
    Ensure image is loaded and has pixels.
    """
    # Ensure image is loaded
    if image.size[0] == 0 or image.size[1] == 0:
        return None

    # Force load if needed
    # image.pixels accesses the buffer; ensure it's loaded via reload or using image.pixels[:]
    # In Blender, image.pixels may be empty if not previously accessed; accessing it loads.
    # We need to ensure the image is in memory.
    # For generated images, it should be fine.
    # For file images, we may need to ensure it is loaded.
    # The safest: try to access pixels, and if length is 0, try reload.

    # Check if pixels are available
    try:
        # In Blender, image.pixels is a FloatProperty that can be sliced
        # If image has not been loaded, size may be 0 or pixels empty
        if len(image.pixels) == 0:
            # Try to reload
            try:
                image.reload()
            except Exception:
                pass
            if len(image.pixels) == 0:
                return None
    except Exception:
        # Fallback: try to get from image.pixels
        pass

    # Now get pixels
    # Using image.pixels[:] returns a copy as tuple/list
    # For large images, this copy is okay
    try:
        pixels = image.pixels[:]
    except Exception:
        # If slicing fails, try direct
        pixels = image.pixels

    return pixels


def _sample_pixel(pixels, img_w, img_h, x, y, height_source='LUMINANCE', invert=False):
    """
    Sample a pixel at integer coordinates (x,y) where (0,0) is bottom-left.
    pixels is flat RGBA float array, length = w*h*4
    """
    # Clamp
    if x < 0:
        x = 0
    elif x >= img_w:
        x = img_w - 1
    if y < 0:
        y = 0
    elif y >= img_h:
        y = img_h - 1

    idx = (y * img_w + x) * 4
    # Ensure idx within bounds
    # pixels may be tuple/list
    try:
        r = pixels[idx]
        g = pixels[idx + 1]
        b = pixels[idx + 2]
        a = pixels[idx + 3]
    except IndexError:
        # Fallback to 0
        return 0.0

    if height_source == 'LUMINANCE':
        # Rec. 709 luma
        val = 0.2126 * r + 0.7152 * g + 0.0722 * b
    elif height_source == 'RED':
        val = r
    elif height_source == 'GREEN':
        val = g
    elif height_source == 'BLUE':
        val = b
    elif height_source == 'ALPHA':
        val = a
    else:
        val = 0.2126 * r + 0.7152 * g + 0.0722 * b

    if invert:
        val = 1.0 - val

    return val


def _compute_target_resolution(img_w, img_h, max_resolution, use_full_resolution):
    """
    Compute target resolution (res_x, res_y) preserving aspect ratio.
    If use_full_resolution True, return img_w, img_h.
    Else, max dimension is max_resolution, other dimension scaled proportionally.
    """
    if use_full_resolution:
        return img_w, img_h

    # Clamp max_resolution to at least 16 and at most 2048
    max_res = max(16, min(max_resolution, 2048))

    aspect = img_w / img_h if img_h != 0 else 1.0

    if img_w >= img_h:
        # Width is longest
        res_x = max_res
        res_y = max(1, int(round(max_res / aspect)))
    else:
        res_y = max_res
        res_x = max(1, int(round(max_res * aspect)))

    # Ensure at least 2 for faces, but allow 1 for edge case
    res_x = max(2, res_x)
    res_y = max(2, res_y)

    # Also clamp to image dimensions if image smaller than max_res? We already handled aspect, but if image is smaller,
    # we might want to not upscale? However for heightmap, upscaling is okay (interpolates). But to avoid excessive
    # vertices when image is small, we should not exceed image dimensions unless explicitly requested?
    # For simplicity, we allow upscaling, but we could also clamp.
    # Let's clamp to image dims if use_full_resolution False and image smaller than max_res: use image dims
    # Actually we want to limit vertices, so if image is 100x100 and max_res=256, res_x=256 would upscale 2.5x, creating
    # more vertices than pixels, which is okay but interpolation will be needed. We could just use the computed res.
    # We'll keep computed res, but also ensure we don't exceed 2048.

    return res_x, res_y


def _compute_mesh_size(img_w, img_h, mesh_size):
    """
    Compute scale_x, scale_y for mesh to preserve aspect ratio.
    mesh_size is the size of the longest side in Blender units.
    """
    aspect = img_w / img_h if img_h != 0 else 1.0
    if aspect >= 1.0:
        scale_x = mesh_size
        scale_y = mesh_size / aspect
    else:
        scale_y = mesh_size
        scale_x = mesh_size * aspect
    return scale_x, scale_y


def create_heightmap_mesh(image, name="ImageMesh", mesh_size=2.0, height_scale=0.3,
                          resolution=256, use_image_resolution=False,
                          height_source='LUMINANCE', invert_height=False,
                          smooth_iterations=0, triangulate=False):
    """
    Create a heightmap displacement mesh from an image.

    :param image: bpy.types.Image
    :param name: Name for mesh and object
    :param mesh_size: Size of longest side in Blender units
    :param height_scale: Scale of displacement in Z
    :param resolution: Max resolution (longest side) when not using full image resolution
    :param use_image_resolution: If True, use image width/height as resolution
    :param height_source: How to compute height: 'LUMINANCE', 'RED', 'GREEN', 'BLUE', 'ALPHA'
    :param invert_height: Invert height
    :param smooth_iterations: Number of smoothing iterations (not implemented yet, placeholder)
    :param triangulate: Triangulate faces
    :return: bpy.types.Mesh or None
    """
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

    # Create bmesh
    bm = bmesh.new()

    # Create vertices
    # We will store verts in a 2D list or flat list for quick access
    verts = []  # flat list, row-major: index = y*res_x + x

    # Precompute for speed: we will sample pixels per vertex
    # For each vertex, compute corresponding image pixel coordinate

    # To avoid repeated division, compute step
    # For res_x >1, step = (img_w-1)/(res_x-1), similarly for y
    # But we will compute per vertex using float then int

    # Use local variables for speed
    # Access pixels as list/tuple

    # For performance, we can use direct index calculation
    # We'll loop over y then x

    # We need to handle that image pixels are bottom to top, so y=0 bottom corresponds to image y=0 bottom, which matches our mesh y= -scale_y/2 bottom
    # That's fine.

    for iy in range(res_y):
        # Compute v = iy/(res_y-1) 0-1
        v = iy / (res_y - 1) if res_y > 1 else 0.5
        # Corresponding image y
        # Map v in [0,1] to image y in [0, img_h-1]
        src_y = int(round(v * (img_h - 1)))
        # Clamp
        if src_y < 0:
            src_y = 0
        elif src_y >= img_h:
            src_y = img_h - 1

        # Compute y position in mesh: from -scale_y/2 to +scale_y/2
        y_pos = (v - 0.5) * scale_y

        for ix in range(res_x):
            u = ix / (res_x - 1) if res_x > 1 else 0.5
            src_x = int(round(u * (img_w - 1)))
            if src_x < 0:
                src_x = 0
            elif src_x >= img_w:
                src_x = img_w - 1

            # Sample height
            # Instead of calling _sample_pixel which does bounds checks again, we can inline for speed
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

    # Create UV layer
    uv_layer = bm.loops.layers.uv.new("UVMap")

    # Create faces
    # For each quad (ix, iy) where ix < res_x-1 and iy < res_y-1, create face
    # Face vertices: i0 = iy*res_x + ix, i1 = iy*res_x + ix+1, i2 = (iy+1)*res_x + ix+1, i3 = (iy+1)*res_x + ix
    # Winding CCW for +Z normal: i0, i1, i2, i3

    for iy in range(res_y - 1):
        for ix in range(res_x - 1):
            i0 = iy * res_x + ix
            i1 = iy * res_x + ix + 1
            i2 = (iy + 1) * res_x + ix + 1
            i3 = (iy + 1) * res_x + ix

            # Ensure indices are valid
            if i0 >= len(verts) or i1 >= len(verts) or i2 >= len(verts) or i3 >= len(verts):
                continue

            try:
                face = bm.faces.new([verts[i0], verts[i1], verts[i2], verts[i3]])
            except ValueError:
                # Face may already exist or verts duplicate
                continue

            # Set UVs
            # UVs: (u, v) where u = ix/(res_x-1), v = iy/(res_y-1), etc.
            # For face, loops are in order of verts passed
            # So loop 0 corresponds to i0, loop1 to i1, etc.

            # Compute UVs
            u0 = ix / (res_x - 1) if res_x > 1 else 0.0
            v0 = iy / (res_y - 1) if res_y > 1 else 0.0
            u1 = (ix + 1) / (res_x - 1) if res_x > 1 else 1.0
            v1 = iy / (res_y - 1) if res_y > 1 else 0.0
            u2 = (ix + 1) / (res_x - 1) if res_x > 1 else 1.0
            v2 = (iy + 1) / (res_y - 1) if res_y > 1 else 1.0
            u3 = ix / (res_x - 1) if res_x > 1 else 0.0
            v3 = (iy + 1) / (res_y - 1) if res_y > 1 else 1.0

            # Assign
            # face.loops is a sequence of 4 loops
            # Need to ensure we have 4 loops
            if len(face.loops) == 4:
                face.loops[0][uv_layer].uv = (u0, v0)
                face.loops[1][uv_layer].uv = (u1, v1)
                face.loops[2][uv_layer].uv = (u2, v2)
                face.loops[3][uv_layer].uv = (u3, v3)

    # Optional triangulate
    if triangulate:
        bmesh.ops.triangulate(bm, faces=bm.faces[:])

    # Create mesh
    mesh = bpy.data.meshes.new(name + "_Mesh")

    bm.to_mesh(mesh)
    bm.free()

    # Validate and update
    mesh.validate()
    mesh.update()

    return mesh


def _sample_mask(pixels, img_w, img_h, x, y, threshold_source='ALPHA', threshold=0.5, invert=False):
    """
    Sample mask value: return True if pixel passes threshold (opaque/bright enough)
    """
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


def create_extruded_mesh(image, name="ImageMesh", mesh_size=2.0,
                         resolution=256, use_image_resolution=False,
                         threshold=0.5, threshold_source='ALPHA', invert_mask=False,
                         extrude_depth=0.3, triangulate=False):
    """
    Create an extruded silhouette mesh from an image.

    The image is thresholded to create a binary mask. Pixels passing the threshold become
    part of the mesh. The mesh is a planar grid where only passing faces are kept,
    then extruded to give thickness.

    :param image: bpy.types.Image
    :param name: Mesh name
    :param mesh_size: Longest side size
    :param resolution: Max resolution
    :param use_image_resolution: Use full image resolution
    :param threshold: Threshold 0-1
    :param threshold_source: Channel to threshold: 'ALPHA' or 'LUMINANCE' etc.
    :param invert_mask: Invert mask
    :param extrude_depth: Depth of extrusion
    :param triangulate: Triangulate
    :return: bpy.types.Mesh
    """
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

    # Create top vertices (flat at z=0)
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

    # UV layer
    uv_layer = bm.loops.layers.uv.new("UVMap")

    # Create top faces only where mask passes
    # For each quad, sample at its center or at its lower-left corner? Use center for better accuracy
    top_faces = []

    for iy in range(res_y - 1):
        for ix in range(res_x - 1):
            # Sample mask at center of quad
            # Center u, v
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

            # UVs
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

    # If no faces created, return None or empty mesh
    if len(top_faces) == 0:
        # No silhouette found - maybe threshold too high, try to create at least a plane?
        # For robustness, create a simple plane as fallback
        # But we will return None to indicate failure, caller can handle
        # Let's still create a mesh with no faces (empty) -> will be freed
        # Instead, we will create a full plane as fallback? Better to return None and let operator report.
        # For now, we will proceed to extrusion only if faces exist.
        pass

    # Now create bottom vertices for all top verts (for simplicity, for all, not just used)
    # This may create unused bottom verts, but we will clean up later with remove_doubles or leave
    # To keep mapping simple, create bottom verts in same order
    top_to_bottom = {}
    bottom_verts = []

    for i, tv in enumerate(top_verts):
        # Only create bottom vert if this top vert is used in any top face?
        # Check if vert is used: if it has any link faces
        # But at this point, after creating top faces, some verts may be unused (if mask false around)
        # We should still create bottom verts only for used verts to avoid unnecessary verts
        # Let's check usage
        if len(tv.link_faces) == 0:
            # Unused top vert, skip bottom creation? But then side face creation for boundary edges will need mapping
            # For simplicity, create bottom vert for all, but we will have many unused verts
            # We'll create for all to keep index correspondence
            pass

        bv = bm.verts.new((tv.co.x, tv.co.y, tv.co.z - extrude_depth))
        bottom_verts.append(bv)
        top_to_bottom[tv] = bv

    bm.verts.ensure_lookup_table()

    # Create bottom faces (reverse winding)
    # For each top face, create bottom face with reversed winding
    bottom_faces = []
    for tf in top_faces:
        # tf.verts are in order i0,i1,i2,i3
        # Need to map to bottom verts
        # Get verts in order
        verts_top = [l.vert for l in tf.loops]  # loops order corresponds to face verts order
        # For bottom, we want reverse: [v0, v3, v2, v1] bottom
        # verts_top[0]=i0, [1]=i1, [2]=i2, [3]=i3
        # So bottom face should be [bottom_i0, bottom_i3, bottom_i2, bottom_i1]
        # That is [verts_top[0], verts_top[3], verts_top[2], verts_top[1]] mapped to bottom
        try:
            bv0 = top_to_bottom.get(verts_top[0])
            bv1 = top_to_bottom.get(verts_top[1])
            bv2 = top_to_bottom.get(verts_top[2])
            bv3 = top_to_bottom.get(verts_top[3])
            if bv0 is None or bv1 is None or bv2 is None or bv3 is None:
                continue
            # Reverse winding: bv0, bv3, bv2, bv1
            bf = bm.faces.new([bv0, bv3, bv2, bv1])
            bottom_faces.append(bf)
            # UVs for bottom face: same as top but reversed order? For simplicity, use same UVs but reversed winding
            # UVs should correspond: bottom face loops 0->bv0 (uv u0,v0), 1->bv3 (uv u3,v3), 2->bv2 (uv u2,v2), 3->bv1 (uv u1,v1)
            # We need to get UVs from top face loops? Or recompute based on original ix,iy?
            # Since we don't have ix,iy for this bottom face directly, we can copy UVs from top face but reversed
            # Top face UVs: loops 0:u0,v0, 1:u1,v1, 2:u2,v2, 3:u3,v3
            # So bottom face UVs: loop0 (bv0) should be u0,v0, loop1 (bv3) u3,v3, loop2 (bv2) u2,v2, loop3 (bv1) u1,v1
            if len(bf.loops) == 4 and len(tf.loops) == 4:
                bf.loops[0][uv_layer].uv = tf.loops[0][uv_layer].uv
                bf.loops[1][uv_layer].uv = tf.loops[3][uv_layer].uv
                bf.loops[2][uv_layer].uv = tf.loops[2][uv_layer].uv
                bf.loops[3][uv_layer].uv = tf.loops[1][uv_layer].uv
        except ValueError:
            continue

    # Now create side faces for boundary edges of top_faces
    # To find boundary edges, we need to check edges that have only one face (top face) before bottom creation?
    # But after creating bottom faces, edges that were boundary of top may now have more than one face? Actually top and bottom are disconnected (different verts), so boundary edges of top are still boundary (one face) before side faces.
    # However, we have created bottom verts separate, so top edges still have only one face.
    # So we can iterate over top_faces loops and check edge.is_boundary

    # Collect side faces to avoid duplicate creation for shared edges? Boundary edges are unique, so no duplicate.

    # We need to ensure we have lookup for edges
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    # For each top face, check its edges
    # To avoid creating side face twice for same edge (though boundary edges appear only once), we can still check.

    side_faces_created = 0

    for tf in top_faces:
        # For each loop in face
        for loop in tf.loops:
            edge = loop.edge
            # Check if edge is boundary (only this face)
            # In bmesh, edge.link_faces gives faces using this edge
            # Since top and bottom are disconnected, boundary edges of top should have 1 face
            if len(edge.link_faces) != 1:
                continue

            # Get the two verts of this edge in order of this face's loop
            # loop.vert is start vertex of this edge in this face's winding
            # loop.next.vert is end vertex
            v_start_top = loop.vert
            v_end_top = loop.link_loop_next.vert

            # Corresponding bottom verts
            v_start_bottom = top_to_bottom.get(v_start_top)
            v_end_bottom = top_to_bottom.get(v_end_top)

            if v_start_bottom is None or v_end_bottom is None:
                continue

            # Create side face with outward normal: [v_end_top, v_start_top, v_start_bottom, v_end_bottom]
            # This should give outward facing normal as reasoned earlier
            try:
                sf = bm.faces.new([v_end_top, v_start_top, v_start_bottom, v_end_bottom])
                # UVs for side face: we can assign simple UVs
                # For side face, we want UVs that map nicely, but simple planar mapping is okay
                # Let's assign UVs: (0,0) for v_end_top, (1,0) for v_start_top, (1,1) for v_start_bottom, (0,1) for v_end_bottom
                # Or based on edge length? For simplicity, use 0-1 range
                if len(sf.loops) == 4:
                    sf.loops[0][uv_layer].uv = (0.0, 0.0)
                    sf.loops[1][uv_layer].uv = (1.0, 0.0)
                    sf.loops[2][uv_layer].uv = (1.0, 1.0)
                    sf.loops[3][uv_layer].uv = (0.0, 1.0)
                side_faces_created += 1
            except ValueError:
                # Face may already exist or invalid
                continue

    if triangulate:
        bmesh.ops.triangulate(bm, faces=bm.faces[:])

    # Create mesh
    mesh = bpy.data.meshes.new(name + "_Mesh")

    bm.to_mesh(mesh)
    bm.free()

    mesh.validate()
    mesh.update()

    return mesh


def create_material_with_image(image, name="ImageMaterial", use_alpha=True):
    """
    Create a material that uses the image as base color (and alpha if needed).
    """
    if image is None:
        return None

    mat = bpy.data.materials.new(name)
    mat.use_nodes = True

    node_tree = mat.node_tree
    nodes = node_tree.nodes
    links = node_tree.links

    # Clear existing nodes
    nodes.clear()

    # Create nodes
    output = nodes.new(type='ShaderNodeOutputMaterial')
    output.location = (300, 0)

    principled = nodes.new(type='ShaderNodeBsdfPrincipled')
    principled.location = (0, 0)

    tex_image = nodes.new(type='ShaderNodeTexImage')
    tex_image.location = (-400, 0)
    tex_image.image = image
    tex_image.interpolation = 'Linear'
    tex_image.extension = 'CLIP'

    # Link color
    links.new(principled.inputs['Base Color'], tex_image.outputs['Color'])

    if use_alpha:
        # Check if image has alpha or if we should use alpha
        # Always link alpha for safety; if image has no alpha, alpha output is 1.0
        try:
            # Principled BSDF has 'Alpha' input
            if 'Alpha' in principled.inputs:
                links.new(principled.inputs['Alpha'], tex_image.outputs['Alpha'])
                # Set blend method for transparency
                mat.blend_method = 'CLIP'
                # For EEVEE, also set shadow method
                if hasattr(mat, 'shadow_method'):
                    mat.shadow_method = 'CLIP'
                # Enable show transparent back? Not necessary
        except Exception:
            pass

    # Link principled to output
    links.new(output.inputs['Surface'], principled.outputs['BSDF'])

    return mat


def create_mesh_object_from_image(filepath, method='HEIGHTMAP', **kwargs):
    """
    High-level function to create a mesh object from an image file.

    This function loads the image and creates a mesh using the specified method.

    :param filepath: Path to image file
    :param method: 'HEIGHTMAP' or 'EXTRUDE'
    :param kwargs: Additional parameters passed to mesh creation functions
    :return: (mesh, image) tuple or (None, None) on failure
    """
    import os

    if not os.path.isfile(filepath):
        return None, None

    # Load image
    try:
        image = bpy.data.images.load(filepath, check_existing=True)
    except RuntimeError:
        return None, None

    if image.size[0] == 0 or image.size[1] == 0:
        # Try to reload
        try:
            image.reload()
        except Exception:
            pass
        if image.size[0] == 0 or image.size[1] == 0:
            return None, None

    # Ensure image has pixels loaded (for some formats, need to load)
    # Accessing pixels will force load

    name = os.path.splitext(os.path.basename(filepath))[0]
    # Sanitize name for Blender (no special chars)
    # Blender will handle

    if method == 'HEIGHTMAP':
        mesh = create_heightmap_mesh(image, name=name, **kwargs)
    elif method == 'EXTRUDE':
        mesh = create_extruded_mesh(image, name=name, **kwargs)
    else:
        # Default to heightmap
        mesh = create_heightmap_mesh(image, name=name, **kwargs)

    return mesh, image
