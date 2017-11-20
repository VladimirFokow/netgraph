#!/usr/bin/env python
# -*- coding: utf-8 -*-

# netgraph.py --- Plot weighted, directed graphs of medium size (10-100 nodes).

# Copyright (C) 2016 Paul Brodersen <paulbrodersen+netgraph@gmail.com>

# Author: Paul Brodersen <paulbrodersen+netgraph@gmail.com>

# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

"""
Netgraph
========

Summary:
--------
Module to plot weighted, directed graphs of medium size (10-100 nodes).
Unweighted, undirected graphs will look perfectly fine, too, but this module
might be overkill for such a use case.

Raison d'etre:
--------------
Existing draw routines for networks/graphs in python use fundamentally different
length units for different plot elements. This makes it hard to
    - provide a consistent layout for different axis / figure dimensions, and
    - judge the relative sizes of elements a priori.
This module amends these issues (while sacrificing speed).

Example:
--------
import numpy as np
import matplotlib.pyplot as plt
import netgraph

# construct sparse, directed, weighted graph
# with positive and negative edges
n = 20
w = np.random.randn(n,n)
p = 0.2
c = np.random.rand(n,n) <= p
w[~c] = 0.

# plot
netgraph.draw(w)
plt.show()
"""

__version__ = 0.0
__author__ = "Paul Brodersen"
__email__ = "paulbrodersen+netgraph@gmail.com"


import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from scipy.sparse import coo_matrix, spdiags


