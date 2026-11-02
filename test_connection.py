import pyvisa
from scope_config import DEFAULT_RESOURCE, RESOURCE_PLACEHOLDER


resource = DEFAULT_RESOURCE
if not resource or resource == RESOURCE_PLACEHOLDER:
    raise SystemExit("Set OSCILLOSCOPE_RESOURCE or create a local .scope_resource file")

rm = pyvisa.ResourceManager()
print("VISA:", rm.visalib)
print("Resources:", rm.list_resources())

scope = rm.open_resource(resource)
scope.timeout = 10000
scope.write_termination = "\n"
scope.read_termination = "\n"

print(scope.query("*IDN?"))

scope.close()
rm.close()
