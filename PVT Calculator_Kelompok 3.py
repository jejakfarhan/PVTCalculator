from __future__ import annotations

import math
import zipfile
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from xml.sax.saxutils import escape
import tkinter as tk
import requests
from io import BytesIO
from PIL import Image, ImageTk
# =========================================================
# DATA MODEL
# =========================================================

@dataclass
class PVTInput:
    pressure: float
    initial_pressure: float
    standard_pressure: float
    temperature_f: float
    gas_gravity: float
    api: float
    bubble_pressure: float
    separator_pressure: float
    separator_temperature_f: float
    co2_percent: float
    h2s_percent: float
    n2_percent: float
    tds_percent: float
    brine_condition: int
    rs_corr: int
    bo_sat_corr: int
    bo_unsat_corr: int
    z_corr: int
    tpc_ppc_corr: int
    critical_corr: int
    gas_vis_corr: int
    brine_vis_corr: int

    @property
    def temperature_r(self) -> float:
        """Konversi Fahrenheit ke Rankine untuk korelasi PVT."""
        return self.temperature_f + 460.0

    @property
    def co2_fraction(self) -> float:
        return self.co2_percent / 100.0

    @property
    def h2s_fraction(self) -> float:
        return self.h2s_percent / 100.0

    @property
    def n2_fraction(self) -> float:
        return self.n2_percent / 100.0


# =========================================================
# UTILITIES
# =========================================================

def positive(value: float, label: str) -> None:
    if value <= 0:
        raise ValueError(f"{label} harus lebih besar dari 0.")


def condition_label(pressure: float, bubble_pressure: float) -> str:
    if pressure < bubble_pressure:
        return "Saturated"
    if pressure > bubble_pressure:
        return "Undersaturated"
    return "At Bubble Point"


def oil_sg(api: float) -> float:
    return 141.5 / (131.5 + api)


def corrected_gas_gravity(gas_gravity: float, api: float, psep: float, tsep_f: float) -> float:
    """Gas gravity terkoreksi separator."""
    return gas_gravity * (1 + 5.912e-5 * api * tsep_f * math.log10(psep / 114.7))


def build_pressure_table(initial_pressure: float, bubble_pressure: float, standard_pressure: float) -> list[float]:
    """
    Membuat 31 titik tekanan seperti program lama:
    index 12 = Pb, index 30 = tekanan standar.
    """
    pressures = [0.0] * 31
    pressures[0] = round(initial_pressure, 3)
    pressures[12] = round(bubble_pressure, 3)
    pressures[11] = round(bubble_pressure + 1, 3)
    pressures[13] = round(bubble_pressure - 1, 3)
    pressures[30] = round(standard_pressure, 3)

    for i in range(1, 11):
        pressures[i] = round(pressures[i - 1] + ((pressures[11] - pressures[0]) / 11), 3)

    for i in range(14, 30):
        pressures[i] = round(pressures[i - 1] + ((pressures[30] - pressures[13]) / 17), 3)

    return pressures


# =========================================================
# OIL CORRELATIONS
# =========================================================

def solution_gor(corr: int, pressure: float, gas_gravity: float, temp_r: float,
                 api: float, psep: float, tsep_f: float, bubble_pressure: float) -> float:
    """Rs dalam MSCF/STB. Jika P > Pb, tekanan efektif dibuat sama dengan Pb."""
    p_eff = min(pressure, bubble_pressure)

    if corr == 1:  # Standing
        x = 0.0125 * api - 0.00091 * (temp_r - 460)
        return gas_gravity * ((p_eff / 18.2 + 1.4) * (10 ** x)) ** 1.2048 / 1000

    if corr == 2:  # Vasquez-Beggs
        gamma_gs = corrected_gas_gravity(gas_gravity, api, psep, tsep_f)
        if api <= 30:
            c1, c2, c3 = 0.0362, 1.0937, 25.724
        else:
            c1, c2, c3 = 0.0178, 1.187, 23.931
        return c1 * gamma_gs * (p_eff ** c2) * math.exp(c3 * api / temp_r) / 1000

    if corr == 3:  # Glaso
        pbst = 10 ** (2.8869 - math.sqrt(14.1811 - 3.3093 * math.log10(p_eff)))
        return gas_gravity / 1000 * ((api ** 0.989) * pbst / ((temp_r - 460) ** 0.172)) ** 1.2255

    raise ValueError("Korelasi Rs tidak valid.")


def oil_fvf_sat(corr: int, rs_mscf_stb: float, gas_gravity: float, api: float,
                temp_r: float, psep: float, tsep_f: float) -> float:
    """Bo saturated dalam RB/STB."""
    rs = rs_mscf_stb * 1000
    sg_oil = oil_sg(api)
    gamma_gs = corrected_gas_gravity(gas_gravity, api, psep, tsep_f)

    if corr == 1:  # Standing
        return 0.9759 + 0.00012 * (rs * math.sqrt(gas_gravity / sg_oil) + 1.25 * (temp_r - 460)) ** 1.2

    if corr == 2:  # Vasquez-Beggs
        if api > 30:
            c1, c2, c3 = 4.67e-4, 1.10e-5, 1.337e-9
        else:
            c1, c2, c3 = 4.677e-4, 1.751e-5, -1.881e-8
        return 1 + c1 * rs + (temp_r - 520) * (api / gamma_gs) * (c2 + c3 * rs)

    if corr == 3:  # Glaso
        bobb = rs * (gas_gravity / sg_oil) ** 0.526 + 0.968 * (temp_r - 460)
        a = -6.58511 + 2.91329 * math.log10(bobb) - 0.27683 * (math.log10(bobb) ** 2)
        return 1 + 10 ** a

    raise ValueError("Korelasi Bo saturated tidak valid.")


def oil_fvf(corr_sat: int, corr_unsat: int, rs_mscf_stb: float, gas_gravity: float,
            api: float, temp_r: float, pressure: float, bubble_pressure: float,
            rs_pb_mscf_stb: float, psep: float, tsep_f: float, bo_pb: float) -> float:
    """Bo dalam RB/STB. Cabang saturated/undersaturated dibuat terpisah agar lebih jelas."""
    if pressure <= bubble_pressure:
        return oil_fvf_sat(corr_sat, rs_mscf_stb, gas_gravity, api, temp_r, psep, tsep_f)

    rs_pb = rs_pb_mscf_stb * 1000
    gamma_gs = corrected_gas_gravity(gas_gravity, api, psep, tsep_f)

    if corr_unsat == 1:  # Vasquez-Beggs
        a1 = 1e-5 * (-1433 + 5 * rs_pb + 17.2 * (temp_r - 460) - 1180 * gamma_gs + 12.61 * api)
        return bo_pb * math.exp(-a1 * math.log(pressure / bubble_pressure))

    if corr_unsat == 2:  # Petrosky-Farshad
        a1 = 4.1646e-7 * (rs_pb ** 0.69357) * (gas_gravity ** 0.1885) * (api ** 0.3272) * ((temp_r - 460) ** 0.6729)
        return bo_pb * math.exp(-a1 * ((pressure ** 0.4094) - (bubble_pressure ** 0.4096)))

    raise ValueError("Korelasi Bo undersaturated tidak valid.")


def dead_oil_viscosity(api: float, temp_r: float, corr: int = 3) -> float:
    """μod/cp. Default dibuat Beggs-Robinson karena stabil untuk uji coba."""
    temp_f = temp_r - 460

    if corr == 1:  # Beals
        a = 10 ** (0.43 + 8.33 / api)
        return (0.32 + 1.8 * 10_000_000 / (api ** 4.53)) * (360 / (temp_r - 260)) ** a

    if corr == 2:  # Glaso
        a = 10.313 * math.log10(temp_f) - 36.447
        return 3.141e10 * (temp_f ** -3.444) * (math.log10(api) ** a)

    if corr == 3:  # Beggs-Robinson
        z = 3.0324 - 0.02023 * api
        y = 10 ** z
        x = y * (temp_f ** -1.163)
        return 10 ** x - 1

    raise ValueError("Korelasi dead oil viscosity tidak valid.")


