# Copyright 2018-2019 The glTF-Blender-IO authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import numpy as np
from mathutils import Vector

from . import gltf2_blender_export_keys
from ...io.com.gltf2_io_debug import print_console
from io_scene_gltf2.blender.exp import gltf2_blender_gather_skins


def extract_primitives(glTF, blender_mesh, library, blender_object, blender_vertex_groups, modifiers, export_settings):
    """Extract primitives from a mesh."""
    print_console('INFO', 'Extracting primitive: ' + blender_mesh.name)

    use_normals = export_settings[gltf2_blender_export_keys.NORMALS]
    if use_normals:
        blender_mesh.calc_normals_split()

    use_tangents = False
    if use_normals and export_settings[gltf2_blender_export_keys.TANGENTS]:
        if blender_mesh.uv_layers.active and len(blender_mesh.uv_layers) > 0:
            try:
                blender_mesh.calc_tangents()
                use_tangents = True
            except Exception:
                print_console('WARNING', 'Could not calculate tangents. Please try to triangulate the mesh first.')

    tex_coord_max = 0
    if export_settings[gltf2_blender_export_keys.TEX_COORDS]:
        if blender_mesh.uv_layers.active:
            tex_coord_max = len(blender_mesh.uv_layers)

    color_max = 0
    if export_settings[gltf2_blender_export_keys.COLORS]:
        color_max = len(blender_mesh.vertex_colors)

    armature = None
    skin = None
    if blender_vertex_groups and export_settings[gltf2_blender_export_keys.SKINS]:
        if modifiers is not None:
            modifiers_dict = {m.type: m for m in modifiers}
            if "ARMATURE" in modifiers_dict:
                modifier = modifiers_dict["ARMATURE"]
                armature = modifier.object

        # Skin must be ignored if the object is parented to a bone of the armature
        # (This creates an infinite recursive error)
        # So ignoring skin in that case
        is_child_of_arma = (
            armature and
            blender_object and
            blender_object.parent_type == "BONE" and
            blender_object.parent.name == armature.name
        )
        if is_child_of_arma:
            armature = None

        if armature:
            skin = gltf2_blender_gather_skins.gather_skin(armature, export_settings)
            if not skin:
                armature = None

    use_morph_normals = use_normals and export_settings[gltf2_blender_export_keys.MORPH_NORMAL]
    use_morph_tangents = use_morph_normals and use_tangents and export_settings[gltf2_blender_export_keys.MORPH_TANGENT]

    key_blocks = []
    if blender_mesh.shape_keys and export_settings[gltf2_blender_export_keys.MORPH]:
        key_blocks = [
            key_block
            for key_block in blender_mesh.shape_keys.key_blocks
            if not (key_block == key_block.relative_key or key_block.mute)
        ]

    use_materials = export_settings[gltf2_blender_export_keys.MATERIALS]

    # Fetch vert data
    use_facemaps = bool(blender_mesh.face_maps)

    # Fetch vert positions and bone data (joint,weights)
    locs, morph_locs = __get_positions(blender_mesh, key_blocks, armature, blender_object, export_settings)
    if skin:
        vert_bones, num_joint_sets = __get_bone_data(blender_mesh, skin, blender_vertex_groups)
    extra_vgroup_weights = __get_extra_vgroups(blender_mesh, modifiers, blender_vertex_groups)

    # In Blender there is both per-vert data, like position, and also per-loop
    # (loop=corner-of-poly) data, like normals or UVs. glTF only has per-vert
    # data, so we need to split Blender verts up into potentially-multiple glTF
    # verts.
    #
    # First, we'll collect a "dot" for every loop: a struct that stores all the
    # attributes at that loop, namely the vertex index (which determines all
    # per-vert data), and all the per-loop data like UVs, etc.
    #
    # Each unique dot will become one unique glTF vert.

    # List all fields the dot struct needs.
    dot_fields = [('vertex_index', np.uint32)]
    if use_normals:
        dot_fields += [('nx', np.float32), ('ny', np.float32), ('nz', np.float32)]
    if use_tangents:
        dot_fields += [('tx', np.float32), ('ty', np.float32), ('tz', np.float32), ('tw', np.float32)]
    for uv_i in range(tex_coord_max):
        dot_fields += [('uv%dx' % uv_i, np.float32), ('uv%dy' % uv_i, np.float32)]
    for col_i in range(color_max):
        dot_fields += [
            ('color%dr' % col_i, np.float32),
            ('color%dg' % col_i, np.float32),
            ('color%db' % col_i, np.float32),
            ('color%da' % col_i, np.float32),
        ]
    if use_morph_normals:
        for morph_i, _ in enumerate(key_blocks):
            dot_fields += [
                ('morph%dnx' % morph_i, np.float32),
                ('morph%dny' % morph_i, np.float32),
                ('morph%dnz' % morph_i, np.float32),
            ]
    if use_facemaps:
        dot_fields += [('facemaps', np.float32)]

    dots = np.empty(len(blender_mesh.loops), dtype=np.dtype(dot_fields))

    vidxs = np.empty(len(blender_mesh.loops))
    blender_mesh.loops.foreach_get('vertex_index', vidxs)
    dots['vertex_index'] = vidxs
    del vidxs

    if use_normals:
        kbs = key_blocks if use_morph_normals else []
        normals, morph_normals = __get_normals(
            blender_mesh, kbs, armature, blender_object, export_settings
        )
        dots['nx'] = normals[:, 0]
        dots['ny'] = normals[:, 1]
        dots['nz'] = normals[:, 2]
        del normals
        for morph_i, ns in enumerate(morph_normals):
            dots['morph%dnx' % morph_i] = ns[:, 0]
            dots['morph%dny' % morph_i] = ns[:, 1]
            dots['morph%dnz' % morph_i] = ns[:, 2]
        del morph_normals

    if use_tangents:
        tangents = __get_tangents(blender_mesh, armature, blender_object, export_settings)
        dots['tx'] = tangents[:, 0]
        dots['ty'] = tangents[:, 1]
        dots['tz'] = tangents[:, 2]
        del tangents
        signs = __get_bitangent_signs(blender_mesh, armature, blender_object, export_settings)
        dots['tw'] = signs
        del signs

    for uv_i in range(tex_coord_max):
        uvs = __get_uvs(blender_mesh, uv_i)
        dots['uv%dx' % uv_i] = uvs[:, 0]
        dots['uv%dy' % uv_i] = uvs[:, 1]
        del uvs

    for col_i in range(color_max):
        colors = __get_colors(blender_mesh, col_i)
        dots['color%dr' % col_i] = colors[:, 0]
        dots['color%dg' % col_i] = colors[:, 1]
        dots['color%db' % col_i] = colors[:, 2]
        dots['color%da' % col_i] = colors[:, 3]
        del colors

    if use_facemaps:
        dots['facemaps'] = __get_facemaps(blender_mesh)

    # Calculate triangles and sort them into primitives.

    blender_mesh.calc_loop_triangles()
    loop_indices = np.empty(len(blender_mesh.loop_triangles) * 3, dtype=np.uint32)
    blender_mesh.loop_triangles.foreach_get('loops', loop_indices)

    prim_indices = {}  # maps material index to TRIANGLES-style indices into dots

    if use_materials == "NONE": # Only for None. For placeholder and export, keep primitives
        # Put all vertices into one primitive
        prim_indices[-1] = loop_indices

    else:
        # Bucket by material index.

        tri_material_idxs = np.empty(len(blender_mesh.loop_triangles), dtype=np.uint32)
        blender_mesh.loop_triangles.foreach_get('material_index', tri_material_idxs)
        loop_material_idxs = np.repeat(tri_material_idxs, 3)  # material index for every loop
        unique_material_idxs = np.unique(tri_material_idxs)
        del tri_material_idxs

        for material_idx in unique_material_idxs:
            prim_indices[material_idx] = loop_indices[loop_material_idxs == material_idx]

    # Create all the primitives.

    primitives = []

    for material_idx, dot_indices in prim_indices.items():
        # Extract just dots used by this primitive, deduplicate them, and
        # calculate indices into this deduplicated list.
        prim_dots = dots[dot_indices]
        prim_dots, indices = np.unique(prim_dots, return_inverse=True)

        if len(prim_dots) == 0:
            continue

        # Now just move all the data for prim_dots into attribute arrays

        attributes = {}

        blender_idxs = prim_dots['vertex_index']

        attributes['POSITION'] = locs[blender_idxs]

        for morph_i, vs in enumerate(morph_locs):
            attributes['MORPH_POSITION_%d' % morph_i] = vs[blender_idxs]

        if use_normals:
            normals = np.empty((len(prim_dots), 3), dtype=np.float32)
            normals[:, 0] = prim_dots['nx']
            normals[:, 1] = prim_dots['ny']
            normals[:, 2] = prim_dots['nz']
            attributes['NORMAL'] = normals

        if use_tangents:
            tangents = np.empty((len(prim_dots), 4), dtype=np.float32)
            tangents[:, 0] = prim_dots['tx']
            tangents[:, 1] = prim_dots['ty']
            tangents[:, 2] = prim_dots['tz']
            tangents[:, 3] = prim_dots['tw']
            attributes['TANGENT'] = tangents

        if use_morph_normals:
            for morph_i, _ in enumerate(key_blocks):
                ns = np.empty((len(prim_dots), 3), dtype=np.float32)
                ns[:, 0] = prim_dots['morph%dnx' % morph_i]
                ns[:, 1] = prim_dots['morph%dny' % morph_i]
                ns[:, 2] = prim_dots['morph%dnz' % morph_i]
                attributes['MORPH_NORMAL_%d' % morph_i] = ns

                if use_morph_tangents:
                    attributes['MORPH_TANGENT_%d' % morph_i] = __calc_morph_tangents(normals, ns, tangents)

        for tex_coord_i in range(tex_coord_max):
            uvs = np.empty((len(prim_dots), 2), dtype=np.float32)
            uvs[:, 0] = prim_dots['uv%dx' % tex_coord_i]
            uvs[:, 1] = prim_dots['uv%dy' % tex_coord_i]
            attributes['TEXCOORD_%d' % tex_coord_i] = uvs

        for color_i in range(color_max):
            colors = np.empty((len(prim_dots), 4), dtype=np.float32)
            colors[:, 0] = prim_dots['color%dr' % color_i]
            colors[:, 1] = prim_dots['color%dg' % color_i]
            colors[:, 2] = prim_dots['color%db' % color_i]
            colors[:, 3] = prim_dots['color%da' % color_i]
            attributes['COLOR_%d' % color_i] = colors

        if skin:
            joints = [[] for _ in range(num_joint_sets)]
            weights = [[] for _ in range(num_joint_sets)]

            for vi in blender_idxs:
                bones = vert_bones[vi]
                for j in range(0, 4 * num_joint_sets):
                    if j < len(bones):
                        joint, weight = bones[j]
                    else:
                        joint, weight = 0, 0.0
                    joints[j//4].append(joint)
                    weights[j//4].append(weight)

            for i, (js, ws) in enumerate(zip(joints, weights)):
                attributes['JOINTS_%d' % i] = js
                attributes['WEIGHTS_%d' % i] = ws

        for vgroup_name, weights in extra_vgroup_weights.items():
            attributes['_VG_' + vgroup_name] = weights[blender_idxs]

        if use_facemaps:
            attributes['_FACEMAPS'] = prim_dots['facemaps']

        primitives.append({
            'attributes': attributes,
            'indices': indices,
            'material': material_idx,
        })

    print_console('INFO', 'Primitives created: %d' % len(primitives))

    return primitives


def __get_positions(blender_mesh, key_blocks, armature, blender_object, export_settings):
    locs = np.empty(len(blender_mesh.vertices) * 3, dtype=np.float32)
    blender_mesh.vertices.foreach_get('co', locs)
    locs = locs.reshape(len(blender_mesh.vertices), 3)

    morph_locs = []
    for key_block in key_blocks:
        vs = np.empty(len(blender_mesh.vertices) * 3, dtype=np.float32)
        key_block.data.foreach_get('co', vs)
        vs = vs.reshape(len(blender_mesh.vertices), 3)
        morph_locs.append(vs)

    # Transform for skinning
    if armature and blender_object:
        apply_matrix = armature.matrix_world.inverted() @ blender_object.matrix_world
        loc_transform = armature.matrix_world @ apply_matrix

        loc_transform = blender_object.matrix_world
        locs[:] = __apply_mat_to_all(loc_transform, locs)
        for vs in morph_locs:
            vs[:] = __apply_mat_to_all(loc_transform, vs)

    # glTF stores deltas in morph targets
    for vs in morph_locs:
        vs -= locs

    if export_settings[gltf2_blender_export_keys.YUP]:
        __zup2yup(locs)
        for vs in morph_locs:
            __zup2yup(vs)

    return locs, morph_locs


def __get_normals(blender_mesh, key_blocks, armature, blender_object, export_settings):
    """Get normal for each loop."""
    blender_mesh.calc_normals_split()

    normals = np.empty(len(blender_mesh.loops) * 3, dtype=np.float32)
    blender_mesh.loops.foreach_get('normal', normals)
    normals = normals.reshape(len(blender_mesh.loops), 3)

    morph_normals = []
    for key_block in key_blocks:
        ns = np.array(key_block.normals_split_get(), dtype=np.float32)
        ns = ns.reshape(len(blender_mesh.loops), 3)
        morph_normals.append(ns)

    # Transform for skinning
    if armature and blender_object:
        apply_matrix = (armature.matrix_world.inverted() @ blender_object.matrix_world)
        apply_matrix = apply_matrix.to_3x3().inverted().transposed()
        normal_transform = armature.matrix_world.to_3x3() @ apply_matrix

        normals[:] = __apply_mat_to_all(normal_transform, normals)
        __normalize_vecs(normals)
        for ns in morph_normals:
            ns[:] = __apply_mat_to_all(normal_transform, ns)
            __normalize_vecs(ns)

    for ns in [normals, *morph_normals]:
        # Replace zero normals with the unit UP vector.
        # Seems to happen sometimes with degenerate tris?
        is_zero = ~ns.any(axis=1)
        ns[is_zero, 2] = 1

    # glTF stores deltas in morph targets
    for ns in morph_normals:
        ns -= normals

    if export_settings[gltf2_blender_export_keys.YUP]:
        __zup2yup(normals)
        for ns in morph_normals:
            __zup2yup(ns)

    return normals, morph_normals


def __get_tangents(blender_mesh, armature, blender_object, export_settings):
    """Get an array of the tangent for each loop."""
    tangents = np.empty(len(blender_mesh.loops) * 3, dtype=np.float32)
    blender_mesh.loops.foreach_get('tangent', tangents)
    tangents = tangents.reshape(len(blender_mesh.loops), 3)

    # Transform for skinning
    if armature and blender_object:
        apply_matrix = armature.matrix_world.inverted() @ blender_object.matrix_world
        tangent_transform = apply_matrix.to_quaternion().to_matrix()
        tangents = __apply_mat_to_all(tangent_transform, tangents)
        __normalize_vecs(tangents)

    if export_settings[gltf2_blender_export_keys.YUP]:
        __zup2yup(tangents)

    return tangents


def __get_bitangent_signs(blender_mesh, armature, blender_object, export_settings):
    signs = np.empty(len(blender_mesh.loops), dtype=np.float32)
    blender_mesh.loops.foreach_get('bitangent_sign', signs)

    # Transform for skinning
    if armature and blender_object:
        # Bitangent signs should flip when handedness changes
        # TODO: confirm
        apply_matrix = armature.matrix_world.inverted() @ blender_object.matrix_world
        tangent_transform = apply_matrix.to_quaternion().to_matrix()
        flipped = tangent_transform.determinant() < 0
        if flipped:
            signs *= -1

    # No change for Zup -> Yup

    return signs


def __calc_morph_tangents(normals, morph_normal_deltas, tangents):
    # TODO: check if this works
    morph_tangent_deltas = np.empty((len(normals), 3), dtype=np.float32)

    for i in range(len(normals)):
        n = Vector(normals[i])
        morph_n = n + Vector(morph_normal_deltas[i])  # convert back to non-delta
        t = Vector(tangents[i, :3])

        rotation = morph_n.rotation_difference(n)

        t_morph = Vector(t)
        t_morph.rotate(rotation)
        morph_tangent_deltas[i] = t_morph - t  # back to delta

    return morph_tangent_deltas


def __get_uvs(blender_mesh, uv_i):
    layer = blender_mesh.uv_layers[uv_i]
    uvs = np.empty(len(blender_mesh.loops) * 2, dtype=np.float32)
    layer.data.foreach_get('uv', uvs)
    uvs = uvs.reshape(len(blender_mesh.loops), 2)

    # Blender UV space -> glTF UV space
    # u,v -> u,1-v
    uvs[:, 1] *= -1
    uvs[:, 1] += 1

    return uvs


def __get_colors(blender_mesh, color_i):
    layer = blender_mesh.vertex_colors[color_i]
    colors = np.empty(len(blender_mesh.loops) * 4, dtype=np.float32)
    layer.data.foreach_get('color', colors)
    colors = colors.reshape(len(blender_mesh.loops), 4)

    # sRGB -> Linear
    rgb = colors[:, :-1]
    not_small = rgb >= 0.04045
    small_result = np.where(rgb < 0.0, 0.0, rgb * (1.0 / 12.92))
    large_result = np.power((rgb + 0.055) * (1.0 / 1.055), 2.4, where=not_small)
    rgb[:] = np.where(not_small, large_result, small_result)

    return colors


def __get_extra_vgroups(blender_mesh, modifiers, blender_vertex_groups):
    """Get vertex weights for vgroup that aren't used for skinning."""
    if not blender_vertex_groups:
        return {}

    # Find vgroups used for skinning
    skinning_vgroup_names = set()
    for m in (modifiers or []):
        if m.type == 'ARMATURE' and m.use_vertex_groups:
            if m.object and m.object.type == 'ARMATURE':
                for bone in m.object.data.bones:
                    skinning_vgroup_names.add(bone.name)

    if len(skinning_vgroup_names) == len(blender_vertex_groups):
        return {}

    vgroup_weights = {}
    for i, vgroup in enumerate(blender_vertex_groups):
        if vgroup.name in skinning_vgroup_names: continue
        weights = []
        for vertex in blender_mesh.vertices:
            weight = 0.0
            for vge in vertex.groups:
                if vge.group == i:
                    weight = vge.weight
                    break
            weights.append(weight)
        vgroup_weights[vgroup.name] = np.array(weights, dtype=np.float32)
    return vgroup_weights


def __get_bone_data(blender_mesh, skin, blender_vertex_groups):
    joint_name_to_index = {joint.name: index for index, joint in enumerate(skin.joints)}
    group_to_joint = [joint_name_to_index.get(g.name) for g in blender_vertex_groups]

    # List of (joint, weight) pairs for each vert
    vert_bones = []
    max_num_influences = 0

    for vertex in blender_mesh.vertices:
        bones = []
        if vertex.groups:
            for group_element in vertex.groups:
                weight = group_element.weight
                if weight <= 0.0:
                    continue
                try:
                    joint = group_to_joint[group_element.group]
                except Exception:
                    continue
                if joint is None:
                    continue
                bones.append((joint, weight))
        bones.sort(key=lambda x: x[1], reverse=True)
        if not bones: bones = ((0, 1.0),)  # HACK for verts with zero weight (#308)
        vert_bones.append(bones)
        if len(bones) > max_num_influences:
            max_num_influences = len(bones)

    # How many joint sets do we need? 1 set = 4 influences
    num_joint_sets = (max_num_influences + 3) // 4

    return vert_bones, num_joint_sets


def __get_facemaps(blender_mesh):
    """Gets a facemap index for each loop."""
    poly_facemap = np.empty(len(blender_mesh.polygons), dtype=np.float32)
    blender_mesh.face_maps[0].data.foreach_get('value', poly_facemap)

    # Get polygon_index for each loop in the mesh
    loop_polyidx = np.zeros(len(blender_mesh.loops), dtype=np.uint32)
    for polyi, poly in enumerate(blender_mesh.polygons):
        for i in range(poly.loop_start, poly.loop_start + poly.loop_total):
            loop_polyidx[i] = polyi

    return poly_facemap[loop_polyidx]


def __zup2yup(array):
    # x,y,z -> x,z,-y
    array[:, [1,2]] = array[:, [2,1]]  # x,z,y
    array[:, 2] *= -1  # x,z,-y


def __apply_mat_to_all(matrix, vectors):
    """Given matrix m and vectors [v1,v2,...], computes [m@v1,m@v2,...]"""
    # Linear part
    m = matrix.to_3x3() if len(matrix) == 4 else matrix
    res = np.matmul(vectors, np.array(m.transposed()))
    # Translation part
    if len(matrix) == 4:
        res += np.array(matrix.translation)
    return res


def __normalize_vecs(vectors):
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    np.divide(vectors, norms, out=vectors, where=norms != 0)