BASE_NODE_SIZE = 1e-2 # i.e. node sizes are in percent of axes space (x,y <- [0, 1], [0,1])
BASE_EDGE_WIDTH = 1e-2 # i.e. edge widths are in percent of axis space (x,y <- [0, 1], [0,1])


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

    """

    # Accept a variety of formats and convert to common denominator.
    edge_list, edge_weight = _parse_graph(graph)

    if edge_weight:

        # If the graph is weighted, we want to visualise the weights using color.
        # Edge width is another popular choice when visualising weighted networks,
        # but if the variance in weights is large, this typically results in less
        # visually pleasing results.
        edge_color  = _get_color(edge_weight, cmap=edge_cmap)
        kwargs.setdefault('edge_color',  edge_color)

        # Plotting darker edges over lighter edges typically results in visually
        # more pleasing results. Here we hence specify the relative order in
        # which edges are plotted according to the color of the edge.
        edge_zorder = _get_zorder(edge_color)
        kwargs.setdefault('edge_zorder', edge_zorder)

    # Plot arrows if the graph has bi-directional edges.
    if _is_directed(edge_list):
        kwargs.setdefault('draw_arrows', True)

    # Initialise node positions if none are given.
    if node_positions is None:
        node_positions = _fruchterman_reingold_layout(edge_list)

    # Create axis if none is given.
    if ax is None:
        ax = plt.gca()

    # Draw plot elements.
    draw_edges(edge_list, node_positions, ax=ax, **kwargs)
    draw_nodes(node_positions, ax=ax, **kwargs)

    if node_labels is not None:
        draw_node_labels(node_labels, node_positions, ax=ax, **kwargs)

    if edge_labels is not None:
        draw_edge_labels(edge_labels, node_positions, ax=ax, **kwargs)

    # Improve default layout of axis.
    _update_view(node_positions, node_size=3, ax=ax)
    _make_pretty(ax)

    return ax


def _parse_graph(graph):
    """
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

    Returns:
    --------
    edge_list: m-long list of 2-tuples
        List of edges. Each tuple corresponds to an edge defined by (source, target).

    edge_weights: dict (source, target) : float or None
        Edge weights. If the graph is unweighted, None is returned.

    """

    if isinstance(graph, (list, tuple, set)):
        return _parse_sparse_matrix_format(graph)

    elif isinstance(graph, np.ndarray):
        rows, columns = graph.shape
        if columns in (2, 3):
            return _parse_sparse_matrix_format(graph)
        else:
            return _parse_adjacency_matrix(graph)

    # this is terribly unsafe but we don't want to import igraph
    # unless we already know that we need it
    elif str(graph.__class__) == "<class 'igraph.Graph'>":
        return _parse_igraph_graph(graph)

    # ditto
    elif str(graph.__class__) in ("<class 'networkx.classes.graph.Graph'>",
                                  "<class 'networkx.classes.digraph.DiGraph'>",
                                  "<class 'networkx.classes.multigraph.MultiGraph'>",
                                  "<class 'networkx.classes.multidigraph.MultiDiGraph'>"):
        return _parse_networkx_graph(graph)

    else:
        allowed = ['list', 'tuple', 'set', 'networkx.Graph', 'igraph.Graph']
        raise NotImplementedError("Input graph must be one of: {}".format("\n\n\t" + "\n\t".join(allowed)))


def _parse_edge_list(edge_list):
    # Edge list may be an array, or a list of lists.
    # We want a list of tuples.
    return [(source, target) for (source, target) in edge_list]


def _parse_sparse_matrix_format(adjacency):
    adjacency = np.array(adjacency)
    rows, columns = adjacency.shape
    if columns == 2:
        return _parse_edge_list(adjacency), None
    elif columns == 3:
        edge_list = _parse_edge_list(adjacency[:,:2])
        edge_weights = {(source, target) : weight for (source, target, weight) in adjacency}

        if len(set(edge_weights.values())) > 1:
            return edge_list, edge_weights
        else:
            return edge_list, None
    else:
        raise ValueError("Graph specification in sparse matrix format needs to consist of an iterable of tuples of length 2 or 3. Got iterable of tuples of length {}.".format(columns))


def _parse_adjacency_matrix(adjacency):
    sources, targets = np.where(adjacency)
    edge_list = list(zip(sources.tolist(), targets.tolist()))
    edge_weights = {(source, target): adjacency[source, target] for (source, target) in edge_list}
    if len(set(list(edge_weights.values()))) == 1:
        return edge_list, None
    return edge_list, edge_weights


def _parse_networkx_graph(graph, attribute_name='weight'):
    edge_list = list(graph.edges())
    try:
        edge_weights = {edge : graph.get_edge_data(*edge)[attribute_name] for edge in edge_list}
    except KeyError: # no weights
        edge_weights = None
    return edge_list, edge_weights


def _parse_igraph_graph(graph):
    edge_list = [(edge.source, edge.target) for edge in graph.es()]
    if graph.is_weighted():
        edge_weights = {(edge.source, edge.target) : edge['weight'] for edge in graph.es()}
    else:
        edge_weights = None
    return edge_list, edge_weights


def _get_color(mydict, cmap='RdGy', vmin=None, vmax=None):

    keys = mydict.keys()
    values = mydict.values()

    # apply edge_vmin, edge_vmax
    if vmin:
        values[values<vmin] = vmin

    if vmax:
        values[values>vmax] = vmax

    def abs(value):
        try:
            return np.abs(value)
        except TypeError as e: # i is probably None
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
    mapper.set_clim(vmin, vmax)
    colors = mapper.to_rgba(values)

    return {key: color for (key, color) in zip(keys, colors)}


def _get_zorder(color_dict):
    # reorder plot elements such that darker items are plotted last
    # and hence most prominent in the graph
    zorder = np.argsort(np.sum(color_dict.values(), axis=1)) # assumes RGB specification
    zorder = np.max(zorder) - zorder # reverse order as greater values correspond to lighter colors
    zorder = {key: index for key, index in zip(color_dict.keys(), zorder)}
    return zorder


def _is_directed(edge_list):
    # test for bi-directional edges
    for source, target in edge_list:
        if (target, source) in edge_list:
            return True
    return False


def draw_nodes(node_positions,
               node_shape='o',
               node_size=3.,
               node_edge_width=0.5,
               node_color='w',
               node_edge_color='k',
               node_alpha=1.0,
               node_edge_alpha=1.0,
               ax=None,
               **kwargs):
    """
    Draw node markers at specified positions.

    Arguments
    ----------
    node_positions : dict node : (float, float)
        Mapping of nodes to (x, y) positions

    node_shape : string or dict key : string (default 'o')
       The shape of the node. Specification is as for matplotlib.scatter
       marker, i.e. one of 'so^>v<dph8'.
       If a single string is provided all nodes will have the same shape.

    node_size : scalar or dict node : float (default 3.)
       Size (radius) of nodes in percent of axes space.

    node_edge_width : scalar or dict key : float (default 0.5)
       Line width of node marker border.

    node_color : matplotlib color specification or dict node : color specification (default 'w')
       Node color.

    node_edge_color : matplotlib color specification or dict node : color specification (default 'k')
       Node edge color.

    node_alpha : scalar or dict node : float (default 1.)
       The node transparency.

    node_edge_alpha : scalar or dict node : float (default 1.)
       The node edge transparency.

    ax : matplotlib.axis instance or None (default None)
       Axis to plot onto; if none specified, one will be instantiated with plt.gca().

    Returns
    -------
    artists: dict node : dict str : artist
        Mapping of nodes to the node face artists and node edge artists,
        where both types are instances of matplotlib.patches.
        To access the node face of a node: artists[node]['face']
        To access the node edge of a node: artists[node]['edge']

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
    if isinstance(node_edge_alpha, (int, float)):
        node_edge_alpha = {node:node_edge_alpha for node in nodes}

    # rescale
    node_size       = {node: size  * BASE_NODE_SIZE for (node, size)  in node_size.items()}
    node_edge_width = {node: width * BASE_NODE_SIZE for (node, width) in node_edge_width.items()}

    artists = dict()
    for node in nodes:
        # create node edge artist:
        # simulate node edge by drawing a slightly larger node artist;
        # I wish there was a better way to do this,
        # but this seems to be the only way to guarantee constant proportions,
        # as linewidth argument in matplotlib.patches will not be proportional
        # to a given node radius
        node_edge_artist = _get_node_artist(shape=node_shape[node],
                                            position=node_positions[node],
                                            size=node_size[node],
                                            facecolor=node_edge_color[node],
                                            alpha=node_edge_alpha[node],
                                            zorder=2)

        # create node artist
        node_artist = _get_node_artist(shape=node_shape[node],
                                       position=node_positions[node],
                                       size=node_size[node] -node_edge_width[node],
                                       facecolor=node_color[node],
                                       alpha=node_alpha[node],
                                       zorder=2)

        # add artists to axis
        ax.add_artist(node_edge_artist)
        ax.add_artist(node_artist)

        # return handles to artists
        artists[node] = dict()
        artists[node]['edge'] = node_edge_artist
        artists[node]['face'] = node_artist

    return artists


