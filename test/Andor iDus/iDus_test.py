import pylablib as pll
import time
#pll.par["devices/dlls/andor_sdk2"] = "path/to/dlls" 
#According to https://pylablib.readthedocs.io/en/latest/devices/Andor.html it searches the default directories for the .dll
from pylablib.devices import Andor
#cam = Andor.AndorSDK2Camera()
num = Andor.get_cameras_number_SDK2()
print('cameras',num)

RamanCamera = Andor.AndorSDK2Camera(idx=0,fan_mode = "low") #indexing would only be needed if we connect multiple Andor cameras


RamanCamera.set_temperature(-30) 

try:
    while True:
        print("Temperature = ",RamanCamera.get_temperature(), "C")
        print("Click on console and press ctrl-c to end temperature spam.")
        time.sleep(1)
except KeyboardInterrupt:
    print("Camera closing...")
    RamanCamera.close() #end the connection when done :) 
    print("Camera closed!")


print("After!")    