def live_oil_viscosity(rs_mscf_stb: float, pressure: float, bubble_pressure: float,
                       dead_vis: float, oil_vis_pb: float | None = None, corr: int = 2) -> float:
    """μo/cp. Untuk P > Pb memakai cabang undersaturated Vasquez-Beggs dari kode lama."""
    rs = rs_mscf_stb * 1000

    if pressure <= bubble_pressure:
        if corr == 1:  # Chew-Connally
            e = 3.74e-3 * rs
            d = 1.1e-3 * rs
            c = 8.62e-5 * rs
            b = 0.68 / (10 ** c) + 0.25 / (10 ** d) + 0.062 / (10 ** e)
            a = rs * (2.2e-7 * rs - 7.4e-4)
            return (10 ** a) * (dead_vis ** b)

        if corr == 2:  # Beggs-Robinson
            a = 10.715 * ((rs + 100) ** -0.515)
            b = 5.44 * ((rs + 150) ** -0.338)
            return a * (dead_vis ** b)

    oil_vis_pb = oil_vis_pb if oil_vis_pb is not None else dead_vis
    a = -3.9e-5 * pressure - 5
    m = 2.6 * (pressure ** 1.187) * (10 ** a)
    return oil_vis_pb * ((pressure / bubble_pressure) ** m) * 1e-2


def oil_density(api: float, rs_scf_stb: float, gas_gravity: float, bo: float) -> float:
    """ρo/lbm-ft3, bentuk general dari kode lama."""
    sg_oil = oil_sg(api)
    return (62.4 * sg_oil + 0.0136 * rs_scf_stb * gas_gravity) / bo


def oil_compressibility(pressure: float, bubble_pressure: float, rs_scf_stb: float,
                        rs_pb_scf_stb: float, temp_r: float, api: float, gas_gravity: float,
                        bg_rb_mscf: float, bo: float, psep: float, tsep_f: float) -> float:
    """Co dalam 1/MMpsi. Default cabang mengikuti Standing/Vasquez-Beggs sederhana."""
    sg_oil = oil_sg(api)
    gamma_gs = corrected_gas_gravity(gas_gravity, api, psep, tsep_f)

    if pressure <= bubble_pressure:
        # Standing saturated compressibility
        bg_rb_scf = bg_rb_mscf / 1000
        term = 0.00014 * math.sqrt(gas_gravity / sg_oil) * ((rs_scf_stb * math.sqrt(gas_gravity / sg_oil) + 1.25 * (temp_r - 460)) ** 0.12) - bg_rb_scf
        co = -rs_scf_stb / (bo * (0.83 * pressure + 21.75)) * term
    else:
        # Vasquez-Beggs undersaturated compressibility
        co = (-1433 + 5 * rs_pb_scf_stb + 17.2 * (temp_r - 460) - 1180 * gamma_gs + 12.61 * api) / (1e5 * pressure)

    return co * 100000


# =========================================================
# GAS CORRELATIONS
# =========================================================

def pseudo_critical_temperature(corr: int, gas_gravity: float) -> float:
    if corr == 1:  # Natural gas - Standing
        return 168 + 325 * gas_gravity - 12.5 * gas_gravity ** 2
    if corr == 2:  # Sutton
        return 169.2 + 349.5 * gas_gravity - 74 * gas_gravity ** 2
    if corr == 3:  # Condensate gas - Standing
        return 187 + 330 * gas_gravity - 71.5 * gas_gravity ** 2
    raise ValueError("Korelasi Tpc tidak valid.")


def pseudo_critical_pressure(corr: int, gas_gravity: float) -> float:
    if corr == 1:  # Natural gas - Standing
        return 677 + 15 * gas_gravity - 37.5 * gas_gravity ** 2
    if corr == 2:  # Sutton
        return 756.8 - 131 * gas_gravity - 3.6 * gas_gravity ** 2
    if corr == 3:  # Condensate gas - Standing
        return 706 - 51.7 * gas_gravity - 11.1 * gas_gravity ** 2
    raise ValueError("Korelasi Ppc tidak valid.")


def corrected_critical_properties(corr: int, tpc: float, ppc: float,
                                  h2s: float, co2: float, n2: float) -> tuple[float, float]:
    """Koreksi impurities untuk Tpc dan Ppc."""
    if corr == 1:  # Wichert-Aziz
        a = h2s + co2
        b = h2s
        eps = 120 * (a ** 0.9 - a ** 1.6) + 15 * (b ** 0.5 - b ** 4)
        t_corr = tpc - eps
        p_corr = (ppc * t_corr) / (tpc + b * (1 - b) * eps)
        return t_corr, p_corr

    if corr == 2:  # Carr-Kobayashi-Burrows
        t_corr = tpc - 80 * co2 + 130 * h2s - 250 * n2
        p_corr = ppc + 440 * co2 + 600 * h2s - 170 * n2
        return t_corr, p_corr

    raise ValueError("Korelasi critical correction tidak valid.")


def y_hall_yarborough(y: float, tpr: float, ppr: float) -> float:
    t = 1 / tpr
    part1 = -0.06125 * ppr * t * math.exp(-1.2 * (1 - t) ** 2)
    part2 = (y + y ** 2 + y ** 3 - y ** 4) / ((1 - y) ** 3)
    part3 = (14.76 * t - 9.76 * t ** 2 + 4.58 * t ** 3) * y ** 2
    part4 = (90.7 * t - 242.2 * t ** 2 + 42.4 * t ** 3) * (y ** (2.18 + 2.82 * t))
    return part1 + part2 - part3 + part4


