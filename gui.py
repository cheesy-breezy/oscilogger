from datetime import datetime
from pathlib import Path

import numpy as np
import pyqtgraph as pg
import pyqtgraph.exporters
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QButtonGroup,
)

from config import UPDATE_MS, FFT_Y_MARGIN_DB, MAX_FRAMES
from processing import fft_dbv, make_summary
from logger import WaveformRecorder, prepare_spectrum
from waveform_buffer import ChannelUnavailableError, validate_waveform


CHANNEL_COLORS = {
    "CH1": {"time": "#55d6ff", "fft": "#2680ff"},
    "CH2": {"time": "#ffd740", "fft": "#ff8c32"},
}


class MainWindow(QMainWindow):
    def __init__(self, scope, title):
        super().__init__()
        self.scope = scope
        self.channels = scope.channels
        self.visible_channels = self.channels
        pg.setConfigOptions(antialias=False)

        self.time_plot = pg.PlotWidget()
        self.fft_plot = pg.PlotWidget()
        for plot in (self.time_plot, self.fft_plot):
            plot.showGrid(x=True, y=True, alpha=0.25)
            plot.getViewBox().setZValue(1)
            for axis in ("bottom", "left"):
                plot.getAxis(axis).setStyle(maxTickLevel=0)
        self.time_plot.addLegend()
        self.fft_plot.addLegend()
        self.time_curves = {}
        self.fft_curves = {}
        for channel in self.channels:
            colors = CHANNEL_COLORS[channel]
            self.time_curves[channel] = self.time_plot.plot(
                pen=pg.mkPen(colors["time"], width=1), name=channel,
            )
            self.fft_curves[channel] = self.fft_plot.plot(
                pen=pg.mkPen(colors["fft"], width=1), name=channel,
            )

        self.time_plot.setLabel("bottom", "Frame time", units="s")
        self.time_plot.setLabel("left", "Voltage", units="V")
        self.fft_plot.setLabel("bottom", "Frequency", units="Hz")
        self.fft_plot.setLabel("left", "Amplitude", units="dBV")

        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setFixedWidth(330)
        self.summary.setFont(QFont("Consolas", 10))
        self.summary.setPlainText("No data yet")
        self.summary.setStyleSheet(
            "QPlainTextEdit { background: #202020; color: #f0f0f0; padding: 8px; }"
        )

        self.recorder = WaveformRecorder(self.channels)
        self.last_record_path = None
        self.last_spec_path = None
        self.last_image_path = None
        self.last_spectra = {}
        self.last_waveforms = {}
        self.fft_xmax = None
        self.last_resets = None
        self.spectrum_in_dbv = True
        self.acquisition_error = None

        self.channel_button_group = QButtonGroup(self)
        self.channel_button_group.setExclusive(True)
        self.channel_buttons = {}
        channel_layout = QHBoxLayout()
        options = [("both", "Оба канала", self.channels)]
        options.extend((channel, channel, (channel,)) for channel in self.channels)
        for key, label, selected in options:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(key == "both")
            button.clicked.connect(
                lambda checked=False, channels=selected: self.set_visible_channels(channels)
            )
            self.channel_button_group.addButton(button)
            self.channel_buttons[key] = button
            channel_layout.addWidget(button)

        self.record_button = QPushButton("Start recording")
        self.record_button.clicked.connect(self.toggle_recording)
        self.spectrum_button = QPushButton("Download spectrum")
        self.spectrum_button.clicked.connect(self.save_spectrum)
        self.res_spectrum_button = QPushButton("Reset spectrum")
        self.res_spectrum_button.clicked.connect(self.reset_spectrum)
        self.spectrum_mode_button = QPushButton("Spectrum mode: dBV")
        self.spectrum_mode_button.clicked.connect(self.change_spectrum_mode)
        self.fft_full_button = QPushButton("FFT full")
        self.fft_full_button.clicked.connect(self.fft_full_range)
        self.fft_peak_button = QPushButton("FFT peak")
        self.fft_peak_button.clicked.connect(self.fft_peak_range)
        self.img_spectrum_button = QPushButton("Save spectrum image")
        self.img_spectrum_button.clicked.connect(self.save_spectrum_image)

        plots_layout = QVBoxLayout()
        plots_layout.addLayout(channel_layout)
        plots_layout.addWidget(self.time_plot)
        plots_layout.addWidget(self.fft_plot)
        side_layout = QVBoxLayout()
        side_layout.addWidget(self.summary, stretch=1)
        for button in (
            self.record_button, self.spectrum_button, self.img_spectrum_button,
            self.spectrum_mode_button, self.res_spectrum_button,
            self.fft_full_button, self.fft_peak_button,
        ):
            side_layout.addWidget(button)
        main_layout = QHBoxLayout()
        main_layout.addLayout(plots_layout, stretch=1)
        main_layout.addLayout(side_layout)
        root = QWidget()
        root.setLayout(main_layout)
        self.setCentralWidget(root)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_data)
        self.timer.start(UPDATE_MS)
        self.setWindowTitle(title)
        self.resize(1400, 800)
        self.update_data()

    @property
    def is_recording(self):
        return self.recorder.is_recording

    def set_visible_channels(self, channels):
        self.visible_channels = tuple(channels)
        for plot, curves in ((self.time_plot, self.time_curves),
                             (self.fft_plot, self.fft_curves)):
            plot.plotItem.legend.clear()
            for channel, curve in curves.items():
                visible = channel in self.visible_channels
                curve.setVisible(visible)
                if visible:
                    plot.plotItem.legend.addItem(curve, channel)
        self.fft_xmax = None
        self.update_time_range(self.visible_waveforms())
        self.update_fft_range()
        self.update_summary()

    def visible_waveforms(self):
        return {
            channel: self.last_waveforms[channel]
            for channel in self.visible_channels if channel in self.last_waveforms
        }

    def visible_spectra(self):
        return {
            channel: self.last_spectra[channel]
            for channel in self.visible_channels if channel in self.last_spectra
        }

    def update_data(self):
        try:
            raw_waveforms = self.scope.read_waveforms()
        except Exception as error:
            self.acquisition_error = f"{type(error).__name__}: {error}"
            self.last_waveforms = {}
            self.last_spectra = {}
            for channel in self.channels:
                self.time_curves[channel].clear()
                self.fft_curves[channel].clear()
                self.scope.mark_channel_unavailable(channel, "Acquisition failed")
            self.timer.setInterval(max(UPDATE_MS, 2000))
            self.update_summary()
            return

        self.acquisition_error = None
        self.timer.setInterval(UPDATE_MS)
        waveforms = {}
        for channel in self.channels:
            if channel not in raw_waveforms:
                self.scope.mark_channel_unavailable(
                    channel, self.scope.channel_errors.get(channel, "No waveform data")
                )
                continue
            try:
                waveforms[channel] = validate_waveform(*raw_waveforms[channel])
                self.scope.channel_errors.pop(channel, None)
            except ChannelUnavailableError as error:
                self.scope.mark_channel_unavailable(channel, str(error))
        self.last_waveforms = waveforms
        self.last_spectra = {}
        for channel in self.channels:
            if channel not in waveforms:
                self.time_curves[channel].clear()
                self.fft_curves[channel].clear()
        for channel, (t, y) in waveforms.items():
            t_fft, y_fft = self.scope.get_whole_waveform(channel)
            f, s = fft_dbv(t_fft, y_fft)
            self.last_spectra[channel] = (f, s)
            self.time_curves[channel].setData(t, y)
            self.fft_curves[channel].setData(f, self.spectrum_plot_mode(s))
        if self.recorder.is_recording:
            self.recorder.write(
                waveforms, frame_timestamp=self.scope.frame_timestamp,
                elapsed=self.scope.frame_monotonic - self.recorder.t0,
            )

        self.update_time_range(self.visible_waveforms())
        self.update_fft_range()
        self.update_summary()

    def update_summary(self):
        sections = []
        if self.acquisition_error:
            sections.append(f"Acquisition failed; retrying\n{self.acquisition_error}")
        for channel in self.visible_channels:
            if channel not in self.last_waveforms:
                sections.append(f"=== {channel} ===\nНет данных на канале {channel}")
                continue
            t, y = self.last_waveforms[channel]
            buffer = self.scope.buffers[channel]
            text = f"=== {channel} ===\n" + make_summary(t, y)
            text += (
                f"\nFFT buffer\n"
                f"frames:   {buffer.frames_for_fft}/{MAX_FRAMES}\n"
                f"points:   {len(buffer.whole_wave)}\n"
                f"dt:       {buffer.accum_dt:.3e} s\n"
            )
            sections.append(text)
        if self.recorder.is_recording:
            sections.append(
                f"Recording {' + '.join(self.channels)}\n"
                f"file:     {self.recorder.path}\n"
                f"frames:   {self.recorder.frame}\n"
                f"rows:     {self.recorder.rows}\n"
                f"size:     {self.recorder.size_text()}"
            )
        if self.last_record_path is not None:
            sections.append(
                f"Last record\n{self.last_record_path}\n"
                f"size:     {self.recorder.last_size_text()}"
            )
        if self.last_spec_path is not None:
            sections.append(f"Last spectrum\n{self.last_spec_path}")
        if self.last_image_path is not None:
            sections.append(f"Last spectrum image\n{self.last_image_path}")
        self.summary.setPlainText("\n\n".join(sections))

    def update_time_range(self, waveforms):
        if not waveforms:
            return
        self.time_plot.setXRange(
            min(float(t[0]) for t, _ in waveforms.values()),
            max(float(t[-1]) for t, _ in waveforms.values()),
            padding=0.02,
        )
        ymin = min(float(np.min(y)) for _, y in waveforms.values())
        ymax = max(float(np.max(y)) for _, y in waveforms.values())
        dy = ymax - ymin or 1.0
        self.time_plot.setYRange(ymin - 0.05 * dy, ymax + 0.05 * dy, padding=0)

    def update_fft_range(self):
        spectra = [(f, s) for f, s in self.visible_spectra().values() if len(f) >= 2]
        if not spectra:
            return
        nyq = max(float(f[-1]) for f, _ in spectra)
        resets = tuple(self.scope.buffers[channel].resets for channel in self.visible_channels)
        if self.fft_xmax is None or resets != self.last_resets:
            self.fft_xmax = nyq
            self.last_resets = resets
            self.fft_plot.setXRange(0, nyq, padding=0)

        x0, x1 = self.fft_plot.viewRange()[0]
        visible_values = []
        for f, s in spectra:
            s_plot = self.spectrum_plot_mode(s)
            visible = (f >= x0) & (f <= x1) & np.isfinite(s_plot)
            if np.any(visible):
                visible_values.append(s_plot[visible])
        if not visible_values:
            return
        smin = min(float(np.percentile(s, 5)) for s in visible_values)
        smax = max(float(np.max(s)) for s in visible_values)
        if self.spectrum_in_dbv:
            y0, y1 = smin - FFT_Y_MARGIN_DB, smax + FFT_Y_MARGIN_DB
        else:
            dy = smax - smin or max(abs(smax), 1e-12)
            y0, y1 = max(0, smin - 0.05 * dy), smax + 0.05 * dy
        self.fft_plot.setYRange(y0, y1, padding=0)

    def toggle_recording(self):
        if not self.is_recording:
            self.last_record_path = None
            self.recorder.start()
            self.record_button.setText("Stop recording")
        else:
            self.last_record_path = self.recorder.stop()
            self.record_button.setText("Start recording")
        self.update_summary()

    def save_spectrum(self):
        if not self.last_spectra:
            return
        spectra = {
            channel: self.last_spectra.get(channel, (np.array([]), np.array([])))
            for channel in self.channels
        }
        self.last_spec_path = prepare_spectrum(spectra)
        self.update_summary()

    def reset_spectrum(self):
        self.scope.reset_waveform()
        self.fft_xmax = None
        self.last_spectra = {}
        for curve in self.fft_curves.values():
            curve.clear()

    def fft_full_range(self):
        limits = [float(f[-1]) for f, _ in self.visible_spectra().values() if len(f) >= 2]
        if limits:
            self.fft_xmax = max(limits)
            self.fft_plot.setXRange(0, self.fft_xmax, padding=0)
            self.update_fft_range()

    def fft_peak_range(self):
        limits = []
        for f, s in self.visible_spectra().values():
            if len(f) < 2:
                continue
            df = f[1] - f[0]
            mask = (f >= df) & np.isfinite(s)
            if np.any(mask):
                f_peak = float(f[mask][np.argmax(s[mask])])
                limits.append(min(float(f[-1]), max(20 * df, 3 * f_peak)))
        if limits:
            self.fft_xmax = max(limits)
            self.fft_plot.setXRange(0, self.fft_xmax, padding=0)
            self.update_fft_range()

    def save_spectrum_image(self):
        log_dir = Path("images")
        log_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = log_dir / f"Spectrum_Plot_{stamp}.png"
        exporter = pyqtgraph.exporters.ImageExporter(self.fft_plot.plotItem)
        exporter.parameters()["width"] = 1600
        exporter.export(str(path))
        self.last_image_path = path
        self.update_summary()

    def spectrum_plot_mode(self, spec_vals):
        if self.spectrum_in_dbv:
            return spec_vals
        return 10 ** (spec_vals / 20)

    def change_spectrum_mode(self):
        self.spectrum_in_dbv = not self.spectrum_in_dbv
        unit = "dBV" if self.spectrum_in_dbv else "V"
        self.spectrum_mode_button.setText(f"Spectrum mode: {unit}")
        self.fft_plot.setLabel("left", "Amplitude", units=unit)
        for channel, (f, s) in self.last_spectra.items():
            self.fft_curves[channel].setData(f, self.spectrum_plot_mode(s))
        self.update_fft_range()

    def closeEvent(self, event):
        self.timer.stop()
        if self.recorder.is_recording:
            self.recorder.stop()
        self.scope.close()
        event.accept()
