import json
import os
from io import BytesIO
from typing import Any

import pandas as pd
import streamlit as st

try:
    from streamlit_sortables import sort_items
except ImportError:
    sort_items = None

from imf_client import (
    dimension_label,
    clean_observations,
    extract_observations,
    filter_observations,
    fetch_data as fetch_sdmx_data,
    fetch_datastructure,
    fetch_metadata,
    find_dimensions,
)
from idata_client import (
    IDataUnavailableError,
    build_key as build_idata_key,
    expand_country_group,
    fetch_data as fetch_idata_data,
    fetch_metadata as fetch_idata_metadata,
    check_environment as check_idata_environment,
    get_dimension_values,
    get_dimensions as get_idata_dimensions,
    list_databases,
)

st.set_page_config(page_title="IMF Fiscal & Macroeconomic Data Explorer", layout="wide")


SDMX_AGENCIES = {
    "IMF Statistics (IMF.STA)": "IMF.STA",
    "IMF Research / WEO (IMF.RES)": "IMF.RES",
    "Fiscal Affairs Department (IMF.FAD)": "IMF.FAD",
    "Monetary and Capital Markets (IMF.MCM)": "IMF.MCM",
    "African Department / Sub-Saharan Africa (IMF.AFR)": "IMF.AFR",
    "Middle East and Central Asia Department (IMF.MCD)": "IMF.MCD",
    "Western Hemisphere Department (IMF.WHD)": "IMF.WHD",
}


def set_api_key_from_ui() -> None:
    key = st.sidebar.text_input(
        "IMF API key",
        type="password",
        value=os.getenv("IMF_API_KEY", ""),
        key="imf_api_key_input",
        help="Used by the SDMX backend only and kept in this local process.",
    )
    if key:
        os.environ["IMF_API_KEY"] = key


def get_dataflows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    dataflows = data.get("dataflows", []) if isinstance(data, dict) else []
    return [item for item in dataflows if isinstance(item, dict) and item.get("id")]


