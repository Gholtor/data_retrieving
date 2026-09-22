import os
import re
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.imf.org/external/sdmx/3.0"


def get_api_key() -> str:
    api_key = os.getenv("IMF_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing IMF_API_KEY. Copy .env.example to .env and set your IMF subscription key."
        )
    return api_key


def get_headers() -> dict[str, str]:
    return {
        "Ocp-Apim-Subscription-Key": get_api_key(),
        "Accept": "application/json",
    }


def request_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(
        f"{BASE_URL}{path}",
        headers=get_headers(),
        params=params,
        timeout=30,
    )

    if response.status_code == 204:
        return {}

    if response.status_code != 200:
        raise RuntimeError(
            f"IMF API request failed: {response.status_code} - {response.text[:500]}"
        )

    if not response.content:
        return {}

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("IMF API did not return valid JSON.") from exc


def fetch_dataflows(agency_id: str = "IMF.STA") -> dict[str, Any]:
    """Fetch the available IMF dataflows and their metadata."""
    return request_json(
        f"/structure/dataflow/{agency_id}/*/+",
        params={"detail": "allstubs"},
    )


def fetch_metadata(agency_id: str = "IMF.STA") -> dict[str, Any]:
    """First call: metadata discovery for IMF dataflows in the agency."""
    return fetch_dataflows(agency_id=agency_id)


def fetch_datastructure(resource_id: str, agency_id: str = "IMF.STA") -> dict[str, Any]:
    """Fetch a dataset structure definition for a given IMF resource."""
    direct = request_json(
        f"/structure/datastructure/{agency_id}/{resource_id}/+",
        params={"detail": "full"},
    )
    if direct:
        return direct

    # Some IMF dataflows reference a differently named DSD (for example WEO -> DSD_WEO).
    # Resolve that reference instead of assuming dataflow_id == datastructure_id.
    definition = request_json(
        f"/structure/dataflow/{agency_id}/{resource_id}/+",
        params={"detail": "full"},
    )
    dataflows = definition.get("data", {}).get("dataflows", [])
    dataflow = dataflows[0] if dataflows and isinstance(dataflows[0], dict) else {}
    reference = dataflow.get("structure")
    match = re.search(
        r"DataStructure=([^:]+):([^ (]+)(?:\(([^)]+)\))?",
        str(reference or ""),
    )
    if not match:
        return {}
    structure_agency, structure_id, version = match.groups()
    return request_json(
        f"/structure/datastructure/{structure_agency}/{structure_id}/{version or '+'}",
        params={"detail": "full", "references": "all"},
    )


def fetch_codelist(codelist_id: str, agency_id: str = "IMF") -> dict[str, Any]:
    """Fetch a codelist such as CL_COUNTRY, CL_INDICATOR, etc."""
    return request_json(f"/structure/codelist/{agency_id}/{codelist_id}/+")


def build_key(dimensions: dict[str, str], order: list[str] | None = None) -> str:
    """Build a key string for IMF SDMX queries from selected dimension values."""
    if order is None:
        order = list(dimensions.keys())
    return ".".join(dimensions.get(dim, "*") for dim in order)


def fetch_data(
    dataflow: str,
    key: str = "*",
    agency_id: str = "IMF.STA",
    version: str = "+",
    start_period: str | None = None,
    end_period: str | None = None,
    **filters: str,
) -> dict[str, Any]:
    """Fetch actual IMF data for a specific dataflow and series key."""
    params: dict[str, Any] = {"format": "json"}
    if start_period:
        params["startPeriod"] = start_period
    if end_period:
        params["endPeriod"] = end_period
    for name, value in filters.items():
        params[f"c[{name}]"] = value

    path = f"/data/dataflow/{agency_id}/{dataflow}/{version}/{key}"
    return request_json(path, params=params)


def extract_observations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten IMF JSON data into a simple list of observations when possible."""
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return []

    structures = data.get("structures", [])
    structure = structures[0] if structures and isinstance(structures[0], dict) else {}
    dimensions = structure.get("dimensions", {})
    series_dimensions = dimensions.get("series", []) if isinstance(dimensions, dict) else []
    observation_dimensions = dimensions.get("observation", []) if isinstance(dimensions, dict) else []

    dimension_values: list[list[str]] = []
    for dimension in series_dimensions:
        dimension_values.append([
            str(item.get("id"))
            for item in dimension.get("values", [])
            if isinstance(item, dict) and item.get("id") is not None
        ])

    time_values = []
    if observation_dimensions and isinstance(observation_dimensions[0], dict):
        time_values = [
            str(item.get("value"))
            for item in observation_dimensions[0].get("values", [])
            if isinstance(item, dict) and item.get("value") is not None
        ]

    rows: list[dict[str, Any]] = []
    for dataset in data.get("dataSets", []):
        series = dataset.get("series", {})
        for series_key, series_payload in series.items():
            obs = series_payload.get("observations", {})
            for position, values in obs.items():
                row: dict[str, Any] = {
                    "series_key": series_key,
                    "position": position,
                    "values": values,
                }
                series_indexes = str(series_key).split(":")
                for index, dimension in enumerate(series_dimensions):
                    if index < len(series_indexes):
                        value_index = int(series_indexes[index]) if series_indexes[index].isdigit() else -1
                        values_for_dimension = dimension_values[index] if index < len(dimension_values) else []
                        row[str(dimension.get("id", f"dimension_{index}"))] = (
                            values_for_dimension[value_index]
                            if 0 <= value_index < len(values_for_dimension)
                            else series_indexes[index]
                        )
                row["TIME_PERIOD"] = (
                    time_values[int(position)]
                    if str(position).isdigit() and int(position) < len(time_values)
                    else position
                )
                if isinstance(values, list) and values:
                    row["OBS_VALUE"] = values[0]
                rows.append(row)
    return rows


def filter_observations(
    rows: list[dict[str, Any]],
    start_period: str | None = None,
    end_period: str | None = None,
) -> list[dict[str, Any]]:
    """Apply inclusive period bounds to flattened IMF observations."""
    def period_key(value: Any) -> tuple[int, int]:
        match = re.match(r"^(\d{4})(?:-([0-9]{2}))?", str(value))
        if not match:
            quarter = re.match(r"^(\d{4})Q([1-4])", str(value))
            return (int(quarter.group(1)), int(quarter.group(2)) * 3) if quarter else (0, 0)
        return int(match.group(1)), int(match.group(2) or 1)

    start = period_key(start_period) if start_period else None
    end = period_key(end_period) if end_period else None
    return [
        row for row in rows
        if (start is None or period_key(row.get("TIME_PERIOD")) >= start)
        and (end is None or period_key(row.get("TIME_PERIOD")) <= end)
    ]


def clean_observations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep user-facing dimensions and values, excluding SDMX index internals."""
    return [
        {
            key: value
            for key, value in row.items()
            if key not in {"series_key", "position", "values"}
        }
        for row in rows
    ]


