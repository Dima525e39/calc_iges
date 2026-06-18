from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


Point3 = Tuple[float, float, float]


@dataclass
class CurveEntity:
    de_id: int
    entity_type: int
    length_mm: float
    start: Optional[Point3] = None
    end: Optional[Point3] = None
    references: List[int] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    supported: bool = True


@dataclass
class AnalysisResult:
    path: Path
    unit_name: str
    unit_to_mm: float
    cut_length_mm: float
    pierces: int
    curves: List[CurveEntity]
    warnings: List[str] = field(default_factory=list)
    unsupported_entities: Dict[int, int] = field(default_factory=dict)
    backend: str = "basic-iges"
    calculation_mode: str = "curves"

    @property
    def cut_length_m(self) -> float:
        return self.cut_length_mm / 1000.0


@dataclass
class ThicknessPrice:
    thickness_mm: float
    cut_price_per_meter: float
    pierce_price: float

    def label(self) -> str:
        return f"{self.thickness_mm:g} mm"

    def calculate(self, result: AnalysisResult) -> float:
        return (
            result.cut_length_m * self.cut_price_per_meter
            + result.pierces * self.pierce_price
        )


DEFAULT_PRICES = [
    ThicknessPrice(1.0, 60.0, 5.0),
    ThicknessPrice(2.0, 80.0, 7.0),
    ThicknessPrice(3.0, 110.0, 10.0),
    ThicknessPrice(5.0, 170.0, 15.0),
    ThicknessPrice(8.0, 260.0, 25.0),
]


def load_price_book(path: Path) -> Tuple[str, List[ThicknessPrice]]:
    if not path.exists():
        return "RUB", list(DEFAULT_PRICES)

    data = json.loads(path.read_text(encoding="utf-8"))
    prices = [
        ThicknessPrice(
            float(item["thickness_mm"]),
            float(item["cut_price_per_meter"]),
            float(item["pierce_price"]),
        )
        for item in data.get("prices", [])
    ]
    return str(data.get("currency", "RUB")), prices or list(DEFAULT_PRICES)


