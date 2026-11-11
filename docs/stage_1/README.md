# Stage 1: Measurement checks

Date: November 9, 2026.

## Temperatures with TEC output off

| Channel | Temperature (°C) |
|---|---:|
| TEC (A0) | 23.22 |
| T1 (A1) | 23.51 |
| T2 (A2) | 23.36 |
| T3 (A3) | 23.53 |
| T4 (A4) | 23.89 |
| T5 (A5) | 23.59 |

Estimated room temperature: **23.52 °C**, the arithmetic average of these six readings (23.5167 °C before rounding). The user entered 23.52 °C in the GUI. This is an apparatus-based estimate, not an independent air-temperature measurement; it assumes the unpowered apparatus has equilibrated with the room.

The full sensor spread at this observation was **0.67 °C**. This is a single baseline observation, not a replacement for the common-bath check across several temperatures.

## Checks reported complete

The user reported that the updated Arduino sketch and Python GUI work and that the following checks work:

- All six temperatures update live.
- Warming a sensor changes its assigned channel.
- Displayed values agree with the saved record.
- STOP leaves PWM at zero.
- Restart begins at zero PWM and creates a new CSV.

These are user-reported physical checks. No screenshot or exact new-format CSV filename was identified for the completed checks in this note.

## Recorded sensor positions

T1 defines x = 0. T1–T5 are at **0, 2.75, 5.00, 8.00, and 10.75 cm** along the rod. TEC/A0 is separate from the rod coordinates. See the [sensor map](../sensor_map/README.md) for measurement details and uncertainties.
