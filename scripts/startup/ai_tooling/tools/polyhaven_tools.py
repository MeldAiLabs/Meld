"""
Langchain tools for interacting with the Poly Haven asset library (polyhaven.com).
Allows searching, downloading, and applying assets directly within Blender.

Derived from code by Siddharth Ahuja: www.github.com/ahujasid © 2025
"""
import requests
import tempfile
import os
import shutil
import traceback
import json
from typing import Optional, List, Dict, Any
import bpy

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ..logging_utils import log


# --- Helper Functions (Internal) ---

def _report_error(message: str, e: Optional[Exception] = None) -> str:
    """Formats an error message for tool output."""
    log(f"PolyHaven Tool Error: {message}", "ERROR")
    if e:
        log(traceback.format_exc(), "DEBUG")
    error_msg = f"[ERROR] {message}"
    if e:
        error_msg += f": {str(e)}"
    return error_msg


# --- Pydantic Models for Tool Arguments ---

class SearchPolyHavenAssetsArgs(BaseModel):
    """Input schema for search_polyhaven_assets."""
    query: Optional[str] = Field(None, description="Optional search query string.")
    asset_type: Optional[str] = Field(
        None,
        description="Filter by asset type. One of: 'hdris', 'textures', 'models', 'all'. Default is 'all'.")
    categories: Optional[str] = Field(
        None,
        description="Filter by categories (comma-separated string). Use get_polyhaven_categories first to see available categories.")


class DownloadPolyHavenAssetArgs(BaseModel):
    """Input schema for download_polyhaven_asset."""
    asset_id: str = Field(..., description="The unique ID of the Poly Haven asset (e.g., 'wood_planks').")
    asset_type: str = Field(..., description="The type of the asset. One of: 'hdris', 'textures', 'models'.")
    resolution: str = Field(
        "1k",
        description="Desired resolution (e.g., '1k', '2k', '4k'). Availability depends on the asset.")
    file_format: Optional[str] = Field(
        None,
        description="Desired file format (e.g., 'hdr', 'exr', 'jpg', 'png', 'gltf', 'blend'). If None, a default is chosen based on asset type.")


class SetPolyHavenTextureArgs(BaseModel):
    """Input schema for set_polyhaven_texture."""
    object_name: str = Field(..., description="The name of the Blender object to apply the material to.")
    texture_id: str = Field(...,
                            description="The Poly Haven asset ID of the texture previously downloaded via 'download_polyhaven_asset'.")


# --- Langchain Tools ---

@tool
def get_polyhaven_categories(asset_type: str) -> str:
    """
    Retrieves the available categories for a specific asset type from Poly Haven (polyhaven.com).
    Use this to find valid category names for filtering searches.

    Args:
        asset_type: The type of asset ('hdris', 'textures', 'models', or 'all').

    Returns:
        A JSON string containing the list of categories for the specified asset type,
        or a JSON string with an error message.
    """
    log(f"Executing get_polyhaven_categories for type: {asset_type}", "INFO")
    valid_types = ["hdris", "textures", "models", "all"]
    if asset_type not in valid_types:
        return _report_error(f"Invalid asset type '{asset_type}'. Must be one of: {valid_types}")

    try:
        url = f"https://api.polyhaven.com/categories/{asset_type}"
        response = requests.get(url, timeout=30)
        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        categories = response.json()
        log(f"Found {len(categories)} categories for {asset_type}", "DEBUG")
        return json.dumps({"categories": categories})
    except requests.exceptions.RequestException as e:
        return _report_error(f"API request failed for categories '{asset_type}'", e)
    except json.JSONDecodeError as e:
        return _report_error(f"Failed to decode API response for categories '{asset_type}'", e)
    except Exception as e:
        return _report_error(f"Unexpected error getting categories for '{asset_type}'", e)


