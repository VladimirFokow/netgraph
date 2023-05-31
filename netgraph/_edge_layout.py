#!/usr/bin/env python
# coding: utf-8

"""
Edge routing routines.
"""

import itertools
import warnings
import numpy as np

from uuid import uuid4
from functools import wraps
from scipy.interpolate import UnivariateSpline
from matplotlib.patches import ConnectionStyle

from ._utils import (
    _bspline,
    _get_n_points_on_a_circle,
    _get_angle,
    _get_unit_vector,
    _edge_list_to_adjacency_list,
    _edge_list_to_adjacency_matrix,
    _get_connected_components,
    _get_orthogonal_unit_vector,
    _normalize_numeric_argument,
    _resample_spline,
    _get_optimal_offsets,
)

from ._node_layout import (
    get_fruchterman_reingold_layout,
    _get_temperature_decay,
    _is_within_bbox,
    _rescale_to_frame,
    _get_fr_attraction,
    _clip_to_frame,
)


# for profiling with kernprof/line_profiler
try:
    profile
except NameError:
    profile = lambda x: x


def _handle_multiple_components(layout_function):
    """If the graph contains multiple components, apply the given layout to each component individually."""
    @wraps(layout_function)
    def wrapped_layout_function(edges, node_positions=None, *args, **kwargs):
        adjacency_list = _edge_list_to_adjacency_list(edges, directed=False)
        components = _get_connected_components(adjacency_list)

        if len(components) > 1:
            return _get_layout_for_multiple_components(edges, node_positions, components, layout_function, *args, **kwargs)
        else:
            return layout_function(edges, node_positions, *args, **kwargs)

    return wrapped_layout_function


def _get_layout_for_multiple_components(edges, node_positions, components, layout_function, *args, **kwargs):
    """Partition network into given components and apply the given layout to each component individually."""
    edge_paths = dict()
    for component in components:
        component_edges = [(source, target) for (source, target) in edges if (source in component) and (target in component)]
        component_node_positions = {node : xy for node, xy in node_positions.items() if node in component}
        component_edge_paths = layout_function(component_edges, component_node_positions, *args, **kwargs)
        edge_paths.update(component_edge_paths)
    return edge_paths


def get_straight_edge_paths(edges, node_positions, selfloop_radius=0.1, selfloop_angle=None):
    """Edge routing using straight lines.

    Computes the edge paths, such that edges are represented by
    straight lines connecting the source and target node.

    Parameters
    ----------
    edges : list
        The edges of the graph, with each edge being represented by a (source node ID, target node ID) tuple.
    node_positions : dict
        Dictionary mapping each node ID to (float x, float y) tuple, the node position.
    selfloop_radius : dict or float, default 0.1
        Dictionary mapping each self-loop edge to a radius. If float, all self-loops have the same radius.
    selfloop_angle : dict, float, or None
        The starting angle of the self-loop in radians.
        If None, the angle is adjusted to minimize collisions with other nodes and edges.

    Returns
    -------
    edge_paths : dict
        Dictionary mapping each edge to an array of (x, y) coordinates representing its path.

    """

    edge_paths = dict()

    nonloops = [(source, target) for (source, target) in edges if source != target]
    nonloop_edge_paths = _get_straight_nonloop_edge_paths(nonloops, node_positions)
    edge_paths.update(nonloop_edge_paths)

    selfloops = [(source, target) for (source, target) in edges if source == target]
    if selfloops:
        selfloop_radius = _normalize_numeric_argument(selfloop_radius, selfloops, 'selfloop_radius')
        if selfloop_angle is not None: # can be zero!
            selfloop_angle = _normalize_numeric_argument(selfloop_angle, selfloops, 'angle', allow_none=True)
        else:
            selfloop_angle = _get_optimal_selfloop_angles(
                selfloops, selfloop_radius, node_positions, nonloop_edge_paths)
        selfloop_edge_paths = _get_straight_selfloop_edge_paths(
            selfloops, node_positions, selfloop_radius, selfloop_angle)
        edge_paths.update(selfloop_edge_paths)

    return edge_paths


