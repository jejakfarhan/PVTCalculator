# PVT Calculator

**A Python-based black-oil PVT calculation and visualization tool**

PVT Calculator is a desktop application developed as a group final project for **TM2202 – Fluida Reservoir Praktikum** at Institut Teknologi Bandung. The application calculates selected pressure-volume-temperature (PVT) properties of reservoir oil, gas, and formation water (brine) using empirical correlations. Results are presented as single-pressure values, a pressure-dependent PVT table, desktop charts, and an Excel workbook export.

> **Project context:** This is an educational engineering calculator. Its outputs should be checked against laboratory PVT data, field data, or a trusted reference simulator before being used for engineering decisions.

## Project Information

| Item | Details |
|---|---|
| Project | PVT Calculator |
| Course | TM2202 – Fluida Reservoir Praktikum |
| Institution | Institut Teknologi Bandung |
| Project type | Group final project |
| Submission date in report | 12 June 2026 |
| Language | Python |
| Interface | Tkinter desktop GUI |

### Team

- Stela Shalsabila Donal
- Farhan Nugraha
- Andhara Putri Nathania

## Objectives

The project was developed to:

1. Build an interactive Python application for calculating and displaying reservoir-fluid properties.
2. Estimate oil, gas, and brine properties over a range of reservoir pressures.
3. Visualize how selected fluid properties change with pressure.
4. Explore how the choice of empirical correlation affects calculated PVT results.

## Features

### 1. Reservoir and fluid input

The GUI accepts input parameters grouped into the following sections:

- **General data:** current pressure, initial reservoir pressure, standard pressure, reservoir temperature, and gas specific gravity.
- **Oil data:** API gravity, bubble-point pressure, separator pressure, and separator temperature.
- **Gas and brine data:** CO₂, H₂S, and N₂ composition, plus brine total dissolved solids (TDS).
- **Correlation settings:** selectable correlations for selected oil, gas, critical-property, and brine calculations.

### 2. Oil, gas, and brine calculations

The calculation engine estimates properties including:

| Fluid system | Calculated properties |
|---|---|
| Oil | Solution gas-oil ratio (Rs), oil formation volume factor (Bo), oil density, dead/live oil viscosity, and oil compressibility (Co) |
| Gas | Gas deviation factor (Z), gas formation volume factor (Bg), gas expansion factor (Eg), gas density, gas viscosity, gas compressibility (Cg), and water content in gas |
| Brine | Brine formation volume factor (Bw), solution gas-water ratio (Rsw), brine density, brine viscosity, and water compressibility (Cw) |

The application also identifies the fluid condition relative to bubble-point pressure as **Saturated**, **Undersaturated**, or **At Bubble Point**.

### 3. Correlation options

The current GUI exposes the following correlation choices:

| Calculation | Available options in the GUI |
|---|---|
| Solution GOR (Rs) | Standing; Vasquez-Beggs; Glaso |
| Saturated oil FVF (Bo) | Standing; Vasquez-Beggs; Glaso |
| Undersaturated oil FVF (Bo) | Vasquez-Beggs; Petrosky-Farshad |
| Gas pseudo-critical properties (Tpc/Ppc) | Natural Gas – Standing; Sutton; Condensate – Standing |
| Critical-property correction | Wichert-Aziz; Carr-Kobayashi-Burrows |
| Gas Z-factor | Dranchuk-Abu-Kassem; Dranchuk-Purvis-Robinson; Hall-Yarborough; Beggs-Brill |
| Gas viscosity | Carr-Kobayashi + Dempsey; Lee-Gonzalez-Eakin |
| Brine condition | Gas-saturated brine; gas-free brine |
| Brine viscosity | Meehan; Beggs-Brill |

Some oil-viscosity correlation branches are implemented internally but are not exposed as GUI selectors in the current version. The primary calculation path currently uses the Beggs-Robinson options for dead/live oil viscosity.

### 4. Single-point results and pressure table

After the user selects **Hitung PVT**, the application calculates properties at the current reservoir pressure and builds a pressure table containing **31 pressure points**. The table spans the configured standard pressure and initial reservoir pressure, with additional points arranged around the bubble point.

The interface presents key summary cards for fluid condition, Bo, Rs, and Z-factor, alongside a detailed summary and a tabular output view.

### 5. PVT charts

The application generates 15 pressure-dependent charts:

- Bo and Rs
- Bg, Bw, and Rsw
- Z-factor
- Oil, gas, and brine density
- Oil, gas, and brine viscosity
- Co, Cg, and Cw

The bubble-point pressure is marked on the charts to help identify changes in fluid behavior across saturated and undersaturated conditions. Charts are drawn with Tkinter Canvas rather than an external plotting library.

### 6. Excel export

The application can export the calculated results to an `.xlsx` workbook with three worksheets:

1. **Summary** – selected single-point property results.
2. **PVT Table** – calculated properties across the pressure range.
3. **Graph Data** – pressure values and the series used to construct the charts.

The workbook is generated by a lightweight XLSX writer implemented in the project; an external Excel-writing package is not required.

