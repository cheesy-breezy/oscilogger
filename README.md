

Create the environment and install dependencies:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Run the simulator with CH1 and CH2:

```powershell
python main.py --mode sim
```

For the instrument, replace the placeholder `0.0.0.0` with its address:

```powershell
$env:OSCILLOSCOPE_RESOURCE = "TCPIP::0.0.0.0::INSTR"
python main.py
```

You can also pass the resource explicitly:

```powershell
python main.py --mode tek --resource "TCPIP::0.0.0.0::INSTR" --channels CH1 CH2
```

Configuration priority is `--resource`, then `OSCILLOSCOPE_RESOURCE`, then a
local `.scope_resource` file containing the VISA resource string. That local
file is excluded from Git, along with recordings, exports and environments.
The connection diagnostic scripts use the same environment variable/local file.
The placeholder is rejected until an actual resource is configured.

For a single channel, use `--channel CH1` or `--channel CH2`.

Both panels overlay two traces and include channel legends. CH1 is light blue
in the waveform panel and blue in the spectrum panel; CH2 is yellow and orange.
The buttons above the plots switch between both channels, CH1 and CH2. The
default view shows both. These buttons change the visible traces and their axis
ranges; acquisition, FFT buffers and recording continue for both channels.
Both plots have a background grid at the major axis divisions only.

If a channel is disabled, returns no samples or its transfer fails, the panel
shows a message identifying that channel. Its old traces are cleared and the
other channel continues working. A constant or noisy signal remains valid data;
the application does not guess whether a probe is connected from its amplitude.

The waveform axis shows seconds within the frame, ending at zero.
Absolute PC time is saved only in signal logs, not displayed in the Qt panel.
One PC timestamp is assigned to each frame before the channel transfers. It is
a receipt-time reference, not an instrument acquisition timestamp. Clock
synchronization and trigger/transfer delays limit its absolute accuracy. More
decimal places do not improve that accuracy, and hardware timing support would
be needed for a precise absolute acquisition time. The sample intervals within
each channel's frame retain their original precision. The application uses the
PC clock, including any synchronization configured in the OS.

Each recording creates one `Signal_Log_*.csv` containing both channels. Columns:

- `frame`, `sample`: frame number and sample index.
- `frame_recording_time_s`: the shared frame reference on a monotonic clock,
  measured from recording start.
- `pc_frame_time_unix_s`, `pc_frame_time_utc`: the common PC frame timestamp
  (Unix seconds and ISO 8601 in UTC), repeated for the samples in that frame.
- `CH1_frame_time_s`, `CH1_voltage_v`, `CH2_frame_time_s`, `CH2_voltage_v`:
  the independent relative times and voltages of both channels.

Rows pair samples by index, not by interpolating their times. If a channel has
fewer samples or no data, its cells are empty. If both channels lack data, one
row retains the frame reference with empty channel cells. Hidden channels remain
in the recording. Existing CSV files are left untouched.

Spectrum export also creates one CSV, with separate frequency/amplitude columns
for CH1 and CH2. Reset and the dBV/V switch apply to both FFT buffers.

For the TDS3000 series, the program enables the selected channels and briefly
stops a running acquisition while it transfers both waveforms. It restores RUN
afterward, including on transfer failure, and leaves an already stopped scope
stopped. Transfer requests are sequential; they read the same frozen acquisition.
No time-base, volts/div or trigger-parameter controls are added.

Run the tests without connecting an instrument:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```