def save_price_book(path: Path, currency: str, prices: Sequence[ThicknessPrice]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "currency": currency,
        "prices": [
            {
                "thickness_mm": price.thickness_mm,
                "cut_price_per_meter": price.cut_price_per_meter,
                "pierce_price": price.pierce_price,
            }
            for price in sorted(prices, key=lambda item: item.thickness_mm)
        ],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def analyze_iges_file(path: str | Path, pierce_tolerance_mm: float = 0.1) -> AnalysisResult:
    path = Path(path)
    try:
        from occ_iges_calc import analyze_iges_file_with_occ

        return analyze_iges_file_with_occ(path, pierce_tolerance_mm=pierce_tolerance_mm)
    except ImportError as exc:
        iges = _IGESDocument.from_file(path)
        result = iges.analyze(pierce_tolerance_mm=pierce_tolerance_mm)
        result.warnings.insert(
            0,
            "Open Cascade 3D reader is not installed; used basic IGES curve parser instead. "
            f"3D solids and surfaces will not be reliable. Details: {exc}",
        )
        return result
    except Exception as exc:
        fallback_warning = (
            "Open Cascade 3D reader failed; used basic IGES curve parser instead. "
            f"Open Cascade error: {exc}"
        )
        iges = _IGESDocument.from_file(path)
        result = iges.analyze(pierce_tolerance_mm=pierce_tolerance_mm)
        result.warnings.insert(0, fallback_warning)
        return result


class _IGESDocument:
    def __init__(
        self,
        path: Path,
        global_lines: List[str],
        directory_lines: List[str],
        parameter_lines: List[str],
    ) -> None:
        self.path = path
        self.global_lines = global_lines
        self.directory_entries = _parse_directory_entries(directory_lines)
        self.parameter_groups = _parse_parameter_groups(parameter_lines)
        self.param_delim, self.record_delim, self.unit_name, self.unit_to_mm = (
            _parse_global_settings(global_lines)
        )
        self._parsed: Dict[int, CurveEntity] = {}

    @classmethod
    def from_file(cls, path: Path) -> "_IGESDocument":
        lines = path.read_text(errors="replace").splitlines()
        sections: Dict[str, List[str]] = {"G": [], "D": [], "P": []}
        for line in lines:
            section = _line_section(line)
            if section in sections:
                sections[section].append(line)
        if not sections["D"] or not sections["P"]:
            raise ValueError("IGES file does not contain readable D/P sections.")
        return cls(path, sections["G"], sections["D"], sections["P"])

    def analyze(self, pierce_tolerance_mm: float = 0.1) -> AnalysisResult:
        curves: List[CurveEntity] = []
        unsupported: Dict[int, int] = {}
        warnings: List[str] = []

        for de_id in sorted(self.directory_entries):
            curve = self._parse_entity(de_id)
            if curve is None:
                continue
            if curve.length_mm > 0:
                curves.append(curve)
            if not curve.supported:
                unsupported[curve.entity_type] = unsupported.get(curve.entity_type, 0) + 1
            warnings.extend(curve.warnings)

        referenced = set()
        for curve in curves:
            referenced.update(curve.references)

        top_level_curves = [
            curve for curve in curves if curve.de_id not in referenced and curve.length_mm > 0
        ]
        if not top_level_curves:
            top_level_curves = curves

        cut_length_mm = sum(curve.length_mm for curve in top_level_curves)
        pierces = _estimate_pierces(top_level_curves, pierce_tolerance_mm)

        if unsupported:
            details = ", ".join(
                f"type {entity_type}: {count}" for entity_type, count in sorted(unsupported.items())
            )
            warnings.append(f"Unsupported or ignored IGES entities: {details}.")

        transformed = [
            entry_id
            for entry_id, entry in self.directory_entries.items()
            if entry.transform_pointer not in (0, None)
        ]
        if transformed:
            warnings.append(
                "Some entities reference transformation matrices; this MVP does not apply them."
            )

        return AnalysisResult(
            path=self.path,
            unit_name=self.unit_name,
            unit_to_mm=self.unit_to_mm,
            cut_length_mm=cut_length_mm,
            pierces=pierces,
            curves=top_level_curves,
            warnings=_dedupe(warnings),
            unsupported_entities=unsupported,
            backend="basic-iges",
            calculation_mode="2D/3D curve entities",
        )

    def _parse_entity(self, de_id: int) -> Optional[CurveEntity]:
        if de_id in self._parsed:
            return self._parsed[de_id]

        entry = self.directory_entries.get(de_id)
        text = self.parameter_groups.get(de_id)
        if entry is None or text is None:
            return None

        tokens = _tokenize_parameters(text, self.param_delim, self.record_delim)
        if not tokens:
            return None

        entity_type = _to_int(tokens[0], default=entry.entity_type)
        parser = {
            100: self._parse_circular_arc,
            102: self._parse_composite_curve,
            106: self._parse_copious_data,
            110: self._parse_line,
            126: self._parse_bspline_curve,
        }.get(entity_type)

        if parser is None:
            curve = CurveEntity(
                de_id=de_id,
                entity_type=entity_type,
                length_mm=0.0,
                supported=entity_type in {116, 124},
            )
            self._parsed[de_id] = curve
            return curve

        try:
            curve = parser(de_id, tokens)
        except Exception as exc:  # Keep GUI usable on imperfect vendor files.
            curve = CurveEntity(
                de_id=de_id,
                entity_type=entity_type,
                length_mm=0.0,
                supported=False,
                warnings=[f"Could not parse IGES entity {de_id} type {entity_type}: {exc}."]
            )
        self._parsed[de_id] = curve
        return curve

    def _parse_line(self, de_id: int, tokens: Sequence[str]) -> CurveEntity:
        values = [_to_float(token) for token in tokens[1:7]]
        if len(values) < 6 or any(value is None for value in values):
            raise ValueError("line entity has incomplete coordinates")
        x1, y1, z1, x2, y2, z2 = [float(value) for value in values]
        start = self._scale_point((x1, y1, z1))
        end = self._scale_point((x2, y2, z2))
        return CurveEntity(
            de_id=de_id,
            entity_type=110,
            length_mm=_distance(start, end),
            start=start,
            end=end,
        )

    def _parse_circular_arc(self, de_id: int, tokens: Sequence[str]) -> CurveEntity:
        values = [_to_float(token) for token in tokens[1:8]]
        if len(values) < 7 or any(value is None for value in values):
            raise ValueError("circular arc entity has incomplete coordinates")
        zt, cx, cy, sx, sy, ex, ey = [float(value) for value in values]

        center = self._scale_point((cx, cy, zt))
        start = self._scale_point((sx, sy, zt))
        end = self._scale_point((ex, ey, zt))
        radius = _distance(center, start)
        if radius == 0:
            raise ValueError("circular arc radius is zero")

        start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
        end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
        sweep = end_angle - start_angle
        while sweep <= 0:
            sweep += math.tau
        if _distance(start, end) <= 1e-7:
            sweep = math.tau

        warnings: List[str] = []
        end_radius = _distance(center, end)
        if abs(end_radius - radius) > 0.05:
            warnings.append(f"Arc {de_id} has inconsistent start/end radius.")

        return CurveEntity(
            de_id=de_id,
            entity_type=100,
            length_mm=radius * sweep,
            start=start,
            end=end,
            warnings=warnings,
        )

    def _parse_composite_curve(self, de_id: int, tokens: Sequence[str]) -> CurveEntity:
        count = _to_int(tokens[1], default=0) if len(tokens) > 1 else 0
        refs = [_to_int(token, default=0) for token in tokens[2 : 2 + count]]
        refs = [ref for ref in refs if ref]
        children = [self._parse_entity(ref) for ref in refs]
        child_curves = [child for child in children if child is not None and child.length_mm > 0]
        warnings: List[str] = []
        for child in children:
            if child is not None:
                warnings.extend(child.warnings)

        start = child_curves[0].start if child_curves else None
        end = child_curves[-1].end if child_curves else None
        nested_refs: List[int] = []
        for child in child_curves:
            nested_refs.extend(child.references)

        return CurveEntity(
            de_id=de_id,
            entity_type=102,
            length_mm=sum(child.length_mm for child in child_curves),
            start=start,
            end=end,
            references=refs + nested_refs,
            warnings=_dedupe(warnings),
            supported=all(child.supported for child in children if child is not None),
        )

    def _parse_copious_data(self, de_id: int, tokens: Sequence[str]) -> CurveEntity:
        if len(tokens) < 4:
            raise ValueError("copious data entity is incomplete")
        tuple_count = _to_int(tokens[2], default=0)
        raw_values = [_to_float(token) for token in tokens[3:]]
        values = [float(value) for value in raw_values if value is not None]
        points: List[Point3] = []

        if tuple_count <= 0:
            raise ValueError("copious data entity has no points")
        if len(values) >= 1 + tuple_count * 2 and len(values) < tuple_count * 3:
            zt = values[0]
            coords = values[1 : 1 + tuple_count * 2]
            points = [
                self._scale_point((coords[index], coords[index + 1], zt))
                for index in range(0, len(coords), 2)
            ]
        elif len(values) >= tuple_count * 3:
            coords = values[: tuple_count * 3]
            points = [
                self._scale_point((coords[index], coords[index + 1], coords[index + 2]))
                for index in range(0, len(coords), 3)
            ]
        elif len(values) >= tuple_count * 2:
            coords = values[: tuple_count * 2]
            points = [
                self._scale_point((coords[index], coords[index + 1], 0.0))
                for index in range(0, len(coords), 2)
            ]
        else:
            raise ValueError("copious data entity has too few coordinates")

        length = sum(_distance(points[index], points[index + 1]) for index in range(len(points) - 1))
        return CurveEntity(
            de_id=de_id,
            entity_type=106,
            length_mm=length,
            start=points[0],
            end=points[-1],
            warnings=[f"Copious data entity {de_id} is treated as a polyline."],
        )

    def _parse_bspline_curve(self, de_id: int, tokens: Sequence[str]) -> CurveEntity:
        if len(tokens) < 10:
            raise ValueError("B-spline entity is incomplete")
        k = _to_int(tokens[1], default=-1)
        degree = _to_int(tokens[2], default=-1)
        if k < 1 or degree < 1:
            raise ValueError("B-spline has invalid degree/control count")

        control_count = k + 1
        knot_count = control_count + degree + 1
        cursor = 7
        knots = [_to_float(token) for token in tokens[cursor : cursor + knot_count]]
        cursor += knot_count
        weights = [_to_float(token) for token in tokens[cursor : cursor + control_count]]
        cursor += control_count

        coord_values = [_to_float(token) for token in tokens[cursor : cursor + control_count * 3]]
        cursor += control_count * 3
        if (
            len(knots) < knot_count
            or len(weights) < control_count
            or len(coord_values) < control_count * 3
            or any(value is None for value in knots + weights + coord_values)
        ):
            raise ValueError("B-spline arrays are incomplete")

        knot_values = [float(value) for value in knots]
        weight_values = [float(value) for value in weights]
        raw_coords = [float(value) for value in coord_values]
        control_points = [
            self._scale_point(
                (raw_coords[index], raw_coords[index + 1], raw_coords[index + 2])
            )
            for index in range(0, len(raw_coords), 3)
        ]

        v0 = _to_float(tokens[cursor]) if cursor < len(tokens) else None
        v1 = _to_float(tokens[cursor + 1]) if cursor + 1 < len(tokens) else None
        start_param = float(v0) if v0 is not None else knot_values[degree]
        end_param = float(v1) if v1 is not None else knot_values[control_count]
        if end_param <= start_param:
            start_param, end_param = knot_values[degree], knot_values[control_count]
        if end_param <= start_param:
            raise ValueError("B-spline parameter range is invalid")

        sample_count = max(32, min(1200, control_count * 32))
        points = [
            _evaluate_rational_bspline(
                knot_values,
                weight_values,
                control_points,
                degree,
                start_param + (end_param - start_param) * index / sample_count,
            )
            for index in range(sample_count + 1)
        ]
        length = sum(_distance(points[index], points[index + 1]) for index in range(sample_count))
        return CurveEntity(
            de_id=de_id,
            entity_type=126,
            length_mm=length,
            start=points[0],
            end=points[-1],
            warnings=[f"B-spline entity {de_id} length is approximated by sampling."],
        )

    def _scale_point(self, point: Point3) -> Point3:
        return (
            point[0] * self.unit_to_mm,
            point[1] * self.unit_to_mm,
            point[2] * self.unit_to_mm,
        )


@dataclass
class _DirectoryEntry:
    de_id: int
    entity_type: int
    parameter_pointer: int
    transform_pointer: int
    form_number: int


def _parse_directory_entries(lines: Sequence[str]) -> Dict[int, _DirectoryEntry]:
    entries: Dict[int, _DirectoryEntry] = {}
    for index in range(0, len(lines) - 1, 2):
        first = _fixed_fields(lines[index], 8, 9)
        second = _fixed_fields(lines[index + 1], 8, 9)
        de_id = _sequence_number(lines[index], fallback=index + 1)
        entity_type = _to_int(first[0], default=0)
        entries[de_id] = _DirectoryEntry(
            de_id=de_id,
            entity_type=entity_type,
            parameter_pointer=_to_int(first[1], default=0),
            transform_pointer=_to_int(first[6], default=0),
            form_number=_to_int(second[4], default=0),
        )
    return entries


def _parse_parameter_groups(lines: Sequence[str]) -> Dict[int, str]:
    groups: Dict[int, List[str]] = {}
    for line in lines:
        de_id = _to_int(line[64:72], default=0) if len(line) >= 72 else 0
        if de_id == 0:
            de_id = 1
        groups.setdefault(de_id, []).append(line[:64])
    return {de_id: "".join(parts) for de_id, parts in groups.items()}


def _parse_global_settings(lines: Sequence[str]) -> Tuple[str, str, str, float]:
    text = "".join(line[:72] for line in lines)
    tokens = _tokenize_parameters(text, ",", ";")
    param_delim = tokens[0] if tokens and len(tokens[0]) == 1 else ","
    record_delim = tokens[1] if len(tokens) > 1 and len(tokens[1]) == 1 else ";"
    if (param_delim, record_delim) != (",", ";"):
        tokens = _tokenize_parameters(text, param_delim, record_delim)

    unit_flag = _to_int(tokens[13], default=2) if len(tokens) > 13 else 2
    unit_name = tokens[14].strip() if len(tokens) > 14 and tokens[14].strip() else "MM"
    return param_delim, record_delim, unit_name, _unit_to_mm(unit_flag, unit_name)


def _tokenize_parameters(text: str, param_delim: str = ",", record_delim: str = ";") -> List[str]:
    tokens: List[str] = []
    current: List[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == param_delim or char == record_delim:
            tokens.append("".join(current).strip())
            current = []
            index += 1
            if char == record_delim:
                break
            continue

        if char.isdigit():
            start = index
            while index < len(text) and text[index].isdigit():
                index += 1
            if index < len(text) and text[index] in {"H", "h"}:
                length = int(text[start:index])
                index += 1
                current.append(text[index : index + length])
                index += length
                continue
            current.append(text[start:index])
            continue

        current.append(char)
        index += 1

    if current:
        tokens.append("".join(current).strip())
    return tokens


def _evaluate_rational_bspline(
    knots: Sequence[float],
    weights: Sequence[float],
    points: Sequence[Point3],
    degree: int,
    t: float,
) -> Point3:
    n = len(points)
    if t >= knots[n]:
        return points[-1]

    basis = [0.0] * n
    for i in range(n):
        if knots[i] <= t < knots[i + 1]:
            basis[i] = 1.0
    if not any(basis):
        basis[-1] = 1.0

    for p in range(1, degree + 1):
        next_basis = [0.0] * n
        for i in range(n):
            left = 0.0
            left_den = knots[i + p] - knots[i]
            if left_den:
                left = (t - knots[i]) / left_den * basis[i]

            right = 0.0
            if i + 1 < n:
                right_den = knots[i + p + 1] - knots[i + 1]
                if right_den:
                    right = (knots[i + p + 1] - t) / right_den * basis[i + 1]
            next_basis[i] = left + right
        basis = next_basis

    denominator = sum(basis[i] * weights[i] for i in range(n))
    if abs(denominator) < 1e-12:
        return points[0]
    return tuple(
        sum(basis[i] * weights[i] * points[i][axis] for i in range(n)) / denominator
        for axis in range(3)
    )  # type: ignore[return-value]


def _estimate_pierces(curves: Sequence[CurveEntity], tolerance_mm: float) -> int:
    active = [curve for curve in curves if curve.start is not None and curve.end is not None]
    if not active:
        return 0

    parent = list(range(len(active)))

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

    endpoints: List[Tuple[int, Point3]] = []
    for index, curve in enumerate(active):
        endpoints.append((index, curve.start))  # type: ignore[arg-type]
        endpoints.append((index, curve.end))  # type: ignore[arg-type]

    for left_index in range(len(endpoints)):
        left_curve, left_point = endpoints[left_index]
        for right_index in range(left_index + 1, len(endpoints)):
            right_curve, right_point = endpoints[right_index]
            if left_curve == right_curve:
                continue
            if _distance(left_point, right_point) <= tolerance_mm:
                union(left_curve, right_curve)

    return len({find(index) for index in range(len(active))})


def _line_section(line: str) -> str:
    if len(line) > 72 and line[72] in {"S", "G", "D", "P", "T"}:
        return line[72]
    return ""


def _fixed_fields(line: str, width: int, count: int) -> List[str]:
    padded = line[: width * count].ljust(width * count)
    return [padded[index : index + width].strip() for index in range(0, width * count, width)]


def _sequence_number(line: str, fallback: int) -> int:
    if len(line) >= 80:
        return _to_int(line[73:80], default=fallback)
    return fallback


def _to_float(value: str, default: Optional[float] = None) -> Optional[float]:
    value = value.strip()
    if not value:
        return default
    try:
        return float(value.replace("D", "E").replace("d", "E"))
    except ValueError:
        return default


def _to_int(value: str, default: int = 0) -> int:
    number = _to_float(value)
    if number is None:
        return default
    return int(number)


def _distance(left: Point3, right: Point3) -> float:
    return math.sqrt(
        (left[0] - right[0]) ** 2
        + (left[1] - right[1]) ** 2
        + (left[2] - right[2]) ** 2
    )


def _unit_to_mm(unit_flag: int, unit_name: str) -> float:
    by_flag = {
        1: 25.4,
        2: 1.0,
        4: 304.8,
        6: 1000.0,
        7: 1_000_000.0,
        8: 0.0254,
        9: 0.001,
        10: 10.0,
        11: 0.0000254,
    }
    if unit_flag in by_flag:
        return by_flag[unit_flag]

    normalized = unit_name.upper().replace(".", "").strip()
    if normalized in {"MM", "MILLIMETER", "MILLIMETERS"}:
        return 1.0
    if normalized in {"IN", "INCH", "INCHES"}:
        return 25.4
    if normalized in {"CM", "CENTIMETER", "CENTIMETERS"}:
        return 10.0
    if normalized in {"M", "METER", "METERS"}:
        return 1000.0
    return 1.0


def _dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