def z_factor(corr: int, tpr: float, ppr: float) -> float:
    """Z-factor gas. Beberapa metode dari kode lama, dibuat lebih aman untuk GUI."""
    positive(tpr, "Tpr")
    positive(ppr, "Ppr")

    if corr == 1:  # Dranchuk-Abu-Kassem
        a1, a2, a3, a4, a5 = 0.3265, -1.07, -0.5339, 0.01569, -0.05165
        a6, a7, a8, a9, a10, a11 = 0.5475, -0.7361, 0.1844, 0.1056, 0.6134, 0.721
        r1 = a1 + a2 / tpr + a3 / tpr ** 3 + a4 / tpr ** 4 + a5 / tpr ** 5
        r2 = 0.27 * ppr / tpr
        r3 = a6 + a7 / tpr + a8 / tpr ** 2
        r4 = a9 * (a7 / tpr + a8 / tpr ** 2)
        r5 = a10 / tpr ** 3

        rho = max(0.27 * ppr / tpr, 0.001)
        for _ in range(100):
            f = r1 * rho - r2 / rho + r3 * rho ** 2 - r4 * rho ** 5 + r5 * (1 + a11 * rho ** 2) * rho ** 2 * math.exp(-a11 * rho ** 2) + 1
            df = r1 + r2 / rho ** 2 + 2 * r3 * rho - 5 * r4 * rho ** 4 + 2 * r5 * rho * math.exp(-a11 * rho ** 2) * ((1 + 2 * a11 * rho ** 2) - a11 * rho ** 2 * (1 + a11 * rho ** 2))
            if abs(df) < 1e-12:
                break
            rho_new = rho - f / df
            if rho_new <= 0:
                rho_new = rho / 2
            if abs((rho_new - rho) / rho_new) < 1e-6:
                rho = rho_new
                break
            rho = rho_new
        return 0.27 * ppr / (rho * tpr)

    if corr == 2:  # Dranchuk-Purvis-Robinson
        a = 0.064225133
        b = 0.53530771 * tpr - 0.61232032
        c = 0.31506237 * tpr - 1.0467099 - 0.57832729 / tpr ** 2
        d = tpr
        e = 0.68157001 / tpr ** 2
        f_const = 0.68446549
        g = 0.27 * ppr

        rho = max(0.27 * ppr / tpr, 0.001)
        for _ in range(100):
            frho = a * rho ** 6 + b * rho ** 3 + c * rho ** 2 + d * rho + e * rho ** 3 * (1 + f_const * rho ** 2) * math.exp(-f_const * rho ** 2) - g
            dfrho = 6 * a * rho ** 5 + 3 * b * rho ** 2 + 2 * c * rho + d + e * rho ** 2 * (3 + f_const * rho ** 2 * (3 - 2 * f_const * rho ** 2)) * math.exp(-f_const * rho ** 2)
            if abs(dfrho) < 1e-12:
                break
            rho_new = rho - frho / dfrho
            if rho_new <= 0:
                rho_new = rho / 2
            if abs((rho_new - rho) / rho_new) < 1e-6:
                rho = rho_new
                break
            rho = rho_new
        return 0.27 * ppr / (rho * tpr)

    if corr == 3:  # Hall-Yarborough
        y = 0.01
        for _ in range(125):
            f = y_hall_yarborough(y, tpr, ppr)
            f2 = y_hall_yarborough(y + 1e-5, tpr, ppr)
            df = (f2 - f) / 1e-5
            if abs(df) < 1e-12:
                break
            y_new = y - f / df
            if y_new <= 0 or y_new >= 1:
                y_new = (y + 0.01) / 2
            if abs(y_new - y) < 1e-7:
                y = y_new
                break
            y = y_new
        return (0.06125 * ppr / (tpr * y)) * math.exp(-1.2 * (1 - 1 / tpr) ** 2)

    if corr == 4:  # Beggs-Brill
        if tpr <= 0.92:
            raise ValueError("Beggs-Brill butuh Tpr > 0.92.")
        a = 1.39 * math.sqrt(tpr - 0.92) - 0.36 * tpr - 0.1
        b = (0.62 - 0.23 * tpr) * ppr + (0.066 / (tpr - 0.86) - 0.037) * ppr ** 2 + 0.32 / (10 ** (9 * (tpr - 1))) * ppr ** 6
        c = 0.132 - 0.32 * math.log10(tpr)
        d = 10 ** (0.3106 - 0.49 * tpr + 0.1824 * tpr ** 2)
        return a + (1 - a) / math.exp(b) + c * ppr ** d

    raise ValueError("Korelasi Z-factor tidak valid.")


def gas_fvf(z: float, temp_r: float, pressure: float) -> float:
    return 0.005035 * z * temp_r * 1000 / pressure


def gas_density(gas_gravity: float, temp_r: float, pressure: float, z: float) -> float:
    molecular_weight = gas_gravity * 28.96
    return pressure * molecular_weight / (z * 10.73 * temp_r)


def gas_compressibility(z_corr: int, temp_r: float, tpc_corr: float, pressure: float, ppc_corr: float) -> float:
    """Cg dalam 1/MMpsi, turunan numerik terhadap tekanan."""
    step = 1e-4
    z1 = z_factor(z_corr, temp_r / tpc_corr, (pressure + step) / ppc_corr)
    z0 = z_factor(z_corr, temp_r / tpc_corr, pressure / ppc_corr)
    dzdp = (z1 - z0) / step
    return ((1 / pressure) - (dzdp / z0)) * 100000


def water_content_in_gas(tds_percent: float, temp_r: float, pressure: float) -> float:
    pv_w = math.exp(69.103501 - 13064.76 / temp_r - 7.3037 * math.log(temp_r) + 1.2856e-6 * temp_r ** 2)
    mw = 18 / 380 * pv_w / pressure
    corr = 1 - 4.92e-3 * tds_percent - 1.7672e-4 * tds_percent ** 2
    return mw * corr * 1000


def gas_viscosity(corr: int, tpc: float, ppc: float, co2: float, n2: float, h2s: float,
                  gas_gravity: float, temp_r: float, pressure: float, rho_g: float) -> float:
    tpr = temp_r / tpc
    ppr = pressure / ppc
    molecular_weight = gas_gravity * 28.96

    if corr == 1:  # Carr-Kobayashi + Dempsey
        mu_nc = 1.709 * (1e-5 - 2.062e-6 * gas_gravity) * (temp_r - 460) + 8.1118e-3 - 6.15e-3 * math.log10(gas_gravity)
        mu_c = co2 * (9.08e-3 * math.log10(gas_gravity) + 6.24e-3)
        mu_n = n2 * (8.48e-3 * math.log10(gas_gravity) + 9.59e-3)
        mu_s = h2s * (8.49e-3 * math.log10(gas_gravity) + 3.73e-3)
        mu1 = mu_nc + mu_c + mu_n + mu_s

        a0, a1, a2, a3 = -2.4621182, 2.970547414, -0.286264054, 0.00805420522
        a4, a5, a6, a7 = 2.80860949, -3.49803305, 0.36037302, -0.01044324
        a8, a9, a10, a11 = -0.793385648, 1.39643306, -0.149144925, 0.00441015512
        a12, a13, a14, a15 = 0.0839387178, -0.186408848, 0.0203367881, -0.000609579263
        logn = (
            a0 + a1 * ppr + a2 * ppr ** 2 + a3 * ppr ** 3
            + tpr * (a4 + a5 * ppr + a6 * ppr ** 2 + a7 * ppr ** 3)
            + tpr ** 2 * (a8 + a9 * ppr + a10 * ppr ** 2 + a11 * ppr ** 3)
            + tpr ** 3 * (a12 + a13 * ppr + a14 * ppr ** 2 + a15 * ppr ** 3)
        )
        return math.exp(logn) * mu1 / tpr

    if corr == 2:  # Lee-Gonzalez-Eakin
        k = (9.4 + 0.02 * molecular_weight) * temp_r ** 1.5 / (209 + 19 * molecular_weight + temp_r)
        x = 3.5 + 986 / temp_r + 0.01 * molecular_weight
        y = 2.4 - 0.2 * x
        return 1e-4 * k * math.exp(x * ((rho_g / 62.4) ** y))

    raise ValueError("Korelasi gas viscosity tidak valid.")


# =========================================================
# BRINE CORRELATIONS
# =========================================================

def brine_fvf(condition: int, temp_r: float, pressure: float) -> float:
    temp_f = temp_r - 460
    if condition == 2:  # gas free brine
        a1 = 0.9947 + 0.58e-6 * temp_f + 1.02e-6 * temp_f ** 2
        a2 = -4.228e-6 + 1.8376e-8 * temp_f - 6.77e-11 * temp_f ** 2
        a3 = 1.3e-10 - 1.3855e-12 * temp_f + 4.285e-15 * temp_f ** 2
    else:  # gas saturated brine
        a1 = 0.9911 + 6.35e-5 * temp_f + 8.5e-7 * temp_f ** 2
        a2 = -1.093e-6 - 3.497e-9 * temp_f + 4.57e-12 * temp_f ** 2
        a3 = -5e-11 + 6.429e-13 * temp_f - 1.43e-15 * temp_f ** 2
    return a1 + a2 * pressure + a3 * pressure ** 2


