#!/usr/bin/env python
import argparse
import glob
import re
import sys
import traceback
from typing import Final, Any

from bokeh.io import output_file
from bokeh.layouts import gridplot
from bokeh.layouts import column as bokeh_column
from bokeh.models import ColumnDataSource, TapTool, OpenURL, BasicTicker, PrintfTickFormatter, LinearColorMapper, \
    ColorBar, Div
from bokeh.plotting import figure, show
from bokeh.transform import linear_cmap

from janelia_emrp.zcorr.plot_cross_correlation import build_neuroglancer_tap_url
from janelia_emrp.zcorr.plot_util import get_stack_metadata, load_json_file_data


# .../z/%s/box/-12825,-7107,26455,14179,0.22/render-parameters
LAYER_URL_PATTERN: Final = re.compile(r".*/box/([^,]+),([^,]+),([^,]+),([^,]+),([^,]+)/render-parameters")

# reversed colormap from https://docs.bokeh.org/en/2.3.3/docs/user_guide/categorical.html?highlight=heatmap
COLORS: Final = ["#550b1d", "#933b41", "#cc7878", "#ddb7b1", "#dfccce", "#e2e2e2", "#c9d9d3", "#a5bab7", "#75968f"]


def build_poor_regional_correlations_for_z(owner: str,
                                           project: str,
                                           stack: str,
                                           res_x: int,
                                           res_y: int,
                                           res_z: int,
                                           layer_result: dict[str, Any]):
    p_z = int(layer_result["pZ"])
    q_z = int(layer_result["qZ"])
    layer_correlation = layer_result["layerCorrelation"]
    regional_correlation = layer_result["regionalCorrelation"]
    row_count = len(regional_correlation)
    column_count = len(regional_correlation[0])
    layer_url_pattern_string = layer_result["layerUrlPattern"]

    match = LAYER_URL_PATTERN.match(layer_url_pattern_string)
    if match is None:
        raise ValueError(f"failed to parse layer_url_pattern_string: {layer_url_pattern_string}")

    layer_x = int(match.group(1))
    layer_y = int(match.group(2))
    layer_width = int(match.group(3))
    layer_height = int(match.group(4))

    plot_size = 500
    if layer_width > layer_height:
        plot_width = plot_size
        plot_height = int(plot_size * layer_height / layer_width)
    else:
        plot_height = plot_size
        plot_width = int(plot_size * layer_width / layer_height)

    region_width = int(layer_width / column_count)
    region_height = int(layer_height / row_count)

    region_center_x = []
    region_center_y = []
    cc_with_next = []
    min_cc = 1.0
    max_cc = 0.0
    for row in range(0, row_count):
        y = layer_y + (row * region_height)
        y_center = y + int(region_height / 2)
        for column in range(0, column_count):
            x = layer_x + (column * region_width)
            x_center = x + int(region_width / 2)
            region_center_x.append(x_center)
            region_center_y.append(y_center)
            cc = regional_correlation[row][column]
            cc_with_next.append(cc)
            min_cc = min(min_cc, cc)
            max_cc = max(max_cc, cc)

    min_cc = min_cc - 0.005
    max_cc = max_cc + 0.005

    tap_help = "to view in Neuroglancer"
    x_y_z_position = f"@x,@y,{p_z}"
    tap_url = build_neuroglancer_tap_url(owner=owner,
                                         project=project,
                                         stack=stack,
                                         res_x=res_x,
                                         res_y=res_y,
                                         res_z=res_z,
                                         cross_section_scale=4,
                                         x_y_z_position=x_y_z_position)

    tooltips = [("region center", "@x, @y"),
                ("correlation with next", "@cc"),
                (tap_help, "click")]

    p = figure(title=f"z {p_z} to {q_z}, layer correlation is {layer_correlation:4.2f}",
               x_axis_location="above",
               x_axis_label='X', y_axis_label='Y',
               x_range=[layer_x, layer_x + layer_width],
               y_range=[layer_y + layer_height, layer_y],
               tooltips=tooltips, tools='tap,save,reset',
               plot_width=plot_width, plot_height=plot_height)
    p.title.align = 'center'

    data_source = ColumnDataSource(data=dict(x=region_center_x, y=region_center_y, cc=cc_with_next))

    rect = p.rect(x="x", y="y", width=region_width, height=region_height, source=data_source,
                  fill_color=linear_cmap("cc", COLORS, low=min_cc, high=max_cc),
                  line_color="black")
    rect.nonselection_glyph = None  # disable block suppression when region is clicked/selected

    mapper = LinearColorMapper(palette=COLORS, low=min_cc, high=max_cc)

    color_bar = ColorBar(color_mapper=mapper,
                         ticker=BasicTicker(desired_num_ticks=len(COLORS)),
                         formatter=PrintfTickFormatter(format="%4.2f"))

    p.add_layout(color_bar, 'right')

    tap_tool = p.select(type=TapTool)
    tap_tool.callback = OpenURL(url=tap_url)

    return p


