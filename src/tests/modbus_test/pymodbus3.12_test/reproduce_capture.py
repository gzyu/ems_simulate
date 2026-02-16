
import asyncio
import logging
import sys
from pymodbus.server import ModbusTcpServer
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusDeviceContext, ModbusServerContext
from pymodbus.client import AsyncModbusTcpClient
from src.proto.pyModbus.server.capture import CreateCaptureSocketFramer
from src.device.core.message.message_capture import MessageCapture
from pymodbus.framer import FRAMER_NAME_TO_CLASS

# Setup logging
logging.basicConfig()
log = logging.getLogger()
log.setLevel(logging.DEBUG)

async def run_server(stop_event, message_capture):
    # Setup server context
    store = ModbusDeviceContext(
        di=ModbusSequentialDataBlock(0, [17]*100),
        co=ModbusSequentialDataBlock(0, [17]*100),
        hr=ModbusSequentialDataBlock(0, [17]*100),
        ir=ModbusSequentialDataBlock(0, [17]*100))
    context = ModbusServerContext(devices=store, single=True)

    # Setup capture framer
    framer_cls = CreateCaptureSocketFramer(message_capture)
    framer_key = f"CAPTURE_SOCKET_TEST"
    FRAMER_NAME_TO_CLASS[framer_key] = framer_cls

    server = ModbusTcpServer(address=("127.0.0.1", 5020), context=context, framer=framer_key)
    
    print("Server starting...")
    server_task = asyncio.create_task(server.serve_forever())
    
    await stop_event.wait()
    print("Server stopping...")
    await server.shutdown()
    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass

async def run_client():
    await asyncio.sleep(1) # Wait for server to start
    client = AsyncModbusTcpClient("127.0.0.1", port=5020)
    await client.connect()
    print("Client connected, reading holding registers...")
    rr = await client.read_holding_registers(0, 1)
    print(f"Client received: {rr}")
    client.close()

async def main():
    message_capture = MessageCapture()
    stop_event = asyncio.Event()
    
    server_task = asyncio.create_task(run_server(stop_event, message_capture))
    await run_client()
    
    stop_event.set()
    await server_task
    
    print(f"Captured messages count: {len(message_capture.message_list)}")
    for msg in message_capture.message_list:
        print(f"Captured: {msg}")

    if len(message_capture.message_list) > 0:
        print("PASS: Messages captured.")
    else:
        print("FAIL: No messages captured.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
