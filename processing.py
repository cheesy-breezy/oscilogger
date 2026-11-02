import numpy as np


def fft_dbv(t, y):
    if len(y) < 2:
        return np.array([]), np.array([])

    dt = float(np.mean(np.diff(t)))
    x = y.astype(np.float64)

    x = x - np.mean(x)

    n = len(x)
    n_fft = int(2 ** np.ceil(np.log2(n)))

    window = np.hanning(n)
    if not np.any(window):
        window = np.ones(n)
    spec = np.fft.rfft(x * window, n=n_fft)

    amp = 2 * np.abs(spec) / np.sum(window)
    amp[0] *= 0.5

    freq = np.fft.rfftfreq(n_fft, dt)
    dbv = 20 * np.log10(np.maximum(amp, 1e-15))

    return freq, dbv


def make_summary(t, y):
    dt = np.mean(np.diff(t))
    fs = 1 / dt

    t_window = len(y) * dt
    df_real = 1 / t_window if t_window > 0 else np.nan

    return (
        f"Signal\n"
        f"N:        {len(y)}\n"
        f"T:        {t_window:.3e} s\n"
        f"dt:       {dt:.3e} s\n"
        f"fs:       {fs:.3e} Hz\n"
        f"mean:     {np.mean(y):.3e} V\n"
        f"rms:      {np.sqrt(np.mean(y ** 2)):.3e} V\n"
        f"min:      {np.min(y):.3e} V\n"
        f"max:      {np.max(y):.3e} V\n"
        f"p-p:      {np.ptp(y):.3e} V\n"
        f"\n"
        f"Spectrum\n"
        f"df:  {df_real:.3e} Hz\n"
    )
