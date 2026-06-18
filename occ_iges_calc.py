from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from iges_calc import AnalysisResult, CurveEntity


Point3 = Tuple[float, float, float]


def analyze_iges_file_with_occ(
    path: str | Path,
    pierce_tolerance_mm: float = 0.1,
) -> AnalysisResult:
    occ = _load_ocp()
    path = Path(path)
    shape = _read_iges_shape(path, occ)

    warnings: List[str] = []

    cylindrical_faces = _collect_cylindrical_faces(shape, occ)
    if cylindrical_faces:
        return _analyze_tube_shape(path, cylindrical_faces, pierce_tolerance_mm, warnings, occ)

    planar_faces = _collect_planar_faces(shape, occ)
    profile_faces = _select_profile_tube_outer_faces(shape, planar_faces, occ)
    if profile_faces:
        return _analyze_profile_tube_shape(path, profile_faces, pierce_tolerance_mm, warnings, occ)

    warnings.append(
        "Tube mode did not find a round tube or a rectangular/square profile tube. "
        "The IGES may describe the tube as NURBS surfaces, as a rotated profile, or as another section type."
    )

    if planar_faces:
        selected_face = max(planar_faces, key=lambda item: item["area"])
        wires = _collect_shapes(selected_face["shape"], occ["TopAbs_WIRE"], occ)
        wire_lengths = [
            _linear_length(wire, occ)
            for wire in wires
            if _linear_length(wire, occ) > pierce_tolerance_mm
        ]
        cut_length_mm = sum(wire_lengths)
        pierces = len(wire_lengths)
        curves = [
            CurveEntity(
                de_id=index + 1,
                entity_type=0,
                length_mm=length,
                warnings=[],
            )
            for index, length in enumerate(wire_lengths)
        ]
        warnings.append(
            "3D mode: calculated contours from the largest planar face. "
            "For sheet cutting this should be the flat cutting profile; verify once with sample parts."
        )
        return AnalysisResult(
            path=path,
            unit_name="MM",
            unit_to_mm=1.0,
            cut_length_mm=cut_length_mm,
            pierces=pierces,
            curves=curves,
            warnings=warnings,
            backend="open-cascade",
            calculation_mode=(
                f"largest planar face, area {selected_face['area']:.3f} mm2, "
                f"{len(wire_lengths)} contour(s)"
            ),
        )

    edges = _collect_unique_edges(shape, occ)
    if not edges:
        raise ValueError("Open Cascade imported the IGES file, but found no faces or edges.")

    edge_lengths = [_linear_length(edge, occ) for edge in edges]
    cut_length_mm = sum(edge_lengths)
    pierces = _estimate_edge_components(edges, pierce_tolerance_mm, occ)
    warnings.append(
        "3D mode: no planar face was found, so the app summed unique model edges. "
        "This may not match sheet cutting length for curved or solid models."
    )
    curves = [
        CurveEntity(de_id=index + 1, entity_type=0, length_mm=length)
        for index, length in enumerate(edge_lengths)
        if length > 0
    ]
    return AnalysisResult(
        path=path,
        unit_name="MM",
        unit_to_mm=1.0,
        cut_length_mm=cut_length_mm,
        pierces=pierces,
        curves=curves,
        warnings=warnings,
        backend="open-cascade",
        calculation_mode=f"unique 3D edges, {len(edges)} edge(s)",
    )


def _load_ocp() -> Dict[str, object]:
    try:
        from OCP.BRep import BRep_Tool
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepBndLib import BRepBndLib
        from OCP.BRepGProp import BRepGProp
        from OCP.Bnd import Bnd_Box
        from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
        from OCP.GProp import GProp_GProps
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.IGESControl import IGESControl_Reader
        from OCP.Interface import Interface_Static
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX, TopAbs_WIRE
        from OCP.TopExp import TopExp, TopExp_Explorer
        from OCP.TopTools import TopTools_IndexedMapOfShape
        from OCP.TopoDS import TopoDS
    except ImportError as exc:
        raise ImportError(
            "Open Cascade backend is not installed. Install cadquery-ocp for 3D IGES support."
        ) from exc

    return {
        "BRep_Tool": BRep_Tool,
        "BRepAdaptor_Surface": BRepAdaptor_Surface,
        "BRepBndLib": BRepBndLib,
        "BRepGProp": BRepGProp,
        "Bnd_Box": Bnd_Box,
        "GeomAbs_Cylinder": GeomAbs_Cylinder,
        "GeomAbs_Plane": GeomAbs_Plane,
        "GProp_GProps": GProp_GProps,
        "IFSelect_RetDone": IFSelect_RetDone,
        "IGESControl_Reader": IGESControl_Reader,
        "Interface_Static": Interface_Static,
        "TopAbs_EDGE": TopAbs_EDGE,
        "TopAbs_FACE": TopAbs_FACE,
        "TopAbs_VERTEX": TopAbs_VERTEX,
        "TopAbs_WIRE": TopAbs_WIRE,
        "TopExp": TopExp,
        "TopExp_Explorer": TopExp_Explorer,
        "TopTools_IndexedMapOfShape": TopTools_IndexedMapOfShape,
        "TopoDS": TopoDS,
    }


