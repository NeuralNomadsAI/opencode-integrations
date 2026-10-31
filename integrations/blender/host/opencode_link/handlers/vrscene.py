# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

"""VRScene material conversion helpers for the OpenCode Blender bundle."""

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportOptionalSubscript=false, reportReturnType=false

from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]


TX_ALTERNATIVES = [".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".hdr"]
TX_SUFFIXES_TO_REMOVE = ["_lin_srgb", "_lin", "_srgb", "_hires"]

SUPPORTED_BLOCK_TYPES = {
    "BitmapBuffer",
    "BRDFVRayMtl",
    "FloatToTex",
    "MtlSingleBRDF",
    "TexAColor",
    "TexAColorOp",
    "TexBitmap",
    "TexRemap",
    "TexTriPlanar",
    "UVWGenMayaPlace2dTexture",
    "UVWGenRandomizer",
}

REFERENCE_KEYS = {
    "alpha_mult",
    "bitmap",
    "brdf",
    "bump_map",
    "color_a",
    "color_b",
    "input",
    "input_value",
    "mult_a",
    "refract",
    "reflect",
    "self_illumination",
    "sheen_color",
    "texture",
    "texture_x",
    "texture_y",
    "texture_z",
    "uvwgen",
}

SOCKET_ALIASES = {
    "image_input": ["Image", "Texture", "Color Input", "Input Color"],
    "alpha_input": ["Alpha", "Input Alpha"],
    "vector_input": ["Vector", "Coordinates", "UV", "Input Vector"],
    "uv_vector_input": ["UV Vector", "UV", "UV Input"],
    "object_vector_input": ["Object Vector", "Object", "Object Input"],
    "use_triplanar_input": ["Use Triplanar", "Triplanar", "Enable Triplanar"],
    "triplanar_size_input": ["Scale", "Size", "Triplanar Scale", "Tri Scale"],
    "triplanar_blend_input": ["Blend", "Triplanar Blend", "Tri Blend"],
    "use_randomizer_input": ["Use Randomizer", "Randomize", "Enable Randomizer"],
    "random_seed_input": ["Seed", "Random Seed"],
    "random_u_min_input": ["Variance U Min", "Random U Min", "U Min"],
    "random_u_max_input": ["Variance U Max", "Random U Max", "U Max"],
    "random_v_min_input": ["Variance V Min", "Random V Min", "V Min"],
    "random_v_max_input": ["Variance V Max", "Random V Max", "V Max"],
    "random_rotation_min_input": ["Variance Rot Min", "Rotation Min", "Random Rotation Min"],
    "random_rotation_max_input": ["Variance Rot Max", "Rotation Max", "Random Rotation Max"],
    "random_uscale_min_input": ["Variance UScale Min", "Scale Min", "Random Scale Min"],
    "random_uscale_max_input": ["Variance UScale Max", "Scale Max", "Random Scale Max"],
    "random_tile_blend_input": ["Tile Blend", "Random Tile Blend"],
    "repeat_u_input": ["Repeat U", "U Repeat"],
    "repeat_v_input": ["Repeat V", "V Repeat"],
    "offset_u_input": ["Offset U", "U Offset"],
    "offset_v_input": ["Offset V", "V Offset"],
    "rotate_uv_input": ["Rotate UV", "UV Rotation", "Rotation"],
    "coverage_u_input": ["Coverage U"],
    "coverage_v_input": ["Coverage V"],
    "color_output": ["Color", "Result", "Texture", "Out"],
    "alpha_output": ["Alpha"],
    "vector_output": ["Vector", "Coordinates", "UV"],
}


def parse_acolor(value: str) -> tuple[float, float, float, float]:
    match = re.search(
        r"AColor\s*\(\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*\)",
        value,
    )
    if match:
        return (
            float(match.group(1)),
            float(match.group(2)),
            float(match.group(3)),
            float(match.group(4)),
        )
    return (0.5, 0.5, 0.5, 1.0)


def parse_float(value: str) -> float:
    try:
        return float(re.sub(r"[;\s]", "", str(value)))
    except (TypeError, ValueError):
        return 0.0


def parse_int(value: str) -> int:
    try:
        return int(float(re.sub(r"[;\s]", "", str(value))))
    except (TypeError, ValueError):
        return 0


def parse_bool(value: str) -> bool:
    clean = str(value).strip().rstrip(";").lower()
    return clean in {"1", "true", "yes"}


def is_reference(value: str) -> bool:
    if not value:
        return False
    value = str(value).strip().rstrip(";")
    if not value or value.startswith('"') or "(" in value or value.startswith("List"):
        return False
    return any(ch.isalpha() for ch in value)


def get_reference_name(value: str) -> str:
    clean = str(value).strip().rstrip(";")
    clean = clean.strip('"')
    if "::" in clean:
        clean = clean.split("::", 1)[0]
    return clean


def normalize_socket_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def resolve_texture_path(path: str) -> str:
    if not path:
        return ""

    path = path.replace("\\", "/")
    if os.path.exists(path):
        return path

    base, ext = os.path.splitext(path)
    if ext.lower() == ".tx":
        for suffix in TX_SUFFIXES_TO_REMOVE:
            if base.endswith(suffix):
                trimmed = base[: -len(suffix)]
                for alt_ext in TX_ALTERNATIVES:
                    candidate = trimmed + alt_ext
                    if os.path.exists(candidate):
                        return candidate
        for alt_ext in TX_ALTERNATIVES:
            candidate = base + alt_ext
            if os.path.exists(candidate):
                return candidate

    return path


@dataclass
class ConverterSettings:
    mapping_group_name: str | None = None
    group_socket_map: dict[str, str] = field(default_factory=dict)
    replace_existing: bool = False
    use_fake_user: bool = True


class VRSceneParser:
    def __init__(self, content: str):
        self.content = content
        self.blocks: dict[str, dict[str, Any]] = {}

    def parse(self) -> dict[str, dict[str, Any]]:
        pattern = r"(\w+)\s+(\S+)\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}"
        for match in re.finditer(pattern, self.content):
            block_type = match.group(1)
            block_name = match.group(2)
            props = self._parse_properties(match.group(3))
            self.blocks[block_name] = {
                "type": block_type,
                "name": block_name,
                "props": props,
            }
        return self.blocks

    def _parse_properties(self, content: str) -> dict[str, str]:
        props: dict[str, str] = {}
        lines = content.split("\n")
        current_key = None
        current_value: list[str] = []
        depth = 0

        for line in lines:
            line = line.strip()
            if not line or line.startswith("//"):
                continue

            depth += line.count("(") + line.count("[") - line.count(")") - line.count("]")

            if "=" in line and depth <= 0 and current_key is None:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().rstrip(";")
                if value.count("(") > value.count(")") or value.count("[") > value.count("]"):
                    current_key = key
                    current_value = [value]
                    depth = value.count("(") + value.count("[") - value.count(")") - value.count("]")
                else:
                    props[key] = value
            elif current_key is not None:
                current_value.append(line.rstrip(";"))
                if depth <= 0:
                    props[current_key] = " ".join(current_value)
                    current_key = None
                    current_value = []

        return props

    def get_block(self, ref_name: str) -> dict[str, Any] | None:
        name = get_reference_name(ref_name)
        if name in self.blocks:
            return self.blocks[name]
        if "@" in name:
            base_name = name.rsplit("@", 1)[0]
            return self.blocks.get(base_name)
        return None

    def iter_chain(self, ref_name: str, visited: set[str] | None = None):
        if visited is None:
            visited = set()

        block = self.get_block(ref_name)
        if not block:
            return

        name = block["name"]
        if name in visited:
            return
        visited.add(name)
        yield block

        for key, value in block["props"].items():
            if key in REFERENCE_KEYS and is_reference(value):
                yield from self.iter_chain(value, visited)

    def find_first_block(self, ref_name: str, block_types: set[str]) -> dict[str, Any] | None:
        for block in self.iter_chain(ref_name):
            if block["type"] in block_types:
                return block
        return None

    def get_brdf_materials(self) -> dict[str, dict[str, Any]]:
        return {k: v for k, v in self.blocks.items() if v["type"] == "BRDFVRayMtl"}

    def get_texture_file(self, ref_name: str) -> str | None:
        bitmap_buffer = self.find_first_block(ref_name, {"BitmapBuffer"})
        if bitmap_buffer:
            return bitmap_buffer["props"].get("file", "").strip('"')

        tex_bitmap = self.find_first_block(ref_name, {"TexBitmap"})
        if tex_bitmap:
            bitmap_ref = tex_bitmap["props"].get("bitmap", "")
            if is_reference(bitmap_ref):
                return self.get_texture_file(bitmap_ref)
        return None

    def get_triplanar_info(self, ref_name: str) -> dict[str, Any] | None:
        block = self.find_first_block(ref_name, {"TexTriPlanar"})
        return dict(block["props"]) if block else None

    def get_randomizer_info(self, ref_name: str) -> dict[str, Any] | None:
        block = self.find_first_block(ref_name, {"UVWGenRandomizer"})
        return dict(block["props"]) if block else None

    def get_placement_info(self, ref_name: str) -> dict[str, Any] | None:
        block = self.find_first_block(ref_name, {"UVWGenMayaPlace2dTexture"})
        return dict(block["props"]) if block else None

    def get_remap_info(self, ref_name: str) -> dict[str, Any] | None:
        block = self.find_first_block(ref_name, {"TexRemap"})
        return dict(block["props"]) if block else None

    def get_texture_invert(self, ref_name: str) -> bool:
        block = self.find_first_block(ref_name, {"TexBitmap"})
        if not block:
            return False
        return parse_bool(block["props"].get("invert", "0"))

    def get_color_value(self, ref_name: str) -> tuple[float, float, float, float] | None:
        block = self.get_block(ref_name)
        if not block:
            return None
        props = block["props"]
        if block["type"] == "TexAColorOp":
            color_a = props.get("color_a", "")
            mult_a = props.get("mult_a", "1")
            if "AColor" in color_a and not is_reference(mult_a):
                base_color = parse_acolor(color_a)
                mult = parse_float(mult_a)
                return (
                    base_color[0] * mult,
                    base_color[1] * mult,
                    base_color[2] * mult,
                    base_color[3],
                )
        if block["type"] == "TexAColor":
            texture = props.get("texture", "")
            if "AColor" in texture:
                return parse_acolor(texture)
        return None

    def get_texture_descriptor(self, ref_name: str) -> dict[str, Any]:
        triplanar = self.get_triplanar_info(ref_name)
        descriptor = {
            "source": get_reference_name(ref_name),
            "file_path": resolve_texture_path(self.get_texture_file(ref_name) or ""),
            "triplanar": triplanar,
            "randomizer": self.get_randomizer_info(ref_name),
            "placement": self.get_placement_info(ref_name),
            "remap": self.get_remap_info(ref_name),
            "invert": self.get_texture_invert(ref_name),
            "has_distinct_triplanar_textures": False,
        }
        if triplanar:
            tex_refs = []
            for key in ("texture_x", "texture_y", "texture_z"):
                value = triplanar.get(key, "")
                if is_reference(value):
                    tex_refs.append(get_reference_name(value))
            descriptor["has_distinct_triplanar_textures"] = len(set(tex_refs)) > 1
        return descriptor

    def analyze(self) -> dict[str, Any]:
        type_counts = Counter(block["type"] for block in self.blocks.values())
        unsupported = {k: v for k, v in type_counts.items() if k not in SUPPORTED_BLOCK_TYPES}
        return {
            "block_count": len(self.blocks),
            "material_count": len(self.get_brdf_materials()),
            "material_names": sorted(self.get_brdf_materials().keys()),
            "block_type_counts": dict(sorted(type_counts.items())),
            "feature_counts": {
                "triplanar": type_counts.get("TexTriPlanar", 0),
                "randomizer": type_counts.get("UVWGenRandomizer", 0),
                "remap": type_counts.get("TexRemap", 0),
                "bitmap": type_counts.get("TexBitmap", 0),
            },
            "unsupported_block_types": unsupported,
        }


class CyclesMaterialBuilder:
    def __init__(self, parser: VRSceneParser, settings: ConverterSettings | None = None):
        self.parser = parser
        self.settings = settings or ConverterSettings()
        self.mat: Any = None
        self.nodes: Any = None
        self.links: Any = None
        self.tex_y_offset = 400
        self.created_images: dict[str, bpy.types.Image] = {}
        self.current_report: dict[str, Any] = {}

    def build(self, brdf_name: str, brdf_block: dict[str, Any]) -> tuple[bpy.types.Material, dict[str, Any]]:
        props = brdf_block["props"]
        mat_name = brdf_name.replace("@vraymtl", "").replace("@material", "")
        self.current_report = {
            "material": mat_name,
            "warnings": [],
            "textures": [],
            "used_mapping_group": False,
        }

        if self.settings.replace_existing and mat_name in bpy.data.materials:
            bpy.data.materials.remove(bpy.data.materials[mat_name], do_unlink=True)

        self.mat = bpy.data.materials.new(name=mat_name)
        self.mat.use_nodes = True
        self.mat.use_fake_user = self.settings.use_fake_user
        self.nodes = self.mat.node_tree.nodes
        self.links = self.mat.node_tree.links
        self.nodes.clear()
        self.tex_y_offset = 400

        principled = self.nodes.new("ShaderNodeBsdfPrincipled")
        principled.location = (0, 0)
        output = self.nodes.new("ShaderNodeOutputMaterial")
        output.location = (400, 0)
        self.links.new(principled.outputs["BSDF"], output.inputs["Surface"])

        self._connect_color_input(props.get("diffuse", ""), principled, "Base Color", is_color=True)

        metalness = props.get("metalness", "0")
        if is_reference(metalness):
            self._connect_texture_input(metalness, principled, "Metallic", non_color=True)
        else:
            principled.inputs["Metallic"].default_value = parse_float(metalness)

        reflect_gloss = props.get("reflect_glossiness", "1.0")
        if is_reference(reflect_gloss):
            self._connect_texture_input(reflect_gloss, principled, "Roughness", non_color=True, invert=True)
        else:
            principled.inputs["Roughness"].default_value = 1.0 - parse_float(reflect_gloss)

        reflect = props.get("reflect", "")
        if "AColor" in reflect:
            spec = parse_acolor(reflect)
            principled.inputs["Specular IOR Level"].default_value = (spec[0] + spec[1] + spec[2]) / 3.0
        elif is_reference(reflect):
            color = self.parser.get_color_value(reflect)
            if color:
                principled.inputs["Specular IOR Level"].default_value = (color[0] + color[1] + color[2]) / 3.0

        refract = props.get("refract", "")
        has_transmission = False
        if "AColor" in refract:
            trans = parse_acolor(refract)
            trans_weight = max(trans[0], trans[1], trans[2])
            if trans_weight > 0.01:
                has_transmission = True
                principled.inputs["Transmission Weight"].default_value = trans_weight
        elif is_reference(refract):
            color = self.parser.get_color_value(refract)
            if color:
                trans_weight = max(color[0], color[1], color[2])
                if trans_weight > 0.01:
                    has_transmission = True
                    principled.inputs["Transmission Weight"].default_value = trans_weight

        principled.inputs["IOR"].default_value = parse_float(
            props.get("refract_ior" if has_transmission else "fresnel_ior", "1.5")
        )

        coat_amount = parse_float(props.get("coat_amount", "0"))
        if coat_amount > 0.01:
            principled.inputs["Coat Weight"].default_value = coat_amount
            principled.inputs["Coat Roughness"].default_value = 1.0 - parse_float(props.get("coat_glossiness", "1"))
            principled.inputs["Coat IOR"].default_value = parse_float(props.get("coat_ior", "1.5"))
            coat_color = props.get("coat_color", "")
            if "AColor" in coat_color:
                principled.inputs["Coat Tint"].default_value = parse_acolor(coat_color)

        sheen = props.get("sheen_color", "")
        if "AColor" in sheen:
            sheen_color = parse_acolor(sheen)
            sheen_intensity = max(sheen_color[0], sheen_color[1], sheen_color[2])
            if sheen_intensity > 0.01:
                principled.inputs["Sheen Weight"].default_value = sheen_intensity
                principled.inputs["Sheen Tint"].default_value = sheen_color
                principled.inputs["Sheen Roughness"].default_value = 1.0 - parse_float(props.get("sheen_glossiness", "0.8"))

        self_illum = props.get("self_illumination", "")
        if "AColor" in self_illum:
            emission = parse_acolor(self_illum)
            if max(emission[0], emission[1], emission[2]) > 0.01:
                principled.inputs["Emission Color"].default_value = emission
                principled.inputs["Emission Strength"].default_value = 1.0

        opacity = props.get("opacity_color", "")
        if "AColor" in opacity:
            alpha = parse_acolor(opacity)
            alpha_val = min(alpha[0], alpha[3])
            if alpha_val < 0.999:
                principled.inputs["Alpha"].default_value = alpha_val
                self.mat.blend_method = "BLEND"
        elif is_reference(opacity):
            self._connect_texture_input(opacity, principled, "Alpha", non_color=True)
            self.mat.blend_method = "BLEND"

        bump_tex = props.get("bump_map", "")
        if is_reference(bump_tex):
            self._connect_bump_input(
                bump_tex,
                principled,
                parse_float(props.get("bump_amount", "1")),
                parse_int(props.get("bump_type", "0")),
            )

        anisotropy = parse_float(props.get("anisotropy", "0"))
        if abs(anisotropy) > 0.01:
            principled.inputs["Anisotropic"].default_value = abs(anisotropy)
            principled.inputs["Anisotropic Rotation"].default_value = parse_float(props.get("anisotropy_rotation", "0")) / 360.0

        return self.mat, self.current_report

    def _warn(self, message: str):
        self.current_report.setdefault("warnings", []).append(message)

    def _register_texture(self, descriptor: dict[str, Any]):
        self.current_report.setdefault("textures", []).append(
            {
                "source": descriptor["source"],
                "file_path": descriptor["file_path"],
                "triplanar": bool(descriptor["triplanar"]),
                "randomizer": bool(descriptor["randomizer"]),
                "remap": bool(descriptor["remap"]),
            }
        )

    def _connect_color_input(self, value: str, node, input_name: str, is_color: bool = False):
        if "AColor" in value:
            node.inputs[input_name].default_value = parse_acolor(value)
            return

        if not is_reference(value):
            return

        ref_name = get_reference_name(value)
        block = self.parser.get_block(ref_name)
        if block and block["type"] == "TexAColorOp":
            props = block["props"]
            color_a = props.get("color_a", "")
            mult_a = props.get("mult_a", "1")

            if "AColor" in color_a and is_reference(mult_a):
                output_socket = self._make_texture_socket(mult_a, non_color=not is_color)
                if output_socket:
                    rgb = self.nodes.new("ShaderNodeRGB")
                    rgb.location = (-500, self.tex_y_offset - 80)
                    rgb.outputs[0].default_value = parse_acolor(color_a)
                    mix = self.nodes.new("ShaderNodeMixRGB")
                    mix.blend_type = "MULTIPLY"
                    mix.inputs["Fac"].default_value = 1.0
                    mix.location = (-50, self.tex_y_offset)
                    self.links.new(rgb.outputs[0], mix.inputs["Color1"])
                    self.links.new(output_socket, mix.inputs["Color2"])
                    self.links.new(mix.outputs["Color"], node.inputs[input_name])
                    self.tex_y_offset -= 350
                    return
                node.inputs[input_name].default_value = parse_acolor(color_a)
                return

            if is_reference(color_a):
                self._connect_texture_input(color_a, node, input_name, non_color=not is_color)
                return

        self._connect_texture_input(value, node, input_name, non_color=not is_color)

    def _connect_texture_input(self, ref_name: str, node, input_name: str, non_color: bool = False, invert: bool = False):
        output_socket = self._make_texture_socket(ref_name, non_color=non_color, invert_output=invert)
        if output_socket:
            self.links.new(output_socket, node.inputs[input_name])
            self.tex_y_offset -= 300

    def _connect_bump_input(self, ref_name: str, principled, amount: float, bump_type: int):
        output_socket = self._make_texture_socket(ref_name, non_color=True)
        if not output_socket:
            return

        node_x = 100
        node_y = self.tex_y_offset
        use_normal_map = bump_type in {1, 5}

        if use_normal_map:
            normal = self.nodes.new("ShaderNodeNormalMap")
            normal.location = (node_x, node_y)
            normal.inputs["Strength"].default_value = amount
            self.links.new(output_socket, normal.inputs["Color"])
            self.links.new(normal.outputs["Normal"], principled.inputs["Normal"])
        else:
            bump = self.nodes.new("ShaderNodeBump")
            bump.location = (node_x, node_y)
            bump.inputs["Strength"].default_value = min(amount * 10, 1.0)
            bump.inputs["Distance"].default_value = 0.02
            self.links.new(output_socket, bump.inputs["Height"])
            self.links.new(bump.outputs["Normal"], principled.inputs["Normal"])

    def _make_texture_socket(self, ref_name: str, non_color: bool = False, invert_output: bool = False):
        descriptor = self.parser.get_texture_descriptor(ref_name)
        if not descriptor["file_path"]:
            self._warn(f"Texture file not found for {ref_name}")
            return None
        if not os.path.exists(descriptor["file_path"]):
            self._warn(f"Texture path does not exist: {descriptor['file_path']}")
            return None

        self._register_texture(descriptor)
        color_socket = self._create_texture_source(descriptor, non_color)
        if not color_socket:
            return None

        output_socket = color_socket
        if descriptor["invert"]:
            output_socket = self._add_invert(output_socket, "TexBitmap Invert")
        if descriptor["remap"]:
            output_socket = self._apply_remap(output_socket, descriptor["remap"])
        if invert_output:
            output_socket = self._add_invert(output_socket, "Gloss to Rough")
        return output_socket

    def _create_texture_source(self, descriptor: dict[str, Any], non_color: bool):
        image = self._load_image(descriptor["file_path"], non_color)
        if not image:
            return None

        if descriptor["has_distinct_triplanar_textures"]:
            self._warn(
                f"{descriptor['source']}: multi-texture triplanar detected, using primary texture only"
            )

        if self.settings.mapping_group_name and (descriptor["triplanar"] or descriptor["randomizer"]):
            socket = self._create_group_texture_source(image, descriptor, non_color)
            if socket:
                self.current_report["used_mapping_group"] = True
                return socket

        if descriptor["randomizer"]:
            self._warn(f"{descriptor['source']}: UVWGenRandomizer present but custom mapping group was not used")

        if descriptor["triplanar"]:
            return self._create_box_texture_source(image, descriptor, non_color)
        return self._create_uv_texture_source(image, descriptor, non_color)

    def _load_image(self, file_path: str, non_color: bool):
        img_name = os.path.basename(file_path)
        if img_name in self.created_images:
            image = self.created_images[img_name]
        elif img_name in bpy.data.images:
            image = bpy.data.images[img_name]
        else:
            try:
                image = bpy.data.images.load(file_path)
                self.created_images[img_name] = image
            except Exception as exc:
                self._warn(f"Failed to load image {file_path}: {exc}")
                return None
        image.colorspace_settings.name = "Non-Color" if non_color else "sRGB"
        return image

    def _create_uv_texture_source(self, image, descriptor: dict[str, Any], non_color: bool):
        tex_coord = self.nodes.new("ShaderNodeTexCoord")
        tex_coord.location = (-1100, self.tex_y_offset)
        mapping = self.nodes.new("ShaderNodeMapping")
        mapping.location = (-900, self.tex_y_offset)
        tex_node = self.nodes.new("ShaderNodeTexImage")
        tex_node.location = (-650, self.tex_y_offset)
        tex_node.image = image
        self.links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])
        self.links.new(mapping.outputs["Vector"], tex_node.inputs["Vector"])
        self._apply_uv_placement(mapping, descriptor.get("placement"))
        return tex_node.outputs["Color"]

    def _create_box_texture_source(self, image, descriptor: dict[str, Any], non_color: bool):
        tex_coord = self.nodes.new("ShaderNodeTexCoord")
        tex_coord.location = (-1100, self.tex_y_offset)
        mapping = self.nodes.new("ShaderNodeMapping")
        mapping.location = (-900, self.tex_y_offset)
        tex_node = self.nodes.new("ShaderNodeTexImage")
        tex_node.location = (-650, self.tex_y_offset)
        tex_node.image = image
        tex_node.projection = "BOX"

        triplanar = descriptor.get("triplanar") or {}
        size = parse_float(triplanar.get("size", "1"))
        if size <= 0:
            size = 1.0
        mapping.inputs["Scale"].default_value = (100.0 / size, 100.0 / size, 100.0 / size)
        tex_node.projection_blend = parse_float(triplanar.get("blend", "0.1"))

        self.links.new(tex_coord.outputs["Object"], mapping.inputs["Vector"])
        self.links.new(mapping.outputs["Vector"], tex_node.inputs["Vector"])
        return tex_node.outputs["Color"]

    def _create_group_texture_source(self, image, descriptor: dict[str, Any], non_color: bool):
        group = bpy.data.node_groups.get(self.settings.mapping_group_name)
        if not group:
            self._warn(f"Mapping node group not found: {self.settings.mapping_group_name}")
            return None
        if group.bl_idname != "ShaderNodeTree":
            self._warn(f"Node group is not a shader node group: {group.name}")
            return None

        group_node = self.nodes.new("ShaderNodeGroup")
        group_node.location = (-650, self.tex_y_offset)
        group_node.node_tree = group

        image_node = self.nodes.new("ShaderNodeTexImage")
        image_node.location = (-900, self.tex_y_offset + 40)
        image_node.image = image

        tex_coord = self.nodes.new("ShaderNodeTexCoord")
        tex_coord.location = (-1150, self.tex_y_offset - 100)

        vector_socket = self._find_socket(group_node.inputs, "vector_input")
        uv_socket = self._find_socket(group_node.inputs, "uv_vector_input")
        object_socket = self._find_socket(group_node.inputs, "object_vector_input")

        if vector_socket:
            output_name = "Object" if descriptor.get("triplanar") else "UV"
            self.links.new(tex_coord.outputs[output_name], vector_socket)
        if uv_socket:
            self.links.new(tex_coord.outputs["UV"], uv_socket)
        if object_socket:
            self.links.new(tex_coord.outputs["Object"], object_socket)

        image_input = self._find_socket(group_node.inputs, "image_input")
        alpha_input = self._find_socket(group_node.inputs, "alpha_input")
        if image_input:
            self.links.new(image_node.outputs["Color"], image_input)
        if alpha_input:
            self.links.new(image_node.outputs["Alpha"], alpha_input)

        self._set_group_values(group_node, descriptor)

        color_output = self._find_socket(group_node.outputs, "color_output")
        alpha_output = self._find_socket(group_node.outputs, "alpha_output")
        vector_output = self._find_socket(group_node.outputs, "vector_output")

        if vector_output:
            image_node.location = (-300, self.tex_y_offset + 40)
            self.links.new(vector_output, image_node.inputs["Vector"])
            return image_node.outputs["Color"]

        if color_output:
            return color_output

        if alpha_output:
            return alpha_output

        self._warn(f"No usable output found on node group {group.name}")
        return None

    def _apply_uv_placement(self, mapping, placement: dict[str, Any] | None):
        if not placement:
            return
        repeat_u = parse_float(placement.get("repeat_u", "1"))
        repeat_v = parse_float(placement.get("repeat_v", "1"))
        coverage_u = parse_float(placement.get("coverage_u", "1")) or 1.0
        coverage_v = parse_float(placement.get("coverage_v", "1")) or 1.0
        offset_u = parse_float(placement.get("offset_u", "0")) + parse_float(placement.get("translate_frame_u", "0"))
        offset_v = parse_float(placement.get("offset_v", "0")) + parse_float(placement.get("translate_frame_v", "0"))
        rotate = parse_float(placement.get("rotate_uv", "0")) + parse_float(placement.get("rotate_frame", "0"))

        mapping.inputs["Scale"].default_value[0] = repeat_u / coverage_u
        mapping.inputs["Scale"].default_value[1] = repeat_v / coverage_v
        mapping.inputs["Location"].default_value[0] = offset_u
        mapping.inputs["Location"].default_value[1] = offset_v
        mapping.inputs["Rotation"].default_value[2] = math.radians(rotate)

    def _find_socket(self, sockets, semantic_key: str):
        explicit = self.settings.group_socket_map.get(semantic_key)
        names = []
        if explicit:
            names.append(explicit)
        names.extend(SOCKET_ALIASES.get(semantic_key, []))
        for candidate in names:
            normalized = normalize_socket_name(candidate)
            for socket in sockets:
                if normalize_socket_name(socket.name) == normalized:
                    return socket
        return None

    def _set_socket_default(self, socket, value):
        if socket is None or not hasattr(socket, "default_value"):
            return False
        try:
            current = socket.default_value
            if isinstance(value, (list, tuple)):
                try:
                    current[:] = value
                except Exception:
                    socket.default_value = value
            else:
                socket.default_value = value
            return True
        except Exception:
            return False

    def _set_group_values(self, group_node, descriptor: dict[str, Any]):
        triplanar = descriptor.get("triplanar") or {}
        randomizer = descriptor.get("randomizer") or {}
        placement = descriptor.get("placement") or {}

        value_map = {
            "use_triplanar_input": bool(triplanar),
            "triplanar_size_input": parse_float(triplanar.get("size", "1")),
            "triplanar_blend_input": parse_float(triplanar.get("blend", "0.1")),
            "use_randomizer_input": bool(randomizer),
            "random_seed_input": parse_int(randomizer.get("seed", "0")),
            "random_u_min_input": parse_float(randomizer.get("variance_u_min", "0")),
            "random_u_max_input": parse_float(randomizer.get("variance_u_max", "0")),
            "random_v_min_input": parse_float(randomizer.get("variance_v_min", "0")),
            "random_v_max_input": parse_float(randomizer.get("variance_v_max", "0")),
            "random_rotation_min_input": parse_float(randomizer.get("variance_rot_min", "0")),
            "random_rotation_max_input": parse_float(randomizer.get("variance_rot_max", "0")),
            "random_uscale_min_input": parse_float(randomizer.get("variance_uscale_min", "100")) / 100.0,
            "random_uscale_max_input": parse_float(randomizer.get("variance_uscale_max", "100")) / 100.0,
            "random_tile_blend_input": parse_float(randomizer.get("tile_blend", "0.05")),
            "repeat_u_input": parse_float(placement.get("repeat_u", "1")),
            "repeat_v_input": parse_float(placement.get("repeat_v", "1")),
            "offset_u_input": parse_float(placement.get("offset_u", "0")),
            "offset_v_input": parse_float(placement.get("offset_v", "0")),
            "rotate_uv_input": parse_float(placement.get("rotate_uv", "0")),
            "coverage_u_input": parse_float(placement.get("coverage_u", "1")),
            "coverage_v_input": parse_float(placement.get("coverage_v", "1")),
        }

        for semantic_key, value in value_map.items():
            socket = self._find_socket(group_node.inputs, semantic_key)
            if socket is not None:
                self._set_socket_default(socket, value)

    def _add_invert(self, input_socket, label: str):
        invert_node = self.nodes.new("ShaderNodeInvert")
        invert_node.location = (input_socket.node.location[0] + 220, input_socket.node.location[1])
        invert_node.label = label
        self.links.new(input_socket, invert_node.inputs["Color"])
        return invert_node.outputs["Color"]

    def _apply_remap(self, input_socket, remap_props: dict[str, Any]):
        map_range = self.nodes.new("ShaderNodeMapRange")
        map_range.location = (input_socket.node.location[0] + 220, input_socket.node.location[1])
        map_range.label = "TexRemap"
        map_range.clamp = True
        map_range.inputs["From Min"].default_value = parse_float(remap_props.get("input_min", "0"))
        map_range.inputs["From Max"].default_value = parse_float(remap_props.get("input_max", "1"))
        map_range.inputs["To Min"].default_value = parse_float(remap_props.get("output_min", "0"))
        map_range.inputs["To Max"].default_value = parse_float(remap_props.get("output_max", "1"))
        self.links.new(input_socket, map_range.inputs["Value"])
        return map_range.outputs["Result"]