def _find_dimensions_raw(payload: Any) -> list[dict[str, Any]]:
    """Find and order SDMX dimensions in a structure response."""
    if isinstance(payload, dict):
        dimensions = payload.get("dimensions")
        if isinstance(dimensions, dict):
            series_dimensions = dimensions.get("series")
            if isinstance(series_dimensions, list):
                return sorted(
                    [item for item in series_dimensions if isinstance(item, dict) and item.get("id")],
                    key=lambda item: item.get("keyPosition", item.get("position", 0)),
                )
        if isinstance(dimensions, list) and all(isinstance(item, dict) for item in dimensions):
            if any(item.get("id") for item in dimensions):
                return sorted(dimensions, key=lambda item: item.get("position", 0))
        for value in payload.values():
            result = _find_dimensions_raw(value)
            if result:
                return result
    elif isinstance(payload, list):
        for value in payload:
            result = _find_dimensions_raw(value)
            if result:
                return result
    return []


def _codelists(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        direct = payload.get("codelists")
        if isinstance(direct, list):
            return [item for item in direct if isinstance(item, dict)]
        for value in payload.values():
            result = _codelists(value)
            if result:
                return result
    elif isinstance(payload, list):
        for value in payload:
            result = _codelists(value)
            if result:
                return result
    return []


def _dimension_codelist(payload: Any, dimension_id: str) -> dict[str, Any] | None:
    codelists = _codelists(payload)
    normalized = dimension_id.upper()
    flow_ids: list[str] = []
    if isinstance(payload, dict):
        data = payload.get("data", {})
        flows = data.get("dataflows", []) if isinstance(data, dict) else []
        flow_ids = [str(item.get("id", "")).upper() for item in flows if isinstance(item, dict)]
    preferred: list[str]
    if normalized == "COUNTRY":
        preferred = [f"CL_{flow}_COUNTRY" for flow in flow_ids if flow]
        preferred += ["CL_WEO_COUNTRY", "CL_COUNTRY"]
    elif normalized == "INDICATOR":
        preferred = [f"CL_{flow}_INDICATOR" for flow in flow_ids if flow]
        preferred += ["CL_WEO_INDICATOR", "CL_INDICATOR"]
    elif normalized in {"FREQUENCY", "FREQ"}:
        preferred = ["CL_FREQ", "CL_FREQUENCY"]
    else:
        preferred = [f"CL_{flow}_{normalized}" for flow in flow_ids if flow]
        preferred += [f"CL_{normalized}"]
    for wanted in preferred:
        match = next((item for item in codelists if str(item.get("id", "")).upper() == wanted), None)
        if match:
            return match
    return None


def find_dimensions(payload: Any) -> list[dict[str, Any]]:
    """Find dimensions, enriching them with embedded official codelist values."""
    dimensions = _find_dimensions_raw(payload)
    enriched: list[dict[str, Any]] = []
    for dimension in dimensions:
        result = dict(dimension)
        codelist = _dimension_codelist(payload, str(result.get("id", "")))
        if codelist:
            result["values"] = [
                {
                    "id": str(code.get("id")),
                    "name": str(code.get("name") or code.get("id")),
                }
                for code in codelist.get("codes", [])
                if isinstance(code, dict) and code.get("id") is not None
            ]
            result["codelist_id"] = codelist.get("id")
            result["codelist_agency"] = codelist.get("agencyID")
        enriched.append(result)
    return enriched


def dimension_label(dimension: dict[str, Any]) -> str:
    """Return the most useful human-readable label for a dimension."""
    return str(
        dimension.get("name")
        or dimension.get("label")
        or dimension.get("description")
        or dimension.get("id")
    )


def main() -> None:
    print("IMF API client is ready.")
    print("This script expects IMF_API_KEY in a .env file or environment variables.")

    try:
        metadata = fetch_metadata()
        print("Metadata fetch succeeded.")
        print(f"Top-level keys: {list(metadata.keys())[:10]}")
    except RuntimeError as exc:
        print(f"Error: {exc}")
        print("Example:")
        print("  1) copy .env.example to .env")
        print("  2) set IMF_API_KEY=your_key")
        print("  3) run this script again")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
