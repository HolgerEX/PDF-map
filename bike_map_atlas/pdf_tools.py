"""PDF utilities for merging and splitting."""

from __future__ import annotations

import os
from pathlib import Path

import PyPDF2


def merge_pdfs(pdf_list: list[Path | str], output_path: str | Path) -> None:
    """Merge multiple PDFs into a single file."""
    merger = PyPDF2.PdfMerger()
    for pdf in pdf_list:
        merger.append(str(pdf))
    merger.write(str(output_path))
    merger.close()


def split_pdfs_by_km(
    pdf_list: list[Path | str],
    sections: list,
    output_base: str | Path,
    km_per_file: float,
    renderer,
) -> int:
    """
    Split PDF files by accumulated distance.

    Args:
        pdf_list: List of PDF paths (first is overview, rest are sections).
        sections: List of MapSection objects.
        output_base: Base directory and name for output files.
        km_per_file: Target kilometers per output file.
        renderer: MapRenderer instance for generating part overviews.

    Returns:
        Number of output files created.
    """
    output_base = Path(output_base)
    output_dir = output_base.parent
    full_overview = pdf_list[0]
    section_pdfs = pdf_list[1:]

    # Group sections by km
    parts = []
    current_part_sections = []
    current_km = 0.0

    for sec in sections:
        current_km += (sec.end_m - sec.start_m) / 1000.0
        current_part_sections.append(sec)
        if current_km >= km_per_file:
            parts.append(current_part_sections)
            current_part_sections = []
            current_km = 0.0

    if current_part_sections:
        parts.append(current_part_sections)

    # Generate output files
    part_start_idx = 0
    for part_idx, part_sections in enumerate(parts):
        part_num = part_idx + 1
        out_file = output_dir / f"{output_base.stem}_part{part_num}.pdf"
        part_pdfs = []

        if part_num == 1:
            part_pdfs.append(full_overview)

        # Part-specific overview
        part_overview = output_dir / f"temp_part{part_num}_overview.pdf"
        part_total_km = sum((sec.end_m - sec.start_m) / 1000.0 for sec in part_sections)
        renderer.render_part_overview(part_sections, part_total_km, str(part_overview))
        part_pdfs.append(part_overview)

        # Add section PDFs
        num_sections = len(part_sections)
        part_pdfs.extend(section_pdfs[part_start_idx : part_start_idx + num_sections])
        part_start_idx += num_sections

        merge_pdfs(part_pdfs, str(out_file))
        os.unlink(part_overview)

    return len(parts)


def split_pdfs_by_filesize(
    pdf_list: list[Path | str],
    sections: list,
    output_base: str | Path,
    max_mb: float,
    renderer,
) -> int:
    """
    Split PDF files by accumulated file size.

    Args:
        pdf_list: List of PDF paths (first is overview, rest are sections).
        sections: List of MapSection objects.
        output_base: Base directory and name for output files.
        max_mb: Target megabytes per output file.
        renderer: MapRenderer instance for generating part overviews.

    Returns:
        Number of output files created.
    """
    output_base = Path(output_base)
    output_dir = output_base.parent
    full_overview = pdf_list[0]
    section_pdfs = pdf_list[1:]

    # Group sections by file size
    parts = []
    current_part_sections = []
    current_size = 0.0

    for sec, pdf_path in zip(sections, section_pdfs):
        size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
        if current_size + size_mb > max_mb and current_part_sections:
            parts.append(current_part_sections)
            current_part_sections = []
            current_size = 0.0
        current_part_sections.append(sec)
        current_size += size_mb

    if current_part_sections:
        parts.append(current_part_sections)

    # Generate output files
    part_start_idx = 0
    for part_idx, part_sections in enumerate(parts):
        part_num = part_idx + 1
        out_file = output_dir / f"{output_base.stem}_part{part_num}.pdf"
        part_pdfs = []

        if part_num == 1:
            part_pdfs.append(full_overview)

        # Part-specific overview
        part_overview = output_dir / f"temp_part{part_num}_overview.pdf"
        part_total_km = sum((sec.end_m - sec.start_m) / 1000.0 for sec in part_sections)
        renderer.render_part_overview(part_sections, part_total_km, str(part_overview))
        part_pdfs.append(part_overview)

        # Add section PDFs
        num_sections = len(part_sections)
        part_pdfs.extend(section_pdfs[part_start_idx : part_start_idx + num_sections])
        part_start_idx += num_sections

        merge_pdfs(part_pdfs, str(out_file))
        os.unlink(part_overview)

    return len(parts)
