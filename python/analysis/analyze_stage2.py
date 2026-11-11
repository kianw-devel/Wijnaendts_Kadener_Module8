"""Stage 2 raw-PWM analysis; never modifies acquisition files.
Run from the repository root: python3 python/analysis/analyze_stage2.py
"""
from pathlib import Path
import csv, json, hashlib, math, os, argparse
os.environ.setdefault('MPLCONFIGDIR','/tmp/module8-matplotlib')
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy.integrate import solve_ivp

ROOT=Path(__file__).resolve().parents[2]
RAW=ROOT/'python/run_data/run_20261109_111119_352877_manual_kp1_ki0p08.csv'
OUT=ROOT/'docs/stage_2';FIG=OUT/'figures';DATA=OUT/'analysis_output'
for p in [FIG,DATA]:p.mkdir(parents=True,exist_ok=True)
AMBIENT=23.52 # user-approved six-sensor estimate, not independently measured air
CHI=.23;TAU=80.;UB=50.
CHI_H=.4872434257285;CHI_C=.19566755027076152;MEASURED_TAU=97.
CHANNELS=['TEC_C','T1_C','T2_C','T3_C','T4_C','T5_C']
COLORS=['#d95f02','#0072B2','#009E73','#CC79A7','#b89b00','#6A3D9A']
plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'figure.dpi':140})
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--snapshot',type=Path,help='Use a previously saved input snapshot directory')
args=parser.parse_args()
raw_bytes=(args.snapshot/'raw.csv').read_bytes() if args.snapshot else RAW.read_bytes()
raw_hash=hashlib.sha256(raw_bytes).hexdigest()
rows=list(csv.DictReader(raw_bytes.decode().splitlines()))
event_path=RAW.with_name(RAW.stem+'_events.jsonl')
event_bytes=(args.snapshot/'events.jsonl').read_bytes() if args.snapshot else event_path.read_bytes()
events=[json.loads(l) for l in event_bytes.decode().splitlines() if l.strip()]
snapshot=DATA/('input_snapshot_'+raw_hash[:12])
snapshot.mkdir(exist_ok=True)
for name,content in [('raw.csv',raw_bytes),('events.jsonl',event_bytes)]:
 target=snapshot/name
 if not target.exists():
  with target.open('xb') as stream:stream.write(content)
 else:assert target.read_bytes()==content, 'Snapshot content mismatch'

origins={str(e['segment_id']):e['phase_origin_s'] for e in events if e.get('event')=='sine_phase_origin'}

def save_csv(name, records):
 if not records:return
 with (DATA/name).open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
def arr(rr,key):return np.array([float(r[key]) for r in rr])
def phase_wrap(x):return (x+np.pi)%(2*np.pi)-np.pi
def fit(t,y,omega,trend=True,harmonics=1):
 center=(t[0]+t[-1])/2
 cols=[np.ones(len(t))]
 if trend:cols.append(t-center)
 for h in range(1,harmonics+1):cols.extend([np.sin(h*omega*t),np.cos(h*omega*t)])
 X=np.column_stack(cols);coef=np.linalg.lstsq(X,y,rcond=None)[0];start=2 if trend else 1
 a,b=coef[start:start+2];prediction=X@coef
 return dict(mean=coef[0],drift=coef[1]*60 if trend else 0,amplitude=np.hypot(a,b),phase=np.arctan2(b,a),
             prediction=prediction,residual=y-prediction,rms=np.sqrt(np.mean((y-prediction)**2)),
             thd=(np.sqrt(sum(coef[start+2:]**2))/np.hypot(a,b) if harmonics>1 else np.nan))
def analytic(t,period,chi=CHI,tau=TAU,ub=UB):
 omega=2*np.pi/period;z=omega*tau
 amp=chi*ub/np.sqrt(1+z*z);delta=np.arctan(z);C=chi*ub*z/(1+z*z)
 return AMBIENT+amp*np.sin(omega*t-delta)+C*np.exp(-t/tau)

