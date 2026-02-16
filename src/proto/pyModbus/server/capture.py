from pymodbus.framer import FramerSocket, FramerRTU

def CreateCaptureSocketFramer(message_capture):
    class CaptureSocketFramer(FramerSocket):
        def decode(self, data):
            if data and message_capture:
                message_capture.add_rx(data)
            return super().decode(data)

        def buildFrame(self, message):
            data = super().buildFrame(message)
            if data and message_capture:
                message_capture.add_tx(data)
            return data
    return CaptureSocketFramer

def CreateCaptureRtuFramer(message_capture):
    class CaptureRtuFramer(FramerRTU):
        def decode(self, data):
            if data and message_capture:
                message_capture.add_rx(data)
            return super().decode(data)

        def buildFrame(self, message):
            data = super().buildFrame(message)
            if data and message_capture:
                message_capture.add_tx(data)
            return data
    return CaptureRtuFramer