@tool("search_polyhaven_assets", args_schema=SearchPolyHavenAssetsArgs)
def search_polyhaven_assets(query: Optional[str] = None, asset_type: Optional[str] = None,
                            categories: Optional[str] = None) -> str:
    """
    Searches for assets on Poly Haven (polyhaven.com) based on type, categories, or a search query.

    Args:
        query: Optional search query string.
        asset_type: Optional filter by asset type ('hdris', 'textures', 'models', 'all').
        categories: Optional filter by categories (comma-separated string). Use get_polyhaven_categories first.

    Returns:
        A JSON string containing a list of found assets (limited to ~20 results for performance)
        including their IDs, names, and types, or a JSON string with an error message.
    """
    log(f"Executing search_polyhaven_assets: query='{query}', type='{asset_type}', categories='{categories}'", "INFO")
    url = "https://api.polyhaven.com/assets"
    params: Dict[str, Any] = {}

    # Explicitly treat string "None" or empty string as None for categories
    if categories and categories.strip().lower() in ["none", ""]:
        log("Treating categories='None' or empty string as None.", "DEBUG")
        categories = None

    if query:
        log(
            f"Warning: PolyHaven /assets API does not directly support text query '{query}'. Performing client-side filtering on key and categories.",
            "WARNING")
        if not asset_type:
            asset_type = 'all'  # Default to all if query is present but type isn't

    if asset_type and asset_type != "all":
        valid_types = ["hdris", "textures", "models"]
        if asset_type not in valid_types:
            return _report_error(f"Invalid asset type: {asset_type}. Must be one of: {valid_types}, 'all'")
        params["type"] = asset_type

    # Add categories only if it's a non-empty string after potential modification
    if categories:
        params["categories"] = categories

    try:
        log(f"Requesting Poly Haven API with params: {params}", "INFO")
        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()
        all_assets = response.json()
        log(f"API returned {len(all_assets)} assets based on type/category params.", "INFO")

        # Client-side query filtering (check key and categories)
        filtered_assets = {}
        if query:
            query_lower = query.lower()
            log(f"Performing client-side filtering for query: '{query_lower}'", "INFO")
            for key, value in all_assets.items():
                match = False
                # Check if query is in the asset key (ID)
                if query_lower in key.lower():
                    match = True
                    log(f"  Match found in key: '{key}'", "INFO")
                # Check if query is in any of the asset's categories
                elif isinstance(value.get("categories"), list):
                    asset_categories = [cat.lower() for cat in value["categories"]]
                    if any(query_lower in cat for cat in asset_categories):
                        match = True
                        log(f"  Match found in categories for key '{key}': {asset_categories}", "INFO")

                if match:
                    filtered_assets[key] = value
            log(f"Client-side filtering resulted in {len(filtered_assets)} assets.", "INFO")
        else:
            # No query provided, use all assets returned by API based on type/category
            filtered_assets = all_assets
            log("No query provided, using all assets returned by API.", "INFO")

        # Limit the response size
        limit = 20
        limited_assets = {}
        if not filtered_assets:
            log("No assets found matching the criteria after filtering.", "INFO")
        else:
            for i, (key, value) in enumerate(filtered_assets.items()):
                if i >= limit:
                    break
                # Simplify asset data slightly for the LLM if needed
                limited_assets[key] = {
                    "name": key,  # Use the key as the name/ID
                    "type": value.get("type", "unknown"),
                    "categories": value.get("categories", [])
                }

        result = {
            "assets": limited_assets,
            "total_found_before_limit": len(filtered_assets),
            "returned_count": len(limited_assets)
        }
        log(f"Final result: Found {len(filtered_assets)} assets matching criteria, returning {len(limited_assets)}.", "INFO")
        return json.dumps(result)

    except requests.exceptions.RequestException as e:
        return _report_error("API request failed searching assets", e)
    except json.JSONDecodeError as e:
        return _report_error("Failed to decode API response searching assets", e)
    except Exception as e:
        return _report_error("Unexpected error searching assets", e)