def rsw(temp_r: float, pressure: float, tds_percent: float) -> float:
    temp_f = temp_r - 460
    a0 = 8.15839 - 6.12265e-2 * temp_f + 1.91663e-4 * temp_f ** 2 - 2.1654e-7 * temp_f ** 3
    a1 = 1.01021e-2 - 7.44241e-5 * temp_f + 3.05553e-7 * temp_f ** 2 - 2.94883e-10 * temp_f ** 3
    a2 = -1e-7 * (9.02505 - 0.130237 * temp_f + 8.53425e-4 * temp_f ** 2 - 2.34122e-6 * temp_f ** 3 + 2.37049e-9 * temp_f ** 4)
    rw = a0 + a1 * pressure + a2 * pressure ** 2
    corr = 10 ** (-0.0840655 * tds_percent * (temp_f ** -0.285854))
    return rw * corr / 1000


def brine_density(tds_percent: float, bw: float) -> float:
    rho_std = 62.368 + 0.438603 * tds_percent + 1.60074e-3 * tds_percent ** 2
    return rho_std / bw


def brine_viscosity(corr: int, tds_percent: float, pressure: float, temp_r: float) -> float:
    temp_f = temp_r - 460
    if corr == 1:  # Meehan
        s = tds_percent / 100
        d = 1.12166 - 0.0263951 * s + 6.79461e-4 * s ** 2 + 5.47119e-5 * s ** 3 - 1.55586e-6 * s ** 4
        vis_wt = (109.574 - 8.40564 * s + 0.313314 * s ** 2 + 8.72213e-3 * s ** 3) * temp_f ** (-d)
        return vis_wt * (0.9994 + 4.0295e-5 * pressure + 3.1062e-9 * pressure ** 2)

    if corr == 2:  # Beggs-Brill
        return math.exp(1.003 - 1.479e-2 * temp_f + 1.982e-5 * temp_f ** 2)

    raise ValueError("Korelasi brine viscosity tidak valid.")


def water_compressibility(condition: int, temp_r: float, pressure: float, rsw_value: float, tds_percent: float) -> float:
    temp_f = temp_r - 460
    c0 = 3.8546 - 0.000134 * pressure
    c1 = -0.01052 + 0.000000477 * pressure
    c2 = 0.000039267 - 0.00000000088 * pressure
    cwf = (c0 + c1 * temp_f + c2 * temp_f ** 2) / 1_000_000
    corr_salt = (-0.052 + 0.00027 * temp_f - 0.00000114 * temp_f ** 2 + 0.000000001121 * temp_f ** 3) * tds_percent + 1
    corr_gas = 1 + 0.0089 * rsw_value if condition == 1 else 1
    return cwf * corr_salt * corr_gas * 100000


# =========================================================
# CALCULATION ENGINE
# =========================================================

def calculate_at_pressure(data: PVTInput, pressure: float) -> dict[str, float | str]:
    temp_r = data.temperature_r

    # Critical properties gas
    tpc = pseudo_critical_temperature(data.tpc_ppc_corr, data.gas_gravity)
    ppc = pseudo_critical_pressure(data.tpc_ppc_corr, data.gas_gravity)
    tpc_corr, ppc_corr = corrected_critical_properties(
        data.critical_corr, tpc, ppc, data.h2s_fraction, data.co2_fraction, data.n2_fraction
    )
    tpr = temp_r / tpc_corr
    ppr = pressure / ppc_corr
    z = z_factor(data.z_corr, tpr, ppr)
    bg = gas_fvf(z, temp_r, pressure)
    eg = 1 / bg
    rho_g = gas_density(data.gas_gravity, temp_r, pressure, z)
    mu_g = gas_viscosity(data.gas_vis_corr, tpc_corr, ppc_corr, data.co2_fraction, data.n2_fraction,
                         data.h2s_fraction, data.gas_gravity, temp_r, pressure, rho_g)
    cg = gas_compressibility(data.z_corr, temp_r, tpc_corr, pressure, ppc_corr)
    h2o_gas = water_content_in_gas(data.tds_percent, temp_r, pressure)

    # Oil properties
    rs = solution_gor(data.rs_corr, pressure, data.gas_gravity, temp_r, data.api,
                      data.separator_pressure, data.separator_temperature_f, data.bubble_pressure)
    rs_pb = solution_gor(data.rs_corr, data.bubble_pressure, data.gas_gravity, temp_r, data.api,
                         data.separator_pressure, data.separator_temperature_f, data.bubble_pressure)
    bo_pb = oil_fvf_sat(data.bo_sat_corr, rs_pb, data.gas_gravity, data.api, temp_r,
                        data.separator_pressure, data.separator_temperature_f)
    bo = oil_fvf(data.bo_sat_corr, data.bo_unsat_corr, rs, data.gas_gravity, data.api, temp_r,
                 pressure, data.bubble_pressure, rs_pb, data.separator_pressure,
                 data.separator_temperature_f, bo_pb)
    rho_o = oil_density(data.api, rs * 1000, data.gas_gravity, bo)
    mu_dead = dead_oil_viscosity(data.api, temp_r, corr=3)
    mu_o_pb = live_oil_viscosity(rs_pb, data.bubble_pressure, data.bubble_pressure, mu_dead, corr=2)
    mu_o = live_oil_viscosity(rs, pressure, data.bubble_pressure, mu_dead, oil_vis_pb=mu_o_pb, corr=2)
    co = oil_compressibility(pressure, data.bubble_pressure, rs * 1000, rs_pb * 1000, temp_r, data.api,
                             data.gas_gravity, bg, bo, data.separator_pressure, data.separator_temperature_f)

    # Brine properties
    bw = brine_fvf(data.brine_condition, temp_r, pressure)
    rsw_value = rsw(temp_r, pressure, data.tds_percent)
    rho_w = brine_density(data.tds_percent, bw)
    mu_w = brine_viscosity(data.brine_vis_corr, data.tds_percent, pressure, temp_r)
    cw = water_compressibility(data.brine_condition, temp_r, pressure, rsw_value, data.tds_percent)

    return {
        "Pressure (psia)": pressure,
        "Condition": condition_label(pressure, data.bubble_pressure),
        "Bo (RB/STB)": bo,
        "Rs (MSCF/STB)": rs,
        "Oil Density (lbm/ft3)": rho_o,
        "Oil Viscosity (cp)": mu_o,
        "Dead Oil Viscosity (cp)": mu_dead,
        "Co (1/MMpsi)": co,
        "Tpc corrected (R)": tpc_corr,
        "Ppc corrected (psia)": ppc_corr,
        "Tpr": tpr,
        "Ppr": ppr,
        "Z Factor": z,
        "Bg (RB/MSCF)": bg,
        "Eg (MSCF/RB)": eg,
        "Gas Density (lbm/ft3)": rho_g,
        "Gas Viscosity (cp)": mu_g,
        "Cg (1/MMpsi)": cg,
        "H2O in Gas (lbm/MSCF)": h2o_gas,
        "Bw (RB/STB)": bw,
        "Rsw (SCF/STB)": rsw_value,
        "Brine Density (lbm/ft3)": rho_w,
        "Brine Viscosity (cp)": mu_w,
        "Cw (1/MMpsi)": cw,
    }


def calculate_table(data: PVTInput) -> list[dict[str, float | str]]:
    pressures = build_pressure_table(data.initial_pressure, data.bubble_pressure, data.standard_pressure)
    return [calculate_at_pressure(data, pressure) for pressure in pressures]


# =========================================================
# LIBRARY XLSX EXPORTER
# =========================================================