def _read_iges_shape(path: Path, occ: Dict[str, object]):
    interface_static = occ["Interface_Static"]
    try:
        interface_static.SetCVal_s("xstep.cascade.unit", "MM")
    except Exception:
        pass

    reader = occ["IGESControl_Reader"]()
    status = reader.ReadFile(str(path))
    if status != occ["IFSelect_RetDone"]:
        raise ValueError("Open Cascade could not read the IGES file.")
    reader.TransferRoots()
    shape = reader.OneShape()
    if shape.IsNull():
        raise ValueError("Open Cascade imported an empty shape.")
    return shape


def _collect_planar_faces(shape, occ: Dict[str, object]) -> List[Dict[str, object]]:
    faces = []
    for face_shape in _collect_shapes(shape, occ["TopAbs_FACE"], occ):
        face = occ["TopoDS"].Face_s(face_shape)
        plane = _face_plane(face, occ)
        if plane is None:
            continue
        area = _surface_area(face, occ)
        if area > 0:
            faces.append(
                {
                    "shape": face,
                    "area": area,
                    "normal": _plane_normal(plane),
                    "center": _surface_center(face, occ),
                }
            )
    return faces


def _collect_cylindrical_faces(shape, occ: Dict[str, object]) -> List[Dict[str, object]]:
    faces = []
    for face_shape in _collect_shapes(shape, occ["TopAbs_FACE"], occ):
        face = occ["TopoDS"].Face_s(face_shape)
        cylinder = _face_cylinder(face, occ)
        if cylinder is None:
            continue
        area = _surface_area(face, occ)
        radius = float(cylinder.Radius())
        if area > 0 and radius > 0:
            faces.append({"shape": face, "area": area, "radius": radius})
    return faces


def _analyze_tube_shape(
    path: Path,
    cylindrical_faces: Sequence[Dict[str, object]],
    pierce_tolerance_mm: float,
    warnings: List[str],
    occ: Dict[str, object],
) -> AnalysisResult:
    max_radius = max(float(face["radius"]) for face in cylindrical_faces)
    radius_tolerance = max(0.05, max_radius * 0.002)
    outer_faces = [
        face
        for face in cylindrical_faces
        if abs(float(face["radius"]) - max_radius) <= radius_tolerance
    ]

    candidate_edges = _collect_outer_tube_boundary_edges(outer_faces, occ)
    edge_lengths_with_shapes = [
        (edge, _linear_length(edge, occ))
        for edge in candidate_edges
    ]
    edge_lengths_with_shapes = [
        (edge, length)
        for edge, length in edge_lengths_with_shapes
        if length > pierce_tolerance_mm
    ]
    edges = [edge for edge, _length in edge_lengths_with_shapes]
    edge_lengths = [length for _edge, length in edge_lengths_with_shapes]
    cut_length_mm = sum(edge_lengths)
    pierces = _estimate_edge_components(edges, pierce_tolerance_mm, occ)

    warnings.append(
        "Tube mode: calculated cut contours on the outer cylindrical surface. "
        "Hole cylinders and inner tube walls are ignored by radius."
    )
    if len(cylindrical_faces) != len(outer_faces):
        warnings.append(
            f"Detected {len(cylindrical_faces)} cylindrical face(s); "
            f"used {len(outer_faces)} outer face(s) with radius about {max_radius:.3f} mm."
        )
    if not edges:
        warnings.append(
            "No boundary edges were found on the outer tube surface. "
            "The IGES may contain only analytic surfaces without trimmed cut contours."
        )

    curves = [
        CurveEntity(de_id=index + 1, entity_type=0, length_mm=length)
        for index, length in enumerate(edge_lengths)
        if length > 0
    ]
    return AnalysisResult(
        path=path,
        unit_name="MM",
        unit_to_mm=1.0,
        cut_length_mm=cut_length_mm,
        pierces=pierces,
        curves=curves,
        warnings=warnings,
        backend="open-cascade",
        calculation_mode=(
            f"tube outer cylinder, radius {max_radius:.3f} mm, "
            f"{len(edge_lengths)} boundary edge(s)"
        ),
    )


def _select_profile_tube_outer_faces(
    shape,
    planar_faces: Sequence[Dict[str, object]],
    occ: Dict[str, object],
) -> List[Dict[str, object]]:
    if not planar_faces:
        return []

    return _select_profile_tube_outer_faces_by_bounds(planar_faces, _shape_bounds(shape, occ))


