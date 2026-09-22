# IMF Fiscal & Macroeconomic Data Explorer

An interactive Streamlit app for discovering and retrieving IMF macroeconomic and fiscal data
through the IMF SDMX API, with an optional iData backend for users who have IMF-authorized access.

1. Fetch metadata / structure information first
2. Inspect dataflows, dataset structures, and codelists
3. Query the actual data series using the selected dimensions

## Setup

### Prerequisites

- [Git](https://git-scm.com/downloads) (`winget install --id Git.Git -e`)
- [Python 3.12+](https://www.python.org/downloads/) (`winget install --id Python.Python.3.12 -e`)

### Steps

1. Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env`
4. Set your IMF subscription key:

```env
IMF_API_KEY=your_imf_subscription_key_here
```

5. Run the Streamlit interface:

```powershell
streamlit run streamlit_app.py
```

The interface supports two complementary backends:

- **SDMX API** uses the IMF API key and standardized dataflow/structure queries.
- **iData** uses the IMF-approved `imf_datatools` package for iData databases such as WEO when the user has the required package and account access. That package is not included in `requirements.txt` because it is distributed through IMF-specific channels.

The SDMX source selector currently includes `IMF.STA`, `IMF.RES`, `IMF.FAD`, `IMF.MCM`,
`IMF.AFR`, `IMF.MCD`, and `IMF.WHD`. Each selection loads the dataflows exposed by that agency;
availability of a dataflow or codelist does not guarantee observations for every country or
period.

For iData, the interface supports database search, dimension-value discovery, backend-specific
query keys, long/wide/panel output, metadata-aware scale and unit labels, and WEO country-group
expansion. iData uses an empty key segment for an open dimension; SDMX uses `*`.

For SDMX responses, the app resolves the dataflow's referenced structure and codelists to preserve
official dimension order and provide selectable codes. It supports multi-selection with `+`, and
shows an advanced key mapping. WEO year inputs are inclusive and are also applied locally because
some WEO responses include observations outside the requested bounds.

Downloads contain economist-facing columns such as `COUNTRY`, `INDICATOR`, `FREQUENCY`, `TIME_PERIOD`, and `OBS_VALUE`. Internal SDMX indexes (`series_key`, `position`, and `values`) are retained only in the raw response view. CSV and Stata (`.dta`) downloads are available.

Or run the command-line metadata check:

```powershell
python imf_client.py
```

## Tests

Run the local, network-independent client tests with:

```powershell
python -m unittest -v test_clients.py
```

## User workflow

For SDMX, the Streamlit interface has two network actions:

1. **Fetch metadata** discovers the dataflows available to the IMF API key.
2. **Fetch data** sends the selected dataflow, SDMX key, and period bounds, then offers CSV or Stata downloads and a raw-response view.

The SDMX key must follow the dimension order defined by the selected dataset. Use `*` for an open dimension, `.` between dimensions, and `+` to select multiple values within one dimension.

For iData, query keys must contain exactly one segment per discovered dimension. Use an empty
segment for all values in a dimension and `+` to select multiple values.

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
- Never commit `.env`, Streamlit secrets, or an actual API key. Each user must provide their own authorized key; `.env.example` is only a placeholder template.
- iData access is separate from SDMX API access. It requires the IMF-approved `imf_datatools` distribution and an active, authorized IMF session; do not install an unofficial package or enter an IMF password into this app.
- IMF APIs may return no observations for valid codes when that series is not available for the selected country and period. Check the warning and raw response before interpreting an empty result.
- Network access to IMF endpoints may be restricted by local firewall, VPN, or proxy policy.
