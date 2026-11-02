import time

import numpy as np

from config import MAX_FRAMES


class ChannelUnavailableError(ValueError):
    """The channel has no usable frame; other channels may still be read."""


def validate_waveform(t, y):
    t = np.asarray(t, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if t.ndim != 1 or y.ndim != 1 or len(t) != len(y):
        raise ChannelUnavailableError("Invalid waveform shape")
    if len(y) < 2:
        raise ChannelUnavailableError("No waveform data")
    if not np.all(np.isfinite(t)) or not np.all(np.isfinite(y)):
        raise ChannelUnavailableError("Waveform contains non-finite samples")
    if np.any(np.diff(t) <= 0):
        raise ChannelUnavailableError("Invalid sample times")
    return t, y


class WaveformBuffer:
    """Accumulated samples for one channel, with its own sampling interval."""

    def __init__(self):
        self.whole_wave = np.array([], dtype=np.float64)
        self.wave_frames = []
        self.accum_dt = None
        self.whole_frames = 0
        self.frames_for_fft = 0
        self.resets = 0

    def append(self, t, y):
        t, y = validate_waveform(t, y)
        dt = float(np.mean(np.diff(t)))
        if not np.isfinite(dt) or dt <= 0:
            raise ValueError("The sampling interval must be positive")

        if self.accum_dt is not None and abs(dt - self.accum_dt) / self.accum_dt > 1e-6:
            self.reset()
        self.accum_dt = dt
        self.wave_frames.append(np.asarray(y, dtype=np.float64).copy())
        if len(self.wave_frames) > MAX_FRAMES:
            self.wave_frames.pop(0)

        self.whole_wave = np.concatenate(self.wave_frames)
        self.frames_for_fft = len(self.wave_frames)
        self.whole_frames += 1

    def get_waveform(self):
        n = len(self.whole_wave)
        if n == 0 or self.accum_dt is None:
            return np.array([]), np.array([])
        t = (np.arange(n, dtype=np.float64) - n + 1) * self.accum_dt
        return t, self.whole_wave

    def reset(self):
        self.whole_wave = np.array([], dtype=np.float64)
        self.wave_frames = []
        self.accum_dt = None
        self.frames_for_fft = 0
        self.resets += 1


class ChannelScope:
    """Shared channel buffers and the original single-channel read interface."""

    def __init__(self, channels):
        if isinstance(channels, str):
            channels = (channels,)
        self.channels = tuple(channel.upper() for channel in channels)
        if not self.channels or len(set(self.channels)) != len(self.channels):
            raise ValueError("Select one or two distinct channels")
        if any(channel not in ("CH1", "CH2") for channel in self.channels):
            raise ValueError("Supported channels: CH1, CH2")
        self.channel = self.channels[0]
        self.buffers = {channel: WaveformBuffer() for channel in self.channels}
        self.channel_errors = {}
        self.frame_timestamp = None
        self.frame_monotonic = None

    def mark_frame_time(self):
        # A shared PC receipt timestamp for the entire acquisition, not per transfer.
        self.frame_timestamp = time.time()
        self.frame_monotonic = time.perf_counter()

    def read_waveform(self):
        return self.read_waveforms()[self.channel]

    def mark_channel_unavailable(self, channel, message):
        self.channel_errors[channel] = message
        if self.buffers[channel].frames_for_fft:
            self.buffers[channel].reset()

    def get_whole_waveform(self, channel=None):
        return self.buffers[channel or self.channel].get_waveform()

    def reset_waveform(self, channel=None):
        channels = self.channels if channel is None else (channel,)
        for name in channels:
            self.buffers[name].reset()

    @property
    def whole_wave(self):
        return self.buffers[self.channel].whole_wave

    @property
    def wave_frames(self):
        return self.buffers[self.channel].wave_frames

    @property
    def accum_dt(self):
        return self.buffers[self.channel].accum_dt

    @property
    def whole_frames(self):
        return self.buffers[self.channel].whole_frames

    @property
    def frames_for_fft(self):
        return self.buffers[self.channel].frames_for_fft

    @property
    def resets(self):
        return self.buffers[self.channel].resets
