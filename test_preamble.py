import pyvisa
from scope_config import DEFAULT_RESOURCE, RESOURCE_PLACEHOLDER


def log(text):
    print(text, flush=True)


resource = DEFAULT_RESOURCE
if not resource or resource == RESOURCE_PLACEHOLDER:
    raise SystemExit("Set OSCILLOSCOPE_RESOURCE or create a local .scope_resource file")

log("start")

rm = pyvisa.ResourceManager()
log(f"visa: {rm.visalib}")

scope = rm.open_resource(resource)
log("opened")

scope.timeout = 3000
scope.write_termination = "\n"
scope.read_termination = "\n"

log("idn query")
print(scope.query("*IDN?").strip(), flush=True)

try:
    scope.clear()
    log("clear ok")
except Exception as e:
    log(f"clear failed: {type(e).__name__}: {e}")

commands = [
    "HEADER OFF",
    "DATA:SOURCE CH1",
    "DATA:START 1",
    "DATA:STOP 10000",
    "DATA:WIDTH 1",
    "DATA:ENCDG RIBINARY",
]

for cmd in commands:
    log(f"write: {cmd}")
    scope.write(cmd)

queries = [
    "WFMPRE?",
    "WFMPRE:XINCR?",
    "WFMPRE:XZERO?",
    "WFMPRE:YMULT?",
    "WFMPRE:YOFF?",
    "WFMPRE:YZERO?",
    "WFMPRE:NR_PT?",
    "WFMPRE:BYT_NR?",
    "WFMOUTPRE?",
    "WFMOUTPRE:XINCR?",
]

for q in queries:
    log(f"query: {q}")
    try:
        ans = scope.query(q).strip()
        log(ans)
    except Exception as e:
        log(f"{type(e).__name__}: {e}")

scope.close()
rm.close()

log("done")
