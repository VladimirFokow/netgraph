#!/usr/bin/env python
# -*- coding: utf-8 -*-

import itertools
import warnings
import numpy as np
import matplotlib
import matplotlib.pyplot as plt

from uuid import uuid4
from ._utils import (
    _get_unique_nodes,
    _bspline,
    _get_angle,
    _get_interior_angle_between,
    _get_signed_angle_between,
    _get_n_points_on_a_circle,
    _get_orthogonal_unit_vector,
    _get_point_along_spline,
    _get_tangent_at_point,
    _get_text_object_dimensions,
    _make_pretty,
    _edge_list_to_adjacency_matrix,
    _flatten,
    _get_orthogonal_projection_onto_segment,
)

from ._layout import get_fruchterman_reingold_layout, _clip_to_frame, _get_temperature_decay
from ._artists import NodeArtist, EdgeArtist
from ._data_io import parse_graph, _parse_edge_list
from ._deprecated import deprecated


# for profiling with line_profiler
try:
    profile
except NameError:
    profile = lambda x: x


BASE_NODE_SIZE = 1e-2
BASE_EDGE_WIDTH = 1e-2
TOTAL_POINTS_PER_EDGE = 100


@deprecated("Use Graph.draw() or InteractiveGraph.draw() instead.")
def draw(graph, node_positions=None, node_labels=None, edge_labels=None, edge_cmap='RdGy', ax=None, **kwargs):
    """
    Convenience function that tries to do "the right thing".

    For a full list of available arguments, and
    for finer control of the individual draw elements,
    please refer to the documentation of

        draw_nodes()
        draw_edges()
        draw_node_labels()
        draw_edge_labels()

    Arguments
    ----------
    graph: various formats
        Graph object to plot. Various input formats are supported.
        In order of precedence:
            - Edge list:
                Iterable of (source, target) or (source, target, weight) tuples,
                or equivalent (m, 2) or (m, 3) ndarray.
            - Adjacency matrix:
                Full-rank (n,n) ndarray, where n corresponds to the number of nodes.
                The absence of a connection is indicated by a zero.
            - igraph.Graph object
            - networkx.Graph object

    node_positions : dict node : (float, float)
        Mapping of nodes to (x, y) positions.
        If 'graph' is an adjacency matrix, nodes must be integers in range(n).

    node_labels : dict node : str (default None)
       Mapping of nodes to node labels.
       Only nodes in the dictionary are labelled.
       If 'graph' is an adjacency matrix, nodes must be integers in range(n).

    edge_labels : dict (source, target) : str (default None)
        Mapping of edges to edge labels.
        Only edges in the dictionary are labelled.

    ax : matplotlib.axis instance or None (default None)
       Axis to plot onto; if none specified, one will be instantiated with plt.gca().

    See Also
    --------
    draw_nodes()
    draw_edges()
    draw_node_labels()
    draw_edge_labels()

    TODO: return plot elements as dictionary

    """

    # Accept a variety of formats and convert to common denominator.
    edge_list, edge_weight, is_directed = parse_graph(graph)

    if edge_weight:

        # If the graph is weighted, we want to visualise the weights using color.
        # Edge width is another popular choice when visualising weighted networks,
        # but if the variance in weights is large, this typically results in less
        # visually pleasing results.
        edge_color  = get_color(edge_weight, cmap=edge_cmap)
        kwargs.setdefault('edge_color',  edge_color)

        # Plotting darker edges over lighter edges typically results in visually
        # more pleasing results. Here we hence specify the relative order in
        # which edges are plotted according to the color of the edge.
        edge_zorder = _get_zorder(edge_color)
        kwargs.setdefault('edge_zorder', edge_zorder)

    # Plot arrows if the graph has bi-directional edges.
    if is_directed:
        kwargs.setdefault('draw_arrows', True)
    else:
        kwargs.setdefault('draw_arrows', False)

    # Initialise node positions if none are given.
    if node_positions is None:
        node_positions = get_fruchterman_reingold_layout(edge_list, **kwargs)
    else:
        if set(node_positions.keys()).issuperset(_get_unique_nodes(edge_list)):
            # All node positions are given; nothing left to do.
            pass
        else:
            # Some node positions are given; however, either
            # 1) not all positions are provided, or
            # 2) there are some unconnected nodes in the graph.
            node_positions = get_fruchterman_reingold_layout(edge_list,
                                                             node_positions = node_positions,
                                                             fixed_nodes    = node_positions.keys(),
                                                             **kwargs)

    # Create axis if none is given.
    if ax is None:
        ax = plt.gca()

    # Draw plot elements.
    draw_edges(edge_list, node_positions, ax=ax, **kwargs)
    draw_nodes(node_positions, ax=ax, **kwargs)

    # This function needs to be called before any font sizes are adjusted.
    if 'node_size' in kwargs:
        _update_view(node_positions, ax=ax, node_size=kwargs['node_size'])
    else:
        _update_view(node_positions, ax=ax)

    if node_labels is not None:
        if not 'node_label_font_size' in kwargs:
            # set font size such that even the largest label fits inside node label face artist
            font_size = _get_font_size(ax, node_labels, **kwargs) * 0.9 # conservative fudge factor
            draw_node_labels(node_labels, node_positions, node_label_font_size=font_size, ax=ax, **kwargs)
        else:
            draw_node_labels(node_labels, node_positions, ax=ax, **kwargs)

    if edge_labels is not None:
        draw_edge_labels(edge_list, edge_labels, node_positions, ax=ax, **kwargs)

    # Improve default layout of axis.
    _make_pretty(ax)
    return ax


def get_color(mydict, cmap='RdGy', vmin=None, vmax=None):
    """
    Map positive and negative floats to a diverging colormap,
    such that
        1) the midpoint of the colormap corresponds to a value of 0., and
        2) values above and below the midpoint are mapped linearly and in equal measure
           to increases in color intensity.

    Arguments:
    ----------
    mydict: dict key : float
        Mapping of graph element (node, edge) to a float.
        For example (source, target) : edge weight.

    cmap: str
        Matplotlib colormap specification.

    vmin, vmax: float
        Minimum and maximum float corresponding to the dynamic range of the colormap.

    Returns:
    --------
    newdict: dict key : (float, float, float, float)
        Mapping of graph element to RGBA tuple.

    """

    keys = mydict.keys()
    values = np.array(list(mydict.values()), dtype=np.float64)

    # apply edge_vmin, edge_vmax
    if vmin:
        values[values<vmin] = vmin

    if vmax:
        values[values>vmax] = vmax

    def abs(value):
        try:
            return np.abs(value)
        except TypeError as e: # value is probably None
            if isinstance(value, type(None)):
                return 0
            else:
                raise e

    # rescale values such that
    #  - the colormap midpoint is at zero-value, and
    #  - negative and positive values have comparable intensity values
    values /= np.nanmax([np.nanmax(np.abs(values)), abs(vmax), abs(vmin)]) # [-1, 1]
    values += 1. # [0, 2]
    values /= 2. # [0, 1]

    # convert value to color
    mapper = matplotlib.cm.ScalarMappable(cmap=cmap)
    mapper.set_clim(vmin=0., vmax=1.)
    colors = mapper.to_rgba(values)

    return {key: color for (key, color) in zip(keys, colors)}


def _get_zorder(color_dict):
    # reorder plot elements such that darker items are plotted last
    # and hence most prominent in the graph
    # TODO: assumes that background is white (or at least light); might want to reverse order for dark backgrounds
    zorder = np.argsort(np.sum(list(color_dict.values()), axis=1)) # assumes RGB specification
    zorder = np.max(zorder) - zorder # reverse order as greater values correspond to lighter colors
    zorder = {key: index for key, index in zip(color_dict.keys(), zorder)}
    return zorder


def _get_font_size(ax, node_labels, **kwargs):
    """
    Determine the maximum font size that results in labels that still all fit inside the node artist.

    TODO:
    -----
    - add font / fontfamily as optional argument
    - potentially, return a dictionary of font sizes instead; then rescale font sizes individually on a per node basis
    """

    # check if there are relevant parameters in kwargs:
    #   - node size,
    #   - edge width, or
    #   - node_label_font_size
    if 'node_size' in kwargs:
        node_size = kwargs['node_size']
    else:
        node_size = 3. # default

    if 'node_edge_width' in kwargs:
        node_edge_width = kwargs['node_edge_width']
    else:
        node_edge_width = 0.5 # default

    if 'node_label_font_size' in kwargs:
        node_label_font_size = kwargs['node_label_font_size']
    else:
        node_label_font_size = 12. # default

    # find widest node label; use its rescale factor to set font size for all labels
    widest = 0.
    rescale_factor = np.nan
    for key, label in node_labels.items():

        if isinstance(node_size, (int, float)):
            r = node_size
        elif isinstance(node_size, dict):
            r = node_size[key]

        if isinstance(node_edge_width, (int, float)):
            e = node_edge_width
        elif isinstance(node_edge_width, dict):
            e = node_edge_width[key]

        node_diameter = 2 * (r-e) * BASE_NODE_SIZE

        width, height = _get_text_object_dimensions(ax, label, size=node_label_font_size)

        if width > widest:
            widest = width
            rescale_factor = node_diameter / np.sqrt(width**2 + height**2)

    font_size = node_label_font_size * rescale_factor
    return font_size


@deprecated("Use Graph.draw_nodes() or InteractiveGraph.draw_nodes() instead.")
def draw_nodes(node_positions,
               node_shape='o',
               node_size=3.,
               node_edge_width=0.5,
               node_color='w',
               node_edge_color='k',
               node_alpha=1.0,
               ax=None,
               **kwargs):
    """
    Draw node markers at specified positions.

    Arguments
    ----------
    node_positions : dict node : (float, float)
        Mapping of nodes to (x, y) positions.

    node_shape : string or dict key : string (default 'o')
       The shape of the node. Specification is as for matplotlib.scatter
       marker, i.e. one of 'so^>v<dph8'.
       If a single string is provided all nodes will have the same shape.

    node_size : scalar or dict node : float (default 3.)
       Size (radius) of nodes.
       NOTE: Value is rescaled by BASE_NODE_SIZE (1e-2) to work well with layout routines in igraph and networkx.

    node_edge_width : scalar or dict key : float (default 0.5)
       Line width of node marker border.
       NOTE: Value is rescaled by BASE_NODE_SIZE (1e-2) to work well with layout routines in igraph and networkx.

    node_color : matplotlib color specification or dict node : color specification (default 'w')
       Node color.

    node_edge_color : matplotlib color specification or dict node : color specification (default 'k')
       Node edge color.

    node_alpha : scalar or dict node : float (default 1.)
       The node transparency.

    ax : matplotlib.axis instance or None (default None)
       Axis to plot onto; if none specified, one will be instantiated with plt.gca().

    Returns
    -------
    node_artists: dict node : artist
        Mapping of nodes to the node artists.

    """

    if ax is None:
        ax = plt.gca()

    # convert all inputs to dicts mapping node:property
    nodes = node_positions.keys()
    number_of_nodes = len(nodes)

    if isinstance(node_shape, str):
        node_shape = {node:node_shape for node in nodes}
    if isinstance(node_size, (int, float)):
        node_size = {node:node_size for node in nodes}
    if isinstance(node_edge_width, (int, float)):
        node_edge_width = {node: node_edge_width for node in nodes}
    if not isinstance(node_color, dict):
        node_color = {node:node_color for node in nodes}
    if not isinstance(node_edge_color, dict):
        node_edge_color = {node:node_edge_color for node in nodes}
    if isinstance(node_alpha, (int, float)):
        node_alpha = {node:node_alpha for node in nodes}

    # rescale
    node_size = {node: size  * BASE_NODE_SIZE for (node, size) in node_size.items()}
    node_edge_width = {node: width  * BASE_NODE_SIZE for (node, width) in node_edge_width.items()}

    artists = dict()
    for node in nodes:
        node_artist = NodeArtist(shape=node_shape[node],
                                 xy=node_positions[node],
                                 radius=node_size[node],
                                 facecolor=node_color[node],
                                 edgecolor=node_edge_color[node],
                                 linewidth=node_edge_width[node],
                                 alpha=node_alpha[node],
                                 zorder=2)

        # add artists to axis
        ax.add_artist(node_artist)

        # return handles to artists
        artists[node] = node_artist

    return artists