def _get_node_artist(shape, position, size, facecolor, alpha, zorder=2):
    if shape == 'o': # circle
        artist = matplotlib.patches.Circle(xy=position,
                                           radius=size,
                                           facecolor=facecolor,
                                           alpha=alpha,
                                           linewidth=0.,
                                           zorder=zorder)
    elif shape == '^': # triangle up
        artist = matplotlib.patches.RegularPolygon(xy=position,
                                                   radius=size,
                                                   numVertices=3,
                                                   facecolor=facecolor,
                                                   alpha=alpha,
                                                   orientation=0,
                                                   linewidth=0.,
                                                   zorder=zorder)
    elif shape == '<': # triangle left
        artist = matplotlib.patches.RegularPolygon(xy=position,
                                                   radius=size,
                                                   numVertices=3,
                                                   facecolor=facecolor,
                                                   alpha=alpha,
                                                   orientation=np.pi*0.5,
                                                   linewidth=0.,
                                                   zorder=zorder)
    elif shape == 'v': # triangle down
        artist = matplotlib.patches.RegularPolygon(xy=position,
                                                   radius=size,
                                                   numVertices=3,
                                                   facecolor=facecolor,
                                                   alpha=alpha,
                                                   orientation=np.pi,
                                                   linewidth=0.,
                                                   zorder=zorder)
    elif shape == '>': # triangle right
        artist = matplotlib.patches.RegularPolygon(xy=position,
                                                   radius=size,
                                                   numVertices=3,
                                                   facecolor=facecolor,
                                                   alpha=alpha,
                                                   orientation=np.pi*1.5,
                                                   linewidth=0.,
                                                   zorder=zorder)
    elif shape == 's': # square
        artist = matplotlib.patches.RegularPolygon(xy=position,
                                                   radius=size,
                                                   numVertices=4,
                                                   facecolor=facecolor,
                                                   alpha=alpha,
                                                   orientation=np.pi*0.25,
                                                   linewidth=0.,
                                                   zorder=zorder)
    elif shape == 'd': # diamond
        artist = matplotlib.patches.RegularPolygon(xy=position,
                                                   radius=size,
                                                   numVertices=4,
                                                   facecolor=facecolor,
                                                   alpha=alpha,
                                                   orientation=np.pi*0.5,
                                                   linewidth=0.,
                                                   zorder=zorder)
    elif shape == 'p': # pentagon
        artist = matplotlib.patches.RegularPolygon(xy=position,
                                                   radius=size,
                                                   numVertices=5,
                                                   facecolor=facecolor,
                                                   alpha=alpha,
                                                   linewidth=0.,
                                                   zorder=zorder)
    elif shape == 'h': # hexagon
        artist = matplotlib.patches.RegularPolygon(xy=position,
                                                   radius=size,
                                                   numVertices=6,
                                                   facecolor=facecolor,
                                                   alpha=alpha,
                                                   linewidth=0.,
                                                   zorder=zorder)
    elif shape == 8: # octagon
        artist = matplotlib.patches.RegularPolygon(xy=position,
                                                   radius=size,
                                                   numVertices=8,
                                                   facecolor=facecolor,
                                                   alpha=alpha,
                                                   linewidth=0.,
                                                   zorder=zorder)
    else:
        raise ValueError("Node shape one of: ''so^>v<dph8'. Current shape:{}".format(shape))

    return artist


