# IMF Data Retrieval

This project demonstrates the working IMF SDMX flow:

1. Fetch metadata / structure information first
2. Inspect dataflows, dataset structures, and codelists
3. Query the actual data series using the selected dimensions

## Setup

1. Copy `.env.example` to `.env`
2. Set your IMF subscription key:

```env
IMF_API_KEY=your_imf_subscription_key_here
```

3. Activate the virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

4. Run the client:

```powershell
python imf_client.py
```

## Supported flows

- `fetch_metadata()` -> list IMF dataflows
- `fetch_datastructure(resource_id)` -> read a specific dataset structure
- `fetch_codelist(codelist_id)` -> fetch valid values for a dimension
- `fetch_data(dataflow, key, ...)` -> retrieve actual data observations
- `build_key(dimensions, order)` -> help build a valid SDMX key string
- `extract_observations(payload)` -> flatten the returned data payload

## Example usage

```python
from imf_client import fetch_metadata, fetch_datastructure, fetch_codelist, fetch_data

meta = fetch_metadata()
print(meta)

structure = fetch_datastructure("CPI")
print(structure)

countries = fetch_codelist("CL_COUNTRY")
print(countries)

series = fetch_data(
    dataflow="CPI",
    key="USA.*.*.IX.M",
    start_period="2020-01",
    end_period="2024-12",
)
print(series)
```

## Notes

- The IMF API authenticates with the `Ocp-Apim-Subscription-Key` header.
- The API key should never be committed to GitHub.
- The repo is ready for anyone with a valid IMF key to run it locally.
- This project assumes the runtime environment is not blocked by local security policy.
