import os
from typing import Any

import streamlit as st

from imf_client import (
    build_key,
    fetch_codelist,
    fetch_data,
    fetch_datastructure,
    fetch_metadata,
)

st.set_page_config(page_title="IMF Data Explorer", layout="wide")

COMMON_CODELISTS = {
    "Country": ["CL_COUNTRY", "CL_AREA"],
    "Indicator": ["CL_INDICATOR", "CL_INDICATOR_CODE"],
    "Frequency": ["CL_FREQ", "CL_FREQUENCY"],
    "Unit": ["CL_UNIT"],
}


def set_api_key_from_ui() -> None:
    key = st.sidebar.text_input(
        "IMF API key",
        type="password",
        value=os.getenv("IMF_API_KEY", ""),
        key="imf_api_key_input",
        help="Paste your IMF subscription key. It is stored only in the current process / environment.",
    )
    if key:
        os.environ["IMF_API_KEY"] = key


def extract_code_labels(payload: dict[str, Any]) -> list[tuple[str, str]]:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    codelists = []
    if isinstance(data, dict):
        codelists = data.get("codelists") or data.get("codeLists") or []

    options: list[tuple[str, str]] = []
    for codelist in codelists:
        if not isinstance(codelist, dict):
            continue
        for code in codelist.get("codes", []):
            if not isinstance(code, dict):
                continue
            code_id = str(code.get("id", "")).strip()
            if not code_id:
                continue
            label = code.get("name") or code.get("names", {}).get("en") or code_id
            options.append((code_id, str(label)))
    # deduplicate while preserving order
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for code_id, label in options:
        if code_id not in seen:
            unique.append((code_id, label))
            seen.add(code_id)
    return unique


def load_codelist_options() -> dict[str, list[tuple[str, str]]]:
    result: dict[str, list[tuple[str, str]]] = {}
    for label, candidates in COMMON_CODELISTS.items():
        for candidate in candidates:
            try:
                payload = fetch_codelist(candidate)
                values = extract_code_labels(payload)
                if values:
                    result[label] = values
                    break
            except Exception:
                continue
    return result


st.title("IMF Data Explorer")
st.caption("Metadata-first workflow for IMF SDMX data")

if not os.getenv("IMF_API_KEY"):
    st.warning("Add your IMF API key in the sidebar before loading metadata or data.")

with st.sidebar:
    st.header("IMF settings")
    set_api_key_from_ui()
    st.markdown("---")
    st.caption("Metadata choices")

if not os.getenv("IMF_API_KEY"):
    st.stop()

if st.button("Load metadata"):
    with st.spinner("Loading IMF metadata and dimension choices..."):
        try:
            metadata = fetch_metadata()
            st.session_state["metadata"] = metadata
            st.session_state["codelists"] = load_codelist_options()
            st.success("Metadata loaded successfully.")
        except Exception as exc:
            st.error(f"Failed to load metadata: {exc}")

if "metadata" in st.session_state:
    payload = st.session_state["metadata"]
    dataflows = payload.get("data", {}).get("dataflows", [])
    options = [item.get("id") for item in dataflows if item.get("id")]
    if options:
        st.subheader("1. Dataset / dataflow")
        selected_dataflow = st.selectbox("Choose a dataset", options)
        selected_item = next((item for item in dataflows if item.get("id") == selected_dataflow), {})
        st.json({
            "id": selected_item.get("id"),
            "name": selected_item.get("name"),
            "agencyID": selected_item.get("agencyID"),
            "version": selected_item.get("version"),
        })

        if st.button("Load structure for this dataset"):
            with st.spinner("Loading dataset structure..."):
                try:
                    structure = fetch_datastructure(selected_dataflow)
                    st.session_state["structure"] = structure
                    st.success("Structure loaded.")
                except Exception as exc:
                    st.error(f"Failed to load structure: {exc}")

        if "structure" in st.session_state:
            with st.expander("Dataset structure preview"):
                st.json(st.session_state["structure"])

        st.subheader("2. Metadata filters")
        codelists = st.session_state.get("codelists", {})

        country_values = codelists.get("Country", [("*", "All countries")])
        indicator_values = codelists.get("Indicator", [("*", "All indicators")])
        frequency_values = codelists.get("Frequency", [("*", "All frequencies")])

        country_choice = st.selectbox("Country", [label for _, label in country_values], index=0)
        indicator_choice = st.selectbox("Indicator", [label for _, label in indicator_values], index=0)
        frequency_choice = st.selectbox("Frequency", [label for _, label in frequency_values], index=0)

        country_code = next((code for code, label in country_values if label == country_choice), "*")
        indicator_code = next((code for code, label in indicator_values if label == indicator_choice), "*")
        frequency_code = next((code for code, label in frequency_values if label == frequency_choice), "*")

        start_period = st.text_input("Start period", value="2020-01")
        end_period = st.text_input("End period", value="2024-12")

        generated_key = build_key({
            "country": country_code,
            "indicator": indicator_code,
            "frequency": frequency_code,
        }, ["country", "indicator", "frequency"])

        st.info(f"Generated key preview: {generated_key}")

        st.subheader("3. Fetch data")
        if st.button("Fetch selected series"):
            with st.spinner("Fetching IMF data..."):
                try:
                    result = fetch_data(
                        dataflow=selected_dataflow,
                        key=generated_key,
                        start_period=start_period,
                        end_period=end_period,
                    )
                    st.session_state["data_result"] = result
                    st.success("Data loaded.")
                except Exception as exc:
                    st.error(f"Failed to fetch data: {exc}")

        if "data_result" in st.session_state:
            with st.expander("Fetched data payload"):
                st.json(st.session_state["data_result"])

    else:
        st.info("No dataflows were returned for the current IMF key.")

st.subheader("Quick codelist lookup")
with st.form("codelist_form"):
    codelist_id = st.text_input("Codelist ID", value="CL_COUNTRY")
    submitted = st.form_submit_button("Load codelist")

if submitted:
    try:
        codelist = fetch_codelist(codelist_id)
        st.json(codelist)
    except Exception as exc:
        st.error(f"Failed to fetch codelist: {exc}")

st.markdown("---")
st.caption("This app uses IMF metadata to propose dataset, country, indicator, frequency, and time-period choices before fetching the data.")