def observations_csv(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8")


def observations_stata(frame: pd.DataFrame) -> bytes:
    output = BytesIO()
    frame.to_stata(output, write_index=False, version=118)
    return output.getvalue()


def dataframe_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def advanced_key_mapping(dimensions: list[dict[str, Any]], key: str) -> pd.DataFrame:
    """Explain each position in an SDMX key for manual/advanced queries."""
    parts = key.split(".")
    rows = []
    for position, dimension in enumerate(dimensions):
        segment = parts[position] if position < len(parts) else "*"
        rows.append(
            {
                "Position": position + 1,
                "Dimension": str(dimension.get("id", f"dimension_{position}")),
                "Description": dimension_label(dimension),
                "Current segment": segment or "*",
                "Meaning": "All values" if segment in {"", "*"} else "Selected value(s)",
            }
        )
    return pd.DataFrame(rows)


def missing_sdmx_values(
    rows: list[dict[str, Any]],
    dimensions: list[dict[str, Any]],
    key: str,
) -> dict[str, list[str]]:
    """Find explicitly requested codes that the API did not return observations for."""
    missing: dict[str, list[str]] = {}
    parts = key.split(".")
    for position, dimension in enumerate(dimensions):
        requested = set(parts[position].split("+")) if position < len(parts) else set()
        requested -= {"", "*"}
        if not requested:
            continue
        dimension_id = str(dimension.get("id", f"dimension_{position}"))
        returned = {str(row.get(dimension_id)) for row in rows if row.get(dimension_id) is not None}
        absent = sorted(requested - returned)
        if absent:
            missing[dimension_id] = absent
    return missing


def drag_mapping(
    dimensions: list[dict[str, Any]],
    options_by_dimension: dict[str, list[str]],
    selected_by_dimension: dict[str, list[str]],
    labels_by_dimension: dict[str, dict[str, str]],
) -> dict[str, list[str]] | None:
    """Render a drag-and-drop value selector without allowing dimension reordering."""
    if sort_items is None or not all(options_by_dimension.get(str(d.get("id"))) for d in dimensions):
        return None
    selected_tokens = {
        f"{dimension_id}::{code}"
        for dimension_id, codes in selected_by_dimension.items()
        for code in codes
    }
    available = [
        f"{dimension_id}::{code}"
        for dimension in dimensions
        for dimension_id in [str(dimension.get("id"))]
        for code in options_by_dimension[dimension_id]
        if f"{dimension_id}::{code}" not in selected_tokens
    ]
    containers = [{"header": "Available values", "items": available}]
    for dimension in dimensions:
        dimension_id = str(dimension.get("id"))
        containers.append(
            {
                "header": dimension_id,
                "items": [f"{dimension_id}::{code}" for code in selected_by_dimension.get(dimension_id, [])],
            }
        )
    st.caption("Drag values from Available values into the fixed dimension containers. Dimension order cannot be changed.")
    result = sort_items(containers, multi_containers=True)
    if not isinstance(result, list):
        return selected_by_dimension
    selections: dict[str, list[str]] = {}
    for container in result[1:]:
        dimension_id = str(container.get("header"))
        selections[dimension_id] = [str(item).split("::", 1)[1] for item in container.get("items", []) if "::" in str(item)]
    return selections


st.title("IMF Fiscal & Macroeconomic Data Explorer")
st.caption("Explore IMF fiscal, macroeconomic, statistical, and WEO data through SDMX and iData.")

with st.sidebar:
    st.header("Connection")
    backend = st.radio("Data backend", ["SDMX API", "iData"], key="backend")
    if backend == "SDMX API":
        set_api_key_from_ui()
    else:
        st.caption("iData requires the IMF-approved imf_datatools package and account access.")
        if st.button("Check iData environment"):
            with st.spinner("Checking local iData dependencies..."):
                environment = check_idata_environment()
            if environment["ready"]:
                st.success("The local iData dependencies are available.")
            else:
                st.warning("The iData backend is not ready on this Python environment.")
            st.json(environment)

if backend == "SDMX API" and not os.getenv("IMF_API_KEY"):
    st.info("Add your IMF API key in the sidebar to use the SDMX backend.")
    st.stop()

st.header("1. Fetch metadata")
structure: dict[str, Any] = {}

if backend == "SDMX API":
    agency_options = SDMX_AGENCIES
    selected_agency_label = st.selectbox("IMF data source", list(agency_options))
    selected_agency = agency_options[selected_agency_label]
    if st.button("Fetch metadata", type="primary"):
        with st.spinner("Fetching SDMX dataflow metadata..."):
            try:
                st.session_state[f"metadata:{selected_agency}"] = fetch_metadata(agency_id=selected_agency)
                st.session_state["agency_id"] = selected_agency
                st.session_state.pop("idata_databases", None)
                st.session_state.pop("data_result", None)
                st.success("Metadata loaded.")
            except Exception as exc:
                st.error(f"Metadata request failed: {exc}")

    metadata = st.session_state.get(f"metadata:{selected_agency}")
    if metadata:
        dataflows = get_dataflows(metadata)
        if not dataflows:
            st.warning("The API returned no dataflows for this account.")
            st.stop()
        dataflow_options = [str(item["id"]) for item in dataflows]
        selected_dataset = st.selectbox("Dataset / dataflow", dataflow_options)
        selected_item = next(item for item in dataflows if item.get("id") == selected_dataset)
        st.caption(str(selected_item.get("name") or "No dataset description available."))
        structure_key = f"structure:{selected_agency}:{selected_dataset}"
        if structure_key not in st.session_state:
            try:
                st.session_state[structure_key] = fetch_datastructure(
                    selected_dataset,
                    agency_id=selected_agency,
                )
            except Exception as exc:
                st.session_state[structure_key] = {"error": str(exc)}
        structure = st.session_state[structure_key]
        dimensions = find_dimensions(structure)
        with st.expander("Selected metadata"):
            st.json(selected_item)
    else:
        dimensions = []
else:
    database_search = st.text_input(
        "Search iData databases",
        placeholder="e.g. WEO, CPI, fiscal, trade",
        help="Search database names and descriptions. Leave empty to list all accessible databases.",
    )
    refresh_databases = st.checkbox("Refresh database catalog", value=False)
    if st.button("Fetch metadata", type="primary"):
        with st.spinner("Fetching iData database metadata..."):
            try:
                st.session_state["idata_databases"] = list_databases(
                    keyword=database_search.strip() or None,
                    refresh=refresh_databases,
                )
                st.session_state.pop("data_result", None)
                st.success("iData metadata loaded.")
            except IDataUnavailableError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"iData metadata request failed: {exc}")

    databases = st.session_state.get("idata_databases")
    if databases is not None and len(databases):
        database_options = [str(value) for value in databases.index]
        selected_dataset = st.selectbox("iData database", database_options)
        try:
            dimensions_frame = get_idata_dimensions(selected_dataset)
            dimensions = [
                {
                    "id": str(index),
                    "name": str(row.get("Description", row.iloc[0])),
                    "position": position,
                }
                for position, (index, row) in enumerate(dimensions_frame.iterrows())
            ]
        except Exception as exc:
            dimensions = []
            st.error(f"Could not load iData dimensions: {exc}")
    else:
        dimensions = []

