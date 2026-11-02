import csv
from contextlib import contextmanager
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication

from gui import CHANNEL_COLORS, MainWindow
from main import make_scope
from logger import WaveformRecorder, prepare_spectrum
from processing import fft_dbv
from real_scope import Tektronix
from scope_simulator import ScopeSimulator
from scope_config import get_scope_resource, RESOURCE_PLACEHOLDER
from waveform_buffer import ChannelScope


@contextmanager
def export_directory():
    # Use inherited permissions; Windows' mode-0700 temp dirs can exclude the runner.
    directory = Path(__file__).resolve().parents[1] / f"test_exports_{uuid4().hex}"
    directory.mkdir()
    try:
        yield directory
    finally:
        for path in directory.iterdir():
            path.unlink()
        directory.rmdir()


class FakeInstrument:
    def __init__(self, running=True, fail_channel=None, empty_channel=None):
        self.running = running
        self.fail_channel = fail_channel
        self.empty_channel = empty_channel
        self.enabled = {"CH1": True, "CH2": True}
        self.source = "CH1"
        self.commands = []
        self.transfers = []
        self.closed = False

    def write(self, command):
        self.commands.append(command)
        if command.startswith("DATA:SOURCE "):
            self.source = command.split()[-1]
        elif command == "ACQUIRE:STATE STOP":
            self.running = False
        elif command == "ACQUIRE:STATE RUN":
            self.running = True
        elif command.startswith("SELECT:"):
            channel, state = command.removeprefix("SELECT:").split()
            self.enabled[channel] = state == "ON"

    def query(self, command):
        if command == "*IDN?":
            return "TEKTRONIX,TDS3052C,TEST,4.0"
        if command == "ACQUIRE:STATE?":
            return str(int(self.running))
        if command == "*OPC?":
            return "1"
        if command.startswith("SELECT:"):
            return str(int(self.enabled[command.removeprefix("SELECT:").rstrip("?")]))
        preamble = {
            "CH1": {"XINCR": 0.001, "YMULT": 0.1, "YOFF": 2, "YZERO": 0.5},
            "CH2": {"XINCR": 0.001, "YMULT": 0.5, "YOFF": -1, "YZERO": -0.2},
        }
        return str(preamble[self.source][command.removeprefix("WFMPRE:").rstrip("?")])

    def query_binary_values(self, command, **kwargs):
        if self.running:
            raise AssertionError("Channels must be transferred from a frozen acquisition")
        if self.source == self.fail_channel:
            raise OSError("Simulated transfer failure")
        self.transfers.append(self.source)
        if self.source == self.empty_channel:
            return np.array([], dtype=np.int8)
        return np.array([-2, 0, 2, 4], dtype=np.int8)

    def close(self):
        self.closed = True