@deprecated("Use Graph.draw_edges() or InteractiveGraph.draw_edges() instead.")
def draw_edges(edge_list,
               node_positions,
               node_size=3.,
               edge_width=1.,
               edge_color='k',
               edge_alpha=1.,
               edge_zorder=None,
               draw_arrows=True,
               curved=False,
               ax=None,
               **kwargs):
    """
    Draw the edges of the network.

    Arguments
    ----------
    edge_list : m-long iterable of 2-tuples or equivalent (such as (m, 2) ndarray)
        List of edges. Each tuple corresponds to an edge defined by (source, target).

    node_positions : dict key : (float, float)
        Mapping of nodes to (x,y) positions

    node_size : scalar or (n,) or dict key : float (default 3.)
        Size (radius) of nodes.
        Used to offset edges when drawing arrow heads,
        such that the arrow heads are not occluded.
        If draw_nodes() and draw_edges() are called independently,
        make sure to set this variable to the same value.

    edge_width : float or dict (source, key) : width (default 1.)
        Line width of edges.
        NOTE: Value is rescaled by BASE_EDGE_WIDTH (1e-2) to work well with layout routines in igraph and networkx.

    edge_color : matplotlib color specification or dict (source, target) : color specification (default 'k')
       Edge color.

    edge_alpha : float or dict (source, target) : float (default 1.)
        The edge transparency,

    edge_zorder : int or dict (source, target) : int (default None)
        Order in which to plot the edges.
        If None, the edges will be plotted in the order they appear in 'adjacency'.
        Note: graphs typically appear more visually pleasing if darker coloured edges
        are plotted on top of lighter coloured edges.

    draw_arrows : bool, optional (default True)
        If True, draws edges with arrow heads.

    curved : bool, optional (default False)
        If True, draw edges as curved splines.

    ax : matplotlib.axis instance or None (default None)
       Axis to plot onto; if none specified, one will be instantiated with plt.gca().

    Returns
    -------
    artists: dict (source, target) : artist
        Mapping of edges to EdgeArtists.

    """

    if ax is None:
        ax = plt.gca()

    edge_list = _parse_edge_list(edge_list) # TODO: why?
    nodes = node_positions.keys()

    # convert node and edge to dictionaries if they are not dictionaries already;
    # if dictionaries are provided, make sure that they are complete;
    # fill any missing entries with the default values
    if not isinstance(node_size, dict):
        node_size = {node:node_size for node in nodes}
    else:
        for node in nodes:
            node_size.setdefault(node, 3.)

    if not isinstance(edge_width, dict):
        edge_width = {edge: edge_width for edge in edge_list}
    else:
        for edge in edge_list:
            edge_width.setdefault(edge, 1.)

    if not isinstance(edge_color, dict):
        edge_color = {edge: edge_color for edge in edge_list}
    else:
        for edge in edge_list:
            edge_color.setdefault(edge, 'k')

    if not isinstance(edge_alpha, dict):
        edge_alpha = {edge: edge_alpha for edge in edge_list}
    else:
        for edge in edge_list:
            edge_alpha.setdefault(edge, 1.)

    # rescale
    node_size  = {node: size  * BASE_NODE_SIZE  for (node, size)  in node_size.items()}
    edge_width = {edge: width * BASE_EDGE_WIDTH for (edge, width) in edge_width.items()}

    # order edges if necessary
    if edge_zorder:
        for edge in edge_list:
            edge_zorder.setdefault(edge, max(edge_zorder.values()))
        edge_list = sorted(edge_zorder, key=lambda k: edge_zorder[k])

    # compute edge paths
    if not curved:
        edge_paths = _get_straight_edge_paths(edge_list, node_positions, edge_width)
    else:
        edge_paths = _get_curved_edge_paths(edge_list, node_positions)

    # NOTE: At the moment, only the relative zorder is honored, not the absolute value.
    artists = dict()
    for (source, target) in edge_list:

        width = edge_width[(source, target)]
        color = edge_color[(source, target)]
        alpha = edge_alpha[(source, target)]
        offset = node_size[target]

        if ((target, source) in edge_list) and not curved: # i.e. bidirectional, straight edges
            # plot half arrow / line
            shape = 'right'
        else:
            shape = 'full'

        if draw_arrows:
            head_length = 2 * width
            head_width = 3 * width
        else:
            head_length = 1e-10 # 0 throws error
            head_width = 1e-10 # 0 throws error

        edge_artist = EdgeArtist(
            midline     = edge_paths[(source, target)],
            width       = width,
            facecolor   = color,
            alpha       = alpha,
            head_length = head_length,
            head_width  = head_width,
            zorder      = 1,
            edgecolor   = 'none',
            linewidth   = 0.1,
            offset      = offset,
            shape       = shape,
            curved      = curved,
        )
        ax.add_artist(edge_artist)
        artists[(source, target)] = edge_artist

    return artists


def _get_straight_edge_paths(edge_list, node_positions, edge_width):
    edge_paths = dict()
    for (source, target) in edge_list:

        if source == target:
            msg = "Plotting of self-loops not supported for straight edges."
            msg += "Ignoring edge ({}, {}).".format(source, target)
            warnings.warn(msg)
            continue

        x1, y1 = node_positions[source]
        x2, y2 = node_positions[target]

        if (target, source) in edge_list: # i.e. bidirectional
            # shift edge to the right (looking along the arrow)
            x1, y1, x2, y2 = _shift_edge(x1, y1, x2, y2, delta=-0.5*edge_width[(source, target)])

        edge_paths[(source, target)] = np.c_[[x1, x2], [y1, y2]]

    return edge_paths


def _shift_edge(x1, y1, x2, y2, delta):
    # get orthogonal unit vector
    v = np.r_[x2-x1, y2-y1] # original
    v = np.r_[-v[1], v[0]] # orthogonal
    v = v / np.linalg.norm(v) # unit
    dx, dy = delta * v
    return x1+dx, y1+dy, x2+dx, y2+dy


def _get_curved_edge_paths(edge_list, node_positions,
                           total_control_points_per_edge = 11,
                           bspline_degree                = 5,
                           selfloop_radius               = 0.1,
                           origin                        = np.array([0, 0]),
                           scale                         = np.array([1, 1]),
                           k                             = None,
                           initial_temperature           = 0.1,
                           total_iterations              = 50,
                           node_size                     = None,
                           *args, **kwargs):


    expanded_edge_list, edge_to_control_points = _insert_control_points(
        edge_list, total_control_points_per_edge)

    control_point_positions = _initialize_control_point_positions(
        edge_to_control_points, node_positions, selfloop_radius, origin, scale)

    expanded_node_positions = _optimize_control_point_positions(
        expanded_edge_list, node_positions, control_point_positions, total_control_points_per_edge,
        origin, scale, k, initial_temperature, total_iterations, node_size,
    )

    edge_to_path = _fit_splines_through_control_points(
        edge_to_control_points, expanded_node_positions, bspline_degree
    )

    return edge_to_path


def _insert_control_points(edge_list, total_control_points_per_edge=11):
    """
    Create a new, expanded edge list, in which each edge is split into multiple segments.
    There are total_control_points + 1 segments / edges for each original edge.
    """
    expanded_edge_list = []
    edge_to_control_points = dict()

    for source, target in edge_list:
        control_points = [uuid4() for _ in range(total_control_points_per_edge)]
        edge_to_control_points[(source, target)] = control_points

        sources = [source] + control_points
        targets = control_points + [target]
        expanded_edge_list.extend(zip(sources, targets))

    return expanded_edge_list, edge_to_control_points


def _initialize_control_point_positions(edge_to_control_points, node_positions,
                                        selfloop_radius = 0.1,
                                        origin          = np.array([0, 0]),
                                        scale           = np.array([1, 1])
):
    """
    Initialise the positions of the control points to positions on a straight line between source and target node.
    For self-loops, initialise the positions on a circle next to the node.
    """

    nonloops_to_control_points = {(source, target) : pts for (source, target), pts in edge_to_control_points.items() if source != target}
    selfloops_to_control_points = {(source, target) : pts for (source, target), pts in edge_to_control_points.items() if source == target}

    control_point_positions = dict()
    control_point_positions.update(_initialize_nonloops(nonloops_to_control_points, node_positions))
    control_point_positions.update(_initialize_selfloops(selfloops_to_control_points, node_positions, selfloop_radius, origin, scale))

    return control_point_positions


def _initialize_nonloops(edge_to_control_points, node_positions):
    control_point_positions = dict()
    for (source, target), control_points in edge_to_control_points.items():
        control_point_positions.update(_init_nonloop(source, target, control_points, node_positions))
    return control_point_positions


def _init_nonloop(source, target, control_points, node_positions):
    delta = node_positions[target] - node_positions[source]
    distance = np.linalg.norm(delta)
    unit_vector = delta / distance
    output = dict()
    for ii, control_point in enumerate(control_points):
        # y = mx + b
        m = (ii+1) * distance / (len(control_points) + 1)
        output[control_point] = m * unit_vector + node_positions[source]
    return output


def _initialize_selfloops(edge_to_control_points, node_positions,
                          selfloop_radius = 0.1,
                          origin          = np.array([0, 0]),
                          scale           = np.array([1, 1])
):
    control_point_positions = dict()
    for (source, target), control_points in edge_to_control_points.items():
        # Source and target have the same position, such that
        # using the strategy employed above the control points
        # also end up at the same position. Instead we make a loop.
        control_point_positions.update(
            _init_selfloop(source, control_points, node_positions, selfloop_radius, origin, scale)
        )
    return control_point_positions


def _init_selfloop(source, control_points, node_positions, selfloop_radius, origin, scale):
    # To minimise overlap with other edges, we want the loop to be
    # on the side of the node away from the centroid of the graph.
    if len(node_positions) > 1:
        centroid = np.mean(list(node_positions.values()), axis=0)
        delta = node_positions[source] - centroid
        distance = np.linalg.norm(delta)
        unit_vector = delta / distance
    else: # single node in graph; self-loop points upwards
        unit_vector = np.array([0, 1])

    selfloop_center = node_positions[source] + selfloop_radius * unit_vector

    selfloop_control_point_positions = _get_n_points_on_a_circle(
        selfloop_center, selfloop_radius, len(control_points),
        _get_signed_angle_between(np.array([1., 0.]), node_positions[source] - selfloop_center)
    )

    # ensure that the loop stays within the bounding box
    selfloop_control_point_positions = _clip_to_frame(selfloop_control_point_positions, origin, scale)

    output = dict()
    for ii, control_point in enumerate(control_points):
        output[control_point] = selfloop_control_point_positions[ii]

    return output


