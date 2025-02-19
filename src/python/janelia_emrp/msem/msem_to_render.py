import argparse
import logging
import sys
import time
import traceback
from pathlib import Path
from typing import List, Any, Optional
from itertools import product
import numpy as np

import renderapi
import xarray
from renderapi import Render
from renderapi.errors import RenderError

from janelia_emrp.fibsem.render_api import RenderApi
from janelia_emrp.fibsem.volume_transfer_info import params_to_render_connect
from janelia_emrp.msem.field_of_view_layout import FieldOfViewLayout, build_mfov_column_group, \
    NINETY_ONE_SFOV_ADJACENT_MFOV_DELTA_Y, NINETY_ONE_SFOV_NAME_TO_ROW_COL
from janelia_emrp.msem.ingestion_ibeammsem.assembly import (
    get_xys_sfov_and_paths, get_max_scans, get_SFOV_width, get_SFOV_height, get_effective_scans
)
from janelia_emrp.msem.ingestion_ibeammsem.path import get_slab_path
from janelia_emrp.msem.ingestion_ibeammsem.constant import N_BEAMS
from janelia_emrp.msem.scan_fit_parameters import ScanFitParameters, WAFER_60_61_SCAN_FIT_PARAMETERS
from janelia_emrp.msem.slab_info import load_slab_info, ContiguousOrderedSlabGroup
from janelia_emrp.root_logger import init_logger

program_name = "msem_to_render.py"

logger = logging.getLogger(__name__)


def build_tile_spec(image_path: Path,
                    stage_x: int,
                    stage_y: int,
                    stage_z: int,
                    tile_id: str,
                    tile_width: int,
                    tile_height: int,
                    layout: FieldOfViewLayout,
                    mfov_id: int,
                    sfov_index_name: str,
                    min_x: int,
                    min_y: int,
                    scan_fit_parameters: ScanFitParameters,
                    margin: int) -> dict[str, Any]:

    section_id = f'{stage_z}.0'
    image_row, image_col = layout.row_and_col(mfov_id, sfov_index_name)

    mipmap_level_zero = {"imageUrl": f'file:{image_path}'}

    transform_data_string = f'1 0 0 1 {stage_x - min_x + margin} {stage_y - min_y + margin}'

    tile_spec = {
        "tileId": tile_id, "z": stage_z,
        "layout": {
            "sectionId": section_id,
            "imageRow": image_row, "imageCol": image_col,
            "stageX": stage_x, "stageY": stage_y
        },
        "width": tile_width, "height": tile_height, "minIntensity": 0, "maxIntensity": 255,
        "mipmapLevels": {
            "0": mipmap_level_zero
        },
        "transforms": {
            "type": "list",
            "specList": [
                scan_fit_parameters.to_transform_spec(),
                {"className": "mpicbg.trakem2.transform.AffineModel2D", "dataString": transform_data_string}
            ]
        }
    }

    return tile_spec


def build_tile_specs_for_slab_scan(slab_scan_path: Path,
                                   scan: int,
                                   slab: int,
                                   mfovs: list[int],
                                   sfov_path_list: list[Path],
                                   sfov_xy_list: list[tuple[int, int]],
                                   stage_z: int,
                                   layout: FieldOfViewLayout,
                                   wafer_id: str,
                                   tile_width: int,
                                   tile_height: int) -> list[dict[str, Any]]:
    """
    Beware of the indexing mismatch between the ingestion code and the actual file images.
    See comment in path.get_sfov_path.
    SFOV IDs are 0-indexed everywhere in the ingestion code.
    The only place where there is a 1-indexed numbering
        is the name of the SFOVs generated by the microscope.
        This numbering from the microscope cannot be changed
            and must remain 1-indexed.
    """

    scan_fit_parameters = WAFER_60_61_SCAN_FIT_PARAMETERS  # load_scan_fit_parameters(slab_scan_path)

    min_x, min_y = np.array(sfov_xy_list).min(axis=0)
    
    fixed_tilespec_params = dict(
        stage_z=stage_z,
        tile_width=tile_width,
        tile_height=tile_height,
        layout=layout,
        min_x=min_x,
        min_y=min_y,
        scan_fit_parameters=scan_fit_parameters,
        margin=400,
    )

    tile_specs = [
        build_tile_spec(image_path=image_path,
                        stage_x=stage_x,
                        stage_y=stage_y,
                        tile_id=create_tile_id(wafer_id, slab, scan, mfov, sfov),
                        mfov_id=mfov,
                        sfov_index_name=f"{(sfov+1):03}",
                        **fixed_tilespec_params,
        )
        for (mfov, sfov), image_path, (stage_x, stage_y) in zip(
            product(mfovs, range(N_BEAMS)), sfov_path_list, sfov_xy_list
        )
    ]

    logger.info(f'build_tile_specs_for_slab_scan: loaded {len(tile_specs)} tile specs from {slab_scan_path}')

    return tile_specs