def draw_edges(edge_list,
               node_positions,
               node_size=3.,
               edge_width=1.,
               edge_color='k',
               edge_alpha=1.,
               edge_zorder=None,
               draw_arrows=True,
               ax=None,
               **kwargs):
    """

    Draw the edges of the network.

    Arguments
    ----------
    edge_list: m-long iterable of 2-tuples or equivalent (such as (m, 2) ndarray)
        List of edges. Each tuple corresponds to an edge defined by (source, target).

    node_positions : dict key : (float, float)
        Mapping of nodes to (x,y) positions

    node_size : scalar or (n,) or dict key : float (default 3.)
        Size (radius) of nodes in percent of axes space.
        Used to offset edges when drawing arrow heads,
        such that the arrow heads are not occluded.
        If draw_nodes() and draw_edges() are called independently,
        make sure to set this variable to the same value.

    edge_width : float or dict (source, key) : width (default 1.)
        Line width of edges.

    edge_color : matplotlib color specification or
                 dict (source, target) : color specification (default 'k')
       Edge color.

    edge_alpha : float or dict (source, target) : float (default 1.)
        The edge transparency,

    edge_zorder: int or dict (source, target) : int (default None)
        Order in which to plot the edges.
        If None, the edges will be plotted in the order they appear in 'adjacency'.
        Note: graphs typically appear more visually pleasing if darker coloured edges
        are plotted on top of lighter coloured edges.

    draw_arrows : bool, optional (default True)
        If True, draws edges with arrow heads.

    ax : matplotlib.axis instance or None (default None)
       Axis to plot onto; if none specified, one will be instantiated with plt.gca().

    Returns
    -------
    artists: dict (source, target) : artist
        Mapping of edges to matplotlib.patches.FancyArrow artists.

    """

    if ax is None:
        ax = plt.gca()

    edge_list = _parse_edge_list(edge_list)
    nodes = node_positions.keys()
    number_of_nodes = len(nodes)

    if isinstance(node_size, (int, float)):
        node_size = {node:node_size for node in nodes}
    if isinstance(edge_width, (int, float)):
        edge_width = {edge: edge_width for edge in edge_list}
    if not isinstance(edge_color, dict):
        edge_color = {edge: edge_color for edge in edge_list}
    if isinstance(edge_alpha, (int, float)):
        edge_alpha = {edge: edge_alpha for edge in edge_list}

    # rescale
    node_size  = {node: size  * BASE_NODE_SIZE  for (node, size)  in node_size.items()}
    edge_width = {edge: width * BASE_EDGE_WIDTH for (edge, width) in edge_width.items()}

    # order edges if necessary
    if not (edge_zorder is None):
       edge_list = sorted(edge_zorder, key=lambda k: edge_zorder[k])

    # NOTE: At the moment, only the relative zorder is honoured, not the absolute value.

    artists = dict()
    for (source, target) in edge_list:

        x1, y1 = node_positions[source]
        x2, y2 = node_positions[target]

        dx = x2-x1
        dy = y2-y1

        width = edge_width[(source, target)]
        color = edge_color[(source, target)]
        alpha = edge_alpha[(source, target)]

        bidirectional = (target, source) in edge_list

        if draw_arrows and bidirectional:
            # shift edge to the right (looking along the arrow)
            x1, y1, x2, y2 = _shift_edge(x1, y1, x2, y2, delta=0.5*width)
            # plot half arrow
            patch = _arrow(ax,
                           x1, y1, dx, dy,
                           offset=node_size[target],
                           facecolor=color,
                           width=width,
                           head_length=2*width,
                           head_width=3*width,
                           length_includes_head=True,
                           zorder=1,
                           edgecolor='none',
                           linewidth=0.1,
                           shape='right',
                           )

        elif draw_arrows and not bidirectional:
            # don't shift edge, plot full arrow
            patch = _arrow(ax,
                           x1, y1, dx, dy,
                           offset=node_size[target],
                           facecolor=color,
                           width=width,
                           head_length=2*width,
                           head_width=3*width,
                           length_includes_head=True,
                           edgecolor='none',
                           linewidth=0.1,
                           zorder=1,
                           shape='full',
                           )

        elif not draw_arrows and bidirectional:
            # shift edge to the right (looking along the line)
            x1, y1, x2, y2 = _shift_edge(x1, y1, x2, y2, delta=0.5*width)
            patch = _line(ax,
                          x1, y1, dx, dy,
                          facecolor=color,
                          width=width,
                          head_length=1e-10, # 0 throws error
                          head_width=1e-10, # 0 throws error
                          length_includes_head=False,
                          edgecolor='none',
                          linewidth=0.1,
                          zorder=1,
                          shape='right',
                          )
        else:
            patch = _line(ax,
                          x1, y1, dx, dy,
                          facecolor=color,
                          width=width,
                          head_length=1e-10, # 0 throws error
                          head_width=1e-10, # 0 throws error
                          length_includes_head=False,
                          edgecolor='none',
                          linewidth=0.1,
                          zorder=1,
                          shape='full',
                          )

        ax.add_artist(patch)
        artists[(source, target)] = patch

    return artists


def _shift_edge(x1, y1, x2, y2, delta):
    # get orthogonal unit vector
    v = np.r_[x2-x1, y2-y1] # original
    v = np.r_[-v[1], v[0]] # orthogonal
    v = v / np.linalg.norm(v) # unit
    dx, dy = delta * v
    return x1+dx, y1+dy, x2+dx, y2+dy


def _arrow(ax, x1, y1, dx, dy, offset, **kwargs):
    # offset to prevent occlusion of head from nodes
    r = np.sqrt(dx**2 + dy**2)
    dx *= (r-offset)/r
    dy *= (r-offset)/r
    return _line(ax, x1, y1, dx, dy, **kwargs)


def _line(ax, x1, y1, dx, dy, **kwargs):
    # use FancyArrow instead of e.g. LineCollection to ensure consistent scaling across elements;
    # return matplotlib.patches.FancyArrow(x1, y1, dx, dy, **kwargs)
    return FancyArrow(x1, y1, dx, dy, **kwargs)