def _select_profile_tube_outer_faces_by_bounds(
    planar_faces: Sequence[Dict[str, object]],
    bounds: Tuple[float, float, float, float, float, float],
) -> List[Dict[str, object]]:
    extents = (
        bounds[3] - bounds[0],
        bounds[4] - bounds[1],
        bounds[5] - bounds[2],
    )
    if max(extents) <= 0:
        return []

    axis_index = max(range(3), key=lambda index: extents[index])
    cross_indices = [index for index in range(3) if index != axis_index]
    cross_extent = max(extents[index] for index in cross_indices)
    envelope_tolerance = max(0.25, cross_extent * 0.015)
    side_normal_limit = 0.25

    selected: List[Dict[str, object]] = []
    for face in planar_faces:
        normal = face["normal"]
        center = face["center"]
        if abs(normal[axis_index]) > side_normal_limit:
            continue

        dominant_axis = max(cross_indices, key=lambda index: abs(normal[index]))
        face_position = center[dominant_axis]
        low = bounds[dominant_axis]
        high = bounds[dominant_axis + 3]
        distance_to_envelope = min(abs(face_position - low), abs(high - face_position))
        if distance_to_envelope <= envelope_tolerance:
            selected.append(face)

    if len(selected) < 3:
        return []
    return selected


def _analyze_profile_tube_shape(
    path: Path,
    outer_faces: Sequence[Dict[str, object]],
    pierce_tolerance_mm: float,
    warnings: List[str],
    occ: Dict[str, object],
) -> AnalysisResult:
    candidate_edges = _collect_profile_tube_cut_edges(outer_faces, occ)
    edge_lengths_with_shapes = [
        (edge, _linear_length(edge, occ))
        for edge in candidate_edges
    ]
    edge_lengths_with_shapes = [
        (edge, length)
        for edge, length in edge_lengths_with_shapes
        if length > pierce_tolerance_mm
    ]
    edges = [edge for edge, _length in edge_lengths_with_shapes]
    edge_lengths = [length for _edge, length in edge_lengths_with_shapes]
    cut_length_mm = sum(edge_lengths)
    pierces = _estimate_edge_components(edges, pierce_tolerance_mm, occ)

    warnings.append(
        "Profile tube mode: calculated cut contours on the outer planar side faces. "
        "Longitudinal profile corners are excluded."
    )
    warnings.append(
        f"Detected {len(outer_faces)} outer profile face(s); "
        f"used {len(edge_lengths)} cut boundary edge(s)."
    )
    if not edges:
        warnings.append(
            "No cut boundary edges were found on the outer profile faces. "
            "The IGES may contain untrimmed surfaces or a profile orientation this MVP cannot classify."
        )

    curves = [
        CurveEntity(de_id=index + 1, entity_type=0, length_mm=length)
        for index, length in enumerate(edge_lengths)
        if length > 0
    ]
    return AnalysisResult(
        path=path,
        unit_name="MM",
        unit_to_mm=1.0,
        cut_length_mm=cut_length_mm,
        pierces=pierces,
        curves=curves,
        warnings=warnings,
        backend="open-cascade",
        calculation_mode=(
            f"profile tube outer faces, {len(outer_faces)} face(s), "
            f"{len(edge_lengths)} boundary edge(s)"
        ),
    )


def _collect_profile_tube_cut_edges(
    outer_faces: Sequence[Dict[str, object]],
    occ: Dict[str, object],
) -> List[object]:
    edge_faces: Dict[int, Dict[str, object]] = {}
    for face_info in outer_faces:
        face = face_info["shape"]
        for edge_shape in _collect_shapes(face, occ["TopAbs_EDGE"], occ):
            edge = occ["TopoDS"].Edge_s(edge_shape)
            if _is_degenerate_edge(edge, occ):
                continue
            key = _shape_key(edge)
            item = edge_faces.setdefault(key, {"edge": edge, "outer_face_count": 0})
            item["outer_face_count"] = int(item["outer_face_count"]) + 1

    # Edges shared by two selected outer faces are the longitudinal corners of
    # a square/rectangular tube, not laser/plasma cut contours.
    return [
        item["edge"]
        for item in edge_faces.values()
        if int(item["outer_face_count"]) == 1
    ]


def _collect_outer_tube_boundary_edges(
    outer_faces: Sequence[Dict[str, object]],
    occ: Dict[str, object],
) -> List[object]:
    edges = []
    seen = set()
    for face_info in outer_faces:
        face = face_info["shape"]
        for edge_shape in _collect_shapes(face, occ["TopAbs_EDGE"], occ):
            edge = occ["TopoDS"].Edge_s(edge_shape)
            if _is_degenerate_edge(edge, occ) or _is_seam_edge(edge, face, occ):
                continue
            key = _shape_key(edge)
            if key in seen:
                continue
            seen.add(key)
            edges.append(edge)
    return edges