def _optimize_control_point_positions(
        expanded_edge_list, node_positions,
        control_point_positions, total_control_points_per_edge,
        origin                        = np.array([0, 0]),
        scale                         = np.array([1, 1]),
        k                             = None,
        initial_temperature           = 0.1,
        total_iterations              = 50,
        node_size                     = None,
):

    # If the spacing of nodes is approximately k, the spacing of control points should be k / (total control points per edge + 1).
    # This would maximise the use of the available space. However, we do not want space to be filled with edges like a Peano-curve.
    # Therefor, we apply an additional fudge factor that pulls the edges a bit more taut.
    unique_nodes = list(node_positions.keys())
    if k is None:
        total_nodes = len(unique_nodes)
        area = np.product(scale)
        k = np.sqrt(area / float(total_nodes)) / (total_control_points_per_edge + 1)
        k *= 0.5

    control_point_positions.update(node_positions)

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)

        expanded_node_positions = get_fruchterman_reingold_layout(
            expanded_edge_list,
            node_positions      = control_point_positions,
            scale               = scale,
            origin              = origin,
            k                   = k,
            initial_temperature = initial_temperature,
            total_iterations    = total_iterations,
            node_size           = node_size,
            fixed_nodes         = unique_nodes,
        )

    return expanded_node_positions


def _get_path_through_control_points(edge_to_control_points, expanded_node_positions):
    edge_to_path = dict()
    for (source, target), control_points in edge_to_control_points.items():
        control_point_positions = [expanded_node_positions[source]] \
            + [expanded_node_positions[node] for node in control_points] \
            + [expanded_node_positions[target]]
        edge_to_path[(source, target)] = control_point_positions
    return edge_to_path


def _fit_splines_through_control_points(edge_to_control_points, expanded_node_positions, bspline_degree):
    # Fit a BSpline to each set of control points (+ anchors).
    edge_to_path = dict()
    for (source, target), control_points in edge_to_control_points.items():
        control_point_positions = [expanded_node_positions[source]] \
            + [expanded_node_positions[node] for node in control_points] \
            + [expanded_node_positions[target]]
        path = _bspline(np.array(control_point_positions), n=TOTAL_POINTS_PER_EDGE, degree=bspline_degree)
        edge_to_path[(source, target)] = path
    return edge_to_path


@profile
def _get_bundled_edge_paths(edge_list, node_positions,
                            total_control_points_per_edge=16,
                            total_iterations=25, initial_temperature=0.05, k=10000,
                            bspline_degree=20,
):
    """Bundle edges using the method proposed by Holten & Wijk (2009).

    The implementation follows the paper closely with the exception
    that forces are computed using matrix operations rather than
    computing them on pairs of points. In doing so, memory
    allocation becomes the rate limiting step rather than the force updates.

    This has several consequences:

    1. We do not neglect interactions between edges with a low
    compatibility scores.

    2. We use a (large) constant number of control points on each
    iteration instead of starting with a small number of control
    points and increasing the number on each iteration.

    A further difference is in the definition of the scale compatibility,
    which appears to be wrong as stated in the paper. Instead we define this
    value as the minimum of the two possible ratios of the edge lengths.

    Arguments:
    ----------
    edge_list : list
        List of (source node, target node) 2-tuples.

    node_positions : dict node : (float x, float y)
        Dictionary mapping nodes to (x, y) positions.

    total_control_points_per_edge : int (default 16)
        Determines the number of segments, into which each edge is partitioned.

    total_iterations : int (default 20)
        The number of updates of the force simulation.

    initial_temperature : float (default 0.4)
        The temperature controls the maximum displacement of a control point on each iteration.
        Starting from this initial value, the temperature is decreased on each iteration.

    k : float (default 0.1)
        The stiffness of the springs that connect control points.

    bspline_degree : int (default 5)
        Determines the stiffness of the splines fitted to the updated control
        point positions.

    Returns:
    --------
    edge_to_paths : dict edge : path
        Dictionary mapping edges to arrays of (x, y) tuples, the edge paths.

    """

    # Filter out self-loops.
    # TODO: raise warning if any self-loops are present.
    edge_list = [(source, target) for (source, target) in edge_list if source != target]

    # Filter out bi-directional edges.
    unidirectional_edges = set()
    for (source, target) in edge_list:
        if (target, source) not in unidirectional_edges:
            unidirectional_edges.add((source, target))
    reverse_edges = list(set(edge_list) - unidirectional_edges)
    edge_list = list(unidirectional_edges)

    expanded_edge_list, edge_to_control_points = _insert_control_points(
        edge_list, total_control_points_per_edge)

    # Initialize control point positions to regularly spaced points on
    # a line between source and target node.
    control_point_positions = _initialize_nonloops(
        edge_to_control_points, node_positions)

    # x, y = zip(*control_point_positions.values())
    # plt.plot(x, y, 'ro')

    # Convert the list of segments to an adjacency matrix.
    # Convert the dictionary of node and control point positions to an array of positions.
    # Ensure that the indices into the adjacency matrix match the indices into the positions array.
    expanded_positions = control_point_positions.copy()
    expanded_positions.update(node_positions)
    expanded_nodes = list(expanded_positions.keys())
    expanded_positions_as_array = np.array([expanded_positions[node] for node in expanded_nodes])
    expanded_adjacency = _edge_list_to_adjacency_matrix(
        expanded_edge_list, unique_nodes=expanded_nodes)
    expanded_adjacency += expanded_adjacency.transpose() # make symmetric

    # Holten & Wijk compute electrostatic attraction Fe in a pairwise fashion,
    # i.e. the first control point in edge A only attracts the first control point in edge B, etc.
    # Here we construct an adjacency matrix for these pairs.
    valid_control_point_pairs = _get_valid_control_point_pairs(
        edge_list, node_positions, edge_to_control_points)
    control_point_compatibility = _edge_list_to_adjacency_matrix(
        valid_control_point_pairs, unique_nodes=expanded_nodes)
    control_point_compatibility += control_point_compatibility.transpose()

    # Compute edge compatibility scores and map to (all combinations of) control points.
    edge_compatibility = _get_edge_compatibility(edge_list, node_positions)
    edge_compatibility_matrix = np.zeros_like(expanded_adjacency)
    edge_to_indices = {edge : np.in1d(expanded_nodes, list(edge) + edge_to_control_points[edge]) for edge in edge_list}
    for (e1, e2), compatibility in edge_compatibility.items():
        if compatibility > 0.05:
            mask = edge_to_indices[e1][:, None] & edge_to_indices[e2][None, :]
            edge_compatibility_matrix[mask] = compatibility
    edge_compatibility_matrix += edge_compatibility_matrix.T # make symmetric

    compatibility_matrix = control_point_compatibility * edge_compatibility_matrix

    # TODO compute kp for each edge.
    kp = k

    # Apply force directed layout while only updating control point positions
    # and keeping the node positions constant.
    is_control_point = np.in1d(expanded_nodes, list(control_point_positions.keys()))
    temperatures = _get_temperature_decay(initial_temperature, total_iterations)
    for temperature in temperatures:
        expanded_positions_as_array[is_control_point] = _holten_wijk_inner(
            expanded_positions_as_array, expanded_adjacency,
            compatibility_matrix, kp, temperature)[is_control_point]

    # Construct edge paths from new control point positions.
    expanded_positions = dict(zip(expanded_nodes, expanded_positions_as_array))
    # edge_to_path = _fit_splines_through_control_points(
    #     edge_to_control_points, expanded_positions, bspline_degree
    # )
    edge_to_path = _get_path_through_control_points(
        edge_to_control_points, expanded_positions
    )

    # Add previously removed bi-directional edges back in.
    for (source, target) in reverse_edges:
        edge_to_path[(source, target)] = edge_to_path[(target, source)]

    return edge_to_path


@profile
def _get_edge_compatibility(edge_list, node_positions):
    # precompute edge segments, segment lengths and corresponding vectors
    edge_to_segment = {edge : Segment(node_positions[edge[0]], node_positions[edge[1]]) for edge in edge_list}

    edge_compatibility = dict()
    for e1, e2 in itertools.combinations(edge_list, 2):
        P = edge_to_segment[e1]
        Q = edge_to_segment[e2]
        angle_compatibility      = _get_angle_compatibility(P, Q)
        scale_compatibility      = _get_scale_compatibility(P, Q)
        position_compatibility   = _get_position_compatibility(P, Q)
        visibility_compatibility = _get_visibility_compatibility(P, Q)
        edge_compatibility[(e1, e2)] = angle_compatibility \
            * scale_compatibility \
            * position_compatibility \
            * visibility_compatibility
    return edge_compatibility


class Segment(object):
    def __init__(self, p0, p1):
        self._p0 = p0
        self._p1 = p1
        self.vector = p1 - p0
        self.length = np.linalg.norm(self.vector)
        self.midpoint = self._p0 * 0.5 * self.vector

    def __getitem__(self, idx):
        if idx == 0:
            return self._p0
        elif idx == 1:
            return self._p1
        else:
            raise IndexError


def _get_angle_compatibility(P, Q):
    return np.abs(np.cos(_get_interior_angle_between(P.vector, Q.vector)))


def _get_scale_compatibility(P, Q):
    avg = 0.5 * (P.length + Q.length)

    # The definition in the paper is rubbish, as the result is not on the interval [0, 1].
    # For example, consider an two edges, both 0.5 long:
    # return 2 / (avg * min(length_P, length_Q) + max(length_P, length_Q) / avg)
    # return min(length_P/length_Q, length_Q/length_P)
    return 2 / (avg / min(P.length, Q.length) + max(P.length, Q.length) / avg)


def _get_position_compatibility(P, Q):
    avg = 0.5 * (P.length + Q.length)
    distance_between_midpoints = np.linalg.norm(Q.midpoint - P.midpoint)
    return avg / (avg + distance_between_midpoints)


def _get_visibility_compatibility(P, Q):
    return min(_get_visibility(P, Q), _get_visibility(Q, P))


def _get_visibility(P, Q):
    I0 = _get_orthogonal_projection_onto_segment(Q[0], P)
    I1 = _get_orthogonal_projection_onto_segment(Q[1], P)
    I = Segment(I0, I1)
    distance_between_midpoints = np.linalg.norm(P.midpoint - I.midpoint)
    visibility = 1 - 2 * distance_between_midpoints / I.length
    return max(visibility, 0)


def _get_valid_control_point_pairs(edge_list, node_positions, edge_to_control_points):
    """Zip two sets of control points starting at the two closest edge endpoints."""
    valid_pairs = []
    for (s1, t1), (s2, t2) in itertools.combinations(edge_list, 2):
        # determine the two closest edge endpoints
        P0 = node_positions[s1]
        P1 = node_positions[t1]
        Q0 = node_positions[s2]
        Q1 = node_positions[t2]
        if min(np.linalg.norm(P0-Q0), np.linalg.norm(P1-Q1)) <= \
           min(np.linalg.norm(P0-Q1), np.linalg.norm(P1-Q0)):
            # i.e. if source/source or target/target closest
            valid_pairs.extend(zip(edge_to_control_points[(s1, t1)], edge_to_control_points[(s2, t2)]))
        else:
            # need to reverse one set of control points
            valid_pairs.extend(zip(edge_to_control_points[(s1, t1)], edge_to_control_points[(s2, t2)][::-1]))
    return valid_pairs


