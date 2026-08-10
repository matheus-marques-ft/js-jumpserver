from ..base.device import Device


class PiicoDevice(Device):
    name = "piico"

    def __init__(self):
        self.open()

    # Default to searching the lib path
    def open(self, driver_path="libpiico_ccmu.so"):
        super().open(driver_path)