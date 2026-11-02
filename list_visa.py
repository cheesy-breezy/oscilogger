import pyvisa

rm = pyvisa.ResourceManager()
print(rm.visalib)
print(rm.list_resources())