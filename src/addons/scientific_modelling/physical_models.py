from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from i18n import t

Inputs = dict[str, pd.Series]
Params = dict[str, float]


@dataclass(frozen=True)
class PhysicalModel:
    key: str
    name: str
    description: str
    inputs: tuple[tuple[str, str, str], ...]  # (sleutel, label, eenheid)
    params: tuple[tuple[str, str, float], ...]  # (sleutel, label, standaardwaarde)
    unit: str
    func: Callable[[Inputs, Params], pd.Series]


def _doy(series: pd.Series) -> np.ndarray:
    return series.index.day_of_year.to_numpy().astype(float)


def _extraterrestrial_radiation(doy: np.ndarray, latitude_deg: float) -> np.ndarray:
    """Dagelijkse extraterrestrische straling Ra [MJ m-2 dag-1] volgens FAO-56."""
    phi = np.radians(latitude_deg)
    dr = 1 + 0.033 * np.cos(2 * np.pi * doy / 365)
    delta = 0.409 * np.sin(2 * np.pi * doy / 365 - 1.39)
    sunset = np.arccos(np.clip(-np.tan(phi) * np.tan(delta), -1, 1))
    return (24 * 60 / np.pi) * 0.0820 * dr * (
        sunset * np.sin(phi) * np.sin(delta) + np.cos(phi) * np.cos(delta) * np.sin(sunset)
    )


def _penman_monteith(inputs: Inputs, p: Params) -> pd.Series:
    temp, humidity, wind, radiation = (inputs[k] for k in ("T", "RH", "u", "Rs"))
    z, height = p["altitude"], p["wind_height"]
    temp_c = temp.to_numpy(float)
    rs = radiation.to_numpy(float) * 0.0864  # W/m2 -> MJ/m2/dag
    u2 = np.clip(wind.to_numpy(float), 0, None) * 4.87 / np.log(67.8 * height - 5.42)
    pressure = 101.3 * ((293 - 0.0065 * z) / 293) ** 5.26
    gamma = 0.000665 * pressure
    es = 0.6108 * np.exp(17.27 * temp_c / (temp_c + 237.3))
    ea = es * np.clip(humidity.to_numpy(float), 0, 100) / 100
    slope = 4098 * es / (temp_c + 237.3) ** 2
    ra = _extraterrestrial_radiation(_doy(temp), p["latitude"])
    rso = np.maximum((0.75 + 2e-5 * z) * ra, 1e-6)
    relative = np.clip(rs / rso, 0.3, 1.0)
    rns = (1 - p["albedo"]) * rs
    rnl = 4.903e-9 * (temp_c + 273.16) ** 4 * (0.34 - 0.14 * np.sqrt(np.clip(ea, 0, None))) * (1.35 * relative - 0.35)
    rn = rns - rnl
    et0 = (0.408 * slope * rn + gamma * 900 / (temp_c + 273) * u2 * (es - ea)) / (slope + gamma * (1 + 0.34 * u2))
    return pd.Series(et0, index=temp.index, name="ET0")


def _hargreaves(inputs: Inputs, p: Params) -> pd.Series:
    mean, high, low = (inputs[k] for k in ("T", "Tmax", "Tmin"))
    ra = _extraterrestrial_radiation(_doy(mean), p["latitude"]) * 0.408
    spread = np.sqrt(np.clip(high.to_numpy(float) - low.to_numpy(float), 0, None))
    return pd.Series(0.0023 * ra * (mean.to_numpy(float) + 17.8) * spread, index=mean.index, name="ET0")


def _water_balance(inputs: Inputs, p: Params) -> pd.Series:
    rain = np.nan_to_num(inputs["P"].to_numpy(float))
    evaporation = np.nan_to_num(inputs["ET"].to_numpy(float))
    storage = np.empty(len(rain))
    current = float(np.clip(p["initial"], 0, p["capacity"]))
    for i in range(len(rain)):
        current = min(max(current + rain[i] - evaporation[i], 0.0), p["capacity"])
        storage[i] = current
    return pd.Series(storage, index=inputs["P"].index, name="storage")


def _newton_cooling(inputs: Inputs, p: Params) -> pd.Series:
    environment = inputs["Tenv"]
    values = environment.to_numpy(float)
    hours = np.diff(environment.index.to_numpy().astype("datetime64[s]").astype(float), prepend=np.nan) / 3600.0
    result = np.empty(len(values))
    current = p["T0"]
    for i in range(len(values)):
        if i > 0 and np.isfinite(values[i]) and np.isfinite(hours[i]):
            current = current + (values[i] - current) * (1 - np.exp(-p["k"] * hours[i]))
        result[i] = current
    return pd.Series(result, index=environment.index, name="T_object")


def _pressure_reduction(inputs: Inputs, p: Params) -> pd.Series:
    pressure, temp = inputs["P"], inputs["T"].to_numpy(float)
    h = p["height"]
    factor = (1 - 0.0065 * h / (temp + 0.0065 * h + 273.15)) ** -5.257
    return pd.Series(pressure.to_numpy(float) * factor, index=pressure.index, name="P_sealevel")