# Full synchronized raw history: nothing is removed from this plot.
t_all=arr(rows,'time_ms')/1000
fig,ax=plt.subplots(2,1,figsize=(11,7),sharex=True,gridspec_kw={'height_ratios':[3,1]})
for k,c in zip(CHANNELS,COLORS):ax[0].plot(t_all,arr(rows,k),label=k,color=c,lw=1.5 if k=='TEC_C' else 1)
ax[0].set(ylabel='Raw temperature (°C)',title='Stage 2: complete recorded history, including startup and period changes')
ax[0].legend(ncol=3);ax[1].plot(t_all,arr(rows,'signed_pwm'),color='#333333');ax[1].set(xlabel='Arduino elapsed time (s)',ylabel='Applied PWM')
for step,o in origins.items():
 for a in ax:a.axvline(o,color='.6',ls=':',lw=1)
for a in ax:a.grid(alpha=.2)
fig.tight_layout();fig.savefig(FIG/'01_complete_history.png');plt.close(fig)

summaries=[];cycle_stats=[];sensor_metrics=[];residual_records=[];theory=[];segments=[]
for step in ['3','4','5']:
 rr=[r for r in rows if r['step']==step and r['control_mode']=='RAW_SINE' and r['run_state']=='RUNNING']
 assert rr and all(r['safety']=='OK' for r in rr)
 assert all(float(r['ub_pwm'])==50 and float(r['ua_pwm'])==0 for r in rr)
 period=float(rr[0]['period_s']);omega=2*np.pi/period;origin=float(origins[step]);t=arr(rr,'time_ms')/1000-origin;u=arr(rr,'signed_pwm');Y={k:arr(rr,k) for k in CHANNELS}
 # First echoed sample is slightly later than phase zero. Allow one normal
 # sample interval at either edge, but reject substantial gaps within a cycle.
 dt=float(np.median(np.diff(t)));complete=[]
 for j in range(int(t[-1]//period)):
  mask=(t>=j*period)&(t<(j+1)*period)
  tt=t[mask]
  if len(tt)<5 or tt[0]-j*period>2*dt or (j+1)*period-tt[-1]>2*dt or np.max(np.diff(tt))>5:continue
  complete.append((j,mask))
  fu=fit(tt,u[mask],omega,trend=False)
  for k in CHANNELS:
   f=fit(tt,Y[k][mask],omega)
   cycle_stats.append(dict(step=step,period_s=period,cycle=j+1,channel=k,mean_C=f['mean'],amplitude_C=f['amplitude'],lag_rad=phase_wrap(fu['phase']-f['phase']),drift_C_per_min=f['drift'],fit_rms_C=f['rms']))
 assert complete,'No complete cycle available'
 # A long-period record has just one complete cycle. Never label it settled.
 retained=complete[-2:] if len(complete)>=3 else complete[-1:]
 mask=np.logical_or.reduce([m for _,m in retained]);tw=t[mask];uw=u[mask];fu=fit(tw,uw,omega,trend=False)
 firstcycle=retained[0][0]+1;lastcycle=retained[-1][0]+1
 status='startup-contaminated; one complete cycle only' if period==500 else 'limited repeatability; period-transition influence' if period==200 else 'approximately repeatable TEC; not certified for all six sensors'
 for k in CHANNELS:
  f=fit(tw,Y[k][mask],omega);fh=fit(tw,Y[k][mask],omega,harmonics=3);lag=phase_wrap(fu['phase']-f['phase'])
  metric=dict(step=step,period_s=period,channel=k,first_cycle=firstcycle,last_cycle=lastcycle,window_start_arduino_s=origin+tw[0],window_end_arduino_s=origin+tw[-1],mean_at_window_midpoint_C=f['mean'],fundamental_amplitude_C=f['amplitude'],lag_rad=lag,lag_s=lag/omega,drift_C_per_min=f['drift'],fundamental_rms_C=f['rms'],three_harmonic_THD=fh['thd'],status=status)
  sensor_metrics.append(metric)
  if k=='TEC_C':tecfit=f;summary=dict(metric);summary.update(input_amplitude_pwm=fu['amplitude'],observed_complete_cycles=len(complete),ub_pwm=50,upper_excursion_C=float(np.max(Y[k][mask])-f['mean']),lower_excursion_C=float(f['mean']-np.min(Y[k][mask])));summaries.append(summary)
 # Model with historical unequal susceptibilities, driven by actual applied PWM.
 def rhs(time,state):
  command=np.interp(time,t,u);chi=CHI_H if command>=0 else CHI_C
  return [(AMBIENT+chi*command-state[0])/MEASURED_TAU]
 branch=solve_ivp(rhs,(t[0],t[-1]),[Y['TEC_C'][0]],t_eval=t,rtol=1e-8,atol=1e-9,max_step=.5).y[0]
 fig,ax=plt.subplots(3,1,figsize=(10,8),sharex=True,gridspec_kw={'height_ratios':[2,1,1]})
 ax[0].plot(t,Y['TEC_C'],label='Measured TEC',color=COLORS[0]);ax[0].plot(t,analytic(t,period),label='Equal χ=0.23, τth=80 s; start at ambient',ls='--',color='#444444')
 ax[0].plot(t,branch,label='Historical unequal χ, τth=97 s; measured initial TEC',ls=':',color='#0072B2')
 ax[0].plot(tw,tecfit['prediction'],label='Selected-window sine + linear drift fit',color='#009E73',lw=1.7)
 ax[0].axvspan(tw[0],tw[-1],color='#009E73',alpha=.07)
 ax[0].set(ylabel='TEC temperature (°C)',title=f'Zero-centered PWM: period {period:g} s, ub = 50 PWM\n{status}')
 ax[0].legend(fontsize=8,ncol=2)
 ax[1].plot(t,u,color='#333333');ax[1].set(ylabel='Applied signed PWM',ylim=(-60,60))
 ax[2].plot(tw,tecfit['residual'],color='#6A3D9A');ax[2].axhline(0,color='.5',lw=.7)
 ax[2].set(xlabel='Time since this segment’s phase origin (s)',ylabel='Measured − fit (°C)')
 for a in ax:a.grid(alpha=.2)
 fig.tight_layout();fig.savefig(FIG/f'period_{int(period)}s.png');plt.close(fig)
 for ti,yi,ui,fi,ri in zip(tw,Y['TEC_C'][mask],uw,tecfit['prediction'],tecfit['residual']):residual_records.append(dict(period_s=period,time_arduino_s=ti+origin,TEC_C=yi,signed_pwm=ui,fit_C=fi,residual_C=ri))
 segments.append(dict(step=step,period_s=period,start_arduino_s=origin+t[0],end_arduino_s=origin+t[-1],complete_cycles=len(complete),status=status))
 # Required exact/numerical check: equal χ and initial T = ambient.
 grid=np.linspace(0,max(4*period,800),2001);num=solve_ivp(lambda tt,yy:[(AMBIENT+CHI*UB*np.sin(omega*tt)-yy[0])/TAU],(0,grid[-1]),[AMBIENT],t_eval=grid,rtol=1e-10,atol=1e-11,max_step=.5).y[0]
 exact=analytic(grid,period);max_error=np.max(abs(num-exact));assert max_error<1e-6
 z=omega*TAU;A=CHI*UB/np.sqrt(1+z*z);delta=np.arctan(z)
 C=CHI*UB*z/(1+z*z)
 derivative=A*omega*np.cos(omega*grid-delta)-C/TAU*np.exp(-grid/TAU)
 ode_error=np.max(abs(TAU*derivative+exact-AMBIENT-CHI*UB*np.sin(omega*grid)));assert ode_error<1e-10
 theory.append(dict(period_s=period,tau_th_s=TAU,chi_equal_C_per_pwm=CHI,ub_pwm=UB,normalized_amplitude=1/np.sqrt(1+z*z),amplitude_C=A,phase_lag_rad=delta,time_lag_s=delta/omega,startup_C=C,max_numerical_error_C=max_error,max_ODE_residual_C=ode_error,initial_condition_error_C=abs(exact[0]-AMBIENT)))
 if len(theory)==1:theoryfig,theoryax=plt.subplots(3,1,figsize=(10,8))
 ai=theoryax[len(theory)-1];ai.plot(grid,exact,label='Analytical including startup');ai.plot(grid[::30],num[::30],'.',label='Numerical integration',ms=3);ai.set(xlabel='Time (s)',ylabel='TEC prediction (°C)',title=f'Equal susceptibility, period {period:g} s');ai.grid(alpha=.2);ai.legend(fontsize=8)
theoryfig.tight_layout();theoryfig.savefig(FIG/'02_analytical_numerical_check.png');plt.close(theoryfig)

# Frequency comparison: fitted values are descriptive when settling is limited.
summaries.sort(key=lambda r:r['period_s']);theory.sort(key=lambda r:r['period_s'])
fig,ax=plt.subplots(1,2,figsize=(10,4))
periods=[r['period_s'] for r in summaries]
ax[0].plot(periods,[r['fundamental_amplitude_C'] for r in summaries],'o-',label='Measured selected-window fit')
ax[0].plot(periods,[r['amplitude_C'] for r in theory],'s--',label='Equal-χ prediction')
ax[1].plot(periods,[r['lag_rad'] for r in summaries],'o-',label='Measured vs applied PWM')
ax[1].plot(periods,[r['phase_lag_rad'] for r in theory],'s--',label='Equal-χ prediction')
for a,ylabel in zip(ax,['TEC fundamental amplitude (°C)','Temperature lag relative to PWM (rad)']):a.set(xlabel='Forcing period (s)',ylabel=ylabel);a.grid(alpha=.2);a.legend(fontsize=8)
fig.suptitle('Fixed ub = 50 PWM; 500 s estimate includes startup, 200 s has limited repeatability',fontsize=10)
fig.tight_layout();fig.savefig(FIG/'03_period_comparison.png');plt.close(fig)
save_csv('tec_metrics.csv',summaries);save_csv('six_sensor_metrics.csv',sensor_metrics);save_csv('cycle_comparison.csv',cycle_stats);save_csv('fit_residuals.csv',residual_records);save_csv('equal_susceptibility_predictions.csv',theory);save_csv('segments.csv',segments)
provenance={'gui_sha256':hashlib.sha256((ROOT/'python/module_08_rod_acquisition_gui.py').read_bytes()).hexdigest(),'arduino_sha256':hashlib.sha256((ROOT/'arduino/module_08_rod_controller/module_08_rod_controller.ino').read_bytes()).hexdigest(),'raw_file':str(RAW.relative_to(ROOT)),'raw_sha256':raw_hash,'rows':len(rows),'ambient_analysis_C':AMBIENT,'ambient_provenance':'user-approved average of six unpowered sensors; not independent air measurement','logged_ambient_C':sorted(set(str(e.get('ambient_C')) for e in events if 'ambient_C' in e)),'analysis_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'event_sha256':hashlib.sha256(event_bytes).hexdigest()}
(DATA/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
# Detect an accidental write to the raw acquisition while generating outputs.
# If the live logger appended samples, report a snapshot instead of overwriting it.
current=RAW.read_bytes()
provenance['input_snapshot_directory']=str(snapshot.relative_to(ROOT))
provenance['live_file_appended_during_analysis']=current!=raw_bytes
provenance['snapshot_preserved_exactly']=(snapshot/'raw.csv').read_bytes()==raw_bytes
if not args.snapshot:
 assert current.startswith(raw_bytes), 'Existing raw bytes changed during analysis'
(DATA/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')

lines=['# Stage 2: Uncompensated sinusoidal PWM','', '## Recorded settings and source','',
'The instructor-directed zero-centered trial used **ua = 0**, **ub = 50 PWM**, and periods **500, 200, then 100 s**. These replace the earlier suggested 100-PWM starting amplitude. The earlier ub = 100 pilot is separate and is not pooled into this comparison.','',
f'Raw record: [CSV](../../{RAW.relative_to(ROOT)}). Keep its matching [command log](../../{RAW.with_name(RAW.stem+"_commands.csv").relative_to(ROOT)}) and [events](../../{event_path.relative_to(ROOT)}). Raw data remain unchanged. All analyzed running segments report safety OK.','',
'Analysis ambient: **23.52 °C**, the user-approved six-sensor baseline estimate. The GUI event log records **22 °C** for these segments; that original metadata is retained. The apparatus was warmer at the start of this file, so 23.52 °C is an assumed reference, not a fresh independent ambient measurement. Sine-fit amplitude and phase do not depend on subtracting this constant.','',
'## Temperature and applied PWM history','', '![Complete six-temperature record](figures/01_complete_history.png)','',
'## Fitted TEC response','',
'Fit at the known drive frequency: T = c + d(t − tmid) + a sin(ωt) + b cos(ωt). Report B = sqrt(a²+b²), phase = atan2(b,a), and lag = phase(PWM) − phase(TEC), wrapped to (−π,π]. Fit applied signed PWM at the same times. Mean c is evaluated at the selected window midpoint; d is a separate linear-drift estimate. Values are descriptive fits, not validated steady-state parameter estimates or total uncertainty bounds.','',
'Use the last complete cycle at 500 and 200 s and the last two complete cycles at 100 s. The event log supplies the phase origin. Small edge gaps up to two median sample intervals are tolerated; gaps over 5 s reject a cycle. No startup samples are deleted from the raw record or full-history plot.','',
'| Period (s) | Retained cycles | TEC mean (°C) | Fundamental B (°C) | Lag (rad) | Lag (s) | Drift (°C/min) | Fit RMS (°C) |','|---:|---:|---:|---:|---:|---:|---:|---:|']
for r in summaries:lines.append(f"| {r['period_s']:g} | {r['first_cycle']}–{r['last_cycle']} | {r['mean_at_window_midpoint_C']:.3f} | {r['fundamental_amplitude_C']:.3f} | {r['lag_rad']:.3f} | {r['lag_s']:.2f} | {r['drift_C_per_min']:+.3f} | {r['fundamental_rms_C']:.3f} |")
lines+=['','| Period (s) | Excursion above fitted mean (°C) | Below fitted mean (°C) | Harmonic distortion estimate (%) |','|---:|---:|---:|---:|']
for r in summaries:lines.append(f"| {r['period_s']:g} | {r['upper_excursion_C']:.3f} | {r['lower_excursion_C']:.3f} | {100*r['three_harmonic_THD']:.2f} |")
lines+=['','Excursions use the observed extrema within the fitted window and its midpoint mean; drift/startup can contribute to their inequality. The distortion estimate is sqrt(B2²+B3²)/B1 from a separate three-harmonic-plus-drift fit. With one transient cycle this is descriptive, not a clean steady harmonic measurement.','', '### Settling limits','',
'**500 s:** only one complete cycle was captured before changing period. It includes startup, so settling and repeatability cannot be established. **200 s:** two complete cycles were captured, with a change in mean between them; only the later complete cycle is fitted. **100 s:** later TEC cycles are approximately repeatable, but this does not certify a settled six-sensor profile. No numerical settling criterion was declared before this acquisition; no new threshold is retroactively presented as a pass. [Cycle-by-cycle fits](analysis_output/cycle_comparison.csv) retain the evidence.','']
for per in [500,200,100]:lines.extend([f'### {per} s trial','',f'![{per} s measured response and fit](figures/period_{per}s.png)',''])
lines+=['## Equal-susceptibility derivation and checks','',
'For the required illustrative equal-branch model take χh = χc = χ = 0.23 °C/PWM, τth = 80 s, and ub = 50 PWM. The common χ is chosen explicitly from the supplied default cooling magnitude; it is not claimed to represent both measured branches. The period is distinct from τth.','',r'For $\theta=T-T_{\rm room}$, $\tau_{\rm th}\dot\theta+\theta=\chi u_b\sin(\omega t)$. Substitute $\theta_p=a\sin(\omega t)+b\cos(\omega t)$:','',
'```math',r'a-\omega\tau_{\rm th}b=\chi u_b,\qquad b+\omega\tau_{\rm th}a=0.',r'a=\frac{\chi u_b}{1+(\omega\tau_{\rm th})^2},\qquad b=-\frac{\chi u_b\omega\tau_{\rm th}}{1+(\omega\tau_{\rm th})^2}.','```','',
'```math',r'T(t)=T_{\rm room}+\frac{\chi u_b}{\sqrt{1+(\omega\tau_{\rm th})^2}}\sin(\omega t-\delta)+Ce^{-t/\tau_{\rm th}},',r'\delta=\tan^{-1}(\omega\tau_{\rm th}),\quad C=\frac{\chi u_b\omega\tau_{\rm th}}{1+(\omega\tau_{\rm th})^2}\quad[T(0)=T_{\rm room}].','```','',
'At t = 0, the sine contribution equals −C, satisfying the initial condition. For the sinusoidal terms, the sine coefficient is a − ωτth b = χub and the cosine coefficient is b + ωτth a = 0. For the homogeneous term, τth d(C exp(−t/τth))/dt + C exp(−t/τth) = 0. Thus the complete expression satisfies both the ODE and initial condition.','',
'| Period (s) | Normalized amplitude B/(χub) | Predicted B (°C) | δ (rad) | δ/ω (s) |','|---:|---:|---:|---:|---:|']
for r in theory:lines.append(f"| {r['period_s']:g} | {r['normalized_amplitude']:.4f} | {r['amplitude_C']:.3f} | {r['phase_lag_rad']:.4f} | {r['time_lag_s']:.2f} |")
lines+=['','![Analytical and numerical check](figures/02_analytical_numerical_check.png)','',f"Numerical integration agrees with the analytical result to a maximum absolute error of **{max(r['max_numerical_error_C'] for r in theory):.2e} °C**. Direct analytical substitution and the initial-condition check pass. [Numerical predictions](analysis_output/equal_susceptibility_predictions.csv).",'',
'## Comparison and interpretation','', '![Period comparison](figures/03_period_comparison.png)','',
'At fixed PWM amplitude, temperature amplitude increases with period. The equal-susceptibility model predicts B/(χub) = 1/sqrt[1+(ωτth)²], so constant PWM amplitude does **not** produce constant temperature amplitude across frequencies. The measured fitted amplitudes show the same ordering, subject to the limited 500/200 s settling evidence. The phase in radians generally increases at shorter periods in the one-lump prediction, while the time delay δ/ω need not increase.','',
'The equal-χ model oscillates about room temperature after startup. The measured TEC response is biased above the ambient estimate and is not perfectly sinusoidal: unequal heating/cooling gains can create a mean shift, unequal excursions/slopes and harmonic distortion. A fixed sign-switching susceptibility is already nonlinear with respect to a single linear sinusoidal model. The linear-drift sine residual plots show the remaining mismatch; third-harmonic fit metrics are saved as a descriptive distortion check, not proof of a settled waveform.','',
'The dotted refined predictions use historical measured χh = 0.4872434 and χc = 0.1956676 °C/PWM, τth = 97 s, actual applied PWM, and measured initial TEC temperature for each segment. They are numerical one-lump estimates, not fits. Attaching the rod can change dynamics and equilibrium gains; use model discrepancies as evidence, not as a reason to alter raw data.','',
'## Saved evidence and remaining work','',
'- [TEC metrics](analysis_output/tec_metrics.csv), [all-six sensor fits](analysis_output/six_sensor_metrics.csv), [residuals](analysis_output/fit_residuals.csv), [segments](analysis_output/segments.csv), and [provenance hashes](analysis_output/provenance.json).',
f'- Reproduce this exact snapshot with `python3 python/analysis/analyze_stage2.py --snapshot {snapshot.relative_to(ROOT)}` from the repository root (NumPy, SciPy and Matplotlib required). The snapshot is a byte-for-byte raw copy; the live logger may append to the original without changing this analysis input.',
'- **S18.4 positive-only PWM has not been acquired in this recording.** It still needs ua > ub > 0, adjustment of ua toward mean TEC ≈40 °C, and comparison of mean/amplitude/phase/symmetry/distortion. No values or result are invented for it.',
'- A repeated complete 500 s cycle is needed if settled-repeatability evidence is required for that period. Preserve this pilot even if a further run is acquired.','']
(OUT/'README.md').write_text('\n'.join(lines))
print('Saved',len(list(FIG.glob('*.png'))),'plots.');print(json.dumps([{k:r[k] for k in ['period_s','mean_at_window_midpoint_C','fundamental_amplitude_C','lag_rad','fundamental_rms_C']} for r in summaries],indent=2))