def plot_poor_regional_correlations(title, run_path, owner, project, stack,
                                    output_file_path=None):
    stack_metadata = get_stack_metadata(owner, project, stack)
    stack_version = stack_metadata["currentVersion"]
    res_x = stack_version["stackResolutionX"]
    res_y = stack_version["stackResolutionY"]
    res_z = stack_version["stackResolutionZ"]

    # [
    #   { "pZ" : 7015.0,
    #     "qZ" : 7016.0,
    #     "layerUrlPattern" : "http://.../z/%s/box/-12825,-7107,26455,14179,0.22/render-parameters",
    #     "regionalCorrelation" : : [][] },
    #   { ... },
    # ]
    poor_data_file_name = "poor_cc_regional_data.json"
    cc_regional_result_list = []
    glob_pathname = f"{run_path}/**/{poor_data_file_name}*"
    for cc_data_path in sorted(glob.glob(glob_pathname, recursive=True)):
        cc_regional_result_list.extend(load_json_file_data(cc_data_path))

    if len(cc_regional_result_list) == 0:
        raise ValueError(f"{run_path} does not contain any {poor_data_file_name} files")

    layer_plots = []
    for layer_result in cc_regional_result_list:
        layer_plots.append(build_poor_regional_correlations_for_z(owner=owner,
                                                                  project=project,
                                                                  stack=stack,
                                                                  res_x=res_x,
                                                                  res_y=res_y,
                                                                  res_z=res_z,
                                                                  layer_result=layer_result))

    grid_title = Div(text=f"<h3>Regional maps for poorly correlated layers in<br/>{title}</h3>")
    grid = gridplot(layer_plots, ncols=1)

    if output_file_path:
        output_file(output_file_path)
        print(f'writing plot to {output_file_path}')

    show(bokeh_column(grid_title, grid))


# noinspection HttpUrlsUsage
def plot_run(base_dir, owner, project, stack, run):
    owner_run_sub_path = f'{owner}/{project}/{stack}/{run}'
    run_path = f'{base_dir}/{owner_run_sub_path}'
    plot_html_name = 'poor_cc_regional_data.html'
    output_file_path = f'{run_path}/{plot_html_name}'
    plot_url = f'http://renderer-data4.int.janelia.org:8080/z_corr_plots/{owner_run_sub_path}/{plot_html_name}'

    plot_poor_regional_correlations(title=f'{owner} : {project} : {stack}',
                                    run_path=run_path,
                                    owner=owner, project=project, stack=stack,
                                    output_file_path=output_file_path)
    print(f'view plot at {plot_url}')


def main(arg_list):
    parser = argparse.ArgumentParser(description="Build plot of regional cross correlation values.")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--stack", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--base_dir", default="/nrs/cellmap/render/z_corr")

    args = parser.parse_args(arg_list)

    plot_run(base_dir=args.base_dir,
             owner=args.owner,
             project=args.project,
             stack=args.stack,
             run=args.run)


if __name__ == '__main__':
    # NOTE: to fix module not found errors, export PYTHONPATH="/.../EM_recon_pipeline/src/python"

    # noinspection PyBroadException
    try:
        main(sys.argv[1:])
        # main([
        #     "--owner", "cellmap",
        #     "--project", "jrc_mus_cerebellum_1",
        #     "--stack", "v1_acquire_trimmed_align",
        #     "--run", "testPoorCorrelationC",
        #     "--base_dir", "/Users/trautmane/Desktop/zcorr",
        # ])
    except Exception as e:
        # ensure exit code is a non-zero value when Exception occurs
        traceback.print_exc()
        sys.exit(1)