def _collect_shapes(shape, shape_type, occ: Dict[str, object]) -> List[object]:
    explorer = occ["TopExp_Explorer"](shape, shape_type)
    shapes = []
    while explorer.More():
        shapes.append(explorer.Current())
        explorer.Next()
    return shapes


def _collect_unique_edges(shape, occ: Dict[str, object]) -> List[object]:
    edge_map = occ["TopTools_IndexedMapOfShape"]()
    occ["TopExp"].MapShapes_s(shape, occ["TopAbs_EDGE"], edge_map)
    return [edge_map.FindKey(index) for index in range(1, edge_map.Extent() + 1)]


def _is_planar_face(face, occ: Dict[str, object]) -> bool:
    return _face_plane(face, occ) is not None


def _face_plane(face, occ: Dict[str, object]):
    surface = occ["BRepAdaptor_Surface"](face, True)
    if surface.GetType() != occ["GeomAbs_Plane"]:
        return None
    return surface.Plane()


def _plane_normal(plane) -> Point3:
    direction = plane.Axis().Direction()
    return _normalize((float(direction.X()), float(direction.Y()), float(direction.Z())))


def _face_cylinder(face, occ: Dict[str, object]):
    surface = occ["BRepAdaptor_Surface"](face, True)
    if surface.GetType() != occ["GeomAbs_Cylinder"]:
        return None
    return surface.Cylinder()


def _is_degenerate_edge(edge, occ: Dict[str, object]) -> bool:
    try:
        return bool(occ["BRep_Tool"].Degenerated_s(edge))
    except Exception:
        return False


def _is_seam_edge(edge, face, occ: Dict[str, object]) -> bool:
    try:
        return bool(occ["BRep_Tool"].IsClosed_s(edge, face))
    except Exception:
        return False


def _shape_key(shape) -> int:
    try:
        return int(shape.HashCode(2_147_483_647))
    except Exception:
        return id(shape)


def _surface_area(shape, occ: Dict[str, object]) -> float:
    props = occ["GProp_GProps"]()
    occ["BRepGProp"].SurfaceProperties_s(shape, props)
    return float(props.Mass())


def _surface_center(shape, occ: Dict[str, object]) -> Point3:
    props = occ["GProp_GProps"]()
    occ["BRepGProp"].SurfaceProperties_s(shape, props)
    point = props.CentreOfMass()
    return (float(point.X()), float(point.Y()), float(point.Z()))


def _shape_bounds(shape, occ: Dict[str, object]) -> Tuple[float, float, float, float, float, float]:
    box = occ["Bnd_Box"]()
    occ["BRepBndLib"].Add_s(shape, box)
    bounds = box.Get()
    return tuple(float(value) for value in bounds)  # type: ignore[return-value]


def _linear_length(shape, occ: Dict[str, object]) -> float:
    props = occ["GProp_GProps"]()
    occ["BRepGProp"].LinearProperties_s(shape, props)
    return float(props.Mass())


def _estimate_edge_components(
    edges: Sequence[object],
    tolerance_mm: float,
    occ: Dict[str, object],
) -> int:
    if not edges:
        return 0

    parent = list(range(len(edges)))
    endpoint_to_edges: Dict[Tuple[int, int, int], List[int]] = {}

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: int, right: int) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for index, edge in enumerate(edges):
        endpoints = _edge_endpoints(edge, occ)
        if not endpoints:
            continue
        for point in endpoints:
            key = _point_key(point, tolerance_mm)
            linked_edges = endpoint_to_edges.setdefault(key, [])
            for linked_edge in linked_edges:
                union(index, linked_edge)
            linked_edges.append(index)

    return len({find(index) for index in range(len(edges))})


def _edge_endpoints(edge, occ: Dict[str, object]) -> List[Point3]:
    points = []
    for vertex_shape in _collect_shapes(edge, occ["TopAbs_VERTEX"], occ):
        vertex = occ["TopoDS"].Vertex_s(vertex_shape)
        point = occ["BRep_Tool"].Pnt_s(vertex)
        points.append((float(point.X()), float(point.Y()), float(point.Z())))
    return _dedupe_points(points)


def _point_key(point: Point3, tolerance_mm: float) -> Tuple[int, int, int]:
    tolerance = max(tolerance_mm, 0.001)
    return (
        round(point[0] / tolerance),
        round(point[1] / tolerance),
        round(point[2] / tolerance),
    )


def _normalize(vector: Point3) -> Point3:
    length = (vector[0] ** 2 + vector[1] ** 2 + vector[2] ** 2) ** 0.5
    if length == 0:
        return (0.0, 0.0, 0.0)
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _dedupe_points(points: Iterable[Point3]) -> List[Point3]:
    result: List[Point3] = []
    for point in points:
        if point not in result:
            result.append(point)
    return result
