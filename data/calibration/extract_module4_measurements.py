"""Recover original plateau endpoints using Module 4's documented selection.

Raw source is read-only. The final 10 samples of the longest uninterrupted
block are plateau estimates, not independently verified steady-state values.
"""
import csv
import hashlib
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent
SOURCE=HERE.parents[2]/'Kadener-Wijnaendts-Module4/temperature_measurements.csv'

def main():
    raw=SOURCE.read_bytes()
    rows=list(csv.DictReader(raw.decode().splitlines()))
    spans={};block=[];previous=None;previous_time=None
    for row in rows:
        key=(int(row['heat_cool']),int(row['pwm']))
        t=float(row['time_s'])
        if key!=previous or previous_time is not None and t<previous_time:
            if block:spans.setdefault(previous,[]).append(block)
            block=[]
        block.append(row);previous=key;previous_time=t
    if block:spans.setdefault(previous,[]).append(block)
    records=[];slopes={}
    for branch,mode,pwms,sign in [('heating',1,[0,8,15,23,30],1),('cooling',0,[0,33,65,98,130],-1)]:
        points=[]
        for pwm in pwms:
            block=max(spans[(mode,pwm)],key=len)
            tail=block[-10:]
            temp=sum(float(r['temperature_C']) for r in tail)/10
            points.append((sign*pwm,temp))
            records.append([branch,sign*pwm,temp,len(block),tail[0]['time_s'],tail[-1]['time_s'],10])
        slopes[branch]=float(np.polyfit([p[0] for p in points],[p[1] for p in points],1)[0])
    with (HERE/'module4_original_plateaus.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['branch','signed_pwm','temperature_C','block_samples','tail_start_s','tail_end_s','averaged_samples']);w.writerows(records)
    # A single inverse lookup needs one zero-PWM point. Use heating zero
    # explicitly; retain both original zero observations in the source table.
    selected=[r for r in records if not(r[0]=='cooling' and r[1]==0)]
    selected.sort(key=lambda r:r[2])
    assert all(b[1]>a[1] and b[2]>a[2] for a,b in zip(selected,selected[1:]))
    with (HERE/'module4_original_lookup.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['temperature_C','signed_pwm']);w.writerows((r[2],r[1]) for r in selected)
    (HERE/'README.md').write_text(f'''# Recovered Module 4 heating/cooling measurements

Source: `{SOURCE}`. SHA-256: `{hashlib.sha256(raw).hexdigest()}`.
Selection follows Module 4 `Part 4/plot_temperature_vs_pwm.py`: longest uninterrupted block for each selected direction/PWM, then mean of final 10 samples. These endpoints are inherited plateau estimates, not a new stationarity assessment. Arduino time may reset between blocks.

[Original points and sample windows](module4_original_plateaus.csv) retain both zero-PWM observations. [GUI-compatible original lookup](module4_original_lookup.csv) uses heating zero only to create one monotonic inverse; the cooling zero is preserved in the original table. No baseline shift or calibration correction is applied here. The resulting join is a historical approximation, not a newly validated continuous curve.

| Quantity | Recovered value |
|---|---:|
| Heating slope | {slopes['heating']:.6f} °C/PWM |
| Cooling slope | {slopes['cooling']:.6f} °C/PWM |
| Heating/cooling ratio | {slopes['heating']/slopes['cooling']:.6f} |
| Heating zero-PWM temperature | 35.600 °C |
| Cooling zero-PWM temperature | 35.290 °C |
| Original lookup range | {selected[0][2]:.3f}–{selected[-1][2]:.3f} °C |

The old Module 4 plotting script additionally shifted heating by −13.600 °C and cooling by −13.290 °C, placing both zero-PWM temperatures at 22 °C. That preserves fitted slopes but changes absolute temperatures; those shifted points must not be described as original raw temperatures. Its corrected heating maximum is 36.850 °C.

**Not ready for the specified 24–56 °C open-loop trial:** the original heating data stop at 50.450 °C. A 30–50 °C request is within the historical raw range, but validity for the present rod loading, room temperature, heat sink, and supply is unverified. The high zero-PWM values must be reconciled with the present measured ambient; they are not automatically today's room temperature. Do not extrapolate or silently shift this curve to make the required waveform fit. Confirm or extend the measured calibration on the current apparatus, or obtain instructor guidance on a restricted waveform.

Regenerate from this repository: `python3 data/calibration/extract_module4_measurements.py`.
''')
    assert SOURCE.read_bytes()==raw
    print('Recovered slopes:',slopes,'ratio',slopes['heating']/slopes['cooling'])

if __name__=='__main__':main()
