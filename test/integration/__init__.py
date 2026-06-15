"""In-process closed-loop integration tests (mock optical plant <-> control loops).

These exercise the whole software chain together — a free-running mock spectrometer whose
emitted spectrum responds to actuator motion, through the real shared-memory handshake, the
real lmfit spectrum fit, the real PID engine, and the real control-loop routines. The only
fake is the physical world itself (`OpticalPlant`). See `optical_plant.py`.
"""
