import serial
#From https://github.com/hrosiak/esp301
#We also looked at https://pypi.org/project/NewportESP/, which is much more complete, but doesn't look finished so this seemed simpler.
class esp:
	def __init__(self, dev="/dev/ttyUSB0", b=19200,axis=1,reset=True, initpos = 0.0,useaxis=[]):
		self.dev = serial.Serial(dev,b)
		self.inuse = useaxis
		if(len(self.inuse)==0):
			self.inuse = [axis]
		self.defaxis = axis
		if(reset):
			for n in self.inuse:
				self.reset(n)
				r = self.check_errors()
				if(r!=0):
					print("Error while setting up controller, error # %d"%r)
				if(initpos!=0):
					self.setpos(initpos)
					r = self.check_errors()
					if(r!=0):
						print("Error while setting up controller, error # %d"%r)

	def reset(self,axis):
		self.dev.write(b"%dOR;%dWS0\r"%(axis,axis))
	
	def check_errors(self):
		self.dev.write(b"TE?\r")
		return float(self.dev.readline())

	def getpos(self,axis=None):
		a = self.defaxis
		if(axis and axis>0):
			a = axis
		self.dev.write(b"%dTP\r"%a)
		return float(self.dev.readline())
	
	def setpos(self,pos,axis=None):
		a = self.defaxis
		if(axis and axis>0):
			a = axis
		print("setting to %f"%pos)
		self.dev.write(b"%dPA%.4f;%dWS1;%dTP\r"%(a,pos,a,a))
		return float(self.dev.readline())

	def position(self,pos=None,axis=None):
		if(isinstance(pos,(float,int))):
			self.setpos(pos,axis)
			self.getpos()
			self.setpos(pos,axis)
		return self.getpos(axis)

#Running it...
controller = esp("COM3",b=19200,axis=1,reset=False,initpos=0.0) #ESP100 with MFA-CC goes to 0 when resetting, before going to initpos
controller.setpos(-18.0,1)

#This works with the ESP100 and UTS150CC!
#In Labview, we always started with
#AP;QP;1MO;1SN2;1OH20
#where 1 is the axis to move.
#followed by 1VAXX where XX is the target velocity in mm/second
#followed by 1PAXX where XX is the target position

#Other useful commands are 1OR2 for homing it and MF to turn the motor off.