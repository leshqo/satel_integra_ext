import asyncio
import logging
import random
from satel_integra import SatelCommand, SatelMessage, partition_bytes, AlarmState

logging.basicConfig(level=logging.DEBUG,format='%(asctime)s %(levelname)5s %(name)s - %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

RESPONSE_OK = SatelMessage(SatelCommand.RESULT, bytearray(b'\x00'))
RESPONSE_DEVICE_INFO = SatelMessage(SatelCommand.DEVICE_INFO, bytearray(
    b'\x01\x01\x00\x52\x75\x63\x68\x20\x77\x69\x61\x74\x72\x6f\x6c\x61\x70\x20\x20'))

ALARMSTATE_TO_CMD = {
    AlarmState.ARMED_MODE0: SatelCommand.ARMED_MODE0,
    AlarmState.ARMED_MODE1: SatelCommand.ARMED_MODE1,
    AlarmState.ARMED_MODE2: SatelCommand.ARMED_MODE2,
    AlarmState.ARMED_MODE3: SatelCommand.ARMED_MODE3,
}

class SatelIntegraEmulator:
    """
        Basic simulator of Satel Integra. It properly responds to satel commands and notifies
        connected clients about state changes.
        Switchable outputs and state of partitions are stored in-memory.
        Arming is instant (no delay).
        Temperature sensors are based on random numbers.
        Inputs and non-switchable outputs are always off.
    """

    def __init__(self, host='127.0.0.1', port=7094):
        self.host = host
        self.port = port
        self.clients = set()
        self.zones = {i: False for i in range(1, 33)}  # 32 zones
        self.outputs = {i: False for i in range(1, 33)}  # 32 outputs
        self.partitions = {i: AlarmState.DISARMED for i in range(1, 5)}  # 4 partitions

    async def handle_client(self, reader, writer):
        addr = writer.get_extra_info('peername')
        logger.info(f"New connection from {addr}")
        self.clients.add(writer)
        try:
            while True:
                data = await reader.readuntil(b'\xFE\x0D')
                if not data:
                    break
                message = SatelMessage.decode_frame(data)
                logger.debug(f"Received from {addr}: {message}")
                await self.process_command(message, writer)
        except:
            pass
        finally:
            self.clients.remove(writer)
            writer.close()
            logger.info(f"Connection closed for {addr}")

    def send_response(self, message: SatelMessage, client=None):
        frame = message.encode_frame()
        for writer in ([client] if client else self.clients):
            writer.write(frame)
            addr = writer.get_extra_info('peername')
            logger.debug(f"Sent to {addr}: {message}")

    async def process_command(self, message, writer):
        try:
            if message.cmd == SatelCommand.CMD_START_MONITORING:
                await self.start_monitoring(message, writer)
            elif message.cmd == SatelCommand.DEVICE_INFO:
                await self.device_info(message, writer)
            elif message.cmd in [SatelCommand.CMD_ARM_MODE_0, SatelCommand.CMD_ARM_MODE_1,
                                 SatelCommand.CMD_ARM_MODE_2, SatelCommand.CMD_ARM_MODE_3]:
                await self.arm(message)
            elif message.cmd == SatelCommand.CMD_DISARM:
                await self.disarm(message)
            elif message.cmd in [SatelCommand.CMD_OUTPUT_ON, SatelCommand.CMD_OUTPUT_OFF]:
                await self.set_output(message)
            elif message.cmd == SatelCommand.CMD_READ_ZONE_TEMP:
                await self.read_temperature(message, writer)
            else:
                logger.warning(f"Unknown command: {message.cmd}")
        except Exception as e:
            logger.error(f"Error processing command", exc_info=e)

    async def start_monitoring(self, message, writer):
        self.send_response(RESPONSE_OK, writer)
        self.broadcast_outputs()
        self.broadcast_partitions(AlarmState.ARMED_MODE0)

    async def device_info(self, message, writer):
        self.send_response(RESPONSE_DEVICE_INFO, writer)

    async def arm(self, message):
        partitions = message.list_set_bits(8, 4)  # skipping 8 bytes of the code
        for partition in partitions:
            self.partitions[partition] = AlarmState.ARMED_MODE0
        self.send_response(RESPONSE_OK)
        self.broadcast_partitions(AlarmState.ARMED_MODE0)

    async def disarm(self, message):
        partitions = message.list_set_bits(8, 4)  # skipping 8 bytes of the code
        for partition in partitions:
            self.partitions[partition] = AlarmState.DISARMED
        self.send_response(RESPONSE_OK)
        self.broadcast_partitions(AlarmState.ARMED_MODE0)

    async def set_output(self, message):
        outputs = message.list_set_bits(8, 32)  # skipping 8 bytes of the code
        state = message.cmd == SatelCommand.CMD_OUTPUT_ON
        for output in outputs:
            self.outputs[output] = state
        logger.info("Setting outputs %s to %s", str(outputs), str(state))
        self.send_response(RESPONSE_OK)
        self.broadcast_outputs()

    async def read_temperature(self, message, writer):
        zone = message.msg_data[0]
        temp = random.uniform(21, 24)
        response = SatelMessage(SatelCommand.ZONE_TEMP, bytearray([zone, 0, int((temp + 55) * 2)]))
        self.send_response(response, writer)

    def broadcast_outputs(self):
        enabled_outputs = [i for i, state in self.outputs.items() if state]
        message = SatelMessage(SatelCommand.OUTPUT_STATE, outputs=enabled_outputs)
        self.send_response(message)

    def broadcast_partitions(self, state: AlarmState):
        partitions = [i for i, part_state in self.partitions.items() if part_state==state]
        message = SatelMessage(ALARMSTATE_TO_CMD[state], partitions=partitions)
        self.send_response(message)

    async def notify_state_change(self, command, changed_items):
        data = partition_bytes(changed_items, 4)  # 4 bytes for up to 32 items
        message = SatelMessage(command, data)
        encoded_message = message.encode_frame()

        for client in self.clients:
            try:
                client.write(encoded_message)
                await client.drain()
            except Exception as e:
                logger.error(f"Error notifying client: {e}")

    async def run(self):
        server = await asyncio.start_server(self.handle_client, self.host, self.port)
        addr = server.sockets[0].getsockname()
        logger.info(f'Serving on {addr}')
        async with server:
            await server.serve_forever()

if __name__ == "__main__":
    emulator = SatelIntegraEmulator()
    asyncio.run(emulator.run())
