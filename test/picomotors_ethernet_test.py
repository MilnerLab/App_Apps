from pylablib.devices import Newport
#Use New Focus picomotor app to find controller IP address (by selecting "properties" from the right click menu)
controller1 = Newport.Picomotor8742("10.1.137.239")  

print('Found picomotor controller with these parameters:',controller1.get_full_info())
print('Which has axes',controller1.get_all_axes())

active_axis = 2

step = -50

print('Moving axis',active_axis, 'by',step,'steps.')

controller1.move_by(axis=active_axis,steps=step)

controller1.close()