if dimensions or (backend == "SDMX API" and metadata):
    st.subheader("Dataset mapping")
    if backend == "iData":
        with st.expander("WEO country-group helper"):
            st.caption("Resolve an official WEO group into ISO3 codes for the country dimension.")
            group_query = st.text_input(
                "Country group name or code",
                placeholder="e.g. advanced economies or G001",
                key="idata_group_query",
            )
            if st.button("Resolve country group", key="resolve_idata_group") and group_query.strip():
                try:
                    group = expand_country_group(group_query)
                    st.success(f"{group['name']} ({group['code']}): {len(group['countries'])} countries")
                    st.code(group["key_value"], language="text")
                    st.caption("Copy this value into the country dimension above.")
                except Exception as exc:
                    st.error(f"Could not resolve country group: {exc}")
    if dimensions:
        st.caption("Values are entered in the official dimension order returned by the selected backend.")
        key_parts: list[str] = []
        mapping_options_by_dimension: dict[str, list[str]] = {}
        mapping_selected_by_dimension: dict[str, list[str]] = {}
        mapping_labels_by_dimension: dict[str, dict[str, str]] = {}
        for position, dimension in enumerate(dimensions):
            dimension_id = str(dimension.get("id", f"dimension_{position}"))
            options: list[str] = []
            option_labels: dict[str, str] = {}
            if backend == "iData":
                values_cache_key = f"idata_values:{selected_dataset}:{dimension_id}"
                search_key = f"idata_value_search:{selected_dataset}:{dimension_id}"
                value_search = st.text_input(
                    f"Search {dimension_label(dimension)} values",
                    key=search_key,
                    placeholder="Search by code or description",
                )
                if st.button("Load values", key=f"load_values:{selected_dataset}:{dimension_id}"):
                    try:
                        values_frame = get_dimension_values(
                            selected_dataset,
                            dimension_id,
                            keyword=value_search.strip() or None,
                        )
                        name_column = "Name" if "Name" in values_frame.columns else values_frame.columns[0]
                        option_labels = {
                            str(code): f"{code} — {row[name_column]}"
                            for code, row in values_frame.iterrows()
                        }
                        st.session_state[values_cache_key] = option_labels
                    except Exception as exc:
                        st.error(f"Could not load {dimension_id} values: {exc}")
                option_labels = st.session_state.get(values_cache_key, {})
                options = list(option_labels)
            else:
                options = [
                    str(item.get("id"))
                    for item in dimension.get("values", [])
                    if isinstance(item, dict) and item.get("id")
                ]
                option_labels = {
                    str(item.get("id")): f"{item.get('id')} — {item.get('name', item.get('id'))}"
                    for item in dimension.get("values", [])
                    if isinstance(item, dict) and item.get("id")
                }
            mapping_options_by_dimension[dimension_id] = options
            mapping_labels_by_dimension[dimension_id] = option_labels
            if options:
                selected_values = st.multiselect(
                    f"{position + 1}. {dimension_label(dimension)} ({dimension_id})",
                    options,
                    format_func=lambda code, labels=option_labels: labels.get(code, code),
                    key=f"dimension_value:{backend}:{selected_dataset}:{position}",
                    help="Select one or more official codes. Leave empty to include all values.",
                )
                mapping_selected_by_dimension[dimension_id] = selected_values
                key_parts.append("+".join(selected_values) if selected_values else ("" if backend == "iData" else "*"))
            else:
                value = st.text_input(
                    f"{position + 1}. {dimension_label(dimension)} ({dimension_id})",
                    value="" if backend == "iData" else "*",
                    key=f"dimension_value:{backend}:{selected_dataset}:{position}",
                    help=(
                        "Enter a code, use + for multiple codes, or leave blank for all values."
                        if backend == "iData"
                        else "Enter a code, use + for multiple codes, or leave * for all values."
                    ),
                )
                key_parts.append(value.strip() or ("" if backend == "iData" else "*"))
        if sort_items is not None and all(mapping_options_by_dimension.values()):
            with st.expander("Drag-and-drop mapping"):
                dragged = drag_mapping(
                    dimensions,
                    mapping_options_by_dimension,
                    mapping_selected_by_dimension,
                    mapping_labels_by_dimension,
                )
            if dragged is not None:
                key_parts = [
                    "+".join(dragged.get(str(dimension.get("id")), []))
                    for dimension in dimensions
                ]
        generated_key = build_idata_key(key_parts, backend="idata" if backend == "iData" else "sdmx")
        with st.expander("Advanced key mapping"):
            if backend == "iData":
                st.caption("iData segments must remain in this exact order. Leave a segment empty for all values and use + for multiple codes.")
            else:
                st.caption("SDMX segments must remain in this exact order. Use * for an open dimension and + for multiple codes.")
            st.dataframe(advanced_key_mapping(dimensions, generated_key), hide_index=True, use_container_width=True)
    else:
        st.warning("The dataset structure did not expose readable dimensions.")
        if isinstance(structure, dict) and structure.get("error"):
            st.error(f"Structure request failed: {structure['error']}")
        with st.expander("Raw structure response"):
            st.json(structure)
        generated_key = st.text_input(
            "Advanced SDMX key",
            value="USA.NGDP_RPCH.A" if selected_dataset == "WEO" else "*",
            help="Temporary fallback until this dataset's dimensions are available from metadata.",
        ).strip() or "*"
    st.code(generated_key, language="text")

    st.header("2. Fetch data")
    period_col1, period_col2 = st.columns(2)
    with period_col1:
        if backend == "SDMX API" and selected_dataset == "WEO":
            start_period = str(st.number_input("Start year", min_value=1900, max_value=2200, value=2020, step=1))
        else:
            start_period = st.text_input("Start period", value="2020")
    with period_col2:
        if backend == "SDMX API" and selected_dataset == "WEO":
            end_period = str(st.number_input("End year", min_value=1900, max_value=2200, value=2024, step=1))
        else:
            end_period = st.text_input("End period", value="2024")

    if backend == "iData":
        output_format = st.radio(
            "iData output layout",
            ["long", "wide"],
            horizontal=True,
            format_func=lambda value: "Long / tidy" if value == "long" else "Wide / spreadsheet",
            key="idata_output_format",
            help="Long format is best for analysis; wide format is convenient for spreadsheets.",
        )
        panel_options = ["None"] + [str(dimension.get("id")) for dimension in dimensions]
        selected_panel = st.selectbox(
            "Optional panel dimension",
            panel_options,
            key="idata_panel_dimension_select",
            help="Panel output groups rows by this dimension and is only available with wide output.",
        )
        st.session_state["idata_panel_dimension"] = (
            None if output_format == "long" or selected_panel == "None" else selected_panel
        )

    if st.button("Fetch data", type="primary"):
        with st.spinner("Fetching selected IMF data..."):
            try:
                if backend == "SDMX API":
                    result = fetch_sdmx_data(
                        dataflow=selected_dataset,
                        key=generated_key,
                        agency_id=st.session_state.get("agency_id", selected_agency),
                        start_period=start_period.strip() or None,
                        end_period=end_period.strip() or None,
                    )
                    rows = extract_observations(result)
                    missing = missing_sdmx_values(rows, dimensions, generated_key)
                    if missing:
                        details = "; ".join(f"{dimension}: {', '.join(codes)}" for dimension, codes in missing.items())
                        st.warning(
                            "The IMF API did not return observations for some requested codes. "
                            f"They may be valid codes without data for this country/period: {details}"
                        )
                    rows = filter_observations(rows, start_period, end_period)
                    frame = pd.DataFrame(clean_observations(rows))
                else:
                    frame = fetch_idata_data(
                        database=selected_dataset,
                        key=generated_key,
                        start=start_period.strip() or None,
                        end=end_period.strip() or None,
                        longformat=st.session_state.get("idata_output_format", "long") == "long",
                        panel=st.session_state.get("idata_panel_dimension") or None,
                    )
                    result = {"records": dataframe_records(frame) if frame is not None else []}
                    st.session_state["idata_metadata"] = fetch_idata_metadata(selected_dataset, generated_key)
                st.session_state["data_result"] = result
                st.session_state["data_frame"] = frame
                st.success("Data loaded.")
            except IDataUnavailableError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Data request failed: {exc}")

if "data_frame" in st.session_state:
    frame = st.session_state["data_frame"]
    st.subheader("Results")
    if backend == "iData":
        st.caption("iData values are normalized using the series metadata when available.")
        idata_metadata = st.session_state.get("idata_metadata")
        if idata_metadata is not None:
            with st.expander("Series metadata"):
                st.dataframe(idata_metadata, use_container_width=True)
    if len(frame):
        st.dataframe(frame, use_container_width=True)
        st.caption("Technical SDMX fields such as series_key, position, and values are retained only in the raw response.")
        download_format = st.radio("Download format", ["CSV", "Stata (.dta)"], horizontal=True)
        if download_format == "CSV":
            st.download_button("Download CSV", data=observations_csv(frame), file_name="imf_data.csv", mime="text/csv")
        else:
            st.download_button("Download Stata file", data=observations_stata(frame), file_name="imf_data.dta", mime="application/x-stata")
    else:
        st.info("The backend returned no observations for this query.")

if "data_result" in st.session_state:
    with st.expander("Raw result"):
        st.json(st.session_state["data_result"])