def _get_straight_nonloop_edge_paths(edges, node_positions):
    edge_paths = dict()
    for (source, target) in edges:
        x1, y1 = node_positions[source]
        x2, y2 = node_positions[target]
        edge_paths[(source, target)] = np.c_[[x1, x2], [y1, y2]]
    return edge_paths


def _get_optimal_selfloop_angles(selfloops, selfloop_radius, node_positions, edge_paths, total_samples_per_edge=100):
    anchors = np.array([node_positions[source] for source, _ in selfloops])
    offsets = np.array([selfloop_radius[edge] for edge in selfloops])
    avoid = np.concatenate([_resample_spline(path, total_samples_per_edge) for path in edge_paths.values()], axis=0)
    selfloop_centers = _get_optimal_offsets(anchors, offsets, avoid)
    selfloop_angles = [_get_angle(*center) for center in selfloop_centers]
    return dict(zip(selfloops, selfloop_angles))


def _get_straight_selfloop_edge_paths(edges, node_positions, selfloop_radius, selfloop_angle, total_points=100):
    edge_paths = dict()
    for edge in edges:
        edge_paths[edge] = _get_selfloop_path(
            node_positions[edge[0]], selfloop_radius[edge], selfloop_angle[edge], total_points)
    return edge_paths


def _get_selfloop_path(source_position, radius, angle, total_points):
    unit_vector = _get_unit_vector(np.array([np.cos(angle), np.sin(angle)]))
    center = source_position + radius * unit_vector
    # Note: we add pi to the start angle as the start angle lies opposite
    # to the direction in which the self-loop extends.
    return _get_n_points_on_a_circle(
        center, radius, total_points+1,
        _get_angle(*unit_vector) + np.pi,
    )[1:]


def get_curved_edge_paths(edges, node_positions,
                          selfloop_radius               = 0.1,
                          selfloop_angle                = None,
                          origin                        = np.array([0, 0]),
                          scale                         = np.array([1, 1]),
                          k                             = 0.1,
                          initial_temperature           = 0.01,
                          total_iterations              = 50,
                          node_size                     = 0.,
                          bundle_parallel_edges         = True):
    """Edge routing using curved paths that avoid nodes and other edges.

    Computes the edge paths, such that edges are represented by curved
    lines connecting the source and target node. Edges paths avoid
    nodes and each other. The edge layout is determined using the
    Fruchterman-Reingold algorithm.

    Parameters
    ----------
    edges : list
        The edges of the graph, with each edge being represented by a (source node ID, target node ID) tuple.
    node_positions : dict
        Dictionary mapping each node ID to (float x, float y) tuple, the node position.
    selfloop_radius : dict or float, default 0.1
        Dictionary mapping each self-loop edge to a radius. If float, all self-loops have the same radius.
    selfloop_angle : dict, float, or None
        The starting angle of the self-loop in radians.
    origin : numpy.array
        A (float x, float y) tuple corresponding to the lower left hand corner of the bounding box specifying the extent of the canvas.
    scale : numpy.array
        A (float x, float y) tuple representing the width and height of the bounding box specifying the extent of the canvas.
    k : float, default 0.1
        Spring constant, which controls the tautness of edges.
        Small values will result in straight connections, large values in bulging arcs.
    total_iterations : int, default 50
        Number of iterations in the Fruchterman-Reingold algorithm.
    initial_temperature: float, default 1.
        Temperature controls the maximum node displacement on each iteration.
        Temperature is decreased on each iteration to eventually force the algorithm
        into a particular solution. The size of the initial temperature determines how
        quickly that happens. Values should be much smaller than the values of `scale`.
    node_size : float or dict
        Dictionary mapping each node to a float, the node size. Used for node avoidance.
    bundle_parallel_edges: boolean, default True
        If True, parallel edges (including bi-directional edges) have the same path.

    Returns
    -------
    edge_paths : dict
        Dictionary mapping each edge to an array of (x, y) coordinates representing its path.

    """

    edge_paths = dict()

    nonloops = [(source, target) for (source, target) in edges if source != target]
    if bundle_parallel_edges:
        parallel_edges = []
        other_edges = []
        for (source, target) in nonloops:
            if (target, source) in nonloops:
                if (target, source) in parallel_edges:
                    pass
                else:
                    parallel_edges.append((source, target))
            else:
                other_edges.append((source, target))
        nonloop_edge_paths = _get_curved_nonloop_edge_paths(
            parallel_edges + other_edges, node_positions, origin, scale, k, initial_temperature,
            total_iterations, node_size, bundle_parallel_edges)
        for (source, target) in parallel_edges:
            nonloop_edge_paths[(target, source)] = nonloop_edge_paths[(source, target)][::-1]
    else:
        nonloop_edge_paths = _get_curved_nonloop_edge_paths(
            nonloops, node_positions, origin, scale, k, initial_temperature,
            total_iterations, node_size, bundle_parallel_edges)
    edge_paths.update(nonloop_edge_paths)

    selfloops = [(source, target) for (source, target) in edges if source == target]
    if selfloops:
        selfloop_radius = _normalize_numeric_argument(selfloop_radius, selfloops, 'selfloop_radius')
        if selfloop_angle is not None: # can be zero!
            selfloop_angle = _normalize_numeric_argument(selfloop_angle, selfloops, 'angle', allow_none=True)
        else:
            selfloop_angle = _get_optimal_selfloop_angles(
                selfloops, selfloop_radius, node_positions, nonloop_edge_paths)
        selfloop_edge_paths = _get_curved_selfloop_edge_paths(
            selfloops, node_positions, selfloop_radius, selfloop_angle,
            origin, scale, k, initial_temperature,
            total_iterations, node_size, nonloop_edge_paths)
        edge_paths.update(selfloop_edge_paths)

    return edge_paths