def _make_settings(mapping_group_name=None, group_socket_map=None, replace_existing=False, use_fake_user=True):
    return ConverterSettings(
        mapping_group_name=mapping_group_name,
        group_socket_map=group_socket_map or {},
        replace_existing=replace_existing,
        use_fake_user=use_fake_user,
    )


def analyze_vrscene_file(filepath: str) -> dict[str, Any]:
    with open(filepath, "r", encoding="utf-8", errors="ignore") as handle:
        parser = VRSceneParser(handle.read())
    parser.parse()
    result = parser.analyze()
    result["filepath"] = filepath
    return result


def convert_vrscene_file(
    filepath: str,
    mapping_group_name: str | None = None,
    group_socket_map: dict[str, str] | None = None,
    replace_existing: bool = False,
    use_fake_user: bool = True,
) -> dict[str, Any]:
    with open(filepath, "r", encoding="utf-8", errors="ignore") as handle:
        parser = VRSceneParser(handle.read())
    parser.parse()

    builder = CyclesMaterialBuilder(
        parser,
        _make_settings(
            mapping_group_name=mapping_group_name,
            group_socket_map=group_socket_map,
            replace_existing=replace_existing,
            use_fake_user=use_fake_user,
        ),
    )

    materials = []
    reports = []
    for name, block in parser.get_brdf_materials().items():
        try:
            material, report = builder.build(name, block)
            materials.append(material)
            reports.append(report)
        except Exception as exc:
            reports.append({"material": name, "warnings": [str(exc)], "textures": [], "used_mapping_group": False})

    return {
        "filepath": filepath,
        "material_count": len(materials),
        "materials": [mat.name for mat in materials],
        "reports": reports,
        "analysis": parser.analyze(),
    }