# For each multi-SEM MFOV, SFOV numbers start at 1 in the center and spiral counter-clockwise out to 91.
# This list supports mapping an SFOV index to its render order
# with the assumption that rendering should occur top-to-bottom, left-to-right within each MFOV.
#
# This list was copied from
#   https://github.com/saalfeldlab/render/blob/newsolver/render-ws-java-client/src/main/java/org/janelia/render/client/TileReorderingClient.java#L155-L165
RENDER_SFOV_ORDER = [
    46, 47, 36, 35, 45, 56, 57, 48, 37, 27,  #  s1 to s10
    26, 25, 34, 44, 55, 65, 66, 67, 58, 49,  # s11 to s20
    38, 28, 19, 18, 17, 16, 24, 33, 43, 54,  # s21 to s30
    64, 73, 74, 75, 76, 68, 59, 50, 39, 29,  # s31 to s40
    20, 12, 11, 10,  9,  8, 15, 23, 32, 42,  # s41 to s50
    53, 63, 72, 80, 81, 82, 83, 84, 77, 69,  # s51 to s60
    60, 51, 40, 30, 21, 13,  6,  5,  4,  3,  # s61 to s70
     2,  1,  7, 14, 22, 31, 41, 52, 62, 71,  # s71 to s80
    79, 86, 87, 88, 89, 90, 91, 85, 78, 70,  # s81 to s90
    61                                       # s91
]

def create_tile_id(wafer_id: str,
                   slab: int,
                   scan: int,
                   mfov: int,
                   sfov: int)->str:
    """Creates tile ID with a 1-based SFOV number component.
    
    E.g.: w060_magc0002_scan001_m0003_s04
    """

    # scope SFOV numbering (and image paths) are 1-indexed, using that number in the tile ID for consistency
    scope_sfov_number = sfov + 1

    return "_".join(
        (
            f"w{wafer_id}",
            f"magc{slab:04}",
            f"scan{scan:03}",
            f"m{mfov:04}",
            f"r{RENDER_SFOV_ORDER[sfov]:02}",
            f"s{scope_sfov_number:02}",
        )
    )

def get_stack_metadata_or_none(render: Render,
                               stack_name: str) -> Optional[dict[str, Any]]:
    stack_metadata = None
    try:
        stack_metadata = renderapi.stack.get_stack_metadata(render=render, stack=stack_name)
    except RenderError:
        print(f"failed to retrieve metadata for stack {stack_name}")
    return stack_metadata