def _get_curved_nonloop_edge_paths(edges, node_positions, origin, scale,
                                   k, initial_temperature, total_iterations,
                                   node_size, bundle_parallel_edges):

    edge_to_control_points = _initialize_nonloop_control_points(edges, node_positions, scale)

    control_point_positions = _initialize_nonloop_control_point_positions(
        edge_to_control_points, node_positions, bundle_parallel_edges)

    control_point_positions = _optimize_control_point_positions(
        edge_to_control_points, node_positions, control_point_positions,
        origin, scale, k, initial_temperature, total_iterations, node_size,
        bundle_parallel_edges)

    edge_to_path = _get_path_through_control_points(
        edge_to_control_points, node_positions, control_point_positions)

    edge_to_path = _smooth_edge_paths(edge_to_path)

    return edge_to_path


def _initialize_nonloop_control_points(edges, node_positions, scale):
    """Represent each edge with string of control points."""
    edge_to_control_points = dict()
    for source, target in edges:
        edge_length = np.linalg.norm(node_positions[target] - node_positions[source], axis=-1) / np.linalg.norm(scale)
        total_control_points = min(max(int(edge_length * 10), 1), 5) # ensure that there are at least one point but no more than 5
        edge_to_control_points[(source, target)] = [uuid4() for _ in range(total_control_points)]
    return edge_to_control_points


def _initialize_nonloop_control_point_positions(edge_to_control_points, node_positions, bundle_parallel_edges):
    """Initialise the positions of the control points to positions on a straight line between source and target node."""

    control_point_positions = dict()
    for (source, target), control_points in edge_to_control_points.items():
        delta = node_positions[target] - node_positions[source]
        fraction = np.linspace(0, 1, len(control_points)+2)[1:-1]
        positions = fraction[:, np.newaxis] * delta[np.newaxis, :] + node_positions[source]
        if (not bundle_parallel_edges) and ((target, source) in edge_to_control_points):
            # Offset the path ever so slightly to a side, such that bi-directional edges do not overlap completely.
            # This prevents an intertwining of parallel edges.
            offset = 1e-3 * np.linalg.norm(delta) * np.squeeze(_get_orthogonal_unit_vector(np.atleast_2d(delta)))
            positions -= offset
        control_point_positions.update(zip(control_points, positions))
    return control_point_positions