class AcquisitionTests(unittest.TestCase):
    def make_scope(self, running=True, fail_channel=None, channels=("CH1", "CH2"), empty_channel=None):
        instrument = FakeInstrument(running, fail_channel, empty_channel)
        manager = Mock()
        manager.open_resource.return_value = instrument
        with patch("real_scope.pyvisa.ResourceManager", return_value=manager):
            scope = Tektronix("TCPIP::scope.example.test::INSTR", channels)
        self.addCleanup(scope.close)
        return scope, instrument

    def test_pair_uses_same_capture_with_independent_scaling_and_buffers(self):
        scope, instrument = self.make_scope()
        with patch("waveform_buffer.time.time", return_value=1700000000.0):
            frames = scope.read_waveforms()
        self.assertEqual(instrument.transfers, ["CH1", "CH2"])
        self.assertTrue(instrument.running)
        self.assertEqual(instrument.commands[-1], "ACQUIRE:STATE RUN")
        np.testing.assert_allclose(frames["CH1"][0], frames["CH2"][0])
        np.testing.assert_allclose(frames["CH1"][1], [0.1, 0.3, 0.5, 0.7])
        np.testing.assert_allclose(frames["CH2"][1], [-0.7, 0.3, 1.3, 2.3])
        self.assertEqual(scope.frame_timestamp, 1700000000.0)
        for channel in scope.channels:
            self.assertEqual(scope.buffers[channel].frames_for_fft, 1)
            np.testing.assert_array_equal(
                scope.get_whole_waveform(channel)[1], frames[channel][1]
            )
        self.assertIn("SELECT:CH2 ON", instrument.commands)
        self.assertFalse(any("SCALE" in cmd for cmd in instrument.commands))

    def test_stopped_instrument_stays_stopped(self):
        scope, instrument = self.make_scope(running=False)
        scope.read_waveforms()
        self.assertFalse(instrument.running)
        self.assertNotIn("ACQUIRE:STATE RUN", instrument.commands)

    def test_failed_second_transfer_restores_run_and_keeps_healthy_channel(self):
        scope, instrument = self.make_scope(fail_channel="CH2")
        frames = scope.read_waveforms()
        self.assertTrue(instrument.running)
        self.assertEqual(tuple(frames), ("CH1",))
        self.assertEqual(scope.buffers["CH1"].frames_for_fft, 1)
        self.assertEqual(scope.buffers["CH2"].frames_for_fft, 0)
        self.assertIn("CH2", scope.channel_errors)

    def test_empty_channel_is_skipped_and_can_recover(self):
        scope, instrument = self.make_scope(empty_channel="CH2")
        self.assertEqual(tuple(scope.read_waveforms()), ("CH1",))
        self.assertIn("CH2", scope.channel_errors)
        instrument.empty_channel = None
        self.assertEqual(tuple(scope.read_waveforms()), ("CH1", "CH2"))
        self.assertEqual(scope.channel_errors, {})

    def test_disabled_channel_does_not_block_other_channel(self):
        scope, instrument = self.make_scope()
        instrument.enabled["CH2"] = False
        self.assertEqual(tuple(scope.read_waveforms()), ("CH1",))
        self.assertEqual(instrument.transfers, ["CH1"])
        self.assertTrue(instrument.running)

    def test_sampling_interval_change_resets_only_affected_channel(self):
        scope = ChannelScope(("CH1", "CH2"))
        t = np.arange(4, dtype=float)
        scope.buffers["CH1"].append(t, np.ones(4))
        scope.buffers["CH2"].append(t, np.full(4, 2.0))
        scope.buffers["CH1"].append(2 * t, np.full(4, 3.0))
        self.assertEqual(scope.buffers["CH1"].resets, 1)
        self.assertEqual(scope.buffers["CH2"].resets, 0)
        np.testing.assert_array_equal(scope.buffers["CH1"].whole_wave, np.full(4, 3.0))
        np.testing.assert_array_equal(scope.buffers["CH2"].whole_wave, np.full(4, 2.0))

    def test_frame_limit_and_reset_apply_to_each_channel(self):
        scope = ChannelScope(("CH1", "CH2"))
        with patch("waveform_buffer.MAX_FRAMES", 2):
            for frame in range(3):
                for index, channel in enumerate(scope.channels):
                    scope.buffers[channel].append(
                        np.arange(4, dtype=float), np.full(4, 10 * index + frame)
                    )
        for index, channel in enumerate(scope.channels):
            buffer = scope.buffers[channel]
            self.assertEqual(buffer.frames_for_fft, 2)
            np.testing.assert_array_equal(
                buffer.whole_wave, np.repeat([10 * index + 1, 10 * index + 2], 4)
            )
        scope.reset_waveform()
        self.assertTrue(all(buf.frames_for_fft == 0 for buf in scope.buffers.values()))

    def test_simulator_advances_both_channels_by_one_frame(self):
        with patch("scope_simulator.SIM_N", 64):
            scope = ScopeSimulator()
            frames = scope.read_waveforms()
            self.assertEqual(scope.sample_index, 64)
            np.testing.assert_array_equal(frames["CH1"][0], frames["CH2"][0])
            self.assertFalse(np.array_equal(frames["CH1"][1], frames["CH2"][1]))

    def test_two_sample_zero_signal_produces_finite_spectrum(self):
        with np.errstate(all="raise"):
            f, s = fft_dbv(np.array([-1e-9, 0.0]), np.zeros(2))
        self.assertTrue(np.all(np.isfinite(f)))
        self.assertTrue(np.all(np.isfinite(s)))

    def test_factory_defaults_to_two_channels_and_supports_single_channel(self):
        for channels, expected in ((None, ("CH1", "CH2")), (["CH2"], ("CH2",))):
            scope, title = make_scope(Mock(mode="sim", channels=channels))
            self.assertEqual(scope.channels, expected)
            self.assertIn(" + ".join(expected), title)


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.frame_size = patch("scope_simulator.SIM_N", 1024)
        self.frame_size.start()
        self.addCleanup(self.frame_size.stop)
        self.window = MainWindow(ScopeSimulator(), "Dual channel test")
        self.window.timer.stop()
        self.addCleanup(self.window.close)

    def test_both_curves_colors_and_voltage_mode(self):
        for channel in ("CH1", "CH2"):
            for kind, curves in (("time", self.window.time_curves),
                                 ("fft", self.window.fft_curves)):
                curve = curves[channel]
                self.assertEqual(curve.opts["pen"].color().name(), CHANNEL_COLORS[channel][kind])
                self.assertGreater(len(curve.getData()[0]), 0)
            self.assertIn(f"=== {channel} ===", self.window.summary.toPlainText())
        self.window.change_spectrum_mode()
        for channel, (f, s) in self.window.last_spectra.items():
            np.testing.assert_allclose(self.window.fft_curves[channel].getData()[1], 10 ** (s / 20))
        self.window.fft_peak_range()
        self.window.fft_full_range()
        self.window.reset_spectrum()
        self.assertEqual(self.window.last_spectra, {})
        self.window.update_data()
        self.assertEqual(len(self.window.last_spectra), 2)

    def test_shared_recording_and_spectrum_exports_include_hidden_channel(self):
        with export_directory() as tmp, patch("logger.LOG_DIR", str(tmp)):
            self.window.channel_buttons["CH1"].click()
            self.window.toggle_recording()
            self.window.update_data()
            self.window.toggle_recording()
            self.window.save_spectrum()
            self.assertEqual(len(list(tmp.glob("Signal_Log_*.csv"))), 1)
            self.assertEqual(len(list(tmp.glob("Spectrum_Log_*.csv"))), 1)
            with self.window.last_record_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1024)
            for channel, (t, y) in self.window.last_waveforms.items():
                np.testing.assert_allclose(
                    [float(row[f"{channel}_frame_time_s"]) for row in rows], t
                )
                np.testing.assert_allclose(
                    [float(row[f"{channel}_voltage_v"]) for row in rows], y
                )
            self.assertEqual(len({row["pc_frame_time_unix_s"] for row in rows}), 1)
            self.assertEqual(len({row["frame_recording_time_s"] for row in rows}), 1)
            self.assertAlmostEqual(
                float(rows[-1]["pc_frame_time_unix_s"]),
                self.window.scope.frame_timestamp, places=5,
            )
            self.assertTrue(rows[-1]["pc_frame_time_utc"].endswith("+00:00"))
            with self.window.last_spec_path.open(newline="", encoding="utf-8") as handle:
                spectrum_rows = list(csv.DictReader(handle))
            for channel, (f, s) in self.window.last_spectra.items():
                np.testing.assert_allclose(
                    [float(row[f"{channel}_frequency_hz"]) for row in spectrum_rows], f
                )
                np.testing.assert_allclose(
                    [float(row[f"{channel}_amplitude_dbv"]) for row in spectrum_rows], s
                )
            self.assertFalse(self.window.time_plot.getAxis("top").isVisible())
            self.assertNotIn("PC frame time", self.window.summary.toPlainText())

    def test_view_buttons_change_both_plots_without_reacquiring(self):
        self.assertTrue(self.window.channel_buttons["both"].isChecked())
        for channel in self.window.channels:
            self.assertTrue(self.window.time_curves[channel].isVisible())
        with patch.object(self.window.scope, "read_waveforms", wraps=self.window.scope.read_waveforms) as read:
            for channel in self.window.channels:
                self.window.channel_buttons[channel].click()
                self.assertEqual(self.window.visible_channels, (channel,))
                for name in self.window.channels:
                    self.assertEqual(self.window.time_curves[name].isVisible(), name == channel)
                    self.assertEqual(self.window.fft_curves[name].isVisible(), name == channel)
                self.assertEqual(len(self.window.time_plot.plotItem.legend.items), 1)
                self.assertEqual(len(self.window.fft_plot.plotItem.legend.items), 1)
            self.window.channel_buttons["both"].click()
            self.assertEqual(self.window.visible_channels, self.window.channels)
            read.assert_not_called()

    def test_hidden_channel_does_not_determine_time_axis_range(self):
        t, y = self.window.last_waveforms["CH2"]
        self.window.last_waveforms["CH2"] = (t, 1000 * y)
        self.window.channel_buttons["both"].click()
        both_range = self.window.time_plot.viewRange()[1]
        self.window.channel_buttons["CH1"].click()
        selected_range = self.window.time_plot.viewRange()[1]
        self.assertGreater(both_range[1] - both_range[0],
                           100 * (selected_range[1] - selected_range[0]))

    def test_empty_channel_shows_message_clears_stale_data_and_recovers(self):
        original_read = self.window.scope.read_waveforms

        def missing_second_channel():
            frames = original_read()
            frames["CH2"] = (np.array([]), np.array([]))
            return frames

        with patch.object(self.window.scope, "read_waveforms", side_effect=missing_second_channel):
            self.window.update_data()
            self.assertIn("Нет данных на канале CH2", self.window.summary.toPlainText())
            self.assertIsNone(self.window.time_curves["CH2"].getData()[0])
            self.assertIsNone(self.window.fft_curves["CH2"].getData()[0])
            self.assertIn("CH1", self.window.last_spectra)
            self.window.channel_buttons["CH2"].click()
            self.window.change_spectrum_mode()
            self.window.fft_full_range()
            self.window.fft_peak_range()
        self.window.update_data()
        self.assertNotIn("Нет данных", self.window.summary.toPlainText())
        self.assertGreater(len(self.window.time_curves["CH2"].getData()[0]), 0)
        self.assertEqual(self.window.scope.buffers["CH2"].frames_for_fft, 1)

    def test_no_channels_and_constant_signals_are_handled(self):
        original_read = self.window.scope.read_waveforms

        def no_channels():
            self.window.scope.mark_frame_time()
            return {}

        with export_directory() as tmp, patch("logger.LOG_DIR", str(tmp)):
            self.window.toggle_recording()
            with patch.object(self.window.scope, "read_waveforms", side_effect=no_channels):
                self.window.update_data()
                for channel in self.window.channels:
                    self.assertIn(f"Нет данных на канале {channel}", self.window.summary.toPlainText())
                self.window.save_spectrum()
                self.window.reset_spectrum()
                self.window.change_spectrum_mode()
            self.window.toggle_recording()
            with self.window.last_record_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["CH1_voltage_v"], "")

        def zero_signals():
            frames = original_read()
            for channel, (t, y) in frames.items():
                y[:] = 0
                self.window.scope.reset_waveform(channel)
                self.window.scope.buffers[channel].append(t, y)
            return frames

        with patch.object(self.window.scope, "read_waveforms", side_effect=zero_signals):
            self.window.update_data()
        self.assertNotIn("Нет данных", self.window.summary.toPlainText())
        self.assertTrue(np.all(np.isfinite(self.window.fft_plot.viewRange())))

    def test_acquisition_error_is_reported_and_next_frame_can_recover(self):
        with patch.object(self.window.scope, "read_waveforms", side_effect=OSError("connection lost")):
            self.window.update_data()
        self.assertIn("connection lost", self.window.summary.toPlainText())
        self.assertEqual(self.window.last_waveforms, {})
        self.window.update_data()
        self.assertIsNone(self.window.acquisition_error)
        self.assertEqual(len(self.window.last_waveforms), 2)

    def test_grid_has_only_major_divisions_and_is_behind_data(self):
        for plot in (self.window.time_plot, self.window.fft_plot):
            for orientation in ("bottom", "left"):
                axis = plot.getAxis(orientation)
                self.assertGreater(axis.grid, 0)
                self.assertEqual(len(axis.tickValues(0, 10, 600)), 1)
                self.assertLess(axis.zValue(), plot.getViewBox().zValue())

    def test_single_channel_window(self):
        window = MainWindow(ScopeSimulator("CH2"), "Single channel test")
        try:
            window.timer.stop()
            self.assertEqual(tuple(window.time_curves), ("CH2",))
            self.assertEqual(tuple(window.fft_curves), ("CH2",))
        finally:
            window.close()