# This is a copy of matplotlib.patches.FancyArrow.
# They messed up in matplotlib version 2.0.0.
# For shape="full" coords in 2.0.0 are
# coords = np.concatenate([left_half_arrow[:-1], right_half_arrow[-2::-1]])
# when they should be:
# coords = np.concatenate([left_half_arrow[:-1], right_half_arrow[-1::-1]])
# TODO: Remove copy when they fix it, and matplotlib 2.0.0 is unlikely to be very prevalent any more.
# At time of writing, still the default version for Ubuntu 16.04 LTS
from matplotlib.patches import Polygon
class FancyArrow(Polygon):
    """
    Like Arrow, but lets you set head width and head height independently.
    """

    _edge_default = True

    def __str__(self):
        return "FancyArrow()"

    # @docstring.dedent_interpd
    def __init__(self, x, y, dx, dy, width=0.001, length_includes_head=False,
                 head_width=None, head_length=None, shape='full', overhang=0,
                 head_starts_at_zero=False, **kwargs):
        """
        Constructor arguments
          *width*: float (default: 0.001)
            width of full arrow tail

          *length_includes_head*: [True | False] (default: False)
            True if head is to be counted in calculating the length.

          *head_width*: float or None (default: 3*width)
            total width of the full arrow head

          *head_length*: float or None (default: 1.5 * head_width)
            length of arrow head

          *shape*: ['full', 'left', 'right'] (default: 'full')
            draw the left-half, right-half, or full arrow

          *overhang*: float (default: 0)
            fraction that the arrow is swept back (0 overhang means
            triangular shape). Can be negative or greater than one.

          *head_starts_at_zero*: [True | False] (default: False)
            if True, the head starts being drawn at coordinate 0
            instead of ending at coordinate 0.

        Other valid kwargs (inherited from :class:`Patch`) are:
        %(Patch)s

        """
        if head_width is None:
            head_width = 3 * width
        if head_length is None:
            head_length = 1.5 * head_width

        distance = np.hypot(dx, dy)

        if length_includes_head:
            length = distance
        else:
            length = distance + head_length
        if not length:
            verts = []  # display nothing if empty
        else:
            # start by drawing horizontal arrow, point at (0,0)
            hw, hl, hs, lw = head_width, head_length, overhang, width
            left_half_arrow = np.array([
                [0.0, 0.0],                  # tip
                [-hl, -hw / 2.0],             # leftmost
                [-hl * (1 - hs), -lw / 2.0],  # meets stem
                [-length, -lw / 2.0],          # bottom left
                [-length, 0],
            ])
            # if we're not including the head, shift up by head length
            if not length_includes_head:
                left_half_arrow += [head_length, 0]
            # if the head starts at 0, shift up by another head length
            if head_starts_at_zero:
                left_half_arrow += [head_length / 2.0, 0]
            # figure out the shape, and complete accordingly
            if shape == 'left':
                coords = left_half_arrow
            else:
                right_half_arrow = left_half_arrow * [1, -1]
                if shape == 'right':
                    coords = right_half_arrow
                elif shape == 'full':
                    # The half-arrows contain the midpoint of the stem,
                    # which we can omit from the full arrow. Including it
                    # twice caused a problem with xpdf.
                    coords = np.concatenate([left_half_arrow[:-1],
                                             right_half_arrow[-1::-1]])
                else:
                    raise ValueError("Got unknown shape: %s" % shape)
            if distance != 0:
                cx = float(dx) / distance
                sx = float(dy) / distance
            else:
                #Account for division by zero
                cx, sx = 0, 1
            M = np.array([[cx, sx], [-sx, cx]])
            verts = np.dot(coords, M) + (x + dx, y + dy)

        Polygon.__init__(self, list(map(tuple, verts)), closed=True, **kwargs)