def _get_curved_selfloop_edge_paths(edges, node_positions, selfloop_radius, selfloop_angle,
                                    origin, scale, k, initial_temperature, total_iterations, node_size,
                                    nonloop_edge_paths):

    edge_to_control_points = _initialize_selfloop_control_points(edges)

    control_point_positions = _initialize_selfloop_control_point_positions(
        edge_to_control_points, node_positions, selfloop_radius, selfloop_angle)

    expanded_node_positions = node_positions.copy()
    for positions in nonloop_edge_paths.values():
        expanded_node_positions.update(zip([uuid4() for _ in range(len(positions)-2)], positions[1:-1]))

    control_point_positions = _optimize_control_point_positions(
        edge_to_control_points, expanded_node_positions, control_point_positions,
        origin, scale, k, initial_temperature, total_iterations, node_size,
        bundle_parallel_edges=False)

    edge_to_path = _get_path_through_control_points(
        edge_to_control_points, node_positions, control_point_positions)

    edge_to_path = _smooth_edge_paths(edge_to_path)

    return edge_to_path


def _initialize_selfloop_control_points(edges):
    """Represent each edge with string of control points."""
    edge_to_control_points = dict()
    for edge in edges:
        edge_to_control_points[edge] = [uuid4() for _ in range(5)]
    return edge_to_control_points


def _initialize_selfloop_control_point_positions(edge_to_control_points, node_positions, selfloop_radius, selfloop_angle):
    """Initialise the positions on a circle next to the node."""

    control_point_positions = dict()
    for edge, control_points in edge_to_control_points.items():
        positions = _get_selfloop_path(
            node_positions[edge[0]], selfloop_radius[edge], selfloop_angle[edge], len(control_points))
        control_point_positions.update(zip(control_points, positions))

    return control_point_positions


def _optimize_control_point_positions(
        edge_to_control_points, node_positions, control_point_positions,
        origin, scale, k, initial_temperature, total_iterations, node_size,
        bundle_parallel_edges):
    """Optimise the position of control points using the FR algorithm."""
    nodes = list(node_positions.keys())
    expanded_edges = _expand_edges(edge_to_control_points)
    expanded_node_positions = control_point_positions.copy() # TODO: may need deepcopy here
    expanded_node_positions.update(node_positions)

    if isinstance(node_size, float):
        node_size = {node : node_size for node in node_positions}

    # increase size of nodes so that there is a bit more clearance between edges and nodes
    node_size = {node : 2 * size for node, size in node_size.items()}

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)

        if bundle_parallel_edges:
            # Edge control points are repulsed by nodes but not by other edge control points.
            expanded_node_positions = get_fruchterman_reingold_layout.__wrapped__(
                expanded_edges,
                node_positions      = expanded_node_positions,
                scale               = scale,
                origin              = origin,
                k                   = k,
                initial_temperature = initial_temperature,
                total_iterations    = total_iterations,
                node_size           = node_size,
                fixed_nodes         = nodes,
                get_repulsion       = _get_fr_repulsion_variant,
            )
        else:
            # Edge control points are repulsed by other edge control points.
            # This results in a separation of parallel edges.
            expanded_node_positions = get_fruchterman_reingold_layout.__wrapped__(
                expanded_edges,
                node_positions      = expanded_node_positions,
                scale               = scale,
                origin              = origin,
                k                   = k,
                initial_temperature = initial_temperature,
                total_iterations    = total_iterations,
                node_size           = node_size,
                fixed_nodes         = nodes,
            )

    return {node : xy for node, xy in expanded_node_positions.items() if node not in nodes}


def _expand_edges(edge_to_control_points):
    """Create a new, expanded edge list, in which each edge is split into multiple segments.
    There are total_control_points + 1 segments / edges for each original edge.

    """
    expanded_edges = []
    for (source, target), control_points in edge_to_control_points.items():
        sources = [source] + control_points
        targets = control_points + [target]
        expanded_edges.extend(zip(sources, targets))
    return expanded_edges


def _get_fr_repulsion_variant(distance, direction, k):
    """Compute repulsive forces.

    This is a variant of the implementation in the original FR
    algorithm, in as much as repulsion only acts between fixed nodes
    and mobile nodes, not between fixed nodes and other fixed nodes.
    """
    total_mobile = distance.shape[1]
    distance = distance[total_mobile:]
    direction = direction[total_mobile:]
    magnitude = k**2 / distance
    vectors = direction * magnitude[..., None]
    return np.sum(vectors, axis=0)