@profile
def _holten_wijk_inner(node_positions, adjacency, edge_compatibility, k, temperature):
    # compute distances and unit vectors between nodes
    delta        = node_positions[None, :, ...] - node_positions[:, None, ...]
    distance     = np.linalg.norm(delta, axis=-1)

    with np.errstate(divide='ignore', invalid='ignore'):
        direction = delta / distance[..., None] # i.e. the unit vector

    # calculate forces
    Fs = _get_Fs(distance, direction, adjacency, k)
    Fe = _get_Fe(distance, direction, edge_compatibility)

    # print(np.mean(np.linalg.norm(Fs, axis=-1) > np.linalg.norm(Fe, axis=-1)))
    # displacement = Fs
    # displacement = Fe
    displacement = Fs + Fe

    # limit maximum displacement
    displacement_length = np.clip(np.linalg.norm(displacement, axis=-1), 1e-12, None) # prevent divide by 0 error in next line
    displacement = displacement / displacement_length[:, None] * np.clip(displacement_length, None, temperature)[:, None]

    # x, y = (node_positions + displacement).transpose()
    # plt.plot(x, y, 'ko', alpha=0.1)
    return node_positions + displacement


@profile
def _get_Fe(distance, direction, edge_compatibility):
    with np.errstate(divide='ignore', invalid='ignore'):
        magnitude = edge_compatibility / distance
    # # Points that are very close to each other have a high attraction.
    # # However, the displacement of due to that force should not excel the
    # # distance between the two points.
    # magnitude = np.clip(magnitude, None, distance)
    vectors = -direction * magnitude[..., None]
    for ii in range(vectors.shape[-1]):
        np.fill_diagonal(vectors[:, :, ii], 0)
    return np.sum(vectors, axis=0)


@profile
def _get_Fs(distance, direction, adjacency, k):
    magnitude = k * distance * adjacency
    vectors = -direction * magnitude[..., None] # NB: the minus!
    for ii in range(vectors.shape[-1]):
        np.fill_diagonal(vectors[:, :, ii], 0)
    return np.sum(vectors, axis=0)


@deprecated("Use Graph.draw_node_labels() or InteractiveGraph.draw_node_labels() instead.")
def draw_node_labels(node_labels,
                     node_positions,
                     node_label_font_size=12,
                     node_label_font_color='k',
                     node_label_font_family='sans-serif',
                     node_label_font_weight='normal',
                     node_label_font_alpha=1.,
                     node_label_bbox=dict(alpha=0.),
                     node_label_horizontalalignment='center',
                     node_label_verticalalignment='center',
                     node_label_offset=(0., 0.),
                     clip_on=False,
                     ax=None,
                     **kwargs):
    """
    Draw node labels.

    Arguments
    ---------
    node_positions : dict node : (float, float)
        Mapping of nodes to (x, y) positions

    node_labels : dict key : str
       Mapping of nodes to labels.
       Only nodes in the dictionary are labelled.

    node_label_font_size : int (default 12)
       Font size for text labels

    node_label_font_color : str (default 'k')
       Font color string

    node_label_font_family : str (default='sans-serif')
       Font family

    node_label_font_weight : str (default='normal')
       Font weight

    node_label_font_alpha : float (default 1.)
       Text transparency

    node_label_bbox : matplotlib bbox instance (default {'alpha': 0})
       Specify text box shape and colors.

    node_label_horizontalalignment: str
        Horizontal label alignment inside bbox.

    node_label_verticalalignment: str
        Vertical label alignment inside bbox.

    node_label_offset: 2-tuple or equivalent iterable (default (0.,0.))
        (x, y) offset from node centre of label position.

    clip_on : bool (default False)
       Turn on clipping at axis boundaries.

    ax : matplotlib.axis instance or None (default None)
       Axis to plot onto; if none specified, one will be instantiated with plt.gca().

    Returns
    -------
    artists: dict
        Dictionary mapping node indices to text objects.

    @reference
    Borrowed with minor modifications from networkx/drawing/nx_pylab.py

    """

    if ax is None:
        ax = plt.gca()

    dx, dy = node_label_offset

    artists = dict()  # there is no text collection so we'll fake one
    for node, label in node_labels.items():
        try:
            x, y = node_positions[node]
        except KeyError:
            print("Cannot draw node label for node with ID {}. The node has no position assigned to it.".format(node))
            continue
        x += dx
        y += dy
        text_object = ax.text(x, y,
                              label,
                              size=node_label_font_size,
                              color=node_label_font_color,
                              alpha=node_label_font_alpha,
                              family=node_label_font_family,
                              weight=node_label_font_weight,
                              bbox=node_label_bbox,
                              horizontalalignment=node_label_horizontalalignment,
                              verticalalignment=node_label_verticalalignment,
                              transform=ax.transData,
                              clip_on=clip_on)
        artists[node] = text_object

    return artists


@deprecated("Use Graph.draw_edge_labels() or InteractiveGraph.draw_edge_labels() instead.")
def draw_edge_labels(edge_list,
                     edge_labels,
                     node_positions,
                     edge_label_position=0.5,
                     edge_label_font_size=10,
                     edge_label_font_color='k',
                     edge_label_font_family='sans-serif',
                     edge_label_font_weight='normal',
                     edge_label_font_alpha=1.,
                     edge_label_bbox=None,
                     edge_label_horizontalalignment='center',
                     edge_label_verticalalignment='center',
                     clip_on=False,
                     edge_width=1.,
                     ax=None,
                     rotate=True,
                     edge_label_zorder=10000,
                     **kwargs):
    """
    Draw edge labels.

    Arguments
    ---------

    edge_list: m-long list of 2-tuples
        List of edges. Each tuple corresponds to an edge defined by (source, target).

    edge_labels : dict (source, target) : str
        Mapping of edges to edge labels.
        Only edges in the dictionary are labelled.

    node_positions : dict node : (float, float)
        Mapping of nodes to (x, y) positions

    edge_label_position : float
        Relative position along the edge where the label is placed.
            head   : 0.
            centre : 0.5 (default)
            tail   : 1.

    edge_label_font_size : int (default 12)
       Font size

    edge_label_font_color : str (default 'k')
       Font color

    edge_label_font_family : str (default='sans-serif')
       Font family

    edge_label_font_weight : str (default='normal')
       Font weight

    edge_label_font_alpha : float (default 1.)
       Text transparency

    edge_label_bbox : Matplotlib bbox
       Specify text box shape and colors.

    edge_label_horizontalalignment: str
        Horizontal label alignment inside bbox.

    edge_label_verticalalignment: str
        Vertical label alignment inside bbox.

    clip_on : bool (default=False)
       Turn on clipping at axis boundaries.

    edge_label_zorder : int (default 10000)
        Set the zorder of edge labels.
        Choose a large number to ensure that the labels are plotted on top of the edges.

    edge_width : float or dict (source, key) : width (default 1.)
        Line width of edges.
        NOTE: Value is rescaled by BASE_EDGE_WIDTH (1e-2) to work well with layout routines in igraph and networkx.

    ax : matplotlib.axis instance or None (default None)
       Axis to plot onto; if none specified, one will be instantiated with plt.gca().

    Returns
    -------
    artists: dict (source, target) : text object
        Mapping of edges to edge label artists.

    @reference
    Borrowed with minor modifications from networkx/drawing/nx_pylab.py

    """

    if ax is None:
        ax = plt.gca()

    if isinstance(edge_width, (int, float)):
        edge_width = {edge: edge_width for edge in edge_list}

    edge_width = {edge: width * BASE_EDGE_WIDTH for (edge, width) in edge_width.items()}

    text_items = {}
    for (n1, n2), label in edge_labels.items():

        if n1 != n2:

            (x1, y1) = node_positions[n1]
            (x2, y2) = node_positions[n2]

            if (n2, n1) in edge_list: # i.e. bidirectional edge --> need to shift label to stay on edge
                x1, y1, x2, y2 = _shift_edge(x1, y1, x2, y2, delta=1.*edge_width[(n1, n2)])

            (x, y) = (x1 * edge_label_position + x2 * (1.0 - edge_label_position),
                      y1 * edge_label_position + y2 * (1.0 - edge_label_position))

            if rotate:
                ange += _get_angle(x2-x1, y2-y1, radians=True)
                # make label orientation "right-side-up"
                if angle > 90:
                    angle -= 180
                if angle < - 90:
                    angle += 180
                # transform data coordinate angle to screen coordinate angle
                xy = np.array((x, y))
                trans_angle = ax.transData.transform_angles(np.array((angle,)),
                                                            xy.reshape((1, 2)))[0]
            else:
                trans_angle = 0.0

            if edge_label_bbox is None: # use default box of white with white border
                edge_label_bbox = dict(boxstyle='round',
                                       ec=(1.0, 1.0, 1.0),
                                       fc=(1.0, 1.0, 1.0))

            t = ax.text(x, y,
                        label,
                        size=edge_label_font_size,
                        color=edge_label_font_color,
                        alpha=edge_label_font_alpha,
                        family=edge_label_font_family,
                        weight=edge_label_font_weight,
                        bbox=edge_label_bbox,
                        horizontalalignment=edge_label_horizontalalignment,
                        verticalalignment=edge_label_verticalalignment,
                        rotation=trans_angle,
                        transform=ax.transData,
                        zorder=edge_label_zorder,
                        clip_on=clip_on,
                        )

            text_items[(n1, n2)] = t

        else: # n1 == n2, i.e. a self-loop
            warnings.warn("Plotting of edge labels for self-loops not supported. Ignoring edge with label: {}".format(label))

    return text_items


def _update_view(node_positions, ax, node_size=3.):
    # Pad x and y limits as patches are not registered properly
    # when matplotlib sets axis limits automatically.
    # Hence we need to set them manually.

    if isinstance(node_size, dict):
        maxs = np.max(list(node_size.values())) * BASE_NODE_SIZE
    else:
        maxs = node_size * BASE_NODE_SIZE

    maxx, maxy = np.max(list(node_positions.values()), axis=0)
    minx, miny = np.min(list(node_positions.values()), axis=0)

    w = maxx-minx
    h = maxy-miny
    padx, pady = 0.05*w + maxs, 0.05*h + maxs
    corners = (minx-padx, miny-pady), (maxx+padx, maxy+pady)

    ax.update_datalim(corners)
    ax.autoscale_view()
    ax.get_figure().canvas.draw()


# --------------------------------------------------------------------------------
# interactive plotting


def _add_doc(value):
    def _doc(func):
        func.__doc__ = value
        return func
    return _doc


