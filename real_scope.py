import numpy as np
import pyvisa

from waveform_buffer import ChannelScope, ChannelUnavailableError, validate_waveform


class Tektronix(ChannelScope):
    def __init__(self, resource, channel=("CH1", "CH2")):
        super().__init__(channel)
        self.rm = pyvisa.ResourceManager()
        self.scope = self.rm.open_resource(resource)
        self.scope.timeout = 10000
        self.scope.write_termination = "\n"
        self.scope.read_termination = "\n"
        print(self.scope.query("*IDN?").strip())
        self.configure()

    def configure(self):
        self.scope.write("HEADER OFF")
        # Undisplayed channels cannot be transferred on the TDS3000 series.
        for channel in self.channels:
            self.scope.write(f"SELECT:{channel} ON")
        self.scope.write(f"DATA:SOURCE {self.channel}")
        self.scope.write("DATA:START 1")
        self.scope.write("DATA:STOP 10000")
        self.scope.write("DATA:WIDTH 1")
        self.scope.write("DATA:ENCDG RIBINARY")

    def read_channel(self, channel):
        if not int(self.scope.query(f"SELECT:{channel}?").strip()):
            raise ChannelUnavailableError("Channel is disabled on the instrument")
        self.scope.write(f"DATA:SOURCE {channel}")
        # Each channel has independent voltage scaling and offset.
        xincr = float(self.scope.query("WFMPRE:XINCR?"))
        ymult = float(self.scope.query("WFMPRE:YMULT?"))
        yoff = float(self.scope.query("WFMPRE:YOFF?"))
        yzero = float(self.scope.query("WFMPRE:YZERO?"))
        raw = self.scope.query_binary_values(
            "CURVE?",
            datatype="b",
            container=np.array,
            header_fmt="ieee",
        )
        if len(raw) < 2:
            raise ChannelUnavailableError("No waveform data")

        y = (raw.astype(np.float64) - yoff) * ymult + yzero
        # Both channels share the time base; keep the existing right edge at t=0.
        t = (np.arange(len(y), dtype=np.float64) - len(y) + 1) * xincr
        return validate_waveform(t, y)

    def read_waveforms(self):
        was_running = bool(int(self.scope.query("ACQUIRE:STATE?").strip()))
        try:
            if was_running:
                # Freeze one acquisition so CH1 and CH2 belong to the same capture.
                self.scope.write("ACQUIRE:STATE STOP")
                self.scope.query("*OPC?")
            self.mark_frame_time()
            waveforms = {}
            self.channel_errors = {}
            for channel in self.channels:
                try:
                    waveforms[channel] = self.read_channel(channel)
                except (ValueError, OSError, pyvisa.errors.Error) as error:
                    self.mark_channel_unavailable(channel, str(error))
        finally:
            # Also restore acquisition if a channel transfer raises an exception.
            if was_running:
                self.scope.write("ACQUIRE:STATE RUN")

        # Preserve every available channel, even when another has no usable data.
        for channel, (t, y) in waveforms.items():
            self.buffers[channel].append(t, y)
        return waveforms

    def close(self):
        self.scope.close()
        self.rm.close()
