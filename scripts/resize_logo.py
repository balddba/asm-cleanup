"""Utility script for resizing and generating project logo assets.

This module loads the high-resolution master logo asset and resizes it into
all required static icon, favicon, and brand image variations for the web dashboard.
"""

import argparse
import sys
from pathlib import Path
from typing import ClassVar

from loguru import logger
from PIL import Image
from pydantic import BaseModel, Field


class LogoTargetSpec(BaseModel):
    """Specification model for a resized logo output asset shape.

    Attributes:
        name (str): Human-readable descriptive name of the asset.
        filename (str): Output filename relative to the target static directory.
        width (int): Output width in pixels.
        height (int): Output height in pixels.
    """

    model_config = {"extra": "forbid"}

    name: str = Field(..., description="Descriptive asset name")
    filename: str = Field(..., description="Target output filename")
    width: int = Field(..., gt=0, description="Target pixel width")
    height: int = Field(..., gt=0, description="Target pixel height")


class LogoResizer:
    """Manages loading the master logo and generating all target static assets.

    Attributes:
        source_path (Path): Path to the high-resolution master logo image.
        output_dir (Path): Output directory where resized assets are written.
        specs (list[LogoTargetSpec]): List of target size specifications.
    """

    DEFAULT_SPECS: ClassVar[list[LogoTargetSpec]] = [
        LogoTargetSpec(
            name="Small Icon (32x32)",
            filename="logo-icon.png",
            width=32,
            height=32,
        ),
        LogoTargetSpec(
            name="Favicon 16x16",
            filename="favicon-16x16.png",
            width=16,
            height=16,
        ),
        LogoTargetSpec(
            name="Favicon 32x32",
            filename="favicon-32x32.png",
            width=32,
            height=32,
        ),
        LogoTargetSpec(
            name="Apple Touch Icon",
            filename="apple-touch-icon.png",
            width=180,
            height=180,
        ),
        LogoTargetSpec(
            name="Small Brand Badge",
            filename="logo-sm.png",
            width=64,
            height=64,
        ),
        LogoTargetSpec(
            name="Medium Brand Badge",
            filename="logo-md.png",
            width=128,
            height=128,
        ),
        LogoTargetSpec(
            name="Large Brand Emblem",
            filename="logo-lg.png",
            width=256,
            height=256,
        ),
        LogoTargetSpec(
            name="Full Emblem Badge",
            filename="logo-emblem.png",
            width=512,
            height=512,
        ),
        LogoTargetSpec(
            name="Standard Logo PNG",
            filename="logo.png",
            width=512,
            height=512,
        ),
        LogoTargetSpec(
            name="Website Favicon ICO",
            filename="favicon.ico",
            width=48,
            height=48,
        ),
        LogoTargetSpec(
            name="Documentation Logo PNG",
            filename="../docs/images/logo.png",
            width=512,
            height=512,
        ),
    ]

    def __init__(
        self,
        source_path: Path,
        output_dir: Path,
        specs: list[LogoTargetSpec] | None = None,
    ) -> None:
        """Initialize LogoResizer with source image and output directory paths.

        Args:
            source_path (Path): Path to the master logo file.
            output_dir (Path): Target directory for resized assets.
            specs (list[LogoTargetSpec] | None): Optional custom target specs list.
        """
        self.source_path = source_path
        self.output_dir = output_dir
        self.specs = specs or self.DEFAULT_SPECS

    def validate_source(self) -> None:
        """Validate that the source image exists and is readable.

        Raises:
            FileNotFoundError: If the source file does not exist.
        """
        if not self.source_path.is_file():
            logger.error("Source logo file not found: {}", self.source_path)
            raise FileNotFoundError(
                f"Master logo source not found at: {self.source_path}"
            )

    def generate(self, dry_run: bool = False) -> list[Path]:
        """Generate all configured logo asset sizes from the master image.

        Args:
            dry_run (bool): If True, previews operations without writing files.

        Returns:
            list[Path]: List of generated output file paths.

        Raises:
            FileNotFoundError: If the source image path is invalid.
        """
        self.validate_source()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Opening master logo image: {}", self.source_path)
        with Image.open(self.source_path) as master_img:
            master_rgba = master_img.convert("RGBA")
            generated_paths: list[Path] = []

            for spec in self.specs:
                target_path = (self.output_dir / spec.filename).resolve()
                target_path.parent.mkdir(parents=True, exist_ok=True)
                if dry_run:
                    logger.info(
                        "[DRY-RUN] Would resize '{}' to {}x{} -> {}",
                        spec.name,
                        spec.width,
                        spec.height,
                        target_path,
                    )
                else:
                    if spec.filename.endswith(".ico"):
                        master_rgba.save(
                            target_path,
                            format="ICO",
                            sizes=[(16, 16), (32, 32), (48, 48)],
                        )
                    else:
                        resized_img = master_rgba.resize(
                            (spec.width, spec.height), Image.LANCZOS
                        )
                        resized_img.save(target_path, "PNG")
                    logger.success(
                        "Generated asset: {} ({}x{}) -> {}",
                        spec.name,
                        spec.width,
                        spec.height,
                        target_path,
                    )
                generated_paths.append(target_path)

            return generated_paths


def build_arg_parser() -> argparse.ArgumentParser:
    """Build command line argument parser for the logo resizer CLI.

    Returns:
        argparse.ArgumentParser: Configured argument parser instance.
    """
    project_root = Path(__file__).resolve().parent.parent
    default_source = project_root / "asm_cleanup" / "static" / "logo-original.png"
    default_output = project_root / "asm_cleanup" / "static"

    parser = argparse.ArgumentParser(
        description="Resize master logo into all web static asset shapes."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=default_source,
        help="Path to original master logo PNG (default: asm_cleanup/static/logo-original.png)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=default_output,
        help="Path to static output directory (default: asm_cleanup/static/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview resized asset operations without writing files.",
    )
    return parser


def main() -> None:
    """CLI entry point for running the logo resizer utility."""
    parser = build_arg_parser()
    args = parser.parse_args()

    resizer = LogoResizer(source_path=args.source, output_dir=args.output_dir)
    try:
        resizer.generate(dry_run=args.dry_run)
    except (FileNotFoundError, OSError) as exc:
        logger.exception("Failed to generate logo assets: {}", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
