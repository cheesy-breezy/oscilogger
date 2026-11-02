# параметры симулятора осциллографа
SIM_FS = 1000000 # частота дискретизации, dt = 1 мкс
SIM_N = 2**20 # число точек в одном vawefor, длина временного окна примерно 1.05 с 

# параметры основного сигнала
F_LEAD = 1.0 # Hz
A_LEAD = 1.0 # v

# параметры паразитного сигнала
F_EVIL = 150000.0 # Hz
A_EVIL = 0.001 # V

# шум
NOISE_RMS = A_EVIL / 10

# параметры отрисовки GUI
UPDATE_MS = 500 # частота обновления окна в мс

# AUTO_TIME_X = True
# AUTO_TIME_Y = True
# AUTO_FFT_X = True
# AUTO_FFT_Y = True
FFT_Y_MARGIN_DB = 10

# logging info
LOG_DIR = "Signal Logs"
MAX_WAVE_POINTS = 1000000
MAX_FRAMES = 100