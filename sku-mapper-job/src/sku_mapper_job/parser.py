"""Parse Azure VM SKU names into structured components.

Handles the ``Standard_<family><vcpus><suffix>[_<model>]_v<N>`` naming convention,
including multi-letter families (NC, ND, NV, HB, HC, DC …), constrained vCPU counts
(D32-16s_v3), space-separated versions (D16a v4), and embedded GPU model names
(A100, H100, MI300X …).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sku_mapper_job.mapping import (
    MULTI_LETTER_FAMILIES,
    SUFFIX_TAGS,
    category_for_family,
)

# Primary regex — handles the vast majority of Standard_… SKU names.
#
# Group layout (named):
#   family  – one or more uppercase letters (family prefix)
#   vcpus   – first run of digits (vCPU count, optional)
#   suffix  – lowercase letters before an optional _<model>_v<N> or _v<N> or end
#   version – optional trailing v<N> (e.g. v5)
#
# Supports:
#   Standard_D2s_v5          – normal
#   Standard_D32-16s_v3      – constrained vCPU (consumed, not captured)
#   Standard_D16a v4         – space before version
#   Standard_Das             – no vCPU digits
#   Standard_NC24ads_A100_v4 – model segment
_SKU_RE = re.compile(
    r"^Standard_"
    r"(?P<family>[A-Z]+)"  # family letters
    r"(?P<vcpus>\d+)?"  # optional vCPU count
    r"(?:-\d+)?"  # optional constrained vCPU (e.g. -16)
    r"(?P<suffix>[a-z]*)"  # optional lowercase suffix (s, ds, ads, …)
    r"(?:[_ ][A-Za-z0-9]+)*?"  # optional model segment(s) (_A100, _H100, …)
    r"(?:[_ ](?P<version>v\d+))?"  # optional version (_v5, " v4")
    r"$",
    re.ASCII,
)

# Fallback regex for compressed names like Standard_Dv21 (= Standard_D1_v2).
# Pattern: Standard_<family>v<version><vcpus>
_SKU_FALLBACK_RE = re.compile(
    r"^Standard_"
    r"(?P<family>[A-Z]+)"  # family letters
    r"(?P<version>v\d)"  # version (always single digit: v2, v3 …)
    r"(?P<vcpus>\d+)?"  # optional trailing vCPU count
    r"(?P<suffix>[a-z]*)"  # optional suffix
    r"$",
    re.ASCII,
)


@dataclass(frozen=True, slots=True)
class SkuInfo:
    """Parsed components of an Azure VM SKU name."""

    sku_name: str
    tier: str | None = None
    family: str | None = None
    series: str | None = None
    version: str | None = None
    vcpus: int | None = None
    sku_type: str | None = None
    category: str = "other"
    workload_tags: list[str] = field(default_factory=list)


def _extract_family(raw_family: str) -> str:
    """Narrow a raw family match to the canonical multi-letter or single-letter family."""
    upper = raw_family.upper()
    # Check multi-letter families longest-first
    for mf in sorted(MULTI_LETTER_FAMILIES, key=len, reverse=True):
        if upper.startswith(mf):
            return mf
    # Fallback: first letter only
    return upper[0] if upper else upper


def _derive_suffix_tags(suffix: str) -> list[str]:
    """Convert a suffix string like ``ads`` into a sorted list of workload tags."""
    tags: list[str] = []
    for ch in suffix.lower():
        tag = SUFFIX_TAGS.get(ch)
        if tag and tag not in tags:
            tags.append(tag)
    return sorted(tags)


def _derive_sku_type(family: str, suffix: str, version: str | None) -> str:
    """Build a normalised SKU type string like ``Dsv5`` or ``NCadsA100v4``."""
    # Capitalise family, keep suffix lowercase, append version without underscore
    result = family.upper() + suffix.lower()
    if version:
        result += version
    return result


def _derive_series(family: str, suffix: str, version: str | None) -> str:
    """Build a series string like ``Dsv5``."""
    # Series is the same as sku_type for most cases
    return _derive_sku_type(family, suffix, version)


def _build_sku_info(
    sku_name: str,
    raw_family: str,
    vcpus_str: str | None,
    suffix: str,
    version: str | None,
) -> SkuInfo:
    """Build a SkuInfo from parsed regex groups."""
    family = _extract_family(raw_family)
    vcpus = int(vcpus_str) if vcpus_str else None
    category = category_for_family(family)
    tags = _derive_suffix_tags(suffix)
    sku_type = _derive_sku_type(family, suffix, version)
    series = _derive_series(family, suffix, version)

    return SkuInfo(
        sku_name=sku_name,
        tier="Standard",
        family=family,
        series=series,
        version=version,
        vcpus=vcpus,
        sku_type=sku_type,
        category=category,
        workload_tags=tags,
    )


def parse_sku(sku_name: str) -> SkuInfo:
    """Parse an Azure VM SKU name into structured components.

    Tries the primary regex first, then a fallback for compressed names like
    ``Standard_Dv21``. Returns an ``SkuInfo`` with ``category='other'`` and
    ``None`` fields when neither pattern matches.
    """
    m = _SKU_RE.match(sku_name)
    if m is not None:
        return _build_sku_info(
            sku_name=sku_name,
            raw_family=m.group("family"),
            vcpus_str=m.group("vcpus"),
            suffix=m.group("suffix") or "",
            version=m.group("version"),
        )

    m = _SKU_FALLBACK_RE.match(sku_name)
    if m is not None:
        return _build_sku_info(
            sku_name=sku_name,
            raw_family=m.group("family"),
            vcpus_str=m.group("vcpus"),
            suffix=m.group("suffix") or "",
            version=m.group("version"),
        )

    return SkuInfo(sku_name=sku_name)