_SIGMA_Y = {1: 0.22, 2: 0.16, 3: 0.11, 4: 0.08, 5: 0.06, 6: 0.04}
_SIGMA_Z = {
    1: (0.20, 0.0, 0.0), 2: (0.12, 0.0, 0.0), 3: (0.08, 0.0002, -0.5),
    4: (0.06, 0.0015, -0.5), 5: (0.03, 0.0003, -1.0), 6: (0.016, 0.0003, -1.0),
}


def _gaussian_plume(inputs: Inputs, p: Params) -> pd.Series:
    """Concentratie op leefniveau op de pluimas [µg/m3] (Gauss-pluim, Briggs open terrein)."""
    wind = np.clip(inputs["u"].to_numpy(float), 0.5, None)
    x = max(p["distance"], 1.0)
    cls = int(np.clip(round(p["stability"]), 1, 6))
    sigma_y = _SIGMA_Y[cls] * x / np.sqrt(1 + 0.0001 * x)
    a, b, power = _SIGMA_Z[cls]
    sigma_z = a * x * (1 + b * x) ** power if b else a * x
    sigma_z = max(sigma_z, 0.1)
    concentration = p["emission"] / (np.pi * wind * sigma_y * sigma_z) * np.exp(-p["height"] ** 2 / (2 * sigma_z ** 2))
    return pd.Series(concentration * 1e6, index=inputs["u"].index, name="C")


PHYSICAL_MODELS: dict[str, PhysicalModel] = {m.key: m for m in (
    PhysicalModel(
        "penman_monteith", "addon.physical.penman_monteith_fao_56",
        "addon.physical.reference_evapotranspiration_et0_intended_for_daily",
        (("T", "addon.physical.temperature", "°C"), ("RH", "addon.physical.relative_humidity", "%"),
         ("u", "addon.physical.wind_speed", "m/s"), ("Rs", "addon.physical.solar_radiation", "W/m²")),
        (("latitude", "addon.physical.latitude", 51.0), ("altitude", "addon.physical.altitude_m", 10.0),
         ("wind_height", "addon.physical.wind_measurement_height_m", 10.0), ("albedo", "addon.physical.albedo", 0.23)),
        "mm/dag", _penman_monteith),
    PhysicalModel(
        "hargreaves", "addon.physical.hargreaves",
        "addon.physical.simple_et0_estimate_from_daily_temperatures",
        (("T", "addon.physical.mean_temperature", "°C"), ("Tmax", "addon.physical.maximum_temperature", "°C"),
         ("Tmin", "addon.physical.minimum_temperature", "°C")),
        (("latitude", "addon.physical.latitude", 51.0),),
        "mm/dag", _hargreaves),
    PhysicalModel(
        "water_balance", "addon.physical.water_balance_bucket_model",
        "addon.physical.soil_water_storage_from_precipitation_and",
        (("P", "addon.physical.precipitation", "mm"), ("ET", "addon.physical.evapotranspiration", "mm")),
        (("capacity", "addon.physical.capacity_mm", 100.0), ("initial", "addon.physical.initial_storage_mm", 50.0)),
        "mm", _water_balance),
    PhysicalModel(
        "newton_cooling", "addon.physical.newtonian_cooling",
        "addon.physical.temperature_of_an_object_cooling_heating",
        (("Tenv", "addon.physical.ambient_temperature", "°C"),),
        (("T0", "addon.physical.initial_object_temperature_c", 20.0),
         ("k", "addon.physical.cooling_constant_k_1_hour", 0.5)),
        "°C", _newton_cooling),
    PhysicalModel(
        "pressure_reduction", "addon.physical.sea_level_pressure_correction",
        "addon.physical.reduces_station_pressure_to_sea_level",
        (("P", "addon.physical.station_pressure", "hPa"), ("T", "addon.physical.temperature", "°C")),
        (("height", "addon.physical.station_height_m", 10.0),),
        "hPa", _pressure_reduction),
    PhysicalModel(
        "gaussian_plume", "addon.physical.gaussian_plume_air_pollution",
        "addon.physical.ground_level_concentration_on_the_plume",
        (("u", "addon.physical.wind_speed", "m/s"),),
        (("emission", "addon.physical.emission_g_s", 10.0), ("height", "addon.physical.effective_stack_height_m", 30.0),
         ("distance", "addon.physical.distance_to_source_m", 500.0),
         ("stability", "addon.physical.stability_class_1_a_6_f", 4.0)),
        "µg/m³", _gaussian_plume),
)}


def run_physical_model(model: PhysicalModel, mapping: dict[str, str], params: Params, frame: pd.DataFrame) -> pd.Series:
    missing = [key for key, _label, _unit in model.inputs if mapping.get(key) not in frame.columns]
    if missing:
        raise ValueError(t("addon.modelling.assign_a_measured_variable_to", missing=', '.join(missing)))
    inputs = {key: frame[mapping[key]] for key, _label, _unit in model.inputs}
    return model.func(inputs, params)