def convert_vrscene_folder(
    folder_path: str,
    output_blend_path: str | None = None,
    mapping_group_name: str | None = None,
    group_socket_map: dict[str, str] | None = None,
    replace_existing: bool = False,
    use_fake_user: bool = True,
) -> dict[str, Any]:
    folder = Path(folder_path)
    if not folder.exists():
        raise ValueError(f"Folder not found: {folder_path}")

    file_results = []
    all_material_names = []
    export_materials = set()

    for vrscene_file in sorted(folder.glob("*.vrscene")):
        result = convert_vrscene_file(
            str(vrscene_file),
            mapping_group_name=mapping_group_name,
            group_socket_map=group_socket_map,
            replace_existing=replace_existing,
            use_fake_user=use_fake_user,
        )
        file_results.append(result)
        all_material_names.extend(result["materials"])
        for material_name in result["materials"]:
            material = bpy.data.materials.get(material_name)
            if material:
                export_materials.add(material)

    exported = False
    if output_blend_path and export_materials:
        out_path = Path(output_blend_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        bpy.data.libraries.write(str(out_path), export_materials, path_remap="ABSOLUTE", fake_user=True)
        exported = True

    return {
        "folder_path": str(folder),
        "vrscene_count": len(file_results),
        "material_count": len(all_material_names),
        "materials": all_material_names,
        "output_blend_path": output_blend_path,
        "exported": exported,
        "files": file_results,
    }
