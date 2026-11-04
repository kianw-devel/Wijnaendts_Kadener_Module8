# Recovered Module 4 heating/cooling measurements

Source: `/Users/benjaminnakash-kadener/Documents/GitHub/Kadener-Wijnaendts-Module4/temperature_measurements.csv`. SHA-256: `b20ec8aafe045fe5f9569a7981813505b29682e61d9de147a4291a698bf738cd`.
Selection follows Module 4 `Part 4/plot_temperature_vs_pwm.py`: longest uninterrupted block for each selected direction/PWM, then mean of final 10 samples. These endpoints are inherited plateau estimates, not a new stationarity assessment. Arduino time may reset between blocks.

[Original points and sample windows](module4_original_plateaus.csv) retain both zero-PWM observations. [GUI-compatible original lookup](module4_original_lookup.csv) uses heating zero only to create one monotonic inverse; the cooling zero is preserved in the original table. No baseline shift or calibration correction is applied here. The resulting join is a historical approximation, not a newly validated continuous curve.

| Quantity | Recovered value |
|---|---:|
| Heating slope | 0.487243 °C/PWM |
| Cooling slope | 0.195668 °C/PWM |
| Heating/cooling ratio | 2.490160 |
| Heating zero-PWM temperature | 35.600 °C |
| Cooling zero-PWM temperature | 35.290 °C |
| Original lookup range | 9.746–50.450 °C |

The old Module 4 plotting script additionally shifted heating by −13.600 °C and cooling by −13.290 °C, placing both zero-PWM temperatures at 22 °C. That preserves fitted slopes but changes absolute temperatures; those shifted points must not be described as original raw temperatures. Its corrected heating maximum is 36.850 °C.

**Not ready for the specified 24–56 °C open-loop trial:** the original heating data stop at 50.450 °C. A 30–50 °C request is within the historical raw range, but validity for the present rod loading, room temperature, heat sink, and supply is unverified. The high zero-PWM values must be reconciled with the present measured ambient; they are not automatically today's room temperature. Do not extrapolate or silently shift this curve to make the required waveform fit. Confirm or extend the measured calibration on the current apparatus, or obtain instructor guidance on a restricted waveform.

Regenerate from this repository: `python3 data/calibration/extract_module4_measurements.py`.