class BaseGraph(object):

    def __init__(self, edge_list,
                 nodes=None,
                 node_positions=None,
                 node_shape='o',
                 node_size=3.,
                 node_edge_width=0.5,
                 node_color='w',
                 node_edge_color='k',
                 node_alpha=1.0,
                 node_zorder=2,
                 node_labels=None,
                 node_label_offset=(0., 0.),
                 node_label_fontdict={},
                 edge_width=1.,
                 edge_color='k',
                 edge_alpha=1.,
                 edge_zorder=1,
                 arrows=False,
                 edge_layout='straight',
                 edge_labels=None,
                 edge_label_position=0.5,
                 edge_label_rotate=True,
                 edge_label_fontdict={},
                 prettify=True,
                 ax=None,
                 *args, **kwargs,
    ):
        """

        Arguments
        ----------
        edge_list : list of (source node, target node) 2-tuples
            List of edges.

        nodes : list of node IDs or None (default None)
            If None, `nodes` is initialised to the set of the flattened `edge_list`.

        node_positions : dict node : (float, float)
            Mapping of nodes to (x, y) positions.
            If 'graph' is an adjacency matrix, nodes must be integers in range 0 to number of nodes - 1.

        node_shape : string or dict node : string (default 'o')
           The shape of the node. Specification is as for matplotlib.scatter
           marker, i.e. one of 'so^>v<dph8'.
           If a single string is provided all nodes will have the same shape.

        node_size : scalar or dict node : float (default 3.)
           Size (radius) of nodes.
           NOTE: Value is rescaled by BASE_NODE_SIZE (1e-2) to work well with layout routines in igraph and networkx.

        node_edge_width : scalar or dict node : float (default 0.5)
           Line width of node marker border.
           NOTE: Value is rescaled by BASE_NODE_SIZE (1e-2) to work well with layout routines in igraph and networkx.

        node_color : matplotlib color specification or dict node : color (default 'w')
           Node color.

        node_edge_color : matplotlib color specification or dict node : color (default 'k')
           Node edge color.

        node_alpha : scalar or dict node : float (default 1.)
           The node transparency.

        node_zorder : scalar or dict node : float (default 2)
           Order in which to plot the nodes.

        node_labels : dict node : str (default None)
           Mapping of nodes to node labels.
           Only nodes in the dictionary are labelled.
           If 'graph' is an adjacency matrix, nodes must be integers in range 0 to number of nodes - 1.

        node_label_offset: 2-tuple or equivalent iterable (default (0., 0.))
            (x, y) offset from node centre of label position.

        node_label_fontdict : dict
            Keyword arguments passed to matplotlib.text.Text.
            For a full list of available arguments see the matplotlib documentation.
            The following default values differ from the defaults for matplotlib.text.Text:
                - size (adjusted to fit into node artists if offset is (0, 0))
                - horizontalalignment (default here: 'center')
                - verticalalignment (default here: 'center')
                - clip_on (default here: False)

        edge_width : float or dict (source, target) : width (default 1.)
            Line width of edges.
            NOTE: Value is rescaled by BASE_EDGE_WIDTH (1e-2) to work well with layout routines in igraph and networkx.

        edge_color : matplotlib color specification or dict (source, target) : color (default 'k')
           Edge color.

        edge_alpha : float or dict (source, target) : float (default 1.)
            The edge transparency,

        edge_zorder : int or dict (source, target) : int (default 1)
            Order in which to plot the edges.
            If None, the edges will be plotted in the order they appear in 'adjacency'.
            Note: graphs typically appear more visually pleasing if darker edges
            are plotted on top of lighter edges.

        arrows : bool, optional (default False)
            If True, draw edges with arrow heads.

        edge_layout : 'straight', 'curved' or 'bundled' (default 'straight')
            If 'straight', draw edges as straight lines.
            If 'curved', draw edges as curved splines. The spline control points
            are optimised to avoid other nodes and edges.
            If 'bundled', draw edges as edge bundles.

        edge_labels : dict edge : str
            Mapping of edges to edge labels.
            Only edges in the dictionary are labelled.

        edge_label_position : float
            Relative position along the edge where the label is placed.
                head   : 0.
                centre : 0.5 (default)
                tail   : 1.

        edge_label_rotate : bool (default True)
            If True, edge labels are rotated such that they track the orientation of their edges.
            If False, edge labels are not rotated; the angle of the text is parallel to the axis.

        edge_label_fontdict : dict
            Keyword arguments passed to matplotlib.text.Text.
            For a full list of available arguments see the matplotlib documentation.
            The following default values differ from the defaults for matplotlib.text.Text:
                - horizontalalignment (default here 'center'),
                - verticalalignment (default here 'center')
                - clip_on (default here False),
                - bbox (default here dict(boxstyle='round', ec=(1.0, 1.0, 1.0), fc=(1.0, 1.0, 1.0)),
                - zorder (default here 1000),
                - rotation (determined by edge_label_rotate argument)

        ax : matplotlib.axis instance or None (default None)
           Axis to plot onto; if none specified, one will be instantiated with plt.gca().

        See Also
        --------
        draw_nodes()
        draw_edges()
        draw_node_labels()
        draw_edge_labels()

        """
        self.edge_list = edge_list

        nodes_in_edge_list = _get_unique_nodes(edge_list)
        if nodes is None:
            self.nodes = nodes_in_edge_list
        else:
            msg = "There are some node IDs in the edgelist not present in `nodes`. "
            msg += "`nodes` has to be the superset of `edge_list`."
            assert set(nodes).issuperset(nodes_in_edge_list), msg
            self.nodes = nodes

        # Convert all node and edge parameters to dictionaries.
        node_shape      = self._normalize_string_argument(node_shape, self.nodes, 'node_shape')
        node_size       = self._normalize_numeric_argument(node_size, self.nodes, 'node_size')
        node_edge_width = self._normalize_numeric_argument(node_edge_width, self.nodes, 'node_edge_width')
        node_color      = self._normalize_color_argument(node_color, self.nodes, 'node_color')
        node_edge_color = self._normalize_color_argument(node_edge_color, self.nodes, 'node_edge_color')
        node_alpha      = self._normalize_numeric_argument(node_alpha, self.nodes, 'node_alpha')
        node_zorder     = self._normalize_numeric_argument(node_zorder, self.nodes, 'node_zorder')
        edge_width      = self._normalize_numeric_argument(edge_width, self.edge_list, 'edge_width')
        edge_color      = self._normalize_color_argument(edge_color, self.edge_list, 'edge_color')
        edge_alpha      = self._normalize_numeric_argument(edge_alpha, self.edge_list, 'edge_alpha')
        edge_zorder     = self._normalize_numeric_argument(edge_zorder, self.edge_list, 'edge_zorder')

        # Rescale.
        node_size = self._rescale(node_size, BASE_NODE_SIZE)
        node_edge_width = self._rescale(node_edge_width, BASE_NODE_SIZE)
        edge_width = self._rescale(edge_width, BASE_EDGE_WIDTH)

        # Initialise node positions.
        if node_positions is None:
            self.node_positions = self._get_node_positions(self.edge_list)
        else:
            if set(node_positions.keys()).issuperset(self.nodes):
                # All node positions are given; nothing left to do.
                self.node_positions = node_positions
            else:
                # Some node positions are given; however, either
                # 1) not all positions are provided, or
                # 2) there are some unconnected nodes in the graph.
                self.node_positions = self._get_node_positions(self.edge_list,
                                                               node_positions = node_positions,
                                                               fixed_nodes    = node_positions.keys())

        # Create axis if none is given.
        if ax is None:
            self.ax = plt.gca()
        else:
            self.ax = ax

        # Draw plot elements
        self.edge_artists = dict()
        self.draw_edges(self.edge_list, self.node_positions,
                        node_size, edge_width, edge_color, edge_alpha,
                        edge_zorder, arrows, edge_layout)

        self.node_artists = dict()
        self.draw_nodes(self.nodes, self.node_positions,
                        node_shape, node_size, node_edge_width,
                        node_color, node_edge_color, node_alpha, node_zorder)

        # This function needs to be called before any font sizes are adjusted,
        # as the axis dimensions affect the effective font size.
        self._update_view()

        if node_labels:
            node_label_fontdict.setdefault('horizontalalignment', 'center')
            node_label_fontdict.setdefault('verticalalignment', 'center')
            node_label_fontdict.setdefault('clip_on', False)

            if np.all(np.isclose(node_label_offset, (0, 0))):
                # Labels are centered on node artists.
                # Set fontsize such that labels fit the diameter of the node artists.
                size = self._get_font_size(node_labels, node_label_fontdict) * 0.75 # conservative fudge factor
                node_label_fontdict.setdefault('size', size)

            self.node_label_artists = dict()
            self.draw_node_labels(node_labels, node_label_offset, node_label_fontdict)

        if edge_labels:
            self.edge_label_position = edge_label_position
            self.edge_label_rotate = edge_label_rotate
            edge_label_fontdict.setdefault('bbox', dict(boxstyle='round',
                                                        ec=(1.0, 1.0, 1.0),
                                                        fc=(1.0, 1.0, 1.0)))
            edge_label_fontdict.setdefault('horizontalalignment', 'center')
            edge_label_fontdict.setdefault('verticalalignment', 'center')
            edge_label_fontdict.setdefault('clip_on', False)
            edge_label_fontdict.setdefault('zorder', 1000)

            self.edge_label_artists = dict()
            self.draw_edge_labels(edge_labels, self.edge_label_position,
                                  self.edge_label_rotate, edge_label_fontdict)

        if prettify:
            _make_pretty(self.ax)


    def _normalize_numeric_argument(self, numeric_or_dict, dict_keys, variable_name):
        if isinstance(numeric_or_dict, (int, float)):
            return {key : numeric_or_dict for key in dict_keys}
        elif isinstance(node_size, dict):
            self._check_completeness(set(numeric_or_dict), dict_keys, variable_name)
            self._check_types(numeric_or_dict, (int, float), variable_name)
            return numeric_or_dict
        else:
            msg = f"The type of {variable_name} has to be either a int, float, or a dict."
            msg += f"The current type is {type(numeric_or_dict)}."
            raise TypeError(msg)


    def _check_completeness(self, given_set, desired_set):
        complete = given_set.issuperset(desired_set)
        if not complete:
            missing = desired_set - given_set
            msg = f"{variable_name} is incomplete. The following elements are missing:"
            for item in missing:
                msg += f"\n-{item}"
            raise ValueError(msg)


    def _check_types(self, items, types, variable_name):
        for item in items:
            if not isinstance(item, types):
                msg = f"Item {item} in {variable_name} is of the wrong type."
                msg += f"\nExpected type: {types}"
                msg += f"\nActual type: {type(item)}"
                raise TypeError(msg)


    def _normalize_string_argument(self, str_or_dict, dict_keys, variable_name):
        if isinstance(str_or_dict, str):
            return {key : str_or_dict for key in dict_keys}
        elif isinstance(str_or_dict, dict):
            self._check_completeness(set(str_or_dict), dict_keys, variable_name)
            self._check_types(str_or_dict, str, variable_name)
            return str_or_dict
        else:
            msg = f"The type of {variable_name} has to be either a str or a dict."
            msg += f"The current type is {type(str_or_dict)}."
            raise TypeError(msg)


    def _normalize_color_argument(self, color_or_dict, dict_keys, variable_name):
        if matplotlib.colors.is_color_like(color_or_dict):
            return {key : color_or_dict for key in dict_keys}
        elif color_or_dict is None:
            return {key : color_or_dict for key in dict_keys}
        elif isinstance(color_or_dict, dict):
            self._check_completeness(set(color_or_dict), dict_keys, variable_name)
            # TODO: assert that each element is a valid color
            return color_or_dict
        else:
            msg = f"The type of {variable_name} has to be either a valid matplotlib color specification or a dict."
            raise TypeError(msg)


    def _rescale(self, mydict, scalar):
        return {key: value * scalar for (key, value) in mydict.items()}


    def _get_node_positions(self, *args, **kwargs):
        """
        Ultra-thin wrapper around get_fruchterman_reingold_layout.
        Allows method to be overwritten by derived classes.
        """
        return get_fruchterman_reingold_layout(*args, **kwargs)


    def draw_nodes(self, nodes, node_positions, node_shape, node_size,
                   node_edge_width, node_color, node_edge_color, node_alpha,
                   node_zorder):
        """
        Draw node markers at specified positions.

        Arguments
        ----------
        nodes : list
            List of node IDs.

        node_positions : dict node : (float, float)
            Mapping of nodes to (x, y) positions.

        node_shape : dict node : string
           The shape of the node. Specification is as for matplotlib.scatter
           marker, i.e. one of 'so^>v<dph8'.

        node_size : dict node : float
           Size (radius) of nodes.

        node_edge_width : dict node : float
           Line width of node marker border.

        node_color : dict node : matplotlib color specification
           Node color.

        node_edge_color : dict node : matplotlib color specification
           Node edge color.

        node_alpha : dict node : float
           The node transparency.

        node_zorder : dict node : int
            The z-order of nodes.

        Updates
        -------
        node_artists: dict node : artist
            Mapping of nodes to the node artists.

        """
        for node in nodes:
            node_artist = NodeArtist(shape=node_shape[node],
                                     xy=node_positions[node],
                                     radius=node_size[node],
                                     facecolor=node_color[node],
                                     edgecolor=node_edge_color[node],
                                     linewidth=node_edge_width[node],
                                     alpha=node_alpha[node],
                                     zorder=node_zorder[node])
            self.ax.add_artist(node_artist)

            if node in self.node_artists:
                self.node_artists[node].remove()
            self.node_artists[node] = node_artist


    def _update_node_positions(self, nodes, node_positions=None):
        if node_positions:
            self.node_positions.update(node_positions)

        for node in nodes:
            self.node_artists[node].xy = self.node_positions[node]


    def draw_edges(self, edge_list, node_positions, node_size, edge_width,
                   edge_color, edge_alpha, edge_zorder, arrows, edge_layout):
        """
        Draw the edges of the network.

        Arguments
        ----------
        edge_list : m-long iterable of 2-tuples or equivalent (such as (m, 2) ndarray)
            List of edges. Each tuple corresponds to an edge defined by (source, target).

        node_positions : dict node : (float, float)
            Mapping of nodes to (x,y) positions

        node_size : dict node : float
            Size (radius) of nodes. Required to offset edges when drawing arrow heads,
            such that the arrow heads are not occluded.

        edge_width : dict (source, target) : width
            Line width of edges.

        edge_color :  dict (source, target) : matplotlib color specification
           Edge color.

        edge_alpha : dict (source, target) : float
            The edge transparency,

        edge_zorder : dict (source, target) : int
            Order in which to plot the edges.
            Hint: graphs typically appear more visually pleasing if darker edges
            are plotted on top of lighter edges.

        arrows : bool
            If True, draws edges with arrow heads.

        edge_layout : 'straight', 'curved' or 'bundled' (default 'straight')
            If 'straight', draw edges as straight lines.
            If 'curved', draw edges as curved splines. The spline control points
            are optimised to avoid other nodes and edges.
            If 'bundled', draw edges as edge bundles.

        Updates
        -------
        self.edge_artists: dict (source, target) : artist
            Mapping of edges to EdgeArtists.

        """

        if edge_layout is 'straight':
            edge_paths = _get_straight_edge_paths(edge_list, node_positions, edge_width)
        elif edge_layout is 'curved':
            edge_paths = _get_curved_edge_paths(edge_list, node_positions)
        elif edge_layout is 'bundled':
            edge_paths = _get_bundled_edge_paths(edge_list, node_positions)
        else:
            raise NotImplementedError(f"Variable edge_layout one of 'straight', 'curved' or 'bundled', not {edge_layout}")

        # Plot edges sorted by edge order.
        # Note that we are only honouring the relative z-order, not the absolute z-order.
        for edge in sorted(edge_zorder, key=lambda k: edge_zorder[k]):

            source, target = edge
            if ((target, source) in edge_list) and (edge_layout is 'straight'): # i.e. bidirectional, straight edges
                shape = 'right' # i.e. plot half arrow / thin line shifted to the right
            else:
                shape = 'full'

            if arrows:
                head_length = 2 * edge_width[edge]
                head_width = 3 * edge_width[edge]
            else:
                head_length = 1e-10 # 0 throws error
                head_width = 1e-10 # 0 throws error

            edge_artist = EdgeArtist(
                midline     = edge_paths[edge],
                width       = edge_width[edge],
                facecolor   = edge_color[edge],
                alpha       = edge_alpha[edge],
                head_length = head_length,
                head_width  = head_width,
                zorder      = 1,
                edgecolor   = 'none',
                linewidth   = 0.1,
                offset      = node_size[target],
                shape       = shape,
                curved      = False if edge_layout is 'straight' else True,
            )
            self.ax.add_artist(edge_artist)

            if edge in self.edge_artists:
                self.edge_artists[edge].remove()
            self.edge_artists[edge] = edge_artist


    def _update_straight_edge_positions(self, edges):

        # remove self-loops
        edges = [(source, target) for source, target in edges if source != target]

        # move edges to new positions
        for (source, target) in edges:
            x0, y0 = self.node_positions[source]
            x1, y1 = self.node_positions[target]

            # shift edge right if bi-directional
            if (target, source) in edges:
                x0, y0, x1, y1 = _shift_edge(x0, y0, x1, y1, delta=0.5*self.edge_artists[(source, target)].width)

            # update midline & path
            self.edge_artists[(source, target)].update_midline(np.c_[[x0, x1], [y0, y1]])
            self.ax.draw_artist(self.edge_artists[(source, target)])


    def _update_curved_edge_positions(self, edges):
        """Compute a new layout for curved edges keeping all other edges constant."""

        fixed_positions = dict()
        constant_edges = [edge for edge in self.edge_list if edge not in edges]
        for edge in constant_edges:
            edge_artist = self.edge_artists[edge]
            if edge_artist.curved:
                for position in edge_artist.midline[1:-1]:
                    fixed_positions[uuid4()] = position
            else:
                edge_origin = edge_artist.midline[0]
                delta = edge_artist.midline[-1] - edge_artist.midline[0]
                distance = np.linalg.norm(delta)
                unit_vector = delta / distance
                for ii in range(TOTAL_POINTS_PER_EDGE):
                    # y = mx + b
                    m = (ii+1) * distance / (TOTAL_POINTS_PER_EDGE + 1)
                    fixed_positions[uuid4()] = m * unit_vector + edge_origin
        fixed_positions.update(self.node_positions)
        edge_paths = _get_curved_edge_paths(edges, fixed_positions)

        for edge, path in edge_paths.items():
            self.edge_artists[edge].update_midline(path)
            self.ax.draw_artist(self.edge_artists[edge])


    def draw_node_labels(self, node_labels, node_label_offset, node_label_fontdict):
        """
        Draw node labels.

        Arguments
        ---------
        node_labels : dict key : str
           Mapping of nodes to labels.
           Only nodes in the dictionary are labelled.

        node_label_offset: 2-tuple or equivalent iterable (default (0., 0.))
            (x, y) offset from node centre of label position.

        node_label_fontdict : dict
            Keyword arguments passed to matplotlib.text.Text.
            For a full list of available arguments see the matplotlib documentation.
            The following default values differ from the defaults for matplotlib.text.Text:
                - size (adjusted to fit into node artists if offset is (0, 0))
                - horizontalalignment (default here: 'center')
                - verticalalignment (default here: 'center')
                - clip_on (default here: False)

        Updates:
        --------
        self.node_label_artists: dict
            Dictionary mapping nodes to text objects.

        """

        dx, dy = node_label_offset
        for node, label in node_labels.items():
            x, y = self.node_positions[node]
            artist = self.ax.text(x+dx, y+dy, label, **node_label_fontdict)

            if node in self.node_label_artists:
                self.node_label_artists[node].remove()
            self.node_label_artists[node] = artist


    def _get_font_size(self, node_labels, node_label_fontdict):
        """
        Determine the maximum font size such that all labels fit inside their node artist.

        TODO:
        -----
        - potentially rescale font sizes individually on a per node basis
        """

        rescale_factor = np.inf
        for node, label in node_labels.items():
            artist = self.node_artists[node]
            diameter = 2 * (artist.radius - artist._lw_data/artist.linewidth_correction)
            width, height = _get_text_object_dimensions(self.ax, label, **node_label_fontdict)
            rescale_factor = min(rescale_factor, diameter/np.sqrt(width**2 + height**2))

        if 'size' in node_label_fontdict:
            size = rescale_factor * node_label_fontdict['size']
        else:
            size = rescale_factor * plt.rcParams['font.size']
        return size


    def _update_node_label_positions(self, nodes, node_label_positions=None):
        if node_label_positions is None:
            node_label_positions = self.node_positions

        dx, dy = self.node_label_offset
        for node in nodes:
            x, y = node_label_positions[node]
            self.node_label_artists[node].set_position(x + dx, y + dy)


    def draw_edge_labels(self, edge_labels, edge_label_position,
                         edge_label_rotate, edge_label_fontdict):
        """
        Draw edge labels.

        Arguments
        ---------
        edge_labels : dict edge : str
            Mapping of edges to edge labels.
            Only edges in the dictionary are labelled.

        edge_label_position : float
            Relative position along the edge where the label is placed.
                head   : 0.
                centre : 0.5 (default)
                tail   : 1.

        edge_label_rotate : bool
            If True, edge labels are rotated such that they track the orientation of their edges.
            If False, edge labels are not rotated; the angle of the text is parallel to the axis.

        edge_label_fontdict : dict
            Keyword arguments passed to matplotlib.text.Text.

        Updates
        -------
        self.edge_label_artists: dict edge : text object
            Mapping of edges to edge label artists.

        """

        for edge, label in edge_labels.items():

            edge_artist = self.edge_artists[edge]

            if self._is_selfloop(edge) and (edge_artist.curved is False):
                msg = "Plotting of edge labels for self-loops not supported for straight edges."
                msg += "\nIgnoring edge with label: {}".format(label)
                warnings.warn(msg)
                continue

            x, y = _get_point_along_spline(edge_artist.midline, edge_label_position)

            if edge_label_rotate:

                # get tangent in degrees
                dx, dy = _get_tangent_at_point(edge_artist.midline, edge_label_position)
                angle = _get_angle(dx, dy, radians=True)

                # make label orientation "right-side-up"
                if angle > 90:
                    angle -= 180
                if angle < - 90:
                    angle += 180

                # transform data coordinate angle to screen coordinate angle
                xy = np.array((x, y))
                trans_angle = self.ax.transData.transform_angles(np.array((angle,)),
                                                                 xy.reshape((1, 2)))[0]
            else:
                trans_angle = 0.0

            edge_label_artist = self.ax.text(x, y, label,
                                             rotation=trans_angle,
                                             transform=self.ax.transData,
                                             **edge_label_fontdict)

            if edge in self.edge_label_artists:
                self.edge_label_artists[edge].remove()
            self.edge_label_artists[edge] = edge_label_artist


    def _is_selfloop(self, edge):
        return True if edge[0] == edge[1] else False


    def _update_edge_label_positions(self, edges):

        labeled_edges = [edge for edge in edges if edge in self.edge_label_artists]

        for (n1, n2) in labeled_edges:

            edge_artist = self.edge_artists[(n1, n2)]

            if edge_artist.curved:
                x, y = _get_point_along_spline(edge_artist.midline, self.edge_label_position)
                dx, dy = _get_tangent_at_point(edge_artist.midline, self.edge_label_position)

            elif not edge_artist.curved and (n1 != n2):
                (x1, y1) = self.node_positions[n1]
                (x2, y2) = self.node_positions[n2]

                if (n2, n1) in self.edge_list: # i.e. bidirectional edge
                    x1, y1, x2, y2 = _shift_edge(x1, y1, x2, y2, delta=1.5*self.edge_artists[(n1, n2)].width)

                x, y = (x1 * self.edge_label_position + x2 * (1.0 - self.edge_label_position),
                        y1 * self.edge_label_position + y2 * (1.0 - self.edge_label_position))
                dx, dy = x2 - x1, y2 - y1

            else: # self-loop but edge is straight so we skip it
                pass

            self.edge_label_artists[(n1, n2)].set_position((x, y))

            if self.edge_label_rotate:
                angle = _get_angle(dx, dy, radians=True)
                # make label orientation "right-side-up"
                if angle > 90:
                    angle -= 180
                if angle < -90:
                    angle += 180
                # transform data coordinate angle to screen coordinate angle
                trans_angle = self.ax.transData.transform_angles(np.array((angle,)), np.atleast_2d((x, y)))[0]
                self.edge_label_artists[(n1, n2)].set_rotation(trans_angle)


    def _update_view(self):
        # Pad x and y limits as patches are not registered properly
        # when matplotlib sets axis limits automatically.
        # Hence we need to set them manually.

        max_radius = np.max([artist.radius for artist in self.node_artists.values()])

        maxx, maxy = np.max(list(self.node_positions.values()), axis=0)
        minx, miny = np.min(list(self.node_positions.values()), axis=0)

        w = maxx-minx
        h = maxy-miny
        padx, pady = 0.05*w + max_radius, 0.05*h + max_radius
        corners = (minx-padx, miny-pady), (maxx+padx, maxy+pady)

        self.ax.update_datalim(corners)
        self.ax.autoscale_view()
        self.ax.get_figure().canvas.draw()