class ResourceConfigTests(unittest.TestCase):
    def test_environment_address_takes_priority_over_local_file(self):
        with export_directory() as tmp:
            (tmp / ".scope_resource").write_text("TCPIP::local.example.test::INSTR\n", encoding="utf-8")
            with patch("scope_config.__file__", str(tmp / "scope_config.py")), patch.dict(
                os.environ, {"OSCILLOSCOPE_RESOURCE": "TCPIP::env.example.test::INSTR"}
            ):
                self.assertEqual(get_scope_resource(), "TCPIP::env.example.test::INSTR")

    def test_local_file_keeps_existing_workstation_configuration(self):
        with export_directory() as tmp:
            (tmp / ".scope_resource").write_text("TCPIP::local.example.test::INSTR\n", encoding="utf-8")
            with patch("scope_config.__file__", str(tmp / "scope_config.py")), patch.dict(
                os.environ, {"OSCILLOSCOPE_RESOURCE": ""}
            ):
                self.assertEqual(get_scope_resource(), "TCPIP::local.example.test::INSTR")

    def test_missing_configuration_does_not_use_an_embedded_address(self):
        with export_directory() as tmp:
            with patch("scope_config.__file__", str(tmp / "scope_config.py")), patch.dict(
                os.environ, {"OSCILLOSCOPE_RESOURCE": ""}
            ):
                self.assertEqual(get_scope_resource(), RESOURCE_PLACEHOLDER)

    def test_placeholder_is_rejected_before_opening_a_connection(self):
        with patch("main.Tektronix") as open_scope:
            with self.assertRaises(SystemExit):
                make_scope(Mock(mode="tek", channels=None, resource=RESOURCE_PLACEHOLDER))
            open_scope.assert_not_called()


