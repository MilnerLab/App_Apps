import pyvisa
import matplotlib.pyplot as plt
import numpy as np
rm = pyvisa.ResourceManager()
test = rm.list_resources()
print('pyvisa found:',test)

LAKESHORE_PORT = 'ASRL1:INSTR'

#UNTESTED BUT MIGHT WORK :)


LS325 = rm.open_resource(LAKESHORE_PORT,write_termination = '\n',baudrate = 9600)

P = 57.5
I = 120.0
D = 50.0
LS325.write('PID 2,P,I,D;MOUT 2,0.000;HTRRES 2,2;RANGE 2,1') #set PID parameters

TEMPSET = 18.000
LS325.write('SETP 2,' + str(TEMPSET))

T1 = LS325.query('KRDG?A')
print('Temp 1 is ',T1,' K')
T2 = LS325.query('KRDG?B')
print('Temp 2 is ',T2,' K')



LS325.close()

#---------------------------------------------------------------------------------------------------------------------------------------------------------------
#---------------------------------------------------------------------------------------------------------------------------------------------------------------
#---------------------------------------------------------------------------------------------------------------------------------------------------------------
'''
Lakeshore LS325 config:`
baud rate 9600
flow control none
parity odd
data bits 7
stop bits 1
termination character line feed
buffer size 4096

Useful commands:
*IDN? 
which should return
MODEL325

*RST
which will reset the device

*ESE 52;OPSTE 195;*SRE 160;*CLS
which will set the device to default settings

KRDG?A
gets the temperature reading of sensor A in kelvin

SETP 2, 18.0000
sets the temperature setpoint to 18.000 K


PID 2,57.5,120.0,50.0;MOUT 2,0.000;HTRRES 2,2;RANGE 2,1
is a sample of manual PID setup...
PID X1,X2,X3,X4;MOUT X1,X5;HTRRES X1,X6;RANGE X1,X7
are the variables
X1 = loop # to control / adjust

X2 = Gain (P)
X3 = Reset (I)
X4 = Rate (D)

X5 = Manual heater output power 
X6 = heater resistance (1 = 25 ohm, 2 = 50 ohm)
X7 = heater range (0 = off, 1 = low, 2 = high)


'''