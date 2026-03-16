# test_device_controller.py
import asyncio
from src.device_controller import get_device_controller
from src.enums.modbus_def import ProtocolType

async def main():
    print("Initializing device controller...")
    dc = await get_device_controller()
    
    # Create an IEC61850 server device
    device_id = dc.add_device(
        name="TestIEC",
        ip="0.0.0.0",
        port=102,
        protocol_type=ProtocolType.Iec61850Server
    )
    print(f"Added device {device_id}")
    
    # Add a point to the device
    dc.add_point_dynamic(device_id, 1, 0, {
        "channel_id": 1,
        "name": "Test Point"
    })
    
    print("Starting device...")
    success = await dc.start_device(device_id)
    print(f"Device started: {success}")
    
    print("Waiting 5 seconds...")
    await asyncio.sleep(5)
    
    print("Checking status...")
    status = dc.get_device_status(device_id)
    print(f"Device status: {status}")
    
    print("Done")

if __name__ == "__main__":
    asyncio.run(main())
