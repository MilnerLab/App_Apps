# Project Setup & Useful Commands

This repository contains the code for running the Phase Control application.  
Below is a quick reference for setting up the environments and running the programs.

---

## 1. Virtual Environment

### 1.1 Creates / checks / activates environments

Use the provided PowerShell script:

```powershell
. .\setup_env.ps1
```

# 2. Installing PySpin (for FLIR cameras)
https://www.teledynevisionsolutions.com/support/support-center/technical-guidance/iis/installing-pyspin-for-the-spinnaker-sdk/#anchor1
Note that it needs Python 3.10 (as of 2026/03/24)
On 2026 OnlyPy (Antec) PC, the .whl file is in C:\pyspinfiles

## 3. Running the Applications

All applications are started via Python’s module syntax from the repository root.


```powershell
python -m app
```

