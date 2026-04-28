#https://pypi.org/project/newportxps/

from newportxps import NewportXPS

IP = '10.1.137.137'
#This only works with administrator accounts on the XPS for some reason. 
xps = NewportXPS(IP, username='PyControl', password='labview2python',port=5001)


print(xps.status_report())