class Graph(BaseGraph):

    def __init__(self, graph, edge_cmap='RdGy', *args, **kwargs):
        """
        Parses the given graph and initialises the BaseGraph object.

        Upon initialisation, it will try to do "the right thing". Specifically, it will:
        - determine if the graph is weighted, and map weights to edge colors if that is the case;
        - determine if the graph is directed, and set `arrows` to True is that is the case;

        Arguments
        ----------
        graph: various formats
            Graph object to plot. Various input formats are supported.
            In order of precedence:
                - Edge list:
                    Iterable of (source, target) or (source, target, weight) tuples,
                    or equivalent (m, 2) or (m, 3) ndarray.
                - Adjacency matrix:
                    Full-rank (n,n) ndarray, where n corresponds to the number of nodes.
                    The absence of a connection is indicated by a zero.
                - igraph.Graph object
                - networkx.Graph object
        """

        # Accept a variety of formats for 'graph' and convert to common denominator.
        # TODO: parse_graph also returns nodes
        edge_list, edge_weight, directed = parse_graph(graph)
        nodes = _get_unique_nodes(edge_list)

        # Color and reorder edges for weighted graphs.
        if edge_weight:
            # If the graph is weighted, we want to visualise the weights using color.
            # Edge width is another popular choice when visualising weighted networks,
            # but if the variance in weights is large, this typically results in less
            # visually pleasing results.
            edge_color = get_color(edge_weight, cmap=edge_cmap)

            # Plotting darker edges over lighter edges typically results in visually
            # more pleasing results. Here we hence specify the relative order in
            # which edges are plotted according to the color of the edge.
            edge_zorder = _get_zorder(edge_color)
        else: # set to default
            edge_color = 'k'
            edge_zorder = 1

        # Plot arrows if the graph has bi-directional edges.
        if directed:
            arrows = True
        else:
            arrows = False

        super().__init__(edge_list, nodes, edge_color=edge_color,
                         edge_zorder=edge_zorder, arrows=arrows, *args, **kwargs)