def _get_path_through_control_points(edge_to_control_points, node_positions, control_point_positions):
    """Map each edge to an array of (optimised) control points positions."""
    edge_to_path = dict()
    for (source, target), control_points in edge_to_control_points.items():
        path = [node_positions[source]] \
            + [control_point_positions[node] for node in control_points] \
            + [node_positions[target]]
        edge_to_path[(source, target)] = np.array(path)
    return edge_to_path


def _smooth_edge_paths(edge_to_path, *args, **kwargs):
    """Fit splines through edge paths for smoother edge routing."""
    return {edge : _bspline(path, *args, **kwargs) for edge, path in edge_to_path.items()}


def get_arced_edge_paths(edges, node_positions, rad=1., selfloop_radius=0.1, selfloop_angle=np.pi/2):
    """Determine the edge layout, where edges are represented by arcs
    connecting the source and target node.

    Creates simple quadratic Bezier curves between nodes. The curves
    are created so that the middle control points (C1) are located at
    the same distance from the start (C0) and end points (C2) and the
    distance of the C1 to the line connecting C0-C2 is rad times the
    distance of C0-C2.

    Arguments:
    ----------
    edges : list of (source node ID, target node ID) 2-tuples
        The edges.
    node_positions : dict node ID : (x, y) positions
        The node positions.
    rad : float (default 1.0)
        The curvature of the arc.
    selfloop_radius : dict or float, default 0.1
        Dictionary mapping each self-loop edge to a radius. If float, all self-loops have the same radius.
    selfloop_angle : dict or float, default np.pi/2
        The starting angle of the self-loop in radians.

    Returns:
    --------
    edge_paths : dict edge : ndarray
        Dictionary mapping each edge to a list of edge segments.

    """
    edge_paths = dict()

    nonloops = [(source, target) for (source, target) in edges if source != target]
    nonloop_edge_paths = _get_arced_nonloop_edge_paths(nonloops, node_positions, rad)
    edge_paths.update(nonloop_edge_paths)

    selfloops = [(source, target) for (source, target) in edges if source == target]
    if selfloops:
        selfloop_radius = _normalize_numeric_argument(selfloop_radius, selfloops, 'selfloop_radius')
        selfloop_angle = _normalize_numeric_argument(selfloop_angle, selfloops, 'angle', allow_none=False)
        selfloop_edge_paths = _get_arced_selfloop_edge_paths(
            selfloops, node_positions, selfloop_radius, selfloop_angle)
        edge_paths.update(selfloop_edge_paths)

    return edge_paths


def _get_arced_nonloop_edge_paths(edges, node_positions, rad):
    edge_paths = dict()
    for source, target in edges:
        arc_factory = ConnectionStyle.Arc3(rad=rad)
        path = arc_factory(
            node_positions[source],
            node_positions[target],
            shrinkA=0., shrinkB=0.
            )
        edge_paths[(source, target)] = _bspline(path.vertices, 100)
    return edge_paths


def _get_arced_selfloop_edge_paths(edges, node_positions, selfloop_radius, selfloop_angle):
    return _get_straight_selfloop_edge_paths(edges, node_positions, selfloop_radius, selfloop_angle)