def import_slab_stacks_for_wafer(render_ws_host: str,
                                 render_owner: str,
                                 wafer_xlog_path: Path,
                                 import_magc_slab_list: list[int],
                                 include_scan_list: list[int],
                                 exclude_scan_list: list[int],
                                 wafer_id: str,
                                 number_of_slabs_per_render_project: int):

    func_name = "import_slab_stacks_for_wafer"

    logger.info(f"{func_name}: opening {wafer_xlog_path}")

    if wafer_xlog_path.exists():
        xlog = xarray.open_zarr(wafer_xlog_path)
    else:
        raise RuntimeError(f"cannot find wafer xlog: {wafer_xlog_path}")

    logger.info(f"{func_name}: loading slab info, {wafer_id=}, number_of_slabs_per_group={number_of_slabs_per_render_project}")
    
    n_scans_max = get_max_scans(xlog=xlog)
    logger.info(f"the maximum number of scans is {n_scans_max}")

    slab_group_list = load_slab_info(xlog=xlog,
                                     wafer_short_prefix=f"w{wafer_id}_",
                                     number_of_slabs_per_group=number_of_slabs_per_render_project)

    logger.info(f"{func_name}: loaded {len(slab_group_list)} slab groups")
    
    tile_width = get_SFOV_width(xlog)
    tile_height = get_SFOV_height(xlog)

    if len(import_magc_slab_list) > 0:
        logger.info(f"{func_name}: looking for magc slabs {import_magc_slab_list}")

        filtered_slab_group_list: list[ContiguousOrderedSlabGroup] = []
        for slab_group in slab_group_list:
            filtered_slab_group = ContiguousOrderedSlabGroup(ordered_slabs=[])
            for slab_info in slab_group.ordered_slabs:
                if slab_info.magc_id in import_magc_slab_list:
                    filtered_slab_group.ordered_slabs.append(slab_info)
            if len(filtered_slab_group.ordered_slabs) > 0:
                filtered_slab_group_list.append(filtered_slab_group)

        if len(filtered_slab_group_list) > 0:
            slab_group_list = filtered_slab_group_list
            logger.info(f"{func_name}: filtered down to {len(slab_group_list)} slab groups")
        else:
            raise RuntimeError(f"no slabs found with magc ids {import_magc_slab_list}")

    for slab_group in slab_group_list:
        project_name = slab_group.to_render_project_name(number_of_slabs_per_render_project)

        render_connect_params = {
            "host": render_ws_host,
            "port": 8080,
            "owner": render_owner,
            "project": project_name,
            "web_only": True,
            "validate_client": False,
            "client_scripts": "/groups/hess/hesslab/render/client_scripts",
            "memGB": "1G"
        }

        render = renderapi.connect(**render_connect_params)

        render_api = RenderApi(render_owner=render_connect_params["owner"],
                               render_project=render_connect_params["project"],
                               render_connect=params_to_render_connect(render_connect_params))

        for slab_info in slab_group.ordered_slabs:
            stack = slab_info.stack_name
            stack_is_in_loading_state = False
            z = 1
                
            logger.info(f'{func_name}: building layout for stack {stack}')

            mfov_position_list = slab_info.build_mfov_position_list(xlog=xlog)
            mfov_column_group = build_mfov_column_group(mfov_position_list,
                                                        NINETY_ONE_SFOV_ADJACENT_MFOV_DELTA_Y)
            stack_layout = FieldOfViewLayout(mfov_column_group, NINETY_ONE_SFOV_NAME_TO_ROW_COL)

            effective_scans: set[int] = set(get_effective_scans(xlog=xlog, slab=slab_info.magc_id))

            for scan in set(include_scan_list) - effective_scans:
                logger.warning(f'{func_name}: scan {scan} not found for stack {stack}')

            scans: list[int] = sorted(
                ((set(include_scan_list) or effective_scans) & effective_scans)
                - set(exclude_scan_list)
            )
            if not scans:
                logger.warning(f'{func_name}: found no scans to import for stack {stack}')

            logger.info(f'{func_name}: found {len(scans)} scans to import for stack {stack}')

            for scan in scans:
                slab_scan_sfov_path_list: list[Path] = []
                slab_scan_sfov_xy_list: list[tuple[int, int]] = []
                # change //nearline-msem.int.janelia.org/hess/ibeammsem/system_02/wafers/wafer_60/acquisition/scans/scan_004/slabs/slab_0399
                # to     /nearline/hess/ibeammsem/system_02/wafers/wafer_60/acquisition/scans/scan_004/slabs/slab_0399
                slab_scan_path = Path(
                    str(get_slab_path(xlog=xlog, scan=scan,slab=slab_info.magc_id))
                    .replace("//nearline-msem.int.janelia.org", "/nearline")
                )
                for mfov in slab_info.mfovs:
                    mfov_path_list, mfov_xys = get_xys_sfov_and_paths(xlog=xlog,
                                                                      scan=scan,
                                                                      slab=slab_info.magc_id,
                                                                      mfov=mfov,
                                                                      slab_path=slab_scan_path)
                    slab_scan_sfov_path_list.extend(mfov_path_list)
                    slab_scan_sfov_xy_list.extend(mfov_xys)

                logger.info(f"{func_name}: loaded {len(slab_scan_sfov_path_list)} paths and xys for "
                            f"{stack} scan {scan}, mfovs {slab_info.first_mfov} to {slab_info.last_mfov}, "
                            f"first path is {slab_scan_sfov_path_list[0]}, first xy is {slab_scan_sfov_xy_list[0]}")

                first_sfov_path = slab_scan_sfov_path_list[0]
                if not first_sfov_path.exists():
                    logger.warning(f"{func_name}: skipping import of scan {scan} because {first_sfov_path} is missing")
                    continue

                # for wafers 60 and 61, we decided to hardcode the parameters in scan_fit_parameters.py
                # WAFER_60_61_SCAN_FIT_PARAMETERS rather than reading them in for each scan

                tile_specs = build_tile_specs_for_slab_scan(slab_scan_path=slab_scan_path,
                                                            scan=scan,
                                                            slab=slab_info.magc_id,
                                                            mfovs=slab_info.mfovs,
                                                            sfov_path_list=slab_scan_sfov_path_list,
                                                            sfov_xy_list=slab_scan_sfov_xy_list,
                                                            stage_z=z,
                                                            layout=stack_layout,
                                                            wafer_id=wafer_id,
                                                            tile_width=tile_width,
                                                            tile_height=tile_height)

                if len(tile_specs) > 0:

                    if not stack_is_in_loading_state:
                        # TODO: parse resolution from wafer xlog
                        ensure_stack_is_in_loading_state(render=render,
                                                         stack=stack,
                                                         resolution_x=8.0,
                                                         resolution_y=8.0,
                                                         resolution_z=8.0)
                        stack_is_in_loading_state = True

                    tile_id_range = f'{tile_specs[0]["tileId"]} to {tile_specs[-1]["tileId"]}'
                    logger.info(f"{func_name}: saving tiles {tile_id_range} in stack {stack}")
                    render_api.save_tile_specs(stack=stack,
                                               tile_specs=tile_specs,
                                               derive_data=True)
                    z += 1
                else:
                    logger.debug(f'{func_name}: no tile specs in {slab_scan_path.name} for stack {stack}')

            if stack_is_in_loading_state:
                renderapi.stack.set_stack_state(stack, 'COMPLETE', render=render)