class DraggableArtists(object):
    """
    Notes:
    ------
    Methods adapted with some modifications from:
    https://stackoverflow.com/questions/47293499/window-select-multiple-artists-and-drag-them-on-canvas/47312637#47312637
    """

    def __init__(self, artists):

        try:
            self.fig, = set(list(artist.figure for artist in artists))
        except ValueError:
            raise Exception("All artists have to be on the same figure!")

        try:
            self.ax, = set(list(artist.axes for artist in artists))
        except ValueError:
            raise Exception("All artists have to be on the same axis!")

        self.fig.canvas.mpl_connect('button_press_event',   self._on_press)
        self.fig.canvas.mpl_connect('button_release_event', self._on_release)
        self.fig.canvas.mpl_connect('motion_notify_event',  self._on_motion)
        self.fig.canvas.mpl_connect('key_press_event',      self._on_key_press)
        self.fig.canvas.mpl_connect('key_release_event',    self._on_key_release)

        self._draggable_artists = artists
        self._clicked_artist = None
        self._control_is_held = False
        self._currently_clicking_on_artist = False
        self._currently_dragging = False
        self._currently_selecting = False
        self._selected_artists = []
        self._offset = dict()
        self._base_alpha = dict([(artist, artist.get_alpha()) for artist in artists])

        self._rect = plt.Rectangle((0, 0), 1, 1, linestyle="--", edgecolor="crimson", fill=False)
        self.ax.add_patch(self._rect)
        self._rect.set_visible(False)

        self._x0 = 0
        self._y0 = 0
        self._x1 = 0
        self._y1 = 0

    def _on_press(self, event):

        if event.inaxes:

            # reset rectangle
            self._x0 = event.xdata
            self._y0 = event.ydata
            self._x1 = event.xdata
            self._y1 = event.ydata

            self._clicked_artist = None

            # is the press over some artist
            is_on_artist = False
            for artist in self._draggable_artists:

                if artist.contains(event)[0]:
                    if artist in self._selected_artists:
                        # print("Clicked on previously selected artist.")
                        # Some artists are already selected,
                        # and the user clicked on an already selected artist.
                        # It remains to be seen if the user wants to
                        # 1) start dragging, or
                        # 2) deselect everything else and select only the last selected artist.
                        # Hence we will defer decision until the user releases mouse button.
                        self._clicked_artist = artist

                    else:
                        # print("Clicked on new artist.")
                        # the user wants to select artist and drag
                        if not self._control_is_held:
                            self._deselect_all_artists()
                        self._select_artist(artist)

                    # prepare dragging
                    self._currently_clicking_on_artist = True
                    self._offset = {artist : artist.xy - np.array([event.xdata, event.ydata]) for artist in self._selected_artists}

                    # do not check anything else
                    # NOTE: if two artists are overlapping, only the first one encountered is selected!
                    break

            else:
                # print("Did not click on artist.")
                if not self._control_is_held:
                    self._deselect_all_artists()

                # start window select
                self._currently_selecting = True

        else:
            print("Warning: clicked outside axis limits!")


    def _on_release(self, event):

        if self._currently_selecting:

            # select artists inside window
            for artist in self._draggable_artists:
                if self._is_inside_rect(*artist.xy):
                    if self._control_is_held:               # if/else probably superfluouos
                        self._toggle_select_artist(artist)  # as no artists will be selected
                    else:                                   # if control is not held previously
                        self._select_artist(artist)         #

            # stop window selection and draw new state
            self._currently_selecting = False
            self._rect.set_visible(False)
            self.fig.canvas.draw_idle()

        elif self._currently_clicking_on_artist:

            if (self._clicked_artist is not None) & (self._currently_dragging is False):
                if self._control_is_held:
                    self._toggle_select_artist(self._clicked_artist)
                else:
                    self._deselect_all_artists()
                    self._select_artist(self._clicked_artist)

            self._currently_clicking_on_artist = False
            self._currently_dragging = False


    def _on_motion(self, event):
        if event.inaxes:
            if self._currently_clicking_on_artist:
                self._currently_dragging = True
                self._move(event)
            elif self._currently_selecting:
                self._x1 = event.xdata
                self._y1 = event.ydata
                # add rectangle for selection here
                self._selector_on()


    def _move(self, event):
        cursor_position = np.array([event.xdata, event.ydata])
        for artist in self._selected_artists:
            artist.xy = cursor_position + self._offset[artist]
        self.fig.canvas.draw_idle()


    def _on_key_press(self, event):
       if event.key == 'control':
           self._control_is_held = True


    def _on_key_release(self, event):
       if event.key == 'control':
           self._control_is_held = False


    def _is_inside_rect(self, x, y):
        xlim = np.sort([self._x0, self._x1])
        ylim = np.sort([self._y0, self._y1])
        if (xlim[0]<=x) and (x<xlim[1]) and (ylim[0]<=y) and (y<ylim[1]):
            return True
        else:
            return False


    def _toggle_select_artist(self, artist):
        if artist in self._selected_artists:
            self._deselect_artist(artist)
        else:
            self._select_artist(artist)


    def _select_artist(self, artist):
        if not (artist in self._selected_artists):
            alpha = artist.get_alpha()
            try:
                artist.set_alpha(0.5 * alpha)
            except TypeError: # alpha not explicitly set
                artist.set_alpha(0.5)
            self._selected_artists.append(artist)
            self.fig.canvas.draw_idle()


    def _deselect_artist(self, artist):
        if artist in self._selected_artists:
            artist.set_alpha(self._base_alpha[artist])
            self._selected_artists = [a for a in self._selected_artists if not (a is artist)]
            self.fig.canvas.draw_idle()


    def _deselect_all_artists(self):
        for artist in self._selected_artists:
            artist.set_alpha(self._base_alpha[artist])
        self._selected_artists = []
        self.fig.canvas.draw_idle()


    def _selector_on(self):
        self._rect.set_visible(True)
        xlim = np.sort([self._x0, self._x1])
        ylim = np.sort([self._y0, self._y1])
        self._rect.set_xy((xlim[0],ylim[0] ) )
        self._rect.set_width(np.diff(xlim))
        self._rect.set_height(np.diff(ylim))
        self.fig.canvas.draw_idle()