class SimpleXLSXWriter:

    def __init__(self) -> None:
        self.sheets: list[tuple[str, list[list[object]]]] = []

    def add_sheet(self, name: str, rows: list[list[object]]) -> None:
        safe_name = name[:31].replace("/", "-").replace("\\", "-")
        self.sheets.append((safe_name, rows))

    @staticmethod
    def _col_name(index: int) -> str:
        name = ""
        while index:
            index, rem = divmod(index - 1, 26)
            name = chr(65 + rem) + name
        return name

    @classmethod
    def _cell_ref(cls, row: int, col: int) -> str:
        return f"{cls._col_name(col)}{row}"

    @staticmethod
    def _format_number(value: float) -> str:
        if math.isfinite(value):
            return f"{value:.10g}"
        return ""

    def _sheet_xml(self, rows: list[list[object]]) -> str:
        xml_rows = []
        for r_idx, row in enumerate(rows, start=1):
            cells = []
            for c_idx, value in enumerate(row, start=1):
                if value is None:
                    continue
                ref = self._cell_ref(r_idx, c_idx)
                if isinstance(value, bool):
                    cells.append(f'<c r="{ref}" t="b"><v>{1 if value else 0}</v></c>')
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    cells.append(f'<c r="{ref}"><v>{self._format_number(float(value))}</v></c>')
                else:
                    text = escape(str(value))
                    cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
            xml_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

        max_row = max(len(rows), 1)
        max_col = max((len(row) for row in rows), default=1)
        dimension = f"A1:{self._cell_ref(max_row, max_col)}"
        return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <dimension ref="{dimension}"/>
  <sheetViews><sheetView workbookViewId="0"/></sheetViews>
  <sheetFormatPr defaultRowHeight="15"/>
  <sheetData>{''.join(xml_rows)}</sheetData>