def ensure_stack_is_in_loading_state(render: Render,
                                     stack: str,
                                     resolution_x: float,
                                     resolution_y: float,
                                     resolution_z: float) -> None:

    stack_metadata = get_stack_metadata_or_none(render=render, stack_name=stack)
    if stack_metadata is None:
        # TODO: remove render-python hack
        # explicitly set createTimestamp until render-python bug is fixed
        # see https://github.com/AllenInstitute/render-python/pull/158
        create_timestamp = time.strftime('%Y-%m-%dT%H:%M:%S.00Z')
        renderapi.stack.create_stack(stack,
                                     render=render,
                                     createTimestamp=create_timestamp,
                                     stackResolutionX=resolution_x,
                                     stackResolutionY=resolution_y,
                                     stackResolutionZ=resolution_z)
    else:
        renderapi.stack.set_stack_state(stack, 'LOADING', render=render)


def main(arg_list: List[str]):
    parser = argparse.ArgumentParser(
        description="Parse wafer metadata and convert to tile specs that can be saved to render."
    )
    parser.add_argument(
        "--render_host",
        help="Render web services host (e.g. em-services-1.int.janelia.org)",
        required=True,
    )
    parser.add_argument(
        "--render_owner",
        help="Owner for all created render stacks",
        required=True,
    )
    parser.add_argument(
        "--path_xlog",
        help="Path of the wafer xarray (e.g. /groups/hess/hesslab/ibeammsem/system_02/wafers/wafer_60/xlog/xlog_wafer_60.zarr)",
        required=True,
    )
    parser.add_argument(
        "--import_magc_slab",
        help="If specified, only import tile specs for slabs with these magc ids (e.g. 399)",
        type=int,
        nargs='+',
        default=[]
    )
    parser.add_argument(
        "--include_scan",
        help="Only include these scans from the render stacks (e.g. 5 6 for testing).  When specified, exclude_scan is ignored.",
        type=int,
        nargs='+',
        default=[]
    )
    # NOTE: to exclude entire slabs, we decided to simply delete the stack after import
    parser.add_argument(
        "--exclude_scan",
        help="Exclude these scans from the render stacks (e.g. 0 1 2 3 7 18)",
        type=int,
        nargs='+',
        default=[]
    )
    parser.add_argument(
        "--wafer_id",
        help="Wafer ID, e.g. '60' or 'B13', so that 'w60_' or 'wB13_' gets prepended to all project and stack names",
        type=str,
        required=True
    )
    parser.add_argument(
        "--number_of_slabs_per_render_project",
        help="Number of slabs to group together into one render project",
        type=int,
        default=10
    )
    args = parser.parse_args(args=arg_list)

    import_slab_stacks_for_wafer(render_ws_host=args.render_host,
                                 render_owner=args.render_owner,
                                 wafer_xlog_path=Path(args.path_xlog),
                                 import_magc_slab_list=args.import_magc_slab,
                                 include_scan_list=args.include_scan,
                                 exclude_scan_list=args.exclude_scan,
                                 wafer_id=args.wafer_id,
                                 number_of_slabs_per_render_project=args.number_of_slabs_per_render_project)


if __name__ == '__main__':
    # NOTE: to fix module not found errors, export PYTHONPATH="/.../EM_recon_pipeline/src/python"

    # setup logger since this module is the main program (and set render python logging level to DEBUG)
    init_logger(__file__)
    logging.getLogger("renderapi").setLevel(logging.DEBUG)

    # to see more log data, set root level to debug
    # logging.getLogger().setLevel(logging.DEBUG)

    # noinspection PyBroadException
    try:
        main(sys.argv[1:])
        # main([
        #     "--render_host", "10.40.3.113",
        #     "--render_owner", "trautmane",
        #     "--wafer_id", "60",
        #     "--path_xlog", "/groups/hess/hesslab/ibeammsem/system_02/wafers/wafer_60/xlog/xlog_wafer_60.zarr",
        #     "--import_magc_slab",
        #     "399", # s296
        #     "174", # s297
        #
        #     # "--include_scan", "6",
        #     "--exclude_scan", "0", "1", "2", "3", "7", "18"
        # ])
    except Exception as e:
        # ensure exit code is a non-zero value when Exception occurs
        traceback.print_exc()
        sys.exit(1)
