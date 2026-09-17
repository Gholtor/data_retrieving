import os
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
    return request_json(
        f"/structure/datastructure/{agency_id}/{resource_id}/+",
        params={"detail": "full"},
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
    rows: list[dict[str, Any]] = []
    for dataset in payload.get("dataSets", []):
        series = dataset.get("series", {})
        for series_key, series_payload in series.items():
            obs = series_payload.get("observations", {})
            for position, values in obs.items():
                rows.append({
                    "series_key": series_key,
                    "position": position,
                    "values": values,
                })
    return rows


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
