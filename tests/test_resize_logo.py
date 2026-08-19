"""Tests for the logo resizer utility script."""

from pathlib import Path

import pytest
from PIL import Image

from scripts.resize_logo import LogoResizer, LogoTargetSpec, build_arg_parser


@pytest.fixture
def temp_logo_source(tmp_path: Path) -> Path:
    """Fixture creating a temporary dummy RGBA source image.

    Args:
        tmp_path (Path): Pytest temporary directory fixture.

    Returns:
        Path: Path to the dummy master logo image.
    """
    source_file = tmp_path / "logo-original.png"
    img = Image.new("RGBA", (100, 100), (0, 198, 255, 255))
    img.save(source_file, "PNG")
    return source_file


def test_logo_resizer_generate_success(temp_logo_source: Path, tmp_path: Path) -> None:
    """Test successful generation of logo assets at specified sizes.

    Args:
        temp_logo_source (Path): Fixture path to dummy source logo.
        tmp_path (Path): Pytest temporary directory fixture.
    """
    output_dir = tmp_path / "output_static"
    specs = [
        LogoTargetSpec(name="Test Small", filename="small.png", width=16, height=16),
        LogoTargetSpec(name="Test Large", filename="large.png", width=64, height=64),
    ]

    resizer = LogoResizer(
        source_path=temp_logo_source, output_dir=output_dir, specs=specs
    )
    generated_paths = resizer.generate(dry_run=False)

    assert len(generated_paths) == 2
    for p in generated_paths:
        assert p.is_file()

    with Image.open(output_dir / "small.png") as small_img:
        assert small_img.size == (16, 16)

    with Image.open(output_dir / "large.png") as large_img:
        assert large_img.size == (64, 64)


def test_logo_resizer_dry_run(temp_logo_source: Path, tmp_path: Path) -> None:
    """Test that dry_run mode previews without creating files.

    Args:
        temp_logo_source (Path): Fixture path to dummy source logo.
        tmp_path (Path): Pytest temporary directory fixture.
    """
    output_dir = tmp_path / "dry_run_static"
    specs = [
        LogoTargetSpec(name="Test Small", filename="small.png", width=16, height=16)
    ]

    resizer = LogoResizer(
        source_path=temp_logo_source, output_dir=output_dir, specs=specs
    )
    generated_paths = resizer.generate(dry_run=True)

    assert len(generated_paths) == 1
    assert not (output_dir / "small.png").exists()


def test_logo_resizer_missing_source_raises(tmp_path: Path) -> None:
    """Test that missing source image raises FileNotFoundError.

    Args:
        tmp_path (Path): Pytest temporary directory fixture.
    """
    missing_source = tmp_path / "does_not_exist.png"
    output_dir = tmp_path / "output_static"
    resizer = LogoResizer(source_path=missing_source, output_dir=output_dir)

    with pytest.raises(FileNotFoundError):
        resizer.generate()


def test_build_arg_parser_defaults() -> None:
    """Test building CLI argument parser with default values."""
    parser = build_arg_parser()
    args = parser.parse_args([])
    assert args.source.name == "logo-original.png"
    assert args.output_dir.name == "static"
    assert args.dry_run is False