</worksheet>'''

    def _workbook_xml(self) -> str:
        sheets_xml = []
        for idx, (name, _) in enumerate(self.sheets, start=1):
            sheets_xml.append(f'<sheet name="{escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>')
        return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>{''.join(sheets_xml)}</sheets>
</workbook>'''

    def _workbook_rels_xml(self) -> str:
        rels = []
        for idx in range(1, len(self.sheets) + 1):
            rels.append(f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>')
        rels.append(f'<Relationship Id="rId{len(self.sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
        return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{''.join(rels)}</Relationships>'''

    def _content_types_xml(self) -> str:
        overrides = [
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
        ]
        for idx in range(1, len(self.sheets) + 1):
            overrides.append(f'<Override PartName="/xl/worksheets/sheet{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
        return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  {''.join(overrides)}
</Types>'''

    @staticmethod
    def _root_rels_xml() -> str:
        return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''

    @staticmethod
    def _styles_xml() -> str:
        return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/></font></fonts>
  <fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''

    def save(self, path: str | Path) -> None:
        if not self.sheets:
            raise ValueError("Tidak ada sheet untuk disimpan.")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", self._content_types_xml())
            zf.writestr("_rels/.rels", self._root_rels_xml())
            zf.writestr("xl/workbook.xml", self._workbook_xml())
            zf.writestr("xl/_rels/workbook.xml.rels", self._workbook_rels_xml())
            zf.writestr("xl/styles.xml", self._styles_xml())
            for idx, (_, rows) in enumerate(self.sheets, start=1):
                zf.writestr(f"xl/worksheets/sheet{idx}.xml", self._sheet_xml(rows))


# =========================================================
# TKINTER UI
# =========================================================

class MiniLineChart(tk.Canvas):

    def __init__(self, master, title: str, x_values: list[float], y_values: list[float],
                 y_label: str, bubble_pressure: float, colors: dict[str, str],
                 width: int = 305, height: int = 190) -> None:
        super().__init__(master, width=width, height=height, bg=colors["chart_bg"], highlightthickness=0)
        self.title = title
        self.x_values = x_values
        self.y_values = y_values
        self.y_label = y_label
        self.bubble_pressure = bubble_pressure
        self.colors = colors
        self.width = width
        self.height = height
        self.draw_chart()

    def _scale(self, value: float, vmin: float, vmax: float, start: float, end: float) -> float:
        if abs(vmax - vmin) < 1e-12:
            return (start + end) / 2
        return start + (value - vmin) * (end - start) / (vmax - vmin)

    def draw_chart(self) -> None:
        self.delete("all")
        pad_l, pad_r, pad_t, pad_b = 48, 15, 34, 34
        plot_x1, plot_y1 = pad_l, pad_t
        plot_x2, plot_y2 = self.width - pad_r, self.height - pad_b

        self.create_text(self.width / 2, 16, text=self.title, fill=self.colors["text"], font=("Segoe UI", 9, "bold"))
        self.create_rectangle(plot_x1, plot_y1, plot_x2, plot_y2, outline=self.colors["axis"], fill=self.colors["plot_bg"])

        if not self.x_values or not self.y_values:
            self.create_text(self.width / 2, self.height / 2, text="No data", fill=self.colors["muted"])
            return

        xmin, xmax = min(self.x_values), max(self.x_values)
        ymin, ymax = min(self.y_values), max(self.y_values)
        ypad = (ymax - ymin) * 0.08 if abs(ymax - ymin) > 1e-12 else max(abs(ymax), 1) * 0.08
        ymin -= ypad
        ymax += ypad

        for i in range(1, 5):
            x = plot_x1 + i * (plot_x2 - plot_x1) / 5
            y = plot_y1 + i * (plot_y2 - plot_y1) / 5
            self.create_line(x, plot_y1, x, plot_y2, fill=self.colors["grid"])
            self.create_line(plot_x1, y, plot_x2, y, fill=self.colors["grid"])

        if xmin <= self.bubble_pressure <= xmax:
            bx = self._scale(self.bubble_pressure, xmin, xmax, plot_x1, plot_x2)
            self.create_line(bx, plot_y1, bx, plot_y2, fill=self.colors["bubble"], dash=(4, 3), width=1)

        points = []
        for x_val, y_val in zip(self.x_values, self.y_values):
            x = self._scale(x_val, xmin, xmax, plot_x1, plot_x2)
            y = self._scale(y_val, ymin, ymax, plot_y2, plot_y1)
            points.extend([x, y])

        if len(points) >= 4:
            self.create_line(*points, fill=self.colors["line"], width=2, smooth=True)
        for i in range(0, len(points), 2):
            self.create_oval(points[i] - 2, points[i + 1] - 2, points[i] + 2, points[i + 1] + 2,
                             fill=self.colors["line"], outline="")

        self.create_text(plot_x1, plot_y2 + 14, text=f"{xmin:.0f}", fill=self.colors["muted"], font=("Segoe UI", 7), anchor="w")
        self.create_text(plot_x2, plot_y2 + 14, text=f"{xmax:.0f}", fill=self.colors["muted"], font=("Segoe UI", 7), anchor="e")
        self.create_text(8, plot_y1, text=f"{ymax:.3g}", fill=self.colors["muted"], font=("Segoe UI", 7), anchor="w")
        self.create_text(8, plot_y2, text=f"{ymin:.3g}", fill=self.colors["muted"], font=("Segoe UI", 7), anchor="w")


class PVTApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PVT Calculator - Kelompok 3")
        self.geometry("1180x760")
        self.minsize(930, 620)

        self.colors = {
            "bg": "#0f172a",
            "panel": "#111827",
            "card": "#1e293b",
            "card_light": "#243449",
            "accent": "#2dd4bf",
            "accent_hover": "#5eead4",
            "text": "#e5e7eb",
            "muted": "#94a3b8",
            "input": "#f8fafc",
            "input_text": "#0f172a",
            "danger": "#f97373",
            "chart_bg": "#1e293b",
            "plot_bg": "#263244",
            "axis": "#475569",
            "grid": "#334155",
            "line": "#d8b45f",
            "bubble": "#fb7185",
        }

        self.last_rows: list[dict[str, float | str]] = []
        self.last_single: dict[str, float | str] | None = None
        self.vars: dict[str, tk.StringVar] = {}
        self.combo_vars: dict[str, tk.StringVar] = {}
        self.logo_image: tk.PhotoImage | None = None

        self.configure(bg=self.colors["bg"])
        self._setup_style()
        self._build_layout()

    def _setup_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview", background="#172033", fieldbackground="#172033",
                        foreground=self.colors["text"], rowheight=28, borderwidth=0, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background="#334155", foreground=self.colors["text"],
                        font=("Segoe UI", 9, "bold"), relief="flat")
        style.map("Treeview", background=[("selected", "#0f766e")])
        style.configure("TNotebook", background=self.colors["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=self.colors["card"], foreground=self.colors["muted"],
                        padding=(16, 8), font=("Segoe UI", 9, "bold"))
        style.map("TNotebook.Tab", background=[("selected", self.colors["card_light"])],
                  foreground=[("selected", self.colors["accent"])])
        style.configure("TCombobox", fieldbackground=self.colors["input"], background=self.colors["input"],
                        foreground=self.colors["input_text"], arrowcolor=self.colors["input_text"])

    def _load_logo(self) -> tk.PhotoImage | None:
        """Load logo langsung dari cloud URL."""
        logo_url = "https://www.image2url.com/r2/default/images/1781280403746-783d87d9-49e5-42c7-a53c-3215ad92ae48.png"
        target_size = 92
        
        try:
            # Mengambil data gambar dari URL dengan batas waktu 10 detik biar nggak nge-hang
            response = requests.get(logo_url, timeout=10)
            response.raise_for_status()
            
            # Konversi byte response jadi gambar yang bisa dibaca Tkinter
            img = Image.open(BytesIO(response.content)).convert("RGBA")
            img = img.resize((target_size, target_size), Image.LANCZOS)
            
            return ImageTk.PhotoImage(img)
            
        except Exception as e:
            # Kalau internet mati atau URL rusak, kalkulator tetap jalan tanpa logo
            print(f"Gagal memuat logo dari cloud: {e}")
            return None

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._build_input_panel()
        self._build_output_panel()

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=self.colors["bg"], padx=24, pady=18)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        tk.Label(header, text="PVT Calculator", bg=self.colors["bg"], fg=self.colors["text"],
                 font=("Segoe UI", 24, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(header, text="Coded by Kelompok 3 PVT Calculator TM2202: Fluida Reservoir Praktikum",
                 bg=self.colors["bg"], fg=self.colors["muted"], font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.logo_image = self._load_logo()
        if self.logo_image is not None:
            logo_card = tk.Frame(header, bg=self.colors["card"], padx=10, pady=8)
            logo_card.grid(row=0, column=1, sticky="e", padx=(18, 0))
            tk.Label(logo_card, image=self.logo_image, bg=self.colors["card"]).pack()

    def _build_input_panel(self) -> None:
        outer = tk.Frame(self, bg=self.colors["bg"], padx=20, pady=10)
        outer.grid(row=1, column=0, sticky="ns")
        canvas = tk.Canvas(outer, width=350, bg=self.colors["panel"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self.form_frame = tk.Frame(canvas, bg=self.colors["panel"], padx=18, pady=18)
        self.form_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.form_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="ns")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self._section_title("General Data")
        self._entry("pressure", "Tekanan saat ini (psia)", "2500")
        self._entry("initial_pressure", "Tekanan initial (psia)", "3500")
        self._entry("standard_pressure", "Tekanan standar (psia)", "14.7")
        self._entry("temperature_f", "Temperatur reservoir (°F)", "180")
        self._entry("gas_gravity", "Gas gravity", "0.75")

        self._section_title("Oil Data")
        self._entry("api", "API oil", "35")
        self._entry("bubble_pressure", "Bubble pressure / Pb (psia)", "2200")
        self._entry("separator_pressure", "Tekanan separator (psia)", "100")
        self._entry("separator_temperature_f", "Temperatur separator (°F)", "100")

        self._section_title("Gas & Brine Data")
        self._entry("co2_percent", "CO₂ (% mol)", "2")
        self._entry("h2s_percent", "H₂S (% mol)", "0.5")
        self._entry("n2_percent", "N₂ (% mol)", "1")
        self._entry("tds_percent", "TDS brine (%)", "3")

        self._section_title("Korelasi")
        self._combo("rs_corr", "Rs", {"Standing": 1, "Vasquez-Beggs": 2, "Glaso": 3}, "Standing")
        self._combo("bo_sat_corr", "Bo saturated", {"Standing": 1, "Vasquez-Beggs": 2, "Glaso": 3}, "Standing")
        self._combo("bo_unsat_corr", "Bo undersaturated", {"Vasquez-Beggs": 1, "Petrosky-Farshad": 2}, "Vasquez-Beggs")
        self._combo("z_corr", "Z-factor", {"Dranchuk-Abu-Kassem": 1, "Dranchuk-Purvis-Robinson": 2, "Hall-Yarborough": 3, "Beggs-Brill": 4}, "Dranchuk-Abu-Kassem")
        self._combo("tpc_ppc_corr", "Tpc/Ppc", {"Natural Gas - Standing": 1, "Sutton": 2, "Condensate - Standing": 3}, "Natural Gas - Standing")
        self._combo("critical_corr", "Critical correction", {"Wichert-Aziz": 1, "Carr-Kobayashi-Burrows": 2}, "Wichert-Aziz")
        self._combo("gas_vis_corr", "Gas viscosity", {"Carr-Kobayashi + Dempsey": 1, "Lee-Gonzalez-Eakin": 2}, "Lee-Gonzalez-Eakin")
        self._combo("brine_condition", "Kondisi brine", {"Gas saturated brine": 1, "Gas free brine": 2}, "Gas saturated brine")
        self._combo("brine_vis_corr", "Brine viscosity", {"Meehan": 1, "Beggs-Brill": 2}, "Meehan")

        self.calc_button = tk.Button(self.form_frame, text="Hitung PVT", command=self.calculate,
                                     bg=self.colors["accent"], fg="#042f2e", activebackground=self.colors["accent_hover"],
                                     activeforeground="#042f2e", relief="flat", cursor="hand2", font=("Segoe UI", 11, "bold"),
                                     padx=14, pady=12)
        self.calc_button.pack(fill="x", pady=(18, 8))
        self.calc_button.bind("<Enter>", lambda _: self.calc_button.configure(bg=self.colors["accent_hover"]))
        self.calc_button.bind("<Leave>", lambda _: self.calc_button.configure(bg=self.colors["accent"]))

        self.export_button = tk.Button(self.form_frame, text="Export ke Excel (.xlsx)", command=self.export_excel,
                                       bg=self.colors["card_light"], fg=self.colors["text"], activebackground="#334155",
                                       activeforeground=self.colors["text"], relief="flat", cursor="hand2",
                                       font=("Segoe UI", 10, "bold"), padx=14, pady=10)
        self.export_button.pack(fill="x", pady=(0, 8))

    def _section_title(self, text: str) -> None:
        tk.Label(self.form_frame, text=text, bg=self.colors["panel"], fg=self.colors["accent"],
                 font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x", pady=(14, 6))

    def _entry(self, key: str, label_text: str, default: str) -> None:
        wrapper = tk.Frame(self.form_frame, bg=self.colors["panel"])
        wrapper.pack(fill="x", pady=5)
        tk.Label(wrapper, text=label_text, bg=self.colors["panel"], fg=self.colors["muted"],
                 font=("Segoe UI", 9), anchor="w").pack(fill="x")
        var = tk.StringVar(value=default)
        entry = tk.Entry(wrapper, textvariable=var, bg=self.colors["input"], fg=self.colors["input_text"],
                         relief="flat", insertbackground=self.colors["input_text"], font=("Segoe UI", 10))
        entry.pack(fill="x", ipady=8, pady=(3, 0))
        self.vars[key] = var

    def _combo(self, key: str, label_text: str, options: dict[str, int], default_label: str) -> None:
        wrapper = tk.Frame(self.form_frame, bg=self.colors["panel"])
        wrapper.pack(fill="x", pady=5)
        tk.Label(wrapper, text=label_text, bg=self.colors["panel"], fg=self.colors["muted"],
                 font=("Segoe UI", 9), anchor="w").pack(fill="x")
        var = tk.StringVar(value=default_label)
        combo = ttk.Combobox(wrapper, textvariable=var, values=list(options.keys()), state="readonly", font=("Segoe UI", 10))
        combo.pack(fill="x", ipady=5, pady=(3, 0))
        self.combo_vars[key] = var
        setattr(self, f"{key}_options", options)

    def _build_output_panel(self) -> None:
        panel = tk.Frame(self, bg=self.colors["bg"], padx=10, pady=10)
        panel.grid(row=1, column=1, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)

        self.cards_frame = tk.Frame(panel, bg=self.colors["bg"])
        self.cards_frame.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        for i in range(4):
            self.cards_frame.grid_columnconfigure(i, weight=1)
        self.card_vars = {
            "condition": tk.StringVar(value="-"),
            "bo": tk.StringVar(value="-"),
            "rs": tk.StringVar(value="-"),
            "z": tk.StringVar(value="-"),
        }
        self._card(0, "Condition", self.card_vars["condition"])
        self._card(1, "Bo", self.card_vars["bo"])
        self._card(2, "Rs", self.card_vars["rs"])
        self._card(3, "Z Factor", self.card_vars["z"])

        self.summary = tk.Text(panel, height=10, bg=self.colors["card"], fg=self.colors["text"],
                               insertbackground=self.colors["text"], relief="flat", padx=14, pady=12,
                               font=("Consolas", 10))
        self.summary.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.summary.insert("1.0", "Klik 'Hitung PVT' untuk melihat hasil perhitungan.")
        self.summary.configure(state="disabled")

        self.notebook = ttk.Notebook(panel)
        self.notebook.grid(row=2, column=0, sticky="nsew")
        self.table_tab = tk.Frame(self.notebook, bg=self.colors["card"])
        self.graph_tab = tk.Frame(self.notebook, bg=self.colors["card"])
        self.notebook.add(self.table_tab, text="Tabel PVT")
        self.notebook.add(self.graph_tab, text="Grafik PVT")
        self._build_table_tab()
        self._build_graph_tab()

    def _card(self, col: int, title: str, value_var: tk.StringVar) -> None:
        card = tk.Frame(self.cards_frame, bg=self.colors["card"], padx=16, pady=12)
        card.grid(row=0, column=col, sticky="ew", padx=6)
        tk.Label(card, text=title, bg=self.colors["card"], fg=self.colors["muted"],
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(card, textvariable=value_var, bg=self.colors["card"], fg=self.colors["accent"],
                 font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(5, 0))

    def _build_table_tab(self) -> None:
        columns = ("Pressure", "Condition", "Bo", "Rs", "Oil Vis", "Co", "Z", "Bg", "Gas Den", "Gas Vis", "Bw", "Brine Vis")
        self.tree = ttk.Treeview(self.table_tab, columns=columns, show="headings")
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=105, anchor="center")
        y_scroll = ttk.Scrollbar(self.table_tab, orient="vertical", command=self.tree.yview)
        x_scroll = ttk.Scrollbar(self.table_tab, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.table_tab.grid_rowconfigure(0, weight=1)
        self.table_tab.grid_columnconfigure(0, weight=1)

    def _build_graph_tab(self) -> None:
        self.graph_canvas = tk.Canvas(self.graph_tab, bg=self.colors["card"], highlightthickness=0)
        self.graph_scroll_y = ttk.Scrollbar(self.graph_tab, orient="vertical", command=self.graph_canvas.yview)
        self.graph_inner = tk.Frame(self.graph_canvas, bg=self.colors["card"])
        self.graph_inner.bind("<Configure>", lambda e: self.graph_canvas.configure(scrollregion=self.graph_canvas.bbox("all")))
        self.graph_canvas.create_window((0, 0), window=self.graph_inner, anchor="nw")
        self.graph_canvas.configure(yscrollcommand=self.graph_scroll_y.set)
        self.graph_canvas.grid(row=0, column=0, sticky="nsew")
        self.graph_scroll_y.grid(row=0, column=1, sticky="ns")
        self.graph_tab.grid_rowconfigure(0, weight=1)
        self.graph_tab.grid_columnconfigure(0, weight=1)
        tk.Label(self.graph_inner, text="Grafik akan muncul setelah klik Hitung PVT.", bg=self.colors["card"],
                 fg=self.colors["muted"], font=("Segoe UI", 11)).grid(row=0, column=0, padx=20, pady=20)

    def _get_float(self, key: str, label: str) -> float:
        raw = self.vars[key].get().strip().replace(",", ".")
        try:
            return float(raw)
        except ValueError as exc:
            raise ValueError(f"{label} harus berupa angka.") from exc

    def _combo_value(self, key: str) -> int:
        options = getattr(self, f"{key}_options")
        return options[self.combo_vars[key].get()]

    def _read_input(self) -> PVTInput:
        data = PVTInput(
            pressure=self._get_float("pressure", "Tekanan saat ini"),
            initial_pressure=self._get_float("initial_pressure", "Tekanan initial"),
            standard_pressure=self._get_float("standard_pressure", "Tekanan standar"),
            temperature_f=self._get_float("temperature_f", "Temperatur reservoir"),
            gas_gravity=self._get_float("gas_gravity", "Gas gravity"),
            api=self._get_float("api", "API oil"),
            bubble_pressure=self._get_float("bubble_pressure", "Bubble pressure"),
            separator_pressure=self._get_float("separator_pressure", "Tekanan separator"),
            separator_temperature_f=self._get_float("separator_temperature_f", "Temperatur separator"),
            co2_percent=self._get_float("co2_percent", "CO2"),
            h2s_percent=self._get_float("h2s_percent", "H2S"),
            n2_percent=self._get_float("n2_percent", "N2"),
            tds_percent=self._get_float("tds_percent", "TDS"),
            brine_condition=self._combo_value("brine_condition"),
            rs_corr=self._combo_value("rs_corr"),
            bo_sat_corr=self._combo_value("bo_sat_corr"),
            bo_unsat_corr=self._combo_value("bo_unsat_corr"),
            z_corr=self._combo_value("z_corr"),
            tpc_ppc_corr=self._combo_value("tpc_ppc_corr"),
            critical_corr=self._combo_value("critical_corr"),
            gas_vis_corr=self._combo_value("gas_vis_corr"),
            brine_vis_corr=self._combo_value("brine_vis_corr"),
        )
        for key, label in [
            ("pressure", "Tekanan saat ini"), ("initial_pressure", "Tekanan initial"),
            ("standard_pressure", "Tekanan standar"), ("temperature_r", "Temperatur reservoir"),
            ("gas_gravity", "Gas gravity"), ("api", "API oil"),
            ("bubble_pressure", "Bubble pressure"), ("separator_pressure", "Tekanan separator"),
        ]:
            positive(getattr(data, key), label)
        return data

    def calculate(self) -> None:
        try:
            data = self._read_input()
            single = calculate_at_pressure(data, data.pressure)
            rows = calculate_table(data)
        except Exception as exc:
            messagebox.showerror("Input/perhitungan belum valid", str(exc))
            return

        self.last_single = single
        self.last_rows = rows
        self._update_cards(single)
        self._update_summary(single)
        self._update_table(rows)
        self._update_graphs(rows, data.bubble_pressure)
        self.notebook.select(self.graph_tab)

    def _update_cards(self, result: dict[str, float | str]) -> None:
        self.card_vars["condition"].set(str(result["Condition"]))
        self.card_vars["bo"].set(f"{result['Bo (RB/STB)']:.4f}")
        self.card_vars["rs"].set(f"{result['Rs (MSCF/STB)']:.4f}")
        self.card_vars["z"].set(f"{result['Z Factor']:.4f}")

    def _update_summary(self, result: dict[str, float | str]) -> None:
        lines = [
            "OIL PROPERTIES",
            f"  Condition              : {result['Condition']}",
            f"  Bo                     : {result['Bo (RB/STB)']:.4f} RB/STB",
            f"  Rs                     : {result['Rs (MSCF/STB)']:.4f} MSCF/STB",
            f"  Oil Density            : {result['Oil Density (lbm/ft3)']:.4f} lbm/ft³",
            f"  Oil Viscosity          : {result['Oil Viscosity (cp)']:.4f} cp",
            f"  Co                     : {result['Co (1/MMpsi)']:.4f} 1/MMpsi",
            "",
            "GAS PROPERTIES",
            f"  Tpr / Ppr              : {result['Tpr']:.4f} / {result['Ppr']:.4f}",
            f"  Z Factor               : {result['Z Factor']:.4f}",
            f"  Bg                     : {result['Bg (RB/MSCF)']:.4f} RB/MSCF",
            f"  Gas Density            : {result['Gas Density (lbm/ft3)']:.4f} lbm/ft³",
            f"  Gas Viscosity          : {result['Gas Viscosity (cp)']:.4f} cp",
            f"  Cg                     : {result['Cg (1/MMpsi)']:.4f} 1/MMpsi",
            "",
            "BRINE PROPERTIES",
            f"  Bw                     : {result['Bw (RB/STB)']:.4f} RB/STB",
            f"  Rsw                    : {result['Rsw (SCF/STB)']:.4f} SCF/STB",
            f"  Brine Density          : {result['Brine Density (lbm/ft3)']:.4f} lbm/ft³",
            f"  Brine Viscosity        : {result['Brine Viscosity (cp)']:.4f} cp",
            f"  Cw                     : {result['Cw (1/MMpsi)']:.4f} 1/MMpsi",
        ]
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", "\n".join(lines))
        self.summary.configure(state="disabled")

    def _update_table(self, rows: list[dict[str, float | str]]) -> None:
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert("", "end", values=(
                f"{row['Pressure (psia)']:.3f}", row["Condition"],
                f"{row['Bo (RB/STB)']:.4f}", f"{row['Rs (MSCF/STB)']:.4f}",
                f"{row['Oil Viscosity (cp)']:.4f}", f"{row['Co (1/MMpsi)']:.4f}",
                f"{row['Z Factor']:.4f}", f"{row['Bg (RB/MSCF)']:.4f}",
                f"{row['Gas Density (lbm/ft3)']:.4f}", f"{row['Gas Viscosity (cp)']:.4f}",
                f"{row['Bw (RB/STB)']:.4f}", f"{row['Brine Viscosity (cp)']:.4f}",
            ))

    def _graph_specs(self) -> list[tuple[str, str, str]]:
        return [
            ("Bo", "Bo (RB/STB)", "RB/STB"),
            ("Rs", "Rs (MSCF/STB)", "MSCF/STB"),
            ("Bg", "Bg (RB/MSCF)", "RB/MSCF"),
            ("Bw", "Bw (RB/STB)", "RB/STB"),
            ("Rsw", "Rsw (SCF/STB)", "SCF/STB"),
            ("Z", "Z Factor", "dimensionless"),
            ("Oil Density", "Oil Density (lbm/ft3)", "lbm/ft³"),
            ("Gas Density", "Gas Density (lbm/ft3)", "lbm/ft³"),
            ("Brine Density", "Brine Density (lbm/ft3)", "lbm/ft³"),
            ("Oil Viscosity", "Oil Viscosity (cp)", "cp"),
            ("Gas Viscosity", "Gas Viscosity (cp)", "cp"),
            ("Brine Viscosity", "Brine Viscosity (cp)", "cp"),
            ("Co", "Co (1/MMpsi)", "1/MMpsi"),
            ("Cg", "Cg (1/MMpsi)", "1/MMpsi"),
            ("Cw", "Cw (1/MMpsi)", "1/MMpsi"),
        ]

    def _rows_for_graph(self, rows: list[dict[str, float | str]]) -> list[dict[str, float | str]]:
        return sorted(rows, key=lambda row: float(row["Pressure (psia)"]))

    def _update_graphs(self, rows: list[dict[str, float | str]], bubble_pressure: float) -> None:
        for widget in self.graph_inner.winfo_children():
            widget.destroy()
        graph_rows = self._rows_for_graph(rows)
        pressures = [float(row["Pressure (psia)"]) for row in graph_rows]
        specs = self._graph_specs()
        for idx, (title, key, ylabel) in enumerate(specs):
            frame = tk.Frame(self.graph_inner, bg=self.colors["card"], padx=8, pady=8)
            frame.grid(row=idx // 3, column=idx % 3, padx=4, pady=4, sticky="nsew")
            values = [float(row[key]) for row in graph_rows]
            chart = MiniLineChart(frame, title, pressures, values, ylabel, bubble_pressure, self.colors)
            chart.pack()
        for col in range(3):
            self.graph_inner.grid_columnconfigure(col, weight=1)

    def export_excel(self) -> None:
        if not self.last_rows:
            messagebox.showinfo("Belum ada data", "Klik Hitung PVT dulu sebelum export.")
            return
        path = filedialog.asksaveasfilename(title="Simpan hasil PVT ke Excel", defaultextension=".xlsx",
                                            filetypes=[("Excel Workbook", "*.xlsx")],
                                            initialfile="hasil_pvt_calculator.xlsx")
        if not path:
            return
        try:
            self._write_excel(path)
        except Exception as exc:
            messagebox.showerror("Export gagal", f"File Excel gagal dibuat:\n{exc}")
            return
        messagebox.showinfo("Berhasil", f"File Excel berhasil disimpan:\n{path}")

    def _write_excel(self, path: str) -> None:
        writer = SimpleXLSXWriter()
        summary_rows = [["PVT Calculator Result Summary"], []]
        if self.last_single:
            summary_rows += [["Property", "Value", "Unit", "Note"]]
            summary_items = [
                ("Pressure", self.last_single["Pressure (psia)"], "psia"),
                ("Condition", self.last_single["Condition"], ""),
                ("Bo", self.last_single["Bo (RB/STB)"], "RB/STB"),
                ("Rs", self.last_single["Rs (MSCF/STB)"], "MSCF/STB"),
                ("Z Factor", self.last_single["Z Factor"], ""),
                ("Bg", self.last_single["Bg (RB/MSCF)"], "RB/MSCF"),
                ("Oil Viscosity", self.last_single["Oil Viscosity (cp)"], "cp"),
                ("Gas Viscosity", self.last_single["Gas Viscosity (cp)"], "cp"),
                ("Brine Viscosity", self.last_single["Brine Viscosity (cp)"], "cp"),
            ]
            for name, value, unit in summary_items:
                summary_rows.append([name, value, unit, "Single-point result"])

        table_rows = self._rows_for_graph(self.last_rows)
        headers = list(table_rows[0].keys())
        pvt_rows: list[list[object]] = [headers]
        for row in table_rows:
            pvt_rows.append([row[h] for h in headers])

        graph_headers = ["Pressure (psia)"] + [key for _, key, _ in self._graph_specs()]
        graph_rows: list[list[object]] = [graph_headers]
        for row in table_rows:
            graph_rows.append([row["Pressure (psia)"]] + [row[key] for _, key, _ in self._graph_specs()])

        writer.add_sheet("Summary", summary_rows)
        writer.add_sheet("PVT Table", pvt_rows)
        writer.add_sheet("Graph Data", graph_rows)
        writer.save(path)


if __name__ == "__main__":
    app = PVTApp()
    app.mainloop()
