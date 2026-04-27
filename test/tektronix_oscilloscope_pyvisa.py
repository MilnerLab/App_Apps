import pyvisa
import matplotlib.pyplot as plt
import numpy as np
rm = pyvisa.ResourceManager()
test = rm.list_resources()
print('pyvisa found:',test)

SCOPE_USB_PORT = 'USB0::0x0699::0x03A3::C015100::INSTR'

osc = rm.open_resource(SCOPE_USB_PORT,send_end=True)

osc.clear()

print('Selected instrument:',osc.query('*IDN?'))
#print('Data Format before:' , osc.query('DATA?'))
#osc.write('DATA INIT')
#print('Data Format after INIT:' , osc.query('DATA?'))

osc.write('ACQ:STOPA RUNST;')
osc.write('ACQ:STATE ON;')

FORMAT_COMMAND = ':DATA:SOURCE:CH1;:DATA:ENC RPB;WID 2;:WFMPRE:XZE?;XIN?;YZE?;YMU?;YOFF?;' #The one Ian used in Labview
osc.write('DATA:SOURCE:CH1')
osc.write(':DATA:ENC RIB')
osc.write('WID 2')
print('Data format set to:' , osc.query('DATA?'))


XZE = float(osc.query('WFMPRE:XZE?')) #XZE is the time of the first data point in the waveform
XIN = float(osc.query('WFMPRE:XIN?')) #XIN is horizontal sampling interval
YZE = float(osc.query('WFMPRE:YZE?')) #YZE is waveform conversion factor
YMU = float(osc.query('WFMPRE:YMU?')) #YMU is vertical scale factor
YOFF = float(osc.query('WFMPRE:YOFF?')) #YOFF is the vertical position


#print(response)
values = osc.query_binary_values('CURV?',datatype='b',is_big_endian = True) #Not certain this is correct
values = np.array(values)
print('First raw value is ',values[0],' out of ',len(values),' total points.')
values = (values - YOFF)*YMU + YZE


time = XZE + np.linspace(0,len(values)*XIN,len(values))

testfig,ax = plt.subplots(1)
ax.plot(time,values)
ax.set_xlabel('Time (seconds)')
ax.set_ylabel('Amplitude (volts)')
plt.show()
osc.close()