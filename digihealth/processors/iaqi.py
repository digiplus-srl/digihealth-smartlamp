from typing import Dict, Any, List
import logging
log = logging.getLogger("digihealth")

class IAQIProcessor:
    """Processes IAQI (Indoor Air Quality Index) calculations."""

    PM25_BREAKPOINTS = [
        {"C_lo": 0.0, "C_hi": 12.0, "I_lo": 0, "I_hi": 50},
        {"C_lo": 12.1, "C_hi": 35.4, "I_lo": 51, "I_hi": 100},
        {"C_lo": 35.5, "C_hi": 55.4, "I_lo": 101, "I_hi": 150},
        {"C_lo": 55.5, "C_hi": 150.0, "I_lo": 151, "I_hi": 200},
    ]

    PM10_BREAKPOINTS = [
        {"C_lo": 0.0,  "C_hi": 54.0,  "I_lo": 0,   "I_hi": 50},
        {"C_lo": 55.0, "C_hi": 154.0, "I_lo": 51,  "I_hi": 100},
        {"C_lo": 155.0,"C_hi": 254.0, "I_lo": 101, "I_hi": 150},
        {"C_lo": 255.0,"C_hi": 354.0, "I_lo": 151, "I_hi": 200},
        {"C_lo": 355.0,"C_hi": 424.0, "I_lo": 201, "I_hi": 300},
    ]

    CO2_BREAKPOINTS = [
        {"C_lo": 400,  "C_hi": 800,  "I_lo": 0,   "I_hi": 50},
        {"C_lo": 801,  "C_hi": 1000, "I_lo": 51,  "I_hi": 100},
        {"C_lo": 1001, "C_hi": 1500, "I_lo": 101, "I_hi": 150},
        {"C_lo": 1501, "C_hi": 2000, "I_lo": 151, "I_hi": 200},
        {"C_lo": 2001, "C_hi": 5000, "I_lo": 201, "I_hi": 300},
    ]

    CO_BREAKPOINTS = [
        {"C_lo": 0.0,  "C_hi": 4.4,  "I_lo": 0,   "I_hi": 50},
        {"C_lo": 4.5,  "C_hi": 9.4,  "I_lo": 51,  "I_hi": 100},
        {"C_lo": 9.5,  "C_hi": 12.4, "I_lo": 101, "I_hi": 150},
        {"C_lo": 12.5, "C_hi": 15.4, "I_lo": 151, "I_hi": 200},
        {"C_lo": 15.5, "C_hi": 30.4, "I_lo": 201, "I_hi": 300},
    ]

    TVOC_BREAKPOINTS = [
        {"C_lo": 0.0,  "C_hi": 0.3,  "I_lo": 0,   "I_hi": 50},
        {"C_lo": 0.31, "C_hi": 0.6,  "I_lo": 51,  "I_hi": 100},
        {"C_lo": 0.61, "C_hi": 1.0,  "I_lo": 101, "I_hi": 150},
        {"C_lo": 1.01, "C_hi": 3.0,  "I_lo": 151, "I_hi": 200},
    ]

    CH2O_BREAKPOINTS = [
        {"C_lo": 0,   "C_hi": 50,   "I_lo": 0,   "I_hi": 50},
        {"C_lo": 51,  "C_hi": 100,  "I_lo": 51,  "I_hi": 100},
        {"C_lo": 101, "C_hi": 200,  "I_lo": 101, "I_hi": 150},
        {"C_lo": 201, "C_hi": 1000, "I_lo": 151, "I_hi": 200},
    ]

    # NO2 escluso: il ZPHS01B non misura NO2, quei byte restituiscono valori non validi (~10 ppm)
    # NO2_BREAKPOINTS = [...]

    def process(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate IAQI and add to data."""
        pm25 = data.get("PM2_5-Particolato-[µg/m^3]", 0)
        pm10 = data.get("PM10-Particolato-[µg/m^3]", 0)
        co2  = data.get("CO2-AnidrideCarbonica-[ppm]", 0)
        co   = data.get("CO-MonossidoDiCarbonio-[ppm]", 0)
        tvoc = data.get("TVOC-QualitaAria-[G]", 0)
        ch2o = data.get("CH2O-Formaldeie-[mg/m^3]", 0)

        iaq_pm25 = self._calculate_sub_index(pm25, self.PM25_BREAKPOINTS)
        iaq_pm10 = self._calculate_sub_index(pm10, self.PM10_BREAKPOINTS)
        iaq_co2  = self._calculate_sub_index(co2,  self.CO2_BREAKPOINTS)
        iaq_co   = self._calculate_sub_index(co,   self.CO_BREAKPOINTS)
        iaq_tvoc = self._calculate_sub_index(tvoc, self.TVOC_BREAKPOINTS)
        iaq_ch2o = self._calculate_sub_index(ch2o, self.CH2O_BREAKPOINTS)

        iAQI = int(max(iaq_pm25, iaq_pm10, iaq_co2, iaq_co, iaq_tvoc, iaq_ch2o))

        log.debug(
            f"IAQI={iAQI} PM2.5={pm25:.1f}({iaq_pm25:.0f}) PM10={pm10:.1f}({iaq_pm10:.0f}) "
            f"CO2={co2:.0f}({iaq_co2:.0f}) CO={co:.1f}({iaq_co:.0f}) "
            f"TVOC={tvoc:.2f}({iaq_tvoc:.0f}) CH2O={ch2o:.3f}({iaq_ch2o:.0f})"
        )

        data["IAQI"] = iAQI

        data["dashboard"] = {
            "temp":     round(data.get("TEMP-[C]", 0), 1),
            "humidity": data.get("HUM-[%]", 0),
            "co2":      data.get("CO2-AnidrideCarbonica-[ppm]", 0),
            "iaqi":     iAQI,
            "tvoc":     data.get("TVOC-QualitaAria-[G]", 0),
        }

        return data

    def _calculate_sub_index(self, C: float, breakpoints: List[Dict[str, float]]) -> float:
        """Calculate sub-index for a pollutant."""
        if C < breakpoints[0]["C_lo"]:
            return 0
        for bp in breakpoints:
            if bp["C_lo"] <= C <= bp["C_hi"]:
                return ((bp["I_hi"] - bp["I_lo"]) / (bp["C_hi"] - bp["C_lo"])) * (C - bp["C_lo"]) + bp["I_lo"]
        return breakpoints[-1]["I_hi"]
