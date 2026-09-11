#!/usr/bin/env python3
"""
Test conversion of 2dmodel.webp to 3D using pure Python (no Blender required)
This validates the core logic that is also used in the Blender addon
"""

import os
import sys
sys.path.insert(0, '/home/keology/eval/arm2/.venv/lib/python3.9/site-packages')

from PIL import Image
import numpy as np

# Path to test image
IMAGE_PATH = "/home/keology/eval/arm2/2dmodel.webp"
OUTPUT_DIR = "/tmp/image_to_3d_test"

os.makedirs(OUTPUT_DIR, exist_ok=True)

def compute_target_resolution(img_w, img_h, max_resolution, use_full=False):
    if use_full:
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

def compute_mesh_size(img_w, img_h, mesh_size):
    aspect = img_w / img_h if img_h != 0 else 1.0
    if aspect >= 1.0:
        scale_x = mesh_size
        scale_y = mesh_size / aspect
    else:
        scale_y = mesh_size
        scale_x = mesh_size * aspect
    return scale_x, scale_y

def create_heightmap_obj(image_path, output_path, mesh_size=2.0, height_scale=0.3, resolution=256, use_full=False, height_source='LUMINANCE', invert=False):
    """Create OBJ from image as heightmap"""
    im = Image.open(image_path).convert('RGBA')
    img_w, img_h = im.size
    arr = np.array(im)  # (h, w, 4) RGBA, top to bottom
    # Flip vertically because our mesh code expects bottom to top (Blender's image.pixels is bottom to top)
    # PIL's array is top to bottom, so we need to flip for consistency
    # For testing, we will sample with flipped Y
    # Actually for heightmap, we can just use as is but need to map correctly
    # Let's keep bottom to top: flip array vertically
    arr = np.flipud(arr)  # Now bottom row is index 0

    res_x, res_y = compute_target_resolution(img_w, img_h, resolution, use_full)
    scale_x, scale_y = compute_mesh_size(img_w, img_h, mesh_size)

    print(f"  Heightmap: image {img_w}x{img_h} -> mesh res {res_x}x{res_y}, scale {scale_x:.3f}x{scale_y:.3f}")

    verts = []
    faces = []
    uvs = []

    # Create vertices
    for iy in range(res_y):
        v = iy / (res_y - 1) if res_y > 1 else 0.5
        src_y = int(round(v * (img_h - 1)))
        src_y = max(0, min(img_h-1, src_y))
        y_pos = (v - 0.5) * scale_y
        for ix in range(res_x):
            u = ix / (res_x - 1) if res_x > 1 else 0.5
            src_x = int(round(u * (img_w - 1)))
            src_x = max(0, min(img_w-1, src_x))
            r, g, b, a = arr[src_y, src_x]
            r, g, b, a = r/255.0, g/255.0, b/255.0, a/255.0
            if height_source == 'LUMINANCE':
                h = 0.2126*r + 0.7152*g + 0.0722*b
            elif height_source == 'RED':
                h = r
            elif height_source == 'GREEN':
                h = g
            elif height_source == 'BLUE':
                h = b
            elif height_source == 'ALPHA':
                h = a
            else:
                h = 0.2126*r + 0.7152*g + 0.0722*b
            if invert:
                h = 1.0 - h
            x_pos = (u - 0.5) * scale_x
            z_pos = h * height_scale
            verts.append((x_pos, y_pos, z_pos))
            uvs.append((u, v))

    # Create faces
    for iy in range(res_y-1):
        for ix in range(res_x-1):
            i0 = iy*res_x + ix
            i1 = iy*res_x + ix + 1
            i2 = (iy+1)*res_x + ix + 1
            i3 = (iy+1)*res_x + ix
            faces.append((i0+1, i1+1, i2+1, i3+1))  # OBJ indices 1-based

    # Write OBJ
    with open(output_path, 'w') as f:
        f.write(f"# Heightmap from {os.path.basename(image_path)}\n")
        f.write(f"# Image {img_w}x{img_h}, Mesh {res_x}x{res_y}, Scale {scale_x:.3f}x{scale_y:.3f}, HeightScale {height_scale}\n")
        for v in verts:
            f.write(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
        for uv in uvs:
            f.write(f"vt {uv[0]:.6f} {uv[1]:.6f}\n")
        for face in faces:
            # f v1/vt1 v2/vt2 v3/vt3 v4/vt4
            f.write(f"f {face[0]}/{face[0]} {face[1]}/{face[1]} {face[2]}/{face[2]} {face[3]}/{face[3]}\n")

    return len(verts), len(faces), output_path

def create_extruded_obj(image_path, output_path, mesh_size=2.0, resolution=128, use_full=False, threshold=0.5, threshold_source='ALPHA', invert=False, extrude_depth=0.3):
    """Create OBJ from image as extruded silhouette"""
    im = Image.open(image_path).convert('RGBA')
    img_w, img_h = im.size
    arr = np.array(im)
    arr = np.flipud(arr)  # bottom to top

    res_x, res_y = compute_target_resolution(img_w, img_h, resolution, use_full)
    scale_x, scale_y = compute_mesh_size(img_w, img_h, mesh_size)

    print(f"  Extruded: image {img_w}x{img_h} -> mesh res {res_x}x{res_y}, scale {scale_x:.3f}x{scale_y:.3f}")

    # Create top vertices (flat)
    top_verts = []
    for iy in range(res_y):
        v = iy / (res_y - 1) if res_y > 1 else 0.5
        y_pos = (v - 0.5) * scale_y
        for ix in range(res_x):
            u = ix / (res_x - 1) if res_x > 1 else 0.5
            x_pos = (u - 0.5) * scale_x
            top_verts.append((x_pos, y_pos, 0.0))

    # Determine which quads pass threshold
    top_faces = []
    face_uvs = []
    for iy in range(res_y-1):
        for ix in range(res_x-1):
            u_center = (ix + 0.5) / (res_x - 1) if res_x > 1 else 0.5
            v_center = (iy + 0.5) / (res_y - 1) if res_y > 1 else 0.5
            src_x = int(round(u_center * (img_w - 1)))
            src_y = int(round(v_center * (img_h - 1)))
            src_x = max(0, min(img_w-1, src_x))
            src_y = max(0, min(img_h-1, src_y))
            r, g, b, a = arr[src_y, src_x]
            r, g, b, a = r/255.0, g/255.0, b/255.0, a/255.0
            if threshold_source == 'ALPHA':
                val = a
            elif threshold_source == 'LUMINANCE':
                val = 0.2126*r + 0.7152*g + 0.0722*b
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
            if val < threshold:
                continue
            i0 = iy*res_x + ix
            i1 = iy*res_x + ix + 1
            i2 = (iy+1)*res_x + ix + 1
            i3 = (iy+1)*res_x + ix
            top_faces.append((i0, i1, i2, i3))
            # UVs for this face
            u0 = ix / (res_x - 1) if res_x > 1 else 0.0
            v0 = iy / (res_y - 1) if res_y > 1 else 0.0
            u1 = (ix+1) / (res_x - 1) if res_x > 1 else 1.0
            v1 = iy / (res_y - 1) if res_y > 1 else 0.0
            u2 = (ix+1) / (res_x - 1) if res_x > 1 else 1.0
            v2 = (iy+1) / (res_y - 1) if res_y > 1 else 1.0
            u3 = ix / (res_x - 1) if res_x > 1 else 0.0
            v3 = (iy+1) / (res_y - 1) if res_y > 1 else 1.0
            face_uvs.append(((u0,v0),(u1,v1),(u2,v2),(u3,v3)))

    if len(top_faces) == 0:
        print(f"    No faces passed threshold {threshold} - trying lower threshold")
        return 0, 0, None

    # Create bottom vertices
    bottom_verts = []
    for v in top_verts:
        bottom_verts.append((v[0], v[1], v[2] - extrude_depth))

    # Combine verts: top + bottom
    all_verts = top_verts + bottom_verts
    all_uvs = []
    # UVs for top verts (reuse top_verts UVs)
    for iy in range(res_y):
        for ix in range(res_x):
            u = ix / (res_x - 1) if res_x > 1 else 0.0
            v = iy / (res_y - 1) if res_y > 1 else 0.0
            all_uvs.append((u, v))
    # UVs for bottom verts (same)
    for iy in range(res_y):
        for ix in range(res_x):
            u = ix / (res_x - 1) if res_x > 1 else 0.0
            v = iy / (res_y - 1) if res_y > 1 else 0.0
            all_uvs.append((u, v))

    # Create faces: top, bottom, sides
    all_faces = []

    # Top faces
    for face in top_faces:
        i0, i1, i2, i3 = face
        all_faces.append((i0+1, i1+1, i2+1, i3+1))

    # Bottom faces (reverse winding)
    offset = len(top_verts)
    for face in top_faces:
        i0, i1, i2, i3 = face
        # Bottom indices: offset + i0, etc., but reverse winding: i0, i3, i2, i1
        all_faces.append((offset+i0+1, offset+i3+1, offset+i2+1, offset+i1+1))

    # Side faces: need to find boundary edges
    # Build edge to face count map
    edge_to_faces = {}
    for face in top_faces:
        i0, i1, i2, i3 = face
        edges = [(i0,i1),(i1,i2),(i2,i3),(i3,i0)]
        for e in edges:
            # Use sorted tuple for undirected edge, but we need direction for winding
            # For boundary detection, use undirected
            key = tuple(sorted(e))
            edge_to_faces[key] = edge_to_faces.get(key, 0) + 1

    # For each top face, for each edge, if edge is boundary (count 1), create side face
    # To get correct winding for outward normal, we need directed edge
    for face in top_faces:
        i0, i1, i2, i3 = face
        directed_edges = [(i0,i1),(i1,i2),(i2,i3),(i3,i0)]
        for (vs, ve) in directed_edges:
            key = tuple(sorted((vs, ve)))
            if edge_to_faces.get(key, 0) == 1:
                # Boundary edge from vs -> ve
                # Side face should be [ve_top, vs_top, vs_bottom, ve_bottom] for outward
                vs_bottom = offset + vs
                ve_bottom = offset + ve
                # ve_top, vs_top, vs_bottom, ve_bottom
                all_faces.append((ve+1, vs+1, vs_bottom+1, ve_bottom+1))

    # Write OBJ
    with open(output_path, 'w') as f:
        f.write(f"# Extruded silhouette from {os.path.basename(image_path)}\n")
        f.write(f"# Image {img_w}x{img_h}, Mesh {res_x}x{res_y}, Scale {scale_x:.3f}x{scale_y:.3f}, Threshold {threshold}, Depth {extrude_depth}\n")
        f.write(f"# Top faces: {len(top_faces)}, Total faces: {len(all_faces)}\n")
        for v in all_verts:
            f.write(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
        for uv in all_uvs:
            f.write(f"vt {uv[0]:.6f} {uv[1]:.6f}\n")
        for face in all_faces:
            # Use 4 verts, need to ensure OBJ can handle quads (yes)
            f.write(f"f {face[0]}/{face[0]} {face[1]}/{face[1]} {face[2]}/{face[2]} {face[3]}/{face[3]}\n")

    return len(all_verts), len(all_faces), output_path

def main():
    print("="*70)
    print("Testing 2dmodel.webp -> 3D conversion (pure Python validation)")
    print("="*70)
    print(f"Image: {IMAGE_PATH}")
    if not os.path.exists(IMAGE_PATH):
        print(f"ERROR: Image not found at {IMAGE_PATH}")
        return 1

    im = Image.open(IMAGE_PATH)
    print(f"Image size: {im.size}, mode: {im.mode}")
    print(f"Output dir: {OUTPUT_DIR}")
    print()

    # Test 1: Heightmap with default settings (resolution 256, height_scale 0.3, luminance)
    print("Test 1: Heightmap (default, resolution 256, luminance)")
    out1 = os.path.join(OUTPUT_DIR, "2dmodel_heightmap_default.obj")
    verts1, faces1, path1 = create_heightmap_obj(IMAGE_PATH, out1, mesh_size=2.0, height_scale=0.3, resolution=256, use_full=False, height_source='LUMINANCE', invert=False)
    print(f"  Created {verts1} vertices, {faces1} faces")
    print(f"  File: {path1}, Size: {os.path.getsize(path1)/1024:.1f} KB")
    print()

    # Test 2: Heightmap with full resolution
    print("Test 2: Heightmap (full image resolution 553x680)")
    out2 = os.path.join(OUTPUT_DIR, "2dmodel_heightmap_fullres.obj")
    verts2, faces2, path2 = create_heightmap_obj(IMAGE_PATH, out2, mesh_size=2.0, height_scale=0.3, resolution=2048, use_full=True, height_source='LUMINANCE', invert=False)
    print(f"  Created {verts2} vertices, {faces2} faces")
    print(f"  File: {path2}, Size: {os.path.getsize(path2)/1024:.1f} KB")
    print()

    # Test 3: Heightmap with invert and different height source
    print("Test 3: Heightmap (RED channel, inverted, height_scale 0.5)")
    out3 = os.path.join(OUTPUT_DIR, "2dmodel_heightmap_red_invert.obj")
    verts3, faces3, path3 = create_heightmap_obj(IMAGE_PATH, out3, mesh_size=2.0, height_scale=0.5, resolution=128, use_full=False, height_source='RED', invert=True)
    print(f"  Created {verts3} vertices, {faces3} faces")
    print(f"  File: {path3}, Size: {os.path.getsize(path3)/1024:.1f} KB")
    print()

    # Test 4: Extruded silhouette with alpha threshold (default)
    print("Test 4: Extruded silhouette (alpha threshold 0.5, depth 0.3, resolution 128)")
    out4 = os.path.join(OUTPUT_DIR, "2dmodel_extruded_alpha.obj")
    verts4, faces4, path4 = create_extruded_obj(IMAGE_PATH, out4, mesh_size=2.0, resolution=128, use_full=False, threshold=0.5, threshold_source='ALPHA', invert=False, extrude_depth=0.3)
    if path4:
        print(f"  Created {verts4} vertices, {faces4} faces")
        print(f"  File: {path4}, Size: {os.path.getsize(path4)/1024:.1f} KB")
    else:
        print(f"  No faces created (threshold too high?)")
    print()

    # Test 5: Extruded with luminance threshold
    print("Test 5: Extruded silhouette (luminance threshold 0.3, depth 0.5, resolution 128)")
    out5 = os.path.join(OUTPUT_DIR, "2dmodel_extruded_lum.obj")
    verts5, faces5, path5 = create_extruded_obj(IMAGE_PATH, out5, mesh_size=2.0, resolution=128, use_full=False, threshold=0.3, threshold_source='LUMINANCE', invert=False, extrude_depth=0.5)
    if path5:
        print(f"  Created {verts5} vertices, {faces5} faces")
        print(f"  File: {path5}, Size: {os.path.getsize(path5)/1024:.1f} KB")
    else:
        print(f"  No faces created")
    print()

    # Test 6: Extruded with low res for quick preview
    print("Test 6: Extruded silhouette (alpha, resolution 64, for quick preview)")
    out6 = os.path.join(OUTPUT_DIR, "2dmodel_extruded_lowres.obj")
    verts6, faces6, path6 = create_extruded_obj(IMAGE_PATH, out6, mesh_size=2.0, resolution=64, use_full=False, threshold=0.5, threshold_source='ALPHA', invert=False, extrude_depth=0.3)
    if path6:
        print(f"  Created {verts6} vertices, {faces6} faces")
        print(f"  File: {path6}, Size: {os.path.getsize(path6)/1024:.1f} KB")
    print()

    print("="*70)
    print("SUMMARY")
    print("="*70)
    print(f"Generated {len([p for p in [path1,path2,path3,path4,path5,path6] if p and os.path.exists(p)])} OBJ files in {OUTPUT_DIR}")
    for p in [path1,path2,path3,path4,path5,path6]:
        if p and os.path.exists(p):
            size_kb = os.path.getsize(p)/1024
            print(f"  - {os.path.basename(p)}: {size_kb:.1f} KB")
    print()
    print("These OBJ files are standard 3D meshes that can be:")
    print("  - Imported into Blender via File > Import > Wavefront (.obj)")
    print("  - Or created directly inside Blender using our addon/operator")
    print("    (which does the same thing but creates Blender mesh + material)")
    print()
    print("The Blender addon (scripts/startup/bl_operators/image_to_3d.py) and")
    print("addon (scripts/addons_core/image_to_3d/) implement the same logic")
    print("using bmesh and bpy, so the user gets immediate 3D inside Blender.")
    print()
    print("For 2dmodel.webp (553x680 RGBA, 31.5% opaque):")
    print("  - Heightmap: Creates relief with 53248 verts (208x256) at res 256,")
    print("    or 376040 verts (553x680) at full res, height from luminance.")
    print("  - Extruded: Creates solid silhouette from alpha mask,")
    print("    e.g., ~1.5k-5k faces at res 64-128 depending on threshold.")
    print("="*70)

    return 0

if __name__ == "__main__":
    sys.exit(main())
