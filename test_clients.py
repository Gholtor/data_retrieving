import types
import unittest

import pandas as pd

import idata_client
from imf_client import (
    build_key as build_sdmx_key,
    extract_observations,
    filter_observations,
    find_dimensions,
)


class IDataClientTests(unittest.TestCase):
    def test_build_key_uses_backend_specific_open_values(self):
        self.assertEqual(idata_client.build_key(["USA", "NGDP_RPCH", ""]), "USA.NGDP_RPCH.")
        self.assertEqual(idata_client.build_key(["USA", "*", "*"], backend="sdmx"), "USA.*.*")

    def test_validate_key_rejects_wrong_dimension_count(self):
        dimensions = pd.DataFrame(index=["COUNTRY", "INDICATOR", "FREQUENCY"])
        with self.assertRaises(idata_client.IDataQueryError):
            idata_client.validate_key("IMF.RES.WEO:WEO_LIVE", "USA.NGDP_RPCH", dimensions)

    def test_enrich_data_applies_scale_and_unit(self):
        frame = pd.DataFrame(
            {"OBS_VALUE": [1200.0]},
            index=pd.Index([pd.Timestamp("2020-01-01")], name="dates"),
        )
        metadata = pd.DataFrame({"scale": [3], "unit": ["PCT_GDP"]})
        result = idata_client.enrich_data(frame, metadata)
        self.assertEqual(result.loc[0, "OBS_VALUE"], 1.2)
        self.assertEqual(result.loc[0, "SCALE"], "Thousands")
        self.assertEqual(result.loc[0, "UNIT"], "Percent of GDP")

    def test_fetch_data_rejects_panel_with_long_format(self):
        with self.assertRaises(ValueError):
            idata_client.fetch_data("DB", "A.B", longformat=True, panel="COUNTRY")


class SDMXClientTests(unittest.TestCase):
    def test_sdmx_key_and_inclusive_period_filter(self):
        self.assertEqual(build_sdmx_key({"COUNTRY": "USA"}, ["COUNTRY", "INDICATOR"]), "USA.*")
        rows = [
            {"TIME_PERIOD": "2019", "OBS_VALUE": 1},
            {"TIME_PERIOD": "2020", "OBS_VALUE": 2},
            {"TIME_PERIOD": "2021", "OBS_VALUE": 3},
        ]
        result = filter_observations(rows, "2020", "2021")
        self.assertEqual([row["OBS_VALUE"] for row in result], [2, 3])

    def test_extracts_multiple_dimensions_and_series(self):
        payload = {
            "data": {
                "structures": [{
                    "dimensions": {
                        "series": [
                            {"id": "COUNTRY", "values": [{"id": "USA"}, {"id": "CAN"}]},
                            {"id": "INDICATOR", "values": [{"id": "GDP"}, {"id": "CPI"}]},
                        ],
                        "observation": [{"id": "TIME_PERIOD", "values": [{"value": "2020"}]}],
                    }
                }],
                "dataSets": [{"series": {
                    "0:0": {"observations": {"0": [1.5]}},
                    "1:1": {"observations": {"0": [2.5]}},
                }}],
            }
        }
        rows = extract_observations(payload)
        self.assertEqual(
            [(r["COUNTRY"], r["INDICATOR"], r["TIME_PERIOD"], r["OBS_VALUE"]) for r in rows],
            [("USA", "GDP", "2020", 1.5), ("CAN", "CPI", "2020", 2.5)],
        )

    def test_enriches_dimensions_from_dataset_specific_codelists(self):
        payload = {
            "data": {
                "dataflows": [{"id": "FM"}],
                "dataStructures": [{
                    "dataStructureComponents": {
                        "dimensionList": {"dimensions": [
                            {"id": "COUNTRY", "position": 0},
                            {"id": "INDICATOR", "position": 1},
                        ]}
                    }
                }],
                "codelists": [
                    {"id": "CL_COUNTRY", "codes": [{"id": "GEN", "name": "Generic country"}]},
                    {"id": "CL_FM_COUNTRY", "codes": [{"id": "G001", "name": "World"}]},
                    {"id": "CL_FM_INDICATOR", "codes": [{"id": "G1", "name": "Revenue"}]},
                ],
            }
        }
        dimensions = find_dimensions(payload)
        self.assertEqual(dimensions[0]["codelist_id"], "CL_FM_COUNTRY")
        self.assertEqual(dimensions[0]["values"][0]["id"], "G001")
        self.assertEqual(dimensions[1]["values"][0]["name"], "Revenue")


if __name__ == "__main__":
    unittest.main()
