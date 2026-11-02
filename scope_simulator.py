import numpy as np

from config import SIM_FS, SIM_N, F_LEAD, A_LEAD, F_EVIL, A_EVIL, NOISE_RMS
from waveform_buffer import ChannelScope


class ScopeSimulator(ChannelScope):
    def __init__(self, channels=("CH1", "CH2")):
        super().__init__(channels)
        self.rng = np.random.default_rng()
        self.sample_index = 0

    def read_waveforms(self):
        self.mark_frame_time()
        idx = self.sample_index + np.arange(SIM_N)
        t_signal = idx / SIM_FS
        t = (np.arange(SIM_N, dtype=np.float64) - SIM_N + 1) / SIM_FS
        waveforms = {}

        for channel in self.channels:
            # Distinct amplitudes, phases and interference peaks for visual checks.
            second = channel == "CH2"
            gain = 0.7 if second else 1.0
            phase = np.pi / 3 if second else 0.0
            f_evil = 0.8 * F_EVIL if second else F_EVIL
            y = (
                gain * A_LEAD * np.sin(2 * np.pi * F_LEAD * t_signal + phase)
                + A_EVIL * np.sin(2 * np.pi * f_evil * t_signal)
                + self.rng.normal(0.0, NOISE_RMS, SIM_N)
            )
            self.buffers[channel].append(t, y)
            waveforms[channel] = (t, y)

        self.sample_index += SIM_N
        return waveforms

    def close(self):
        pass
