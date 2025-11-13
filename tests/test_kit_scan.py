from pathlib import Path

from scripts.match_materials import match_materials


def _write_text(path: Path, content: str = "stub") -> None:
    path.write_text(content, encoding="utf-8")


def test_match_materials_excludes_canonical_stems(tmp_path):
    materials = tmp_path / "materials"
    materials.mkdir()
    norm_dir = tmp_path / "norm"
    norm_dir.mkdir()

    _write_text(materials / "foo.words.json", "{}")
    _write_text(materials / "foo.canonical.words.json", "{}")
    _write_text(materials / "foo.txt", "main")
    _write_text(materials / "foo.canonical.txt", "derived")
    (materials / "foo.m4a").write_bytes(b"\x00")

    kits = match_materials(
        materials,
        norm_dir,
        ["*.txt"],
        "*.words.json",
        "*.m4a",
    )
    stems = [kit.stem for kit in kits]
    assert stems == ["foo"], stems

    kits_with_canonical = match_materials(
        materials,
        norm_dir,
        ["*.txt"],
        "*.words.json",
        "*.m4a",
        include_canonical_kits=True,
    )
    assert sorted(kit.stem for kit in kits_with_canonical) == ["foo", "foo.canonical"]
