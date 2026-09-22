from __future__ import annotations

import importlib
import sys
from typing import Any

import pandas as pd


class IDataUnavailableError(RuntimeError):
    """Raised when the optional IMF iData package cannot be used."""


def _utilities() -> Any:
    try:
        from imf_datatools import idata_utilities
    except ImportError as exc:
        raise IDataUnavailableError(
            "The optional imf_datatools package is not installed. "
            "Install it through your IMF-approved distribution or use the SDMX backend."
        ) from exc
    # WEO Live and other restricted databases may require the authenticated iData session.
    # The official helper sets this flag before querying private data.
    idata_utilities.PRIVATE = True
    return idata_utilities


class IDataQueryError(ValueError):
    """Raised when an iData query key cannot match the selected database."""


SCALE_LABELS = {0: "Units", 3: "Thousands", 6: "Millions", 9: "Billions"}
UNIT_LABELS = {
    "XDC": "National currency",
    "USD": "US dollars",
    "PCT": "Percent",
    "PC_GDP": "Percent of GDP",
    "PCT_GDP": "Percent of GDP",
    "IX": "Index",
}


def list_databases(
    keyword: str | list[str] | None = None,
    searchmode: str = "or",
    refresh: bool = False,
) -> Any:
    return _utilities().get_databases(
        keyword=keyword,
        searchmode=searchmode,
        refresh=refresh,
    )


def get_dimensions(
    database: str,
    keyword: str | list[str] | None = None,
    searchmode: str = "or",
    refresh: bool = False,
) -> Any:
    return _utilities().get_dimensions(
        database,
        keyword=keyword,
        searchmode=searchmode,
        refresh=refresh,
    )


def get_dimension_values(
    database: str,
    dimension: str,
    keyword: str | list[str] | None = None,
    searchmode: str = "or",
    refresh: bool = False,
) -> Any:
    return _utilities().get_dimension_values(
        database,
        dimension,
        keyword=keyword,
        searchmode=searchmode,
        refresh=refresh,
    )


def build_key(values: list[str], backend: str = "idata") -> str:
    """Build a key while respecting each backend's open-dimension syntax."""
    if backend == "idata":
        # iData represents all values with an empty segment, unlike SDMX's '*'.
        return ".".join("" if value.strip() in {"", "*"} else value.strip() for value in values)
    return ".".join(value.strip() or "*" for value in values)


def validate_key(database: str, key: str, dimensions: Any = None) -> None:
    """Validate the number of key segments against the database dimensions."""
    if not database.strip():
        raise IDataQueryError("An iData database must be selected before fetching data.")
    if not key.strip():
        raise IDataQueryError("The iData query key cannot be empty.")
    dimensions = dimensions if dimensions is not None else get_dimensions(database)
    expected = len(dimensions.index) if hasattr(dimensions, "index") else len(dimensions)
    actual = len(key.split("."))
    if actual != expected:
        raise IDataQueryError(
            f"The key has {actual} dimension segments, but '{database}' requires {expected}. "
            "Reload the database dimensions and check the key order."
        )


def _metadata_values(metadata: Any) -> tuple[int | None, str]:
    if metadata is None or len(metadata) == 0:
        return None, ""
    row = metadata.iloc[0]
    scale: int | None = None
    for column in ("scale", "SCALE"):
        if column in row.index and pd.notna(row[column]):
            try:
                scale = int(row[column])
            except (TypeError, ValueError):
                pass
            break
    unit = ""
    for column in ("unit", "UNIT", "units", "UNITS", "UNIT_MEASURE", "unit_measure"):
        if column in row.index and pd.notna(row[column]):
            raw = str(row[column])
            unit = UNIT_LABELS.get(raw, raw)
            break
    return scale, unit


