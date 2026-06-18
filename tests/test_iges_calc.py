from pathlib import Path
import math
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from iges_calc import analyze_iges_file
from occ_iges_calc import _select_profile_tube_outer_faces_by_bounds


def section_line(payload, section, seq):
    return payload[:72].ljust(72) + section + f"{seq:7d}"


def directory_pair(entity_type, parameter_pointer, de_seq, form=0):
    first_fields = [
        entity_type,
        parameter_pointer,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    ]
    second_fields = [entity_type, 0, 0, 1, form, 0, 0, "", 0]
    first = "".join(f"{str(field):>8}" for field in first_fields)
    second = "".join(f"{str(field):>8}" for field in second_fields)
    return [
        section_line(first, "D", de_seq),
        section_line(second, "D", de_seq + 1),
    ]


def parameter_lines(text, de_seq, first_seq):
    lines = []
    for offset, start in enumerate(range(0, len(text), 64)):
        chunk = text[start : start + 64]
        payload = chunk.ljust(64) + f"{de_seq:8d}"
        lines.append(section_line(payload, "P", first_seq + offset))
    return lines


def write_iges(lines):
    handle = tempfile.NamedTemporaryFile("w", suffix=".igs", delete=False)
    handle.write("\n".join(lines))
    handle.close()
    return Path(handle.name)


def test_square_composite_curve():
    lines = [
        section_line("Simple square", "S", 1),
        section_line(
            "1H,,1H;,7HProduct,4HFile,6HSystem,3HPre,32,75,6,75,15,8HReceiver,1.,2,2HMM;",
            "G",
            1,
        ),
    ]
    de_sequences = [1, 3, 5, 7, 9]
    p_seq = 1
    entities = [
        (110, "110,0,0,0,10,0,0;"),
        (110, "110,10,0,0,10,10,0;"),
        (110, "110,10,10,0,0,10,0;"),
        (110, "110,0,10,0,0,0,0;"),
        (102, "102,4,1,3,5,7;"),
    ]
    for de_seq, (entity_type, param_text) in zip(de_sequences, entities):
        lines.extend(directory_pair(entity_type, p_seq, de_seq))
        p_lines = parameter_lines(param_text, de_seq, p_seq)
        lines.extend(p_lines)
        p_seq += len(p_lines)
    lines.append(section_line("S      1G      1D     10P      5", "T", 1))

    path = write_iges(lines)
    try:
        result = analyze_iges_file(path, pierce_tolerance_mm=0.001)
        assert math.isclose(result.cut_length_mm, 40.0, rel_tol=1e-9)
        assert result.pierces == 1
    finally:
        path.unlink(missing_ok=True)


def test_full_circle_arc():
    lines = [
        section_line("Full circle", "S", 1),
        section_line(
            "1H,,1H;,7HProduct,4HFile,6HSystem,3HPre,32,75,6,75,15,8HReceiver,1.,2,2HMM;",
            "G",
            1,
        ),
    ]
    lines.extend(directory_pair(100, 1, 1))
    lines.extend(parameter_lines("100,0,0,0,1,0,1,0;", 1, 1))
    lines.append(section_line("S      1G      1D      2P      1", "T", 1))

    path = write_iges(lines)
    try:
        result = analyze_iges_file(path, pierce_tolerance_mm=0.001)
        assert math.isclose(result.cut_length_mm, math.tau, rel_tol=1e-9)
        assert result.pierces == 1
    finally:
        path.unlink(missing_ok=True)


def test_profile_tube_outer_face_selection():
    bounds = (-50.0, -25.0, 0.0, 50.0, 25.0, 1000.0)
    faces = [
        {"normal": (1.0, 0.0, 0.0), "center": (50.0, 0.0, 500.0), "name": "outer-right"},
        {"normal": (-1.0, 0.0, 0.0), "center": (-50.0, 0.0, 500.0), "name": "outer-left"},
        {"normal": (0.0, 1.0, 0.0), "center": (0.0, 25.0, 500.0), "name": "outer-top"},
        {"normal": (0.0, -1.0, 0.0), "center": (0.0, -25.0, 500.0), "name": "outer-bottom"},
        {"normal": (1.0, 0.0, 0.0), "center": (45.0, 0.0, 500.0), "name": "inner-right"},
        {"normal": (0.0, 0.0, 1.0), "center": (0.0, 0.0, 1000.0), "name": "end-cut"},
    ]

    selected = _select_profile_tube_outer_faces_by_bounds(faces, bounds)
    assert {face["name"] for face in selected} == {
        "outer-right",
        "outer-left",
        "outer-top",
        "outer-bottom",
    }


if __name__ == "__main__":
    test_square_composite_curve()
    test_full_circle_arc()
    test_profile_tube_outer_face_selection()
    print("OK")