## Technology Stack

- **Python** – calculation logic and application workflow.
- **Tkinter / ttk** – desktop interface, input forms, tables, and chart canvas.
- **`math`** – mathematical functions used by the correlations.
- **Pillow** – image loading and resizing for the application logo.
- **Requests** – retrieving the logo image from its configured URL.
- **`zipfile` and XML utilities** – generating the Excel `.xlsx` workbook.

## Getting Started

### Requirements

- Python 3.10 or newer is recommended.
- Tkinter (usually included with standard Python installations; on some Linux distributions it must be installed separately).
- Pillow
- Requests

Install the external Python packages:

```bash
python -m pip install Pillow requests
```

### Run the application

From the repository directory, run:

```bash
python "PVT Calculator.py"
```

The application opens a desktop window. Enter the reservoir and fluid data, select the desired correlations, and click **Hitung PVT**. Use the **Tabel PVT** and **Grafik PVT** tabs to inspect the outputs. Click **Export ke Excel (.xlsx)** to save the workbook.

> The application attempts to retrieve its logo from a remote URL. If the image cannot be retrieved, the program is designed to continue without displaying the logo. An internet connection is therefore only needed for that optional image retrieval.

## Demonstration Case from the Project Report

The report documents a sample case with the following inputs:

| Parameter | Value |
|---|---:|
| Current pressure | 2,500 psia |
| Initial reservoir pressure | 3,500 psia |
| Standard pressure | 14.7 psia |
| Reservoir temperature | 180 °F |
| Gas gravity | 0.75 |
| Oil API gravity | 35° API |
| Bubble-point pressure | 2,200 psia |
| Separator pressure | 100 psia |
| Separator temperature | 100 °F |
| CO₂ / H₂S / N₂ | 2 / 0.5 / 1 mol% |
| Brine TDS | 3% |

For this input, the application identifies the fluid as **Undersaturated**, because the current pressure is above the bubble-point pressure. The report records approximately Bo = 1.2903 RB/STB, Rs = 0.5244 MSCF/STB, Z = 0.8234, and Bg = 1.0613 RB/MSCF. Re-running the current source code with the same demo inputs reproduces these values to the displayed precision.

## Important Limitations and Validation Notes

- **Oil viscosity above bubble point needs further validation.** The project report identifies a sharp, physically questionable drop in calculated oil viscosity immediately above the bubble point (approximately 0.006 cp in the demonstration case). The undersaturated oil-viscosity branch should be reviewed for its formula, unit handling, and transition at Pb before relying on those values.
- **Correlation applicability ranges are not comprehensively enforced.** Positive-value checks exist for selected inputs, but the current version does not provide complete automatic warnings for every correlation's recommended range.
- **Empirical correlations are estimates.** The results should be compared with laboratory PVT measurements, field data, or a trusted commercial/reference simulator for the fluid system being studied.
- **Correlation comparison is limited by the current interface.** The application lets users choose among several correlations, but it does not yet plot multiple correlation results for the same property side by side.
- **The chart view is for visualization.** The built-in charts are simple Tkinter Canvas plots intended to inspect trends, not a replacement for a dedicated scientific plotting or reporting workflow.

## Possible Future Improvements

- Add validity-range checks and warnings for every selectable correlation.
- Validate calculated properties against laboratory PVT data or a reference simulator.
- Add side-by-side comparison plots for multiple correlations.
- Review and test oil-viscosity behavior around and above bubble-point pressure.
- Improve input validation, unit guidance, and in-application explanations for each parameter.
- Add automated tests for correlation functions and representative fluid cases.

## References

The following references are listed in the accompanying project report and inform the PVT theory and correlations discussed there:

- Ahmed, T. (2006). *Reservoir Engineering Handbook* (3rd ed.). Gulf Professional Publishing.
- Al-Marhoun, M. A. (1988). PVT correlations for Middle East crude oils. *Journal of Petroleum Technology, 40*(05), 650–666.
- Beggs, H. D., & Robinson, J. R. (1975). Estimating the viscosity of crude oil systems. *Journal of Petroleum Technology, 27*(09), 1140–1141.
- Lee, A. L., Gonzalez, M. H., & Eakin, B. E. (1966). The viscosity of natural gases. *Journal of Petroleum Technology, 18*(08), 997–1000.
- McCain, W. D. (1990). *The Properties of Petroleum Fluids* (2nd ed.). PennWell Books.
- Standing, M. B. (1977). *Volumetric and Phase Behavior of Oil Field Hydrocarbon Systems*. SPE.
- Syihab, Z. (2026). *Modul 10A: Sifat-Sifat Black Oil: Data Lapangan & Korelasi*. TM2108 Fluida Reservoir, ITB.
- Vasquez, M., & Beggs, H. D. (1980). Correlations for fluid physical property prediction. *Journal of Petroleum Technology, 32*(06), 968–970.

---

**Disclaimer:** This application was created for academic learning and demonstration. It is not certified engineering software and should not be used as the sole basis for operational, design, or investment decisions.