def enrich_data(frame: Any, metadata: Any = None) -> Any:
    """Normalize iData output and attach human-readable scale/unit columns."""
    if frame is None:
        return frame
    result = frame.copy()
    if getattr(result.index, "name", None):
        result = result.reset_index()
    scale, unit = _metadata_values(metadata)
    if scale:
        excluded = {"dates", "date", "TIME_PERIOD", "SCALE"}
        value_columns = [
            column for column in result.select_dtypes(include="number").columns
            if str(column) not in excluded
        ]
        result[value_columns] = result[value_columns] / (10 ** scale)
    result["SCALE"] = SCALE_LABELS.get(scale, "")
    if unit:
        result["UNIT"] = unit
    return result


def fetch_data(
    database: str,
    key: str,
    start: str | None = None,
    end: str | None = None,
    longformat: bool = True,
    panel: str | None = None,
    enrich: bool = True,
) -> Any:
    if longformat and panel:
        raise ValueError("iData panel output cannot be combined with long format.")
    validate_key(database, key)
    frame = _utilities().get_idata_data(
        database,
        key=key,
        start=start,
        end=end,
        longformat=longformat,
        panel=panel,
    )
    if not enrich or frame is None or len(frame) == 0:
        return frame
    return enrich_data(frame, fetch_metadata(database, key))


def fetch_metadata(database: str, key: str) -> Any:
    """Retrieve metadata for a query without making metadata failure fatal to data."""
    try:
        representative_key = ".".join(part.split("+")[0] for part in key.split("."))
        return _utilities().get_idata_metadata(database, representative_key)
    except Exception:
        return None


def expand_country_group(query: str) -> dict[str, Any]:
    """Resolve an official WEO country group to its member ISO3 codes."""
    import imf_datatools

    groups = imf_datatools.get_weo_country_groups()
    description_column = next(
        (column for column in groups.columns
         if str(column).lower() in {"name", "description", "groupname", "group_name"}),
        groups.columns[0] if len(groups.columns) else None,
    )
    if description_column is None:
        raise RuntimeError("The WEO country-group response has no description column.")
    normalized = query.strip().lower()
    if query.strip().upper() in {str(code).upper() for code in groups.index}:
        matches = groups[groups.index.astype(str).str.upper() == query.strip().upper()]
    else:
        matches = groups[groups[description_column].astype(str).str.lower().str.contains(normalized, na=False)]
    if len(matches) != 1:
        raise ValueError(f"Expected one country-group match for '{query}', found {len(matches)}.")
    group_code = str(matches.index[0])
    info = imf_datatools.get_weo_country_info()
    if group_code not in info.columns:
        raise RuntimeError(f"Country-group code '{group_code}' is not present in WEO country info.")
    iso_column = next(
        (column for column in info.columns
         if str(column).lower() in {"iso3", "isocode", "code", "countrycode", "country"}),
        None,
    )
    member_rows = info[info[group_code] == 1]
    codes = member_rows[iso_column].astype(str).tolist() if iso_column else member_rows.index.astype(str).tolist()
    if not codes:
        raise ValueError(f"Country group '{query}' has no member countries.")
    return {
        "code": group_code,
        "name": str(matches.iloc[0][description_column]),
        "countries": codes,
        "key_value": "+".join(codes),
    }


def check_environment() -> dict[str, Any]:
    """Return a non-invasive diagnostic report for the optional iData backend."""
    report: dict[str, Any] = {
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "modules": {},
    }
    for name in ("imf_datatools", "imf_datatools.idata_utilities", "pandas", "openpyxl"):
        try:
            module = importlib.import_module(name)
            report["modules"][name] = {
                "status": "ok",
                "location": getattr(module, "__file__", None),
                "version": getattr(module, "__version__", None),
            }
        except Exception as exc:
            report["modules"][name] = {"status": "error", "error": str(exc)}
    report["ready"] = all(
        report["modules"][name]["status"] == "ok"
        for name in ("imf_datatools", "imf_datatools.idata_utilities", "pandas")
    )
    report["live_access"] = "not tested"
    return report
