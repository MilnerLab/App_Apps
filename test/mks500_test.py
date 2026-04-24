import serial

#Parameters
COM_PORT = 'COM3' 

#%%
#Set up serial port
MKS500 = serial.Serial()
MKS500.baudrate = 9600 #The baud rate can be set in GPCONNECT. Some are 9600, some are 115200. Other port settings can be left as default. 
MKS500.port = COM_PORT 
MKS500.open()
print('MKS 500 is connected?',MKS500.is_open) #This doesn't ac

MKS500.write(b'HELP\r') 
reading = MKS500.read(409) #The help command returns all possible commands, 409 bytes seems correct.
print(str(reading))

#Could use RU command to get pressure units, or SU to force it. It seems rare that anyone would change the units from Torr. 


pressure_string = MKS500.write(b'RU\r') #RD is the command to read the gauge, \r is a carriage return needed to terminate the MKS500 commands.



MKS500.close()
