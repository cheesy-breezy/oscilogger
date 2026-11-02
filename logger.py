from datetime import datetime, timezone
from itertools import zip_longest
from pathlib import Path
import csv
import time

from config import LOG_DIR


def format_size(size):
    if size < 1024:
        return f"{size} B"
    if size < 1024 ** 2:
        return f"{size / 1024:.1f} KB"
    if size < 1024 ** 3:
        return f"{size / 1024 ** 2:.2f} MB"
    return f"{size / 1024 ** 3:.2f} GB"


def channel_table(data, channels):
    """Pair rows by sample index, preserving each channel's independent X values."""
    streams = [zip(*data[channel]) if channel in data else iter(()) for channel in channels]
    for sample, values in enumerate(zip_longest(*streams, fillvalue=None)):
        columns = []
        for value in values:
            columns.extend(value if value is not None else ("", ""))
        yield sample, columns


def prepare_spectrum(spectra):
    base_dir = Path(__file__).resolve().parent
    log_dir = base_dir / LOG_DIR
    log_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = log_dir / f"Spectrum_Log_{stamp}.csv"
    channels = tuple(spectra)
    header = ["sample"]
    for channel in channels:
        header.extend((f"{channel}_frequency_hz", f"{channel}_amplitude_dbv"))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows([sample, *values] for sample, values in channel_table(spectra, channels))
    return path


class WaveformRecorder:
    def __init__(self, channels=("CH1", "CH2")):
        self.channels = (channels,) if isinstance(channels, str) else tuple(channels)
        self.file = None
        self.writer = None
        self.path = None
        self.frame = 0
        self.rows = 0
        self.t0 = None
        self.last_size = 0

    @property
    def is_recording(self):
        return self.file is not None

    def start(self):
        base_dir = Path(__file__).resolve().parent
        log_dir = base_dir / LOG_DIR
        log_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.path = log_dir / f"Signal_Log_{stamp}.csv"
        self.file = self.path.open("w", newline="", encoding="utf-8")
        self.writer = csv.writer(self.file)
        header = [
            "frame", "sample", "frame_recording_time_s",
            "pc_frame_time_unix_s", "pc_frame_time_utc",
        ]
        for channel in self.channels:
            header.extend((f"{channel}_frame_time_s", f"{channel}_voltage_v"))
        self.writer.writerow(header)
        self.file.flush()
        self.frame = 0
        self.rows = 0
        self.last_size = 0
        self.t0 = time.perf_counter()
        return self.path

    def write(self, waveforms, frame_timestamp=None, elapsed=None):
        if self.file is None:
            return
        if elapsed is None:
            elapsed = time.perf_counter() - self.t0
        if frame_timestamp is None:
            frame_timestamp = time.time()
        iso_time = datetime.fromtimestamp(frame_timestamp, timezone.utc).isoformat(
            timespec="microseconds"
        )
        reference = [elapsed, f"{frame_timestamp:.6f}", iso_time]
        row_count = 0
        for sample, values in channel_table(waveforms, self.channels):
            self.writer.writerow([self.frame, sample, *reference, *values])
            row_count += 1
        if not row_count:
            # Retain the frame reference when neither channel has data.
            self.writer.writerow([self.frame, "", *reference, *([""] * (2 * len(self.channels)))])
            row_count = 1
        self.file.flush()
        self.rows += row_count
        self.frame += 1

    def size_bytes(self):
        if self.path is None or not self.path.exists():
            return 0
        if self.file is not None:
            self.file.flush()
        return self.path.stat().st_size

    def size_text(self):
        return format_size(self.size_bytes())

    def last_size_text(self):
        return format_size(self.last_size)

    def stop(self):
        if self.file is None:
            return None
        path = self.path
        self.last_size = self.size_bytes()
        self.file.flush()
        self.file.close()
        self.file = None
        self.writer = None
        self.path = None
        self.t0 = None
        return path
