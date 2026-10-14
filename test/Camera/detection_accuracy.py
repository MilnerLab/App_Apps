# Accuracy test of ion_detection.py 
import csv  
import numpy as np

real = []
detect = []

generated_ion_file = "C:\\Git\\MilnerLab\\App_Apps\\test\\Camera\\real_positions.csv"
detection_file = "C:\\Git\\MilnerLab\\App_Apps\\test\\Camera\\detected_positions.csv"

with open(detection_file,'r',newline='') as f:
    r = csv.reader(f)
    for row in r:
        detect.append(row)
        
detect_arr = np.array(detect)

with open(generated_ion_file,'r',newline='') as f:
    r = csv.reader(f)
    for row in r:
        real.append(row) 

real_arr = np.array(real)

frames_r = []
n_r = len(real)
n_d = len(detect)
i = 0
j = 0
a = []
while i <= n_r-1: 
    if j == real[i][2]:
        a.append(real[i])
    else:
        j += 1
        frames_r.append(a)
        a = []
    i += 1
    
print(type(frames_r))
    
    
    