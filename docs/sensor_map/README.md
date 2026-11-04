# Module 8: Oscillatory sensor map

Positive x runs from the first rod hole toward the tip. T1 defines x = 0. The user's label “T0” is interpreted as TEC/A0; it is separate from the rod coordinates and remains on the TEC base. Arduino assignments are unchanged.

| Sensor | Arduino pin | Position from T1 (cm) | Position from T1 (m) | Placement |
|---|---|---:|---:|---|
| TEC (“T0”) | A0 | Not a rod coordinate | Not applicable | Same TEC-base location |
| T1 | A1 | 0 | 0 | Same first hole; analysis origin |
| T2 | A2 | 2.75 | 0.0275 | Same as Module 7 |
| T3 | A3 | 5.00 | 0.0500 | Same as Module 7 |
| T4 | A4 | 8.00 | 0.0800 | 3.00 cm farther along the rod than T3 |
| T5 | A5 | 10.75 | 0.1075 | 2.75 cm farther along the rod than T4 |

T1–T3 coordinates are carried from the recorded Module 7 map. T4 = 5.00 + 3.00 = 8.00 cm; T5 = 8.00 + 2.75 = 10.75 cm. These are ruler-measured positions, not the nominal 2.54 cm starting layout. Preserve this map separately from Module 7's full-length map; T5 is no longer at the tip.

## Geometry and uncertainties

The unchanged Module 7 geometry is d = 1.2700 cm (0.012700 m) and analysis length L = 20.50 cm (0.2050 m), measured from T1 to the tip. Caliper diameter bound was provisionally ±0.0025 cm; confirm whether its 0.025 mm specification is accuracy or resolution.

The earlier ruler had 1 mm divisions, with a conservative two-endpoint distance bound of ±0.10 cm. If the same ruler and endpoint visibility apply here, use these provisional worst-case bounds:

- T2 and T3 relative positions: ±0.10 cm.
- Measured T3→T4 and T4→T5 separations: ±0.10 cm each.
- T4 position computed from T3 plus one separation: ±0.20 cm.
- T5 position computed from T3 plus two separations: ±0.30 cm.

These are added bounds, not independent Gaussian standard deviations. Shared endpoints make the positions correlated. Measuring T4 and T5 directly from T1 would avoid accumulating bounds from the chained distances. The smallest division of the ruler used for the new separations has not been reconfirmed. Increase bounds if sensing centers are hard to locate.

## Still to preserve

A labeled photo or diagram identifying TEC, T1–T5, A0–A5, positive x, the tip, overall physical rod length, diameter, and measured positions. Record which acquisition runs use this map; the setup's actual position uncertainties must be confirmed before final fits.