class RecorderTests(unittest.TestCase):
    def test_one_file_preserves_different_channel_times_lengths_and_missing_frames(self):
        with export_directory() as tmp, patch("logger.LOG_DIR", str(tmp)):
            recorder = WaveformRecorder()
            path = recorder.start()
            try:
                recorder.write({
                    "CH1": (np.array([-2e-9, -1e-9, 0.0]), np.array([0.0, 1.0, 2.0])),
                    "CH2": (np.array([-4e-9, 0.0]), np.array([3.0, 4.0])),
                }, frame_timestamp=1700000000.125, elapsed=1.25)
                recorder.write({}, frame_timestamp=1700000001.125, elapsed=2.25)
            finally:
                recorder.stop()
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 4)
            self.assertEqual(float(rows[0]["CH1_frame_time_s"]), -2e-9)
            self.assertEqual(float(rows[0]["CH2_frame_time_s"]), -4e-9)
            self.assertEqual(float(rows[0]["CH1_voltage_v"]), 0.0)
            self.assertEqual(rows[2]["CH2_voltage_v"], "")
            self.assertEqual(rows[2]["CH2_frame_time_s"], "")
            self.assertEqual(rows[3]["frame"], "1")
            self.assertEqual(rows[3]["sample"], "")
            self.assertEqual(rows[3]["CH1_voltage_v"], "")
            self.assertEqual(float(rows[3]["frame_recording_time_s"]), 2.25)
            self.assertEqual(len(list(tmp.glob("*.csv"))), 1)

    def test_spectrum_export_handles_different_frequency_arrays(self):
        with export_directory() as tmp, patch("logger.LOG_DIR", str(tmp)):
            path = prepare_spectrum({
                "CH1": (np.array([0.0, 10.0, 20.0]), np.array([-300.0, -10.0, -30.0])),
                "CH2": (np.array([0.0, 15.0]), np.array([-300.0, -20.0])),
            })
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(float(rows[1]["CH1_frequency_hz"]), 10.0)
            self.assertEqual(float(rows[1]["CH2_frequency_hz"]), 15.0)
            self.assertEqual(rows[2]["CH2_amplitude_dbv"], "")


if __name__ == "__main__":
    unittest.main()