class DraggableGraph(Graph, DraggableArtists):

    def __init__(self, *args, **kwargs):
        Graph.__init__(self, *args, **kwargs)
        DraggableArtists.__init__(self, self.node_artists.values())

        self._node_to_draggable_artist = self.node_artists
        self._draggable_artist_to_node = dict(zip(self.node_artists.values(), self.node_artists.keys()))

        # # trigger resize of labels when canvas size changes
        # self.fig.canvas.mpl_connect('resize_event', self._on_resize)


    def _move(self, event):

        cursor_position = np.array([event.xdata, event.ydata])

        nodes = []
        for artist in self._selected_artists:
            node = self._draggable_artist_to_node[artist]
            self.node_positions[node] = cursor_position + self._offset[artist]
            nodes.append(node)

        self._update_subgraph_element_positions(nodes)


    def _update_subgraph_element_positions(self, nodes=None):
        """Determines stale nodes, edges, and labels (if any), and updates
        their positions or paths."""

        if nodes is None:
            nodes = self._get_stale_nodes()

        edges = self._get_stale_edges(nodes)

        self._update_node_positions(nodes)

        # In the interest of speed, we only compute the straight edge paths here.
        # We will re-compute curved edge paths only on mouse button release,
        # i.e. when the dragging motion has stopped.
        self._update_straight_edge_positions(edges)

        if hasattr(self, 'node_label_artists'):
            self._update_node_label_positions(nodes)

        if hasattr(self, 'edge_label_artists'):
            self._update_edge_label_positions(edges)

        # TODO: might want to force redraw after each individual update.
        self.fig.canvas.draw_idle()


    def _get_stale_nodes(self):
        return [self._draggable_artist_to_node[artist] for artist in self._selected_artists]


    def _get_stale_edges(self, nodes=None):
        if nodes is None:
            nodes = self._get_stale_nodes()
        return [(source, target) for (source, target) in self.edge_list if (source in nodes) or (target in nodes)]


    def _on_release(self, event):
        if self._currently_dragging:
            nodes = self._get_stale_nodes()
            edges = self._get_stale_edges(nodes)

            # subselect curved edges; all other edges are already handled
            edges = [edge for edge in edges if self.edge_artists[edge].curved]

            if edges: # i.e. non-empty list
                self._update_curved_edge_positions(edges)

                if hasattr(self, 'edge_label_artists'): # move edge labels
                    self._update_edge_label_positions(edges)

        super()._on_release(event)


    # def _on_resize(self, event):
    #     if hasattr(self, 'node_labels'):
    #         self.draw_node_labels(self.node_labels)
    #         # print("As node label font size was not explicitly set, automatically adjusted node label font size to {:.2f}.".format(self.node_label_font_size))


class EmphasizeOnHover(object):

    def __init__(self, artists):

        self.emphasizeable_artists = artists
        self.artist_to_alpha = {artist : artist.get_alpha() for artist in self.emphasizeable_artists}
        self.deemphasized_artists = []

        try:
            self.fig, = set(list(artist.figure for artist in artists))
        except ValueError:
            raise Exception("All artists have to be on the same figure!")

        try:
            self.ax, = set(list(artist.axes for artist in artists))
        except ValueError:
            raise Exception("All artists have to be on the same axis!")

        self.fig.canvas.mpl_connect("motion_notify_event", self._on_motion)


    def _on_motion(self, event):

        if event.inaxes == self.ax:
            # on artist
            selected_artist = None
            for artist in self.emphasizeable_artists:
                if artist.contains(event)[0]: # returns two arguments for some reason
                    selected_artist = artist
                    break

            if selected_artist:
                for artist in self.emphasizeable_artists:
                    if artist is not selected_artist:
                        artist.set_alpha(self.artist_to_alpha[artist]/5)
                        self.deemphasized_artists.append(artist)
                self.fig.canvas.draw_idle()

            # not on any artist
            if (selected_artist is None) and self.deemphasized_artists:
                for artist in self.deemphasized_artists:
                    artist.set_alpha(self.artist_to_alpha[artist])
                self.deemphasized_artists = []
                self.fig.canvas.draw_idle()


class EmphasizeOnHoverGraph(Graph, EmphasizeOnHover):

    def __init__(self, *args, **kwargs):
        Graph.__init__(self, *args, **kwargs)

        artists = list(self.node_artists.values()) + list(self.edge_artists.values())
        keys = list(self.node_artists.keys()) + list(self.edge_artists.keys())
        self.artist_to_key = dict(zip(artists, keys))
        EmphasizeOnHover.__init__(self, artists)


    def _on_motion(self, event):

        if event.inaxes == self.ax:

            # determine if the cursor is on an artist
            selected_artist = None
            for artist in self.emphasizeable_artists:
                if artist.contains(event)[0]: # returns bool, {} for some reason
                    selected_artist = artist
                    break

            if selected_artist:
                emphasized_artists = [selected_artist]

                if isinstance(selected_artist, NodeArtist):
                    node = self.artist_to_key[selected_artist]
                    edge_artists = [edge_artist for edge, edge_artist in self.edge_artists.items() if node in edge]
                    emphasized_artists.extend(edge_artists)

                elif isinstance(selected_artist, EdgeArtist):
                    edge = self.artist_to_key[selected_artist]
                    node_artists = [self.node_artists[node] for node in edge[:2]]
                    emphasized_artists.extend(node_artists)

                for artist in self.emphasizeable_artists:
                    if artist not in emphasized_artists:
                        artist.set_alpha(self.artist_to_alpha[artist]/5)
                        self.deemphasized_artists.append(artist)
                self.fig.canvas.draw_idle()

            # not on any artist
            if (selected_artist is None) and self.deemphasized_artists:
                for artist in self.deemphasized_artists:
                    artist.set_alpha(self.artist_to_alpha[artist])
                self.deemphasized_artists = []
                self.fig.canvas.draw_idle()


class AnnotateOnClick(object):

    def __init__(self, artist_to_data):

        self.artist_to_data = artist_to_data

        self.annotatable_artists = artist_to_data.keys()
        self.annotated_artists = set()
        self.artist_to_text_object = dict()

        try:
            self.fig, = set(list(artist.figure for artist in self.annotatable_artists))
        except ValueError:
            raise Exception("All artists have to be on the same figure!")

        try:
            self.ax, = set(list(artist.axes for artist in self.annotatable_artists))
        except ValueError:
            raise Exception("All artists have to be on the same axis!")

        self.fig.canvas.mpl_connect("button_release_event", self._on_release)


    def _on_release(self, event):

        if event.inaxes == self.ax:

            # clicked on already annotated artist
            for artist in self.annotated_artists:
                if artist.contains(event)[0]:
                    self._remove_annotation(artist)
                    self.fig.canvas.draw()
                    return

            # clicked on un-annotated artist
            for artist in self.annotatable_artists:
                if artist.contains(event)[0]:
                    placement = self._get_annotation_placement(artist)
                    self._add_annotation(artist, *placement)
                    self.fig.canvas.draw()
                    return

            # clicked outside of any artist
            for artist in list(self.annotated_artists): # list to force copy
                self._remove_annotation(artist)
            self.fig.canvas.draw()


    def _get_annotation_placement(self, artist):
        vector = self._get_vector_pointing_outwards(artist.xy)
        x, y = artist.xy + 2 * artist.radius * vector
        horizontalalignment, verticalalignment = self._get_text_alignment(vector)
        return x, y, horizontalalignment, verticalalignment


    def _add_annotation(self, artist, x, y, horizontalalignment, verticalalignment):

        if isinstance(self.artist_to_data[artist], str):
            self.artist_to_text_object[artist] = self.ax.text(
                x, y, self.artist_to_data[artist],
                horizontalalignment=horizontalalignment,
                verticalalignment=verticalalignment,
            )
        elif isinstance(self.artist_to_data[artist], dict):
            params = self.artist_to_data[artist].copy()
            params.setdefault('horizontalalignment', horizontalalignment)
            params.setdefault('verticalalignment', verticalalignment)
            self.artist_to_text_object[artist] = self.ax.text(
                x, y, **params
            )
        self.annotated_artists.add(artist)


    def _get_centroid(self):
        return np.mean([artist.xy for artist in self.annotatable_artists], axis=0)


    def _get_vector_pointing_outwards(self, xy):
        centroid = self._get_centroid()
        delta = xy - centroid
        distance = np.linalg.norm(delta)
        unit_vector = delta / distance
        return unit_vector


    def _get_text_alignment(self, vector):
        dx, dy = vector
        angle = _get_angle(dx, dy, radians=True) % 360

        if (45 <= angle < 135):
            horizontalalignment = 'center'
            verticalalignment = 'bottom'
        elif (135 <= angle < 225):
            horizontalalignment = 'right'
            verticalalignment = 'center'
        elif (225 <= angle < 315):
            horizontalalignment = 'center'
            verticalalignment = 'top'
        else:
            horizontalalignment = 'left'
            verticalalignment = 'center'

        return horizontalalignment, verticalalignment


    def _remove_annotation(self, artist):
        text_object = self.artist_to_text_object[artist]
        text_object.remove()
        del self.artist_to_text_object[artist]
        self.annotated_artists.discard(artist)


class AnnotateOnClickGraph(Graph, AnnotateOnClick):

    def __init__(self, *args, **kwargs):
        Graph.__init__(self, *args, **kwargs)

        artist_to_data = dict()
        if 'node_data' in kwargs:
            artist_to_data.update({self.node_artists[node] : data for node, data in kwargs['node_data'].items()})
        if 'edge_data' in kwargs:
            artist_to_data.update({self.edge_artists[edge] : data for edge, data in kwargs['edge_data'].items()})

        AnnotateOnClick.__init__(self, artist_to_data)


    def _get_centroid(self):
        return np.mean([position for position in self.node_positions.values()], axis=0)


    def _get_annotation_placement(self, artist):
        if isinstance(artist, NodeArtist):
            return self._get_node_annotation_placement(artist)
        elif isinstance(artist, EdgeArtist):
            return self._get_edge_annotation_placement(artist)
        else:
            raise NotImplementedError


    def _get_node_annotation_placement(self, artist):
        return super()._get_annotation_placement(artist)


    def _get_edge_annotation_placement(self, artist):
        midpoint = _get_point_along_spline(artist.midline, 0.5)

        tangent = _get_tangent_at_point(artist.midline, 0.5)
        orthogonal_vector = _get_orthogonal_unit_vector(np.atleast_2d(tangent)).ravel()
        vector_pointing_outwards = self._get_vector_pointing_outwards(midpoint)
        if _get_interior_angle_between(orthogonal_vector, vector_pointing_outwards, radians=True) > 90:
            orthogonal_vector *= -1

        x, y = midpoint + 2 * artist.width * orthogonal_vector
        horizontalalignment, verticalalignment = self._get_text_alignment(orthogonal_vector)
        return x, y, horizontalalignment, verticalalignment



class InteractiveGraph(DraggableGraph, EmphasizeOnHoverGraph, AnnotateOnClickGraph):

    def __init__(self, *args, **kwargs):

        DraggableGraph.__init__(self, *args, **kwargs)

        artists = list(self.node_artists.values()) + list(self.edge_artists.values())
        keys = list(self.node_artists.keys()) + list(self.edge_artists.keys())
        self.artist_to_key = dict(zip(artists, keys))
        EmphasizeOnHover.__init__(self, artists)

        artist_to_data = dict()
        if 'node_data' in kwargs:
            artist_to_data.update({self.node_artists[node] : data for node, data in kwargs['node_data'].items()})
        if 'edge_data' in kwargs:
            artist_to_data.update({self.edge_artists[edge] : data for edge, data in kwargs['edge_data'].items()})
        AnnotateOnClick.__init__(self, artist_to_data)


    def _on_motion(self, event):
        DraggableGraph._on_motion(self, event)
        EmphasizeOnHoverGraph._on_motion(self, event)


    def _on_release(self, event):
        if self._currently_dragging is False:
            DraggableGraph._on_release(self, event)
            AnnotateOnClickGraph._on_release(self, event)
        else:
            DraggableGraph._on_release(self, event)
            self._redraw_annotations(event)


    def _redraw_annotations(self, event):
        if event.inaxes == self.ax:
            for artist in self.annotated_artists:
                self._remove_annotation(artist)
                placement = self._get_annotation_placement(artist)
                self._add_annotation(artist, *placement)
            self.fig.canvas.draw()