@tool("download_polyhaven_asset", args_schema=DownloadPolyHavenAssetArgs)
def download_polyhaven_asset(asset_id: str, asset_type: str, resolution: str = "1k",
                             file_format: Optional[str] = None) -> str:
    """
    Downloads a specific asset (HDRI, Texture set, or Model) from Poly Haven (polyhaven.com)
    and imports/sets it up in the current Blender scene.
    For textures, this downloads the maps and creates a new material using them.
    For HDRIs, it sets up the world background.
    For models, it imports the model file (e.g., glTF, Blend).

    Args:
        asset_id: The unique ID of the asset (e.g., 'wood_planks').
        asset_type: The type of the asset ('hdris', 'textures', 'models').
        resolution: Desired resolution ('1k', '2k', '4k', etc.). Default is '1k'.
        file_format: Desired file format ('hdr', 'exr', 'jpg', 'png', 'gltf', 'blend'). Default depends on asset type.

    Returns:
        A JSON string indicating success (including names of created Blender data like materials,
        images, or imported objects) or a JSON string with an error message.
    """
    log(
        f"Executing download_polyhaven_asset: id='{asset_id}', type='{asset_type}', res='{resolution}', format='{file_format}'",
        "INFO")
    temp_dir = None
    temp_files: List[str] = []  # Keep track of temp files created

    try:
        # --- 1. Get File Information ---
        files_url = f"https://api.polyhaven.com/files/{asset_id}"
        files_response = requests.get(files_url, timeout=30)
        files_response.raise_for_status()
        files_data = files_response.json()
        log(f"Retrieved file info for {asset_id}", "DEBUG")

        # --- 2. Handle Asset Type ---
        if asset_type == "hdris":
            if not file_format:
                file_format = "hdr"
            log(f"Processing HDRI: format={file_format}, resolution={resolution}", "DEBUG")

            if "hdri" not in files_data or \
               resolution not in files_data["hdri"] or \
               file_format not in files_data["hdri"][resolution]:
                return _report_error(
                    f"Requested resolution '{resolution}' or format '{file_format}' not available for HDRI '{asset_id}'")

            file_info = files_data["hdri"][resolution][file_format]
            file_url = file_info["url"]

            # Download to temp file
            with tempfile.NamedTemporaryFile(suffix=f".{file_format}", delete=False) as tmp_file:
                response = requests.get(file_url, stream=True, timeout=120)
                response.raise_for_status()
                for chunk in response.iter_content(chunk_size=8192):
                    tmp_file.write(chunk)
                tmp_path = tmp_file.name
                temp_files.append(tmp_path)
                log(f"HDRI downloaded to temporary file: {tmp_path}", "DEBUG")

            # Set up Blender world (MUST run in main thread, ensured by tool execution)
            try:
                world = bpy.context.scene.world
                if not world:
                    world = bpy.data.worlds.new(f"{asset_id}_World")
                    bpy.context.scene.world = world
                    log(f"Created new world: {world.name}", "DEBUG")
                else:
                    log(f"Using existing world: {world.name}", "DEBUG")

                world.use_nodes = True
                node_tree = world.node_tree
                for node in node_tree.nodes:
                    node_tree.nodes.remove(node)  # Clear existing

                tex_coord = node_tree.nodes.new(type='ShaderNodeTexCoord')
                mapping = node_tree.nodes.new(type='ShaderNodeMapping')
                env_tex = node_tree.nodes.new(type='ShaderNodeTexEnvironment')
                background = node_tree.nodes.new(type='ShaderNodeBackground')
                output = node_tree.nodes.new(type='ShaderNodeOutputWorld')

                env_tex.image = bpy.data.images.load(tmp_path, check_existing=True)
                env_tex.image.name = f"{asset_id}_{resolution}.{file_format}"
                # env_tex.image.pack() # Packing large HDRIs might bloat .blend, consider user preference?

                # Set appropriate color space
                cs_settings = env_tex.image.colorspace_settings
                preferred_cs = 'Linear Rec.709' if bpy.app.version >= (2, 80, 0) else 'Linear'
                available_cs = [cs.name for cs in bpy.data.color_spaces]

                if file_format.lower() == 'exr':
                    if 'Linear' in available_cs:
                        cs_settings.name = 'Linear'
                    elif 'Non-Color' in available_cs:
                        cs_settings.name = 'Non-Color'
                else:  # hdr
                    if preferred_cs in available_cs:
                        cs_settings.name = preferred_cs
                    elif 'Linear' in available_cs:
                        cs_settings.name = 'Linear'
                    elif 'Non-Color' in available_cs:
                        cs_settings.name = 'Non-Color'

                log(f"Set HDRI colorspace to: {cs_settings.name}", "DEBUG")

                # Position and link nodes
                tex_coord.location = (-800, 0)
                mapping.location = (-600, 0)
                env_tex.location = (-400, 0)
                background.location = (-200, 0)
                output.location = (0, 0)

                node_tree.links.new(tex_coord.outputs['Generated'], mapping.inputs['Vector'])
                node_tree.links.new(mapping.outputs['Vector'], env_tex.inputs['Vector'])
                node_tree.links.new(env_tex.outputs['Color'], background.inputs['Color'])
                node_tree.links.new(background.outputs['Background'], output.inputs['Surface'])

                log(f"HDRI '{asset_id}' setup complete in world '{world.name}'", "INFO")
                return json.dumps({
                    "success": True,
                    "message": f"HDRI '{asset_id}' ({resolution}, {file_format}) set up as world background.",
                    "world_name": world.name,
                    "image_name": env_tex.image.name
                })

            except Exception as e:
                return _report_error(f"Failed to set up HDRI in Blender for '{asset_id}'", e)

        elif asset_type == "textures":
            if not file_format:
                file_format = "jpg"  # Default to JPG for textures
            log(f"Processing Texture Set: format={file_format}, resolution={resolution}", "DEBUG")

            downloaded_maps = {}
            temp_dir = tempfile.mkdtemp(prefix=f"polyhaven_{asset_id}_")
            log(f"Created temp dir for textures: {temp_dir}", "DEBUG")

            # Download all available maps for the resolution/format
            possible_maps = files_data.get(file_format, files_data.get('png', {}))  # Fallback png? Check API structure

            # --- Refined texture map download logic ---
            download_tasks = []
            for map_type, res_data in files_data.items():
                # Skip non-texture map types like 'blend' or 'gltf'
                # Common map types: diff (diffuse/albedo), rough, nor_gl, nor_dx, disp, ao, arm, metal, spec, emi, etc.
                if map_type in ["blend", "gltf", "fbx", "obj", "usdz", "sbsar", "sbs"]:
                    continue

                if isinstance(res_data, dict) and resolution in res_data:
                    format_data = res_data[resolution]
                    if isinstance(format_data, dict) and file_format in format_data:
                        file_info = format_data[file_format]
                        if isinstance(file_info, dict) and "url" in file_info:
                            download_tasks.append({"map_type": map_type, "url": file_info["url"]})
                        else:
                            log(f"Skipping map '{map_type}': Invalid file info structure.", "DEBUG")
                    else:
                        log(f"Skipping map '{map_type}': Format '{file_format}' not found for resolution '{resolution}'.", "DEBUG")
                else:
                    log(f"Skipping map '{map_type}': Resolution '{resolution}' not found or invalid data structure.", "DEBUG")

            if not download_tasks:
                return _report_error(
                    f"No texture maps found for asset '{asset_id}' with resolution '{resolution}' and format '{file_format}'")

            log(f"Found {len(download_tasks)} texture maps to download.", "DEBUG")

            for task in download_tasks:
                map_type = task["map_type"]
                file_url = task["url"]
                # Sanitize map_type for filename if necessary
                safe_map_type = ''.join(c if c.isalnum() else '_' for c in map_type)
                tmp_filename = f"{asset_id}_{safe_map_type}_{resolution}.{file_format}"
                tmp_path = os.path.join(temp_dir, tmp_filename)

                try:
                    response = requests.get(file_url, stream=True, timeout=120)
                    response.raise_for_status()
                    with open(tmp_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    temp_files.append(tmp_path)  # Add path for cleanup

                    # Load image into Blender
                    image = bpy.data.images.load(tmp_path, check_existing=True)
                    image.name = tmp_filename  # Use the descriptive name
                    image.pack()  # Pack into .blend

                    # Set color space
                    cs_settings = image.colorspace_settings
                    available_cs = [cs.name for cs in bpy.data.color_spaces]
                    is_srgb = map_type.lower() in [
                        'diff',
                        'albedo',
                        'diffuse',
                        'color',
                        'col',
                        'base_color',
                        'basecolor',
                        'spec',
                        'emi']  # Add emissive/specular?
                    target_cs = 'sRGB' if is_srgb else 'Non-Color'

                    if target_cs in available_cs:
                        cs_settings.name = target_cs
                    elif 'Non-Color' in available_cs:
                        cs_settings.name = 'Non-Color'  # Fallback

                    downloaded_maps[map_type] = image  # Store the bpy.types.Image object
                    log(f"Downloaded and loaded map '{map_type}' ({image.name}), colorspace: {cs_settings.name}", "DEBUG")

                except requests.exceptions.RequestException as e:
                    log(f"Failed to download map '{map_type}' from {file_url}: {e}", "WARNING")
                    # Optionally continue to download other maps
                except Exception as e:
                    log(f"Failed to load map '{map_type}' from {tmp_path} into Blender: {e}", "WARNING")
                    # Optionally continue

            if not downloaded_maps:
                return _report_error(f"Failed to download or load any maps for texture '{asset_id}'.")

            # Create material (adapted from set_texture logic)
            try:
                mat_name = f"{asset_id}_{resolution}_mat"
                existing_mat = bpy.data.materials.get(mat_name)
                if existing_mat:
                    bpy.data.materials.remove(existing_mat)  # Replace existing

                mat = bpy.data.materials.new(name=mat_name)
                mat.use_nodes = True
                nodes = mat.node_tree.nodes
                links = mat.node_tree.links
                nodes.clear()

                output = nodes.new(type='ShaderNodeOutputMaterial')
                output.location = (600, 0)
                principled = nodes.new(type='ShaderNodeBsdfPrincipled')
                principled.location = (300, 0)
                links.new(principled.outputs['BSDF'], output.inputs['Surface'])

                tex_coord = nodes.new(type='ShaderNodeTexCoord')
                tex_coord.location = (-800, 0)
                mapping = nodes.new(type='ShaderNodeMapping')
                mapping.location = (-600, 0)
                links.new(tex_coord.outputs['UV'], mapping.inputs['Vector'])

                # Create and connect texture nodes
                y_pos = 300
                x_pos = -400
                tex_nodes = {}
                for map_type, image in downloaded_maps.items():
                    tex_node = nodes.new(type='ShaderNodeTexImage')
                    tex_node.image = image
                    tex_node.location = (x_pos, y_pos)
                    links.new(mapping.outputs['Vector'], tex_node.inputs['Vector'])
                    tex_nodes[map_type] = tex_node
                    y_pos -= 300  # Adjust spacing

                # Connect specific maps
                log("Connecting texture maps to Principled BSDF...", "DEBUG")
                # Base Color (Diffuse, Albedo)
                base_color_map = next(
                    (m for m in [
                        'diff',
                        'albedo',
                        'color',
                        'col',
                        'base_color',
                        'basecolor'] if m in tex_nodes),
                    None)
                if base_color_map:
                    links.new(tex_nodes[base_color_map].outputs['Color'], principled.inputs['Base Color'])

                # Roughness
                rough_map = next((m for m in ['rough', 'roughness'] if m in tex_nodes), None)
                if rough_map:
                    links.new(tex_nodes[rough_map].outputs['Color'], principled.inputs['Roughness'])

                # Metallic
                metal_map = next((m for m in ['metal', 'metalness', 'metallic'] if m in tex_nodes), None)
                if metal_map:
                    links.new(tex_nodes[metal_map].outputs['Color'], principled.inputs['Metallic'])

                # Normal (GL preferred)
                normal_map_node = None
                normal_tex = next((m for m in ['nor_gl', 'nor_dx', 'nor', 'normal'] if m in tex_nodes), None)
                if normal_tex:
                    normal_map_node = nodes.new(type='ShaderNodeNormalMap')
                    normal_map_node.location = (principled.location.x - 250, principled.location.y - 200)
                    # If nor_dx, might need to invert green channel? For simplicity, assume GL or generic.
                    # normal_map_node.uv_map = "UVMap" # Or relevant UV map name
                    links.new(tex_nodes[normal_tex].outputs['Color'], normal_map_node.inputs['Color'])
                    links.new(normal_map_node.outputs['Normal'], principled.inputs['Normal'])

                # Displacement / Height
                disp_map = next((m for m in ['disp', 'displacement', 'height'] if m in tex_nodes), None)
                if disp_map:
                    disp_node = nodes.new(type='ShaderNodeDisplacement')
                    disp_node.location = (principled.location.x + 250, principled.location.y - 400)
                    disp_node.inputs['Scale'].default_value = 0.1  # Default scale
                    links.new(tex_nodes[disp_map].outputs['Color'], disp_node.inputs['Height'])
                    links.new(disp_node.outputs['Displacement'], output.inputs['Displacement'])

                # AO (Ambient Occlusion) - Mix with Base Color if available
                ao_map = next((m for m in ['ao', 'ambient_occlusion'] if m in tex_nodes), None)
                if ao_map and base_color_map:
                    mix_node = nodes.new(type='ShaderNodeMixRGB')  # Or Mix Color in newer Blender
                    mix_node.location = (principled.location.x - 250, principled.location.y + 200)
                    mix_node.blend_type = 'MULTIPLY'
                    mix_node.inputs['Fac'].default_value = 1.0

                    # Re-route base color through mix node
                    base_color_link = next(
                        (l for l in tex_nodes[base_color_map].outputs['Color'].links if l.to_socket == principled.inputs['Base Color']), None)
                    if base_color_link:
                        links.remove(base_color_link)

                    links.new(tex_nodes[base_color_map].outputs['Color'], mix_node.inputs[1])  # Color1
                    links.new(tex_nodes[ao_map].outputs['Color'], mix_node.inputs[2])     # Color2 (AO)
                    links.new(mix_node.outputs['Color'], principled.inputs['Base Color'])

                # TODO: Handle ARM maps (AO, Roughness, Metallic packed) if needed by separating RGB.

                log(f"Material '{mat.name}' created successfully for texture '{asset_id}'", "INFO")
                return json.dumps({
                    "success": True,
                    "message": f"Texture set '{asset_id}' downloaded and material '{mat.name}' created.",
                    "material_name": mat.name,
                    "downloaded_maps": list(downloaded_maps.keys()),
                    "resolution": resolution,
                    "format": file_format
                })

            except Exception as e:
                return _report_error(f"Failed to create material for texture '{asset_id}'", e)

        elif asset_type == "models":
            # Prefer glTF, then blend, then others
            if not file_format:
                file_format = "gltf"
            log(f"Processing Model: format={file_format}, resolution={resolution}", "DEBUG")

            # Check availability (API structure might vary for models)
            if file_format not in files_data or \
               resolution not in files_data[file_format] or \
               file_format not in files_data[file_format][resolution]:  # Nested format key?
                return _report_error(
                    f"Requested resolution '{resolution}' or format '{file_format}' not available for model '{asset_id}'")

            model_section = files_data[file_format][resolution][file_format]
            if "url" not in model_section:
                return _report_error(
                    f"Could not find download URL for model '{asset_id}' ({resolution}, {file_format})")

            file_url = model_section["url"]
            main_file_name = file_url.split("/")[-1]
            if not main_file_name:
                main_file_name = f"{asset_id}_{resolution}.{file_format}"  # Fallback name

            temp_dir = tempfile.mkdtemp(prefix=f"polyhaven_{asset_id}_")
            main_file_path = os.path.join(temp_dir, main_file_name)
            log(f"Created temp dir for model: {temp_dir}", "DEBUG")

            try:
                # Download main file
                response = requests.get(file_url, stream=True, timeout=300)  # Longer timeout for models
                response.raise_for_status()
                with open(main_file_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                log(f"Model main file downloaded: {main_file_path}", "DEBUG")

                # Download included files (textures, etc.) if specified
                if "include" in model_section and isinstance(model_section["include"], dict):
                    log(f"Downloading {len(model_section['include'])} included files...", "DEBUG")
                    for include_path, include_info in model_section["include"].items():
                        if isinstance(include_info, dict) and "url" in include_info:
                            include_url = include_info["url"]
                            # Create necessary subdirectories within temp_dir
                            full_include_path = os.path.join(temp_dir, include_path)
                            os.makedirs(os.path.dirname(full_include_path), exist_ok=True)
                            try:
                                inc_response = requests.get(include_url, stream=True, timeout=120)
                                inc_response.raise_for_status()
                                with open(full_include_path, 'wb') as f_inc:
                                    for chunk in inc_response.iter_content(chunk_size=8192):
                                        f_inc.write(chunk)
                                log(f"Downloaded included file: {include_path}", "DEBUG")
                            except requests.exceptions.RequestException as e_inc:
                                log(f"Failed to download included file '{include_path}': {e_inc}", "WARNING")
                        else:
                            log(f"Skipping include item with invalid structure: {include_path}", "WARNING")

                # Import into Blender
                log(f"Importing model '{main_file_path}' using format '{file_format}'...", "INFO")
                imported_object_names = []
                objects_before = set(bpy.data.objects)

                if file_format == "gltf" or file_format == "glb":
                    # Default import settings might be fine
                    bpy.ops.import_scene.gltf(filepath=main_file_path)
                elif file_format == "fbx":
                    bpy.ops.import_scene.fbx(filepath=main_file_path)
                elif file_format == "obj":
                    bpy.ops.import_scene.obj(filepath=main_file_path)
                elif file_format == "blend":
                    # Append objects from the .blend file
                    with bpy.data.libraries.load(main_file_path, link=False) as (data_from, data_to):
                        data_to.objects = [name for name in data_from.objects]  # Get list of object names
                    # Link appended objects to current scene's collection
                    active_collection = bpy.context.view_layer.active_layer_collection.collection
                    for obj in data_to.objects:
                        if obj:
                            active_collection.objects.link(obj)
                else:
                    return _report_error(f"Unsupported model import format: {file_format}")

                objects_after = set(bpy.data.objects)
                new_objects = objects_after - objects_before
                imported_object_names = [obj.name for obj in new_objects]

                log(f"Model '{asset_id}' imported. New objects: {imported_object_names}", "INFO")
                return json.dumps({
                    "success": True,
                    "message": f"Model '{asset_id}' ({resolution}, {file_format}) imported successfully.",
                    "imported_object_names": imported_object_names
                })

            except Exception as e:
                return _report_error(f"Failed to download or import model '{asset_id}'", e)

        else:
            return _report_error(f"Unsupported asset type: {asset_type}. Must be 'hdris', 'textures', or 'models'.")

    except requests.exceptions.RequestException as e:
        return _report_error(f"API request failed for asset '{asset_id}'", e)
    except Exception as e:
        return _report_error(f"Unexpected error processing asset '{asset_id}'", e)

    finally:
        # Cleanup temporary files and directories
        log("Cleaning up temporary files...", "DEBUG")
        for tmp_file in temp_files:
            try:
                if os.path.exists(tmp_file):
                    os.unlink(tmp_file)
            except Exception as e_clean:
                log(f"Warning: Failed to delete temp file {tmp_file}: {e_clean}", "WARNING")
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                log(f"Removed temp directory: {temp_dir}", "DEBUG")
            except Exception as e_clean:
                log(f"Warning: Failed to remove temp directory {temp_dir}: {e_clean}", "WARNING")


@tool("set_polyhaven_texture", args_schema=SetPolyHavenTextureArgs)
def set_polyhaven_texture(object_name: str, texture_id: str) -> str:
    """
    Applies a previously created Poly Haven texture material to a specified object in the scene.
    This tool assumes the material (e.g., 'wood_planks_1k_mat') was already created by
    a prior call to 'download_polyhaven_asset' for the corresponding texture_id.

    Args:
        object_name: The name of the target Blender object.
        texture_id: The Poly Haven asset ID used when creating the material (e.g., 'wood_planks').
                    The tool will look for a material like '{texture_id}_<res>_mat'.

    Returns:
        A JSON string indicating success or failure.
    """
    log(f"Executing set_polyhaven_texture: object='{object_name}', texture_id='{texture_id}'", "INFO")

    # Find the target object
    obj = bpy.data.objects.get(object_name)
    if not obj:
        return _report_error(f"Object '{object_name}' not found in the scene.")

    # Check if object can have materials
    if not obj.material_slots:
        # Add a material slot if none exist
        obj.data.materials.append(None)
        log(f"Added initial material slot to object '{object_name}'", "DEBUG")
        if not obj.material_slots:  # Check again after adding
            return _report_error(f"Object '{object_name}' of type '{obj.type}' cannot accept materials.")

    # Find the material created by download_polyhaven_asset
    # We need to guess the resolution used, try common ones or find based on texture_id pattern
    possible_resolutions = ["1k", "2k", "4k", "8k", "512"]  # Common resolutions
    found_material = None
    potential_mat_name = ""
    for res in possible_resolutions:
        potential_mat_name = f"{texture_id}_{res}_mat"
        mat = bpy.data.materials.get(potential_mat_name)
        if mat:
            found_material = mat
            log(f"Found matching material: '{found_material.name}'", "DEBUG")
            break

    if not found_material:
        # Fallback: search for any material starting with the texture_id and ending with _mat
        for mat in bpy.data.materials:
            if mat.name.startswith(texture_id) and mat.name.endswith("_mat"):
                found_material = mat
                log(f"Found fallback material: '{found_material.name}'", "DEBUG")
                break

    if not found_material:
        return _report_error(
            f"Material for texture '{texture_id}' not found. Ensure 'download_polyhaven_asset' was successfully run first for this texture.")

    # Apply the material to the object's first slot
    try:
        log(f"Applying material '{found_material.name}' to object '{object_name}'", "INFO")
        # Assign to the first slot (index 0)
        if obj.material_slots:
            obj.material_slots[0].material = found_material
        else:
            # This case should be handled above, but as a safeguard:
            obj.data.materials.append(found_material)

        # Ensure UV map exists if object is a mesh (material needs UVs)
        if obj.type == 'MESH' and obj.data:
            if not obj.data.uv_layers:
                log(f"Object '{object_name}' has no UV map. Creating default 'UVMap'.", "WARNING")
                uv_layer = obj.data.uv_layers.new(name="UVMap")
                obj.data.uv_layers.active = uv_layer
                # Note: This creates an empty UV map. Smart UV project might be needed.
                # The LLM might need to call execute_blender_code for unwrapping if needed.
            else:
                # Ensure one is active
                if not obj.data.uv_layers.active:
                    obj.data.uv_layers.active = obj.data.uv_layers[0]
                log(f"Object '{object_name}' using active UV map: '{obj.data.uv_layers.active.name}'", "DEBUG")

        bpy.context.view_layer.update()  # Force update

        return json.dumps({
            "success": True,
            "message": f"Applied material '{found_material.name}' to object '{object_name}'."
        })
    except Exception as e:
        return _report_error(f"Failed to apply material '{found_material.name}' to '{object_name}'", e)