@profile
@_handle_multiple_components
def get_bundled_edge_paths(edges, node_positions,
                           k                       = 500.,
                           compatibility_threshold = 0.05,
                           total_cycles            = 6,
                           total_iterations        = 50,
                           step_size               = 0.04,
                           straighten_by           = 0.,
):
    """Edge routing with bundled edge paths.

    Uses the FDEB algorithm as proposed in [Holten2009]_.
    This implementation follows the paper closely with the exception
    that instead of doubling the number of control point on each
    iteration (2n), a new control point is inserted between each
    existing pair of control points (2(n-1)+1), as proposed e.g. in Wu
    et al. (2015) [Wu2015]_.

    Parameters
    ----------

    edges : list
        The edges of the graph, with each edge being represented by a (source node ID, target node ID) tuple.
    node_positions : dict
        Dictionary mapping each node ID to (float x, float y) tuple, the node position.
    k : float, default 500.
        The stiffness of the springs that connect control points.
    compatibility_threshold : float, default 0.05
        Edge pairs with a lower compatibility score are not bundled together.
        Set to zero to bundle all edges with each other regardless of compatibility.
        Set to one to prevent bundling of any (non-identical) edges.
    total_cycles : int, default 6
        The number of cycles. The number of control points (P) is doubled each cycle.
    total_iterations : int, default 50
        Number of iterations (I) in the first cycle. Iterations are reduced by 1/3 with each cycle.
    step_size : float, default 0.04
        Maximum step size (S) in the first cycle. Step sizes are halved each cycle.
    straighten_by : float, default 0.
        The amount of edge straightening applied after bundling.
        A small amount of straightening can help indicating the number of
        edges comprising a bundle by widening the bundle.
        If set to one, edges are fully un-bundled and plotted as stright lines.

    Returns
    -------
    edge_paths : dict
        Dictionary mapping each edge to an array of (x, y) coordinates representing its path.

    References
    ----------
    .. [Holten2009] Holten D and Van Wijk JJ. (2009) ‘Force-Directed edge
       bundling for graph visualization’, Computer Graphics Forum.

    .. [Wu2015] Wu J, Yu L, Yu H (2015) ‘Texture-based edge bundling: A
       web-based approach for interactively visualizing large graphs’,
       IEEE International Conference on Big Data.

    """

    # Filter out self-loops.
    if np.any([source == target for source, target in edges]):
        warnings.warn('Edge-bundling of self-loops not supported. Self-loops are removed from the edge list.')
        edges = [(source, target) for (source, target) in edges if source != target]

    # Filter out bi-directional edges.
    unidirectional_edges = set()
    for (source, target) in edges:
        if (target, source) not in unidirectional_edges:
            unidirectional_edges.add((source, target))
    reverse_edges = list(set(edges) - unidirectional_edges)
    edges = list(unidirectional_edges)

    edge_to_k = _get_k(edges, node_positions, k)

    edge_compatibility = _get_edge_compatibility(edges, node_positions, compatibility_threshold)

    edge_to_control_points = _initialize_bundled_control_points(edges, node_positions)

    for _ in range(total_cycles):
        edge_to_control_points = _expand_control_points(edge_to_control_points)

        for _ in range(total_iterations):
            F = _get_Fs(edge_to_control_points, edge_to_k)
            F = _get_Fe(edge_to_control_points, edge_compatibility, F)
            edge_to_control_points = _update_control_point_positions(
                edge_to_control_points, F, step_size)

        step_size /= 2.
        total_iterations = int(2/3 * total_iterations)

    if straighten_by > 0.:
        edge_to_control_points = _straighten_edges(edge_to_control_points, straighten_by)

    edge_to_control_points = _smooth_edges(edge_to_control_points)

    # Add previously removed bi-directional edges back in.
    for (source, target) in reverse_edges:
        edge_to_control_points[(source, target)] = edge_to_control_points[(target, source)][::-1]

    return edge_to_control_points


def _get_k(edges, node_positions, k):
    """Assign each edge a stiffness depending on its length and the global stiffness constant."""
    return {(s, t) : k / np.linalg.norm(node_positions[t] - node_positions[s]) for (s, t) in edges}


@profile
def _get_edge_compatibility(edges, node_positions, threshold):
    """Compute the compatibility between all edge pairs."""
    # precompute edge segments, segment lengths and corresponding vectors
    edge_to_segment = {edge : Segment(node_positions[edge[0]], node_positions[edge[1]]) for edge in edges}

    edge_compatibility = list()
    for e1, e2 in itertools.combinations(edges, 2):
        P = edge_to_segment[e1]
        Q = edge_to_segment[e2]

        compatibility = 1
        compatibility *= _get_scale_compatibility(P, Q)
        if compatibility < threshold:
            continue # with next edge pair
        compatibility *= _get_position_compatibility(P, Q)
        if compatibility < threshold:
            continue # with next edge pair
        compatibility *= _get_angle_compatibility(P, Q)
        if compatibility < threshold:
            continue # with next edge pair
        compatibility *= _get_visibility_compatibility(P, Q)
        if compatibility < threshold:
            continue # with next edge pair

        # Also determine if one of the edges needs to be reversed:
        reverse = min(np.linalg.norm(P[0] - Q[0]), np.linalg.norm(P[1] - Q[1])) > \
            min(np.linalg.norm(P[0] - Q[1]), np.linalg.norm(P[1] - Q[0]))

        edge_compatibility.append((e1, e2, compatibility, reverse))

    return edge_compatibility


