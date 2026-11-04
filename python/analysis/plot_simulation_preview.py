"""Plot approximate screenshot observations; these are not exported simulation data."""
import csv
import os
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR', '/tmp/module8-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'docs/simulation_preview'


def main():
    with (OUT / 'visual_estimates.csv').open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    x = np.array([float(r['x_m']) for r in rows])
    excess = np.array([float(r['estimated_mean_C']) - float(r['simulator_ambient_C']) for r in rows])
    amplitude = np.array([float(r['estimated_amplitude_C']) for r in rows])
    if np.any(excess <= 0) or np.any(amplitude <= 0):
        raise ValueError('Log plots require positive excess temperatures and amplitudes.')
    results = []
    for values, name, ylabel, title in (
        (excess, 'log_mean_excess', 'ln[(mean temperature − 22 °C) / (1 °C)]', 'Mean excess temperature'),
        (amplitude, 'log_amplitude', 'ln[oscillation amplitude / (1 °C)]', 'Oscillation amplitude'),
    ):
        log_y = np.log(values)
        # Descriptive all-point slope only: no precision/uncertainty is inferred
        # because the input values were visually estimated from a screenshot.
        slope, intercept = np.polyfit(x, log_y, 1)
        predicted = intercept + slope * x
        fig, ax = plt.subplots(figsize=(8, 5), constrained_layout=True)
        ax.scatter(x, log_y, color='#0072B2', label='Approximate screenshot estimates', zorder=3)
        ax.plot(x, predicted, '--', color='#d95f02', label=f'Descriptive line: slope {slope:.2f} m⁻¹')
        for row, xi, yi in zip(rows, x, log_y):
            ax.annotate(row['simulated_sensor'], (xi, yi), xytext=(5, 6), textcoords='offset points')
        ax.set(xlabel='Simulated rod position x (m)', ylabel=ylabel,
               title=f'{title}\nSimulation-only preview; approximate visual estimates')
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8)
        fig.savefig(OUT / f'{name}.png', dpi=180)
        plt.close(fig)
        results.append((name, slope, intercept, log_y, log_y - predicted))
    with (OUT / 'log_values.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['simulated_sensor', 'x_m', 'log_mean_excess', 'log_amplitude',
                         'mean_log_line_residual', 'amplitude_log_line_residual'])
        for i, row in enumerate(rows):
            writer.writerow([row['simulated_sensor'], x[i], results[0][3][i], results[1][3][i],
                             results[0][4][i], results[1][4][i]])
    text = f'''## Simulation log plots

![Log mean excess temperature](log_mean_excess.png)

![Log oscillation amplitude](log_amplitude.png)

Simulator ambient is 22 °C. The separately reported physical ambient of approximately 26 °C is not used here. Both logarithms are dimensionless, with temperatures normalized by 1 °C.

All-point descriptive slopes are **{results[0][1]:.2f} m⁻¹** for mean excess and **{results[1][1]:.2f} m⁻¹** for amplitude. Thus the oscillation amplitude falls more steeply than the mean excess temperature. These are descriptive lines through rough visual estimates, not precise fitted transport parameters; no statistical confidence interval is justified by these inputs.

The mean-excess points flatten toward the tip relative to the all-point line, consistent qualitatively with a finite-end effect. The amplitude points are roughly linear on the log plot, but the tiny T4/T5 amplitudes are particularly uncertain. Neither observation validates a boundary condition or semi-infinite approximation quantitatively. Phase delays still require separate estimates or exported data.

[Calculated log values and descriptive residuals](log_values.csv). Regenerate with `python3 python/analysis/plot_simulation_preview.py`.
'''
    note = OUT / 'README.md'
    current = note.read_text()
    marker = '## Simulation log plots'
    if marker in current:
        current = current[:current.index(marker)]
    note.write_text(current.rstrip() + '\n\n' + text)
    print(f'Mean slope: {results[0][1]:.4f} m^-1; amplitude slope: {results[1][1]:.4f} m^-1')
    print('Created two plots and calculated log-value CSV; preview notes updated.')


if __name__ == '__main__':
    main()