def draw_node_labels(node_labels,
                     node_positions,
                     font_size=8,
                     font_color='k',
                     font_family='sans-serif',
                     font_weight='normal',
                     font_alpha=1.,
                     bbox=None,
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

    font_size : int (default 12)
       Font size for text labels

    font_color : str (default 'k')
       Font color string

    font_family : str (default='sans-serif')
       Font family

    font_weight : str (default='normal')
       Font weight

    font_alpha : float (default 1.)
       Text transparency

    bbox : matplotlib bbox instance
       Specify text box shape and colors.

    clip_on : bool
       Turn on clipping at axis boundaries (default False)

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

    # set optional alignment
    horizontalalignment = kwargs.get('horizontalalignment', 'center')
    verticalalignment = kwargs.get('verticalalignment', 'center')

    artists = dict()  # there is no text collection so we'll fake one
    for node, label in node_labels.items():
        x, y = node_positions[node]
        text_object = ax.text(x, y,
                              label,
                              size=font_size,
                              color=font_color,
                              alpha=font_alpha,
                              family=font_family,
                              weight=font_weight,
                              horizontalalignment=horizontalalignment,
                              verticalalignment=verticalalignment,
                              transform=ax.transData,
                              bbox=bbox,
                              clip_on=False)
        artists[node] = text_object

    return artists


def draw_edge_labels(edge_labels,
                     node_positions,
                     font_size=10,
                     font_color='k',
                     font_family='sans-serif',
                     font_weight='normal',
                     font_alpha=1.,
                     bbox=None,
                     clip_on=False,
                     ax=None,
                     rotate=True,
                     edge_label_zorder=10000,
                     **kwargs):
    """
    Draw edge labels.

    Arguments
    ---------

    node_positions : dict node : (float, float)
        Mapping of nodes to (x, y) positions

    edge_labels : dict (source, target) : str
        Mapping of edges to edge labels.
        Only edges in the dictionary are labelled.

    font_size : int (default 12)
       Font size

    font_color : str (default 'k')
       Font color

    font_family : str (default='sans-serif')
       Font family

    font_weight : str (default='normal')
       Font weight

    font_alpha : float (default 1.)
       Text transparency

    bbox : Matplotlib bbox
       Specify text box shape and colors.

    clip_on : bool
       Turn on clipping at axis boundaries (default=True)

    edge_label_zorder : int (default 10000)
        Set the zorder of edge labels.
        Choose a large number to ensure that the labels are plotted on top of the edges.

    ax : matplotlib.axis instance or None (default None)
       Axis to plot onto; if none specified, one will be instantiated with plt.gca().

    Returns
    -------
    artists: dict (source, target) : text object
        Mapping of edges to edge label artists.

    @reference
    Borrowed with minor modifications from networkx/drawing/nx_pylab.py

    """

    # draw labels centered on the midway point of the edge
    label_pos = 0.5

    if ax is None:
        ax = plt.gca()

    text_items = {}
    for (n1, n2), label in edge_labels.items():
        (x1, y1) = node_positions[n1]
        (x2, y2) = node_positions[n2]
        (x, y) = (x1 * label_pos + x2 * (1.0 - label_pos),
                  y1 * label_pos + y2 * (1.0 - label_pos))

        if rotate:
            angle = np.arctan2(y2-y1, x2-x1)/(2.0*np.pi)*360  # degrees
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

        if bbox is None: # use default box of white with white border
            bbox = dict(boxstyle='round',
                        ec=(1.0, 1.0, 1.0),
                        fc=(1.0, 1.0, 1.0),
                        )

        # set optional alignment
        horizontalalignment = kwargs.get('horizontalalignment', 'center')
        verticalalignment = kwargs.get('verticalalignment', 'center')

        t = ax.text(x, y,
                    label,
                    size=font_size,
                    color=font_color,
                    family=font_family,
                    weight=font_weight,
                    horizontalalignment=horizontalalignment,
                    verticalalignment=verticalalignment,
                    rotation=trans_angle,
                    transform=ax.transData,
                    bbox=bbox,
                    zorder=edge_label_zorder,
                    clip_on=True,
                    )

        text_items[(n1, n2)] = t

    return text_items


def _update_view(node_positions, node_size, ax):
    # Pad x and y limits as patches are not registered properly
    # when matplotlib sets axis limits automatically.
    # Hence we need to set them manually.

    if isinstance(node_size, dict):
        maxs = np.max(node_size.values()) * BASE_NODE_SIZE
    else:
        maxs = node_size * BASE_NODE_SIZE

    maxx, maxy = np.max(node_positions.values(), axis=0)
    minx, miny = np.min(node_positions.values(), axis=0)

    w = maxx-minx
    h = maxy-miny
    padx, pady = 0.05*w + maxs, 0.05*h + maxs
    corners = (minx-padx, miny-pady), (maxx+padx, maxy+pady)

    ax.update_datalim(corners)
    ax.autoscale_view()
    ax.get_figure().canvas.draw()


def _make_pretty(ax):
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_aspect('equal')
    ax.get_figure().set_facecolor('w')
    ax.set_frame_on(False)
    ax.get_figure().canvas.draw()


# --------------------------------------------------------------------------------
# Spring layout


def _fruchterman_reingold_layout(edge_list,
                                 edge_weights=None,
                                 k=None,
                                 pos=None,
                                 fixed=None,
                                 iterations=50,
                                 scale=1,
                                 center=np.zeros((2)),
                                 dim=2):
    """
    Position nodes using Fruchterman-Reingold force-directed algorithm.

    Parameters
    ----------
    edge_list: m-long iterable of 2-tuples or equivalent (such as (m, 2) ndarray)
        List of edges. Each tuple corresponds to an edge defined by (source, target).

    edge_weights: dict (source, target) : float or None (default=None)
        Edge weights.

    k : float (default=None)
        Optimal distance between nodes.  If None the distance is set to
        1/sqrt(n) where n is the number of nodes.  Increase this value
        to move nodes farther apart.

    pos : dict or None  optional (default=None)
        Initial positions for nodes as a dictionary with node as keys
        and values as a coordinate list or tuple.  If None, then use
        random initial positions.

    fixed : list or None  optional (default=None)
        Nodes to keep fixed at initial position.

    iterations : int  optional (default=50)
        Number of iterations of spring-force relaxation

    scale : number (default: 1)
        Scale factor for positions. Only used if `fixed is None`.

    center : array-like or None
        Coordinate pair around which to center the layout.
        Only used if `fixed is None`.

    dim : int
        Dimension of layout.

    Returns
    -------
    pos : dict
        A dictionary of positions keyed by node

    Notes:
    ------
    Implementation taken with minor modifications from networkx.spring_layout().

    """

    nodes = np.unique(edge_list)
    total_nodes = len(nodes)

    # translate fixed node ID to position in node list
    if fixed is not None:
        node_to_idx = dict(zip(nodes, range(total_nodes)))
        fixed = np.asarray([node_to_idx[v] for v in fixed])

    if pos is not None:
        # Determine size of existing domain to adjust initial positions
        domain_size = max(coord for pos_tup in pos.values() for coord in pos_tup)
        if domain_size == 0:
            domain_size = 1
        shape = (total_nodes, dim)
        pos_arr = np.random.random(shape) * domain_size + center
        for i, n in enumerate(nodes):
            if n in pos:
                pos_arr[i] = np.asarray(pos[n])
    else:
        pos_arr = None

    if k is None and fixed is not None:
        # We must adjust k by domain size for layouts not near 1x1
        k = domain_size / np.sqrt(total_nodes)

    A = _edge_list_to_sparse_matrix(edge_list, edge_weights)

    if total_nodes > 500:  # sparse solver for large graphs
        pos = _sparse_fruchterman_reingold(A, k, pos_arr, fixed, iterations, dim)
    else:
        pos = _dense_fruchterman_reingold(A.toarray(), k, pos_arr, fixed, iterations, dim)

    if fixed is None:
        pos = _rescale_layout(pos, scale=scale) + center

    return dict(zip(nodes, pos))


def _dense_fruchterman_reingold(A, k=None, pos=None, fixed=None,
                                iterations=50, dim=2):
    """
    Position nodes in adjacency matrix A using Fruchterman-Reingold
    """

    nnodes, _ = A.shape

    if pos is None:
        # random initial positions
        pos = np.asarray(np.random.random((nnodes, dim)), dtype=A.dtype)
    else:
        # make sure positions are of same type as matrix
        pos = pos.astype(A.dtype)

    # optimal distance between nodes
    if k is None:
        k = np.sqrt(1.0/nnodes)
    # the initial "temperature"  is about .1 of domain area (=1x1)
    # this is the largest step allowed in the dynamics.
    # We need to calculate this in case our fixed positions force our domain
    # to be much bigger than 1x1
    t = max(max(pos.T[0]) - min(pos.T[0]), max(pos.T[1]) - min(pos.T[1]))*0.1
    # simple cooling scheme.
    # linearly step down by dt on each iteration so last iteration is size dt.
    dt = t/float(iterations+1)
    delta = np.zeros((pos.shape[0], pos.shape[0], pos.shape[1]), dtype=A.dtype)
    # the inscrutable (but fast) version
    # this is still O(V^2)
    # could use multilevel methods to speed this up significantly
    for iteration in range(iterations):
        # matrix of difference between points
        delta = pos[:, np.newaxis, :] - pos[np.newaxis, :, :]
        # distance between points
        distance = np.linalg.norm(delta, axis=-1)
        # enforce minimum distance of 0.01
        np.clip(distance, 0.01, None, out=distance)
        # displacement "force"
        displacement = np.einsum('ijk,ij->ik',
                                 delta,
                                 (k * k / distance**2 - A * distance / k))
        # update positions
        length = np.linalg.norm(displacement, axis=-1)
        length = np.where(length < 0.01, 0.1, length)
        delta_pos = np.einsum('ij,i->ij', displacement, t / length)
        if fixed is not None:
            # don't change positions of fixed nodes
            delta_pos[fixed] = 0.0
        pos += delta_pos
        # cool temperature
        t -= dt
    return pos


def _sparse_fruchterman_reingold(A, k=None, pos=None, fixed=None,
                                 iterations=50, dim=2):
    # Position nodes in adjacency matrix A using Fruchterman-Reingold
    # Entry point for NetworkX graph is fruchterman_reingold_layout()
    # Sparse version

    nnodes, _ = A.shape

    # make sure we have a list of lists representation
    try:
        A = A.tolil()
    except:
        A = (coo_matrix(A)).tolil()

    if pos is None:
        # random initial positions
        pos = np.asarray(np.random.random((nnodes, dim)), dtype=A.dtype)
    else:
        # make sure positions are of same type as matrix
        pos = pos.astype(A.dtype)

    # no fixed nodes
    if fixed is None:
        fixed = []

    # optimal distance between nodes
    if k is None:
        k = np.sqrt(1.0/nnodes)
    # the initial "temperature"  is about .1 of domain area (=1x1)
    # this is the largest step allowed in the dynamics.
    t = 0.1
    # simple cooling scheme.
    # linearly step down by dt on each iteration so last iteration is size dt.
    dt = t / float(iterations+1)

    displacement = np.zeros((dim, nnodes))
    for iteration in range(iterations):
        displacement *= 0
        # loop over rows
        for i in range(A.shape[0]):
            if i in fixed:
                continue
            # difference between this row's node position and all others
            delta = (pos[i] - pos).T
            # distance between points
            distance = np.sqrt((delta**2).sum(axis=0))
            # enforce minimum distance of 0.01
            distance = np.where(distance < 0.01, 0.01, distance)
            # the adjacency matrix row
            Ai = np.asarray(A.getrowview(i).toarray())
            # displacement "force"
            displacement[:, i] +=\
                (delta * (k * k / distance**2 - Ai * distance / k)).sum(axis=1)
        # update positions
        length = np.sqrt((displacement**2).sum(axis=0))
        length = np.where(length < 0.01, 0.1, length)
        pos += (displacement * t / length).T
        # cool temperature
        t -= dt
    return pos


def _rescale_layout(pos, scale=1):
    """Return scaled position array to (-scale, scale) in all axes.

    The function acts on NumPy arrays which hold position information.
    Each position is one row of the array. The dimension of the space
    equals the number of columns. Each coordinate in one column.

    To rescale, the mean (center) is subtracted from each axis separately.
    Then all values are scaled so that the largest magnitude value
    from all axes equals `scale` (thus, the aspect ratio is preserved).
    The resulting NumPy Array is returned (order of rows unchanged).

    Parameters
    ----------
    pos : numpy array
        positions to be scaled. Each row is a position.

    scale : number (default: 1)
        The size of the resulting extent in all directions.

    Returns
    -------
    pos : numpy array
        scaled positions. Each row is a position.

    """
    # Find max length over all dimensions
    lim = 0  # max coordinate for all axes
    for i in range(pos.shape[1]):
        pos[:, i] -= pos[:, i].mean()
        lim = max(abs(pos[:, i]).max(), lim)
    # rescale to (-scale, scale) in all directions, preserves aspect
    if lim > 0:
        for i in range(pos.shape[1]):
            pos[:, i] *= scale / lim
    return pos


def _edge_list_to_sparse_matrix(edge_list, edge_weights=None):

    nodes = np.unique(edge_list)
    node_to_idx = dict(zip(nodes, range(len(nodes))))
    sources = [node_to_idx[source] for source, _ in edge_list]
    targets = [node_to_idx[target] for _, target in edge_list]

    total_nodes = len(nodes)
    shape = (total_nodes, total_nodes)

    if edge_weights:
        weights = [edge_weights[edge] for edge in edge_list]
        arr = coo_matrix((weights, (sources, targets)), shape=shape)
    else:
        arr = coo_matrix((np.ones((len(sources))), (sources, targets)), shape=shape)

    return arr


# --------------------------------------------------------------------------------
# Test code


def _get_random_weight_matrix(n, p,
                              weighted=True,
                              strictly_positive=False,
                              directed=True,
                              fully_bidirectional=False,
                              dales_law=False):

    if weighted:
        w = np.random.randn(n, n)
    else:
        w = np.ones((n, n))

    if strictly_positive:
        w = np.abs(w)

    if not directed:
        w = np.triu(w)

    if directed and fully_bidirectional:
        c = np.random.rand(n, n) <= p/2
        c = np.logical_or(c, c.T)
    else:
        c = np.random.rand(n, n) <= p
    w[~c] = 0.

    if dales_law and weighted and not strictly_positive:
        w = np.abs(w) * np.sign(np.random.randn(n))[:,None]

    return w


def test(n=20, p=0.15, directed=True, weighted=True, test_format='sparse', ax=None):
    adjacency_matrix = _get_random_weight_matrix(n, p, directed=directed, weighted=weighted)

    sources, targets = np.where(adjacency_matrix)
    weights = adjacency_matrix[sources, targets]
    adjacency = np.c_[sources, targets, weights]

    node_labels = {node: str(node) for node in np.unique(adjacency[:,:2])}
    edge_labels = {(edge[0], edge[1]): str(ii) for ii, edge in enumerate(adjacency)}

    if test_format == "sparse":
        ax = draw(adjacency, node_labels=node_labels, edge_labels=edge_labels, ax=ax)
    elif test_format == "dense":
        ax = draw(adjacency_matrix, node_labels=node_labels, edge_labels=edge_labels, ax=ax)
    elif test_format == "networkx":
        import networkx
        graph = networkx.from_numpy_array(adjacency_matrix, networkx.DiGraph)
        ax = draw(graph, node_labels=node_labels, edge_labels=edge_labels, ax=ax)
    elif test_format == "igraph":
        import igraph
        graph = igraph.Graph.Weighted_Adjacency(adjacency_matrix.tolist())
        ax = draw(graph, node_labels=node_labels, edge_labels=edge_labels, ax=ax)

    return ax


if __name__ == "__main__":

    fig, (ax1, ax2) = plt.subplots(1,2)
    test(directed=True,  ax=ax1)
    test(directed=False, ax=ax2)
    ax1.set_title('Directed')
    ax2.set_title('Undirected')

    fig, (ax1, ax2) = plt.subplots(1,2)
    test(weighted=True,  ax=ax1)
    test(weighted=False, ax=ax2)
    ax1.set_title('Weighted')
    ax2.set_title('Unweighted')

    fig, (ax1, ax2) = plt.subplots(1,2)
    test(test_format="sparse", ax=ax1)
    test(test_format="dense", ax=ax2)
    ax1.set_title('Sparse matrix')
    ax2.set_title('Full-rank adjacency matrix')

    fig, (ax1, ax2) = plt.subplots(1,2)
    test(test_format="networkx", ax=ax1)
    test(test_format="igraph", ax=ax2)
    ax1.set_title('Networkx DiGraph')
    ax2.set_title('Igraph Graph')

    plt.ion(); plt.show()
    raw_input("Press any key to close figures...")
    plt.close()