class Segment(object):
    def __init__(self, p0, p1):
        self.p0 = p0
        self.p1 = p1
        self.vector = p1 - p0
        self.length = np.linalg.norm(self.vector)
        self.unit_vector = self.vector / self.length
        self.midpoint = self.p0 * 0.5 * self.vector

    def __getitem__(self, idx):
        if idx == 0:
            return self.p0
        elif (idx == 1) or (idx == -1):
            return self.p1
        else:
            raise IndexError

    def get_orthogonal_projection_onto_segment(self, point):
        # Adapted from https://stackoverflow.com/a/61343727/2912349
        # The line extending the segment is parameterized as p0 + t (p1 - p0).
        # The projection falls where t = [(point-p0) . (p1-p0)] / |p1-p0|^2
        t = np.sum((point - self.p0) * self.vector) / self.length**2
        return self.p0 + t * self.vector

#     def get_interior_angle_with(self, other_segment):
#         # Adapted from: https://stackoverflow.com/a/13849249/2912349
#         return np.arccos(np.clip(np.dot(self.unit_vector, other_segment.unit_vector), -1.0, 1.0))


# def _get_angle_compatibility(P, Q):
#     return np.abs(np.cos(P.get_interior_angle_with(Q)))


def _get_angle_compatibility(P, Q):
    """Compute the angle compatibility between two segments P and Q.
    The angle compatibility is high if the interior angle between them is small.

    """
    return np.abs(np.clip(np.dot(P.unit_vector, Q.unit_vector), -1.0, 1.0))


def _get_scale_compatibility(P, Q):
    """Compute the scale compatibility between two segments P and Q.
    The scale compatibility is high if their lengths are similar.

    """
    avg = 0.5 * (P.length + Q.length)

    # The definition in the paper is rubbish, as the result is not on the interval [0, 1].
    # For example, consider an two edges, both 0.5 long:
    # return 2 / (avg * min(length_P, length_Q) + max(length_P, length_Q) / avg)

    # my original alternative:
    # return min(length_P/length_Q, length_Q/length_P)

    # typo in original paper corrected in Graser et al. (2019)
    return 2 / (avg / min(P.length, Q.length) + max(P.length, Q.length) / avg)


def _get_position_compatibility(P, Q):
    """Compute the position compatibility between two segments P and Q.
    The position compatibility is high if the distance between their midpoints is small.

    """
    avg = 0.5 * (P.length + Q.length)
    distance_between_midpoints = np.linalg.norm(Q.midpoint - P.midpoint)
    # This is the definition from the paper, but the scaling should probably be more aggressive.
    return avg / (avg + distance_between_midpoints)


def _get_visibility_compatibility(P, Q):
    """Compute the visibility compatibility between two segments P and Q.
    The visibility compatibility is low if bundling would occlude any of the end points of the segments.

    """
    return min(_get_visibility(P, Q), _get_visibility(Q, P))


@profile
def _get_visibility(P, Q):
    I0 = P.get_orthogonal_projection_onto_segment(Q[0])
    I1 = P.get_orthogonal_projection_onto_segment(Q[1])
    I = Segment(I0, I1)
    distance_between_midpoints = np.linalg.norm(P.midpoint - I.midpoint)
    visibility = 1 - 2 * distance_between_midpoints / I.length
    return max(visibility, 0)


def _initialize_bundled_control_points(edges, node_positions):
    """Initialise each edge with two control points, the positions of the source and target nodes."""
    edge_to_control_points = dict()
    for source, target in edges:
        edge_to_control_points[(source, target)] \
            = np.array([node_positions[source], node_positions[target]])
    return edge_to_control_points


def _expand_control_points(edge_to_control_points):
    "Place a new control point between each pair of existing control points."
    for edge, control_points_old in edge_to_control_points.items():
        total_control_points_old = len(control_points_old)
        total_control_points_new = 2 * (total_control_points_old - 1) + 1
        control_points_new = np.zeros((total_control_points_new, 2))
        for ii in range(total_control_points_new):
            if (ii+1) % 2: # ii is even
                control_points_new[ii] = control_points_old[int(ii/2)]
            else: # ii is odd
                p1 = control_points_old[int((ii-1)/2)]
                p2 = control_points_old[int((ii+1)/2)]
                control_points_new[ii] = 0.5 * (p2 - p1) + p1
        edge_to_control_points[edge] = control_points_new
    return edge_to_control_points


def _get_Fs(edge_to_control_points, k):
    """Compute all spring forces."""
    out = dict()
    for edge, control_points in edge_to_control_points.items():
        delta = np.zeros_like(control_points)
        diff = np.diff(control_points, axis=0)
        delta[1:-1] -= diff[:-1]
        delta[1:-1] += diff[1:]
        kp = k[edge] / (len(control_points) - 1)
        out[edge] = kp * delta
    return out


@profile
def _get_Fe(edge_to_control_points, edge_compatibility, out):
    """Compute all electrostatic forces."""
    for e1, e2, compatibility, reverse in edge_compatibility:
        P = edge_to_control_points[e1]
        Q = edge_to_control_points[e2]

        if not reverse:
            # i.e. if source/source or target/target closest
            delta = Q - P
        else:
            # need to reverse one set of control points
            delta = Q[::-1] - P

        # # desired computation:
        # distance = np.linalg.norm(delta, axis=1)
        # displacement = compatibility * delta / distance[..., None]**2

        # actually much faster:
        distance_squared = delta[:, 0]**2 + delta[:, 1]**2
        displacement = compatibility * delta / distance_squared[..., None]

        # Don't move the first and last control point, which are just the node positions.
        displacement[0] = 0
        displacement[-1] = 0

        out[e1] += displacement
        if not reverse:
            out[e2] -= displacement
        else:
            out[e2] -= displacement[::-1]

    return out


def _update_control_point_positions(edge_to_control_points, F, step_size):
    """Update control point positions using the calculated net forces."""
    for edge, displacement in F.items():
        displacement_length = np.clip(np.linalg.norm(displacement), 1e-12, None) # prevent divide by 0 error in next line
        displacement = displacement / displacement_length * np.clip(displacement_length, None, step_size)
        edge_to_control_points[edge] += displacement
    return edge_to_control_points


def _smooth_edges(edge_to_path):
    """Wraps _smooth_path()."""
    return {edge : _smooth_path(path) for edge, path in edge_to_path.items()}


def _smooth_path(path):
    """Smooth a path by fitting a univariate spline.

    Notes
    -----
    Adapted from https://stackoverflow.com/a/52020098/2912349

    """

    # Compute the linear length along the line:
    distance = np.cumsum( np.sqrt(np.sum( np.diff(path, axis=0)**2, axis=1 )) )
    distance = np.insert(distance, 0, 0)/distance[-1]

    # Compute a spline function for each dimension:
    splines = [UnivariateSpline(distance, coords, k=3, s=.001) for coords in path.T]

    # Computed the smoothed path:
    alpha = np.linspace(0, 1, 100)
    return np.vstack([spl(alpha) for spl in splines]).T


def _straighten_edges(edge_to_path, straighten_by):
    """Wraps _straigthen_path()"""
    return {edge : _straighten_path(path, straighten_by) for edge, path in edge_to_path.items()}


def _straighten_path(path, straighten_by):
    """Straigthen a path by computing the weighted average between the path and
    a straight line connecting the end points.

    """
    p0 = path[0]
    p1 = path[-1]
    n = len(path)
    return (1 - straighten_by) * path \
        + straighten_by * (p0 + np.linspace(0, 1, n)[:, np.newaxis] * (p1 - p0))


def _shift_edge(x1, y1, x2, y2, delta):
    """Determine the parallel to a segment defined by points p1: (x1, y1) and p2 : (x2, y2) at a distance delta."""
    # convert segment into a vector
    v = np.r_[x2-x1, y2-y1]
    # compute orthogonal vector
    v = np.r_[-v[1], v[0]]
    # convert to orthogonal unit vector
    v = v / np.linalg.norm(v)
    # compute offsets
    dx, dy = delta * v
    # return new coordinates of point p1' and p2'
    return x1+dx, y1+dy, x2+dx, y2+dy
