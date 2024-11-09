# -*- coding: utf-8 -*-

"""Main module."""

import asyncio
import collections
import logging
from binascii import hexlify
from enum import Enum, unique

_LOGGER = logging.getLogger(__name__)


def checksum(command):
    """Function to calculate checksum as per Satel manual."""
    crc = 0x147A
    for b in command:
        # rotate (crc 1 bit left)
        crc = ((crc << 1) & 0xFFFF) | (crc & 0x8000) >> 15
        crc = crc ^ 0xFFFF
        crc = (crc + (crc >> 8) + b) & 0xFFFF
    return crc


def partition_bytes(partition_list, length):
    ret_val = 0
    for position in partition_list:
        if position > length * 8:
            raise IndexError()
        ret_val = ret_val | (1 << (position - 1))

    return ret_val.to_bytes(length, 'little')


def code_bytes(code):
    return bytearray.fromhex(code.ljust(16, 'F'))


class SatelCommand(Enum):
    ZONE_VIOLATED   = (0x00,)
    ARMED_MODE0     = (0x0A,)
    ARMED_MODE1     = (0x2A,)
    ARMED_MODE2     = (0x0B,)
    ARMED_MODE3     = (0x0C,)
    ARMED_SUPPRESSED = (0x09,)
    ENTRY_TIME      = (0x0E,)
    EXIT_COUNTDOWN_OVER_10 = (0x0F,)
    EXIT_COUNTDOWN_UNDER_10 = (0x10,)
    RTC_AND_STATUS  = (0x1A,)
    DEVICE_INFO     = (0xEE,)
    RESULT          = (0xEF,)
    TRIGGERED       = (0x13,)
    TRIGGERED_FIRE  = (0x14,)
    OUTPUT_STATE    = (0x17,)
    DOORS_OPENED    = (0x18,)
    ZONES_BYPASSED  = (0x06,)
    INTEGRA_VERSION = (0x7E,)
    ZONE_TEMP       = (0x7D,)

    CMD_ARM_MODE_0  = (0x80, True)
    CMD_ARM_MODE_1  = (0x81, True)
    CMD_ARM_MODE_2  = (0x82, True)
    CMD_ARM_MODE_3  = (0x83, True)
    CMD_DISARM      = (0x84, True)
    CMD_CLEAR_ALARM = (0x85, True)
    CMD_ZONE_BYPASS = (0x86, True)
    CMD_OUTPUT_ON   = (0x88, True)
    CMD_OUTPUT_OFF  = (0x89, True)
    CMD_OPEN_DOOR   = (0x8A, True)
    CMD_READ_ZONE_TEMP = (0x7D,)
    CMD_START_MONITORING = (0X7F, True)
    CMD_DEVICE_INFO = (0xEE,)

    def __new__(cls, value, mergeable=False):
        obj = object.__new__(cls)
        obj._value_ = value
        obj.mergeable = mergeable
        return obj

    def bytearray(self):
        return bytearray(self.value.to_bytes(1, 'little'))

    def __add__(self, other):
        return SatelCommand(self.value + other)

    def __str__(self):
        return self.name


class SatelMessage(object):

    def __init__(self, cmd, msg_data:bytearray=None, code=None, partitions=None,
                 outputs=None):
        self.cmd = cmd
        self.msg_data = msg_data if msg_data else bytearray()
        if code:
            self.msg_data += bytearray.fromhex(code.ljust(16, 'F'))
        if partitions:
            self.msg_data += partition_bytes(partitions, 4)
        if outputs:
            self.msg_data += partition_bytes(outputs, 32)

    def compare_cmd(self, other):
        return self.cmd == other.cmd

    def merge(self, other):
        """ Perform bitwise OR on two byte arrays into the first array."""
        for i in range(len(self.msg_data)):
            self.msg_data[i] |= other.msg_data[i]

    def __str__(self):
        return "SatelMessage %s %s" % (self.cmd, hexlify(self.msg_data))

    def encode_frame(self):
        data = self.cmd.bytearray() + self.msg_data
        c = checksum(data)
        data.append(c >> 8)
        data.append(c & 0xFF)
        data = data.replace(b'\xFE', b'\xFE\xF0')
        data = bytearray.fromhex("FEFE") + data + bytearray.fromhex("FE0D")
        return data

    def list_set_bits(self, offset, length):
        length = min(length, len(self.msg_data)-offset)
        """Return list of positions of bits (indexed from 1) set to one in given data. """
        return [by*8+bi+1 for by in range(length) for bi in range(8) if (self.msg_data[by+offset] >> bi) & 1]

    @staticmethod
    def decode_frame(resp):
        """Verify checksum and strip header and footer of received frame."""
        if resp[0:2] != b'\xFE\xFE':
            _LOGGER.error("Houston, we got problem: %s", hexlify(resp))
            raise Exception("Wrong header - got %X%X" % (resp[0], resp[1]))
        if resp[-2:] != b'\xFE\x0D':
            raise Exception("Wrong footer - got %X%X" % (resp[-2], resp[-1]))
        output = resp[2:-2].replace(b'\xFE\xF0', b'\xFE')

        c = checksum(bytearray(output[0:-2]))

        if (256 * output[-2:-1][0] + output[-1:][0]) != c:
            raise Exception("Wrong checksum - got %d expected %d" % (
                (256 * output[-2:-1][0] + output[-1:][0]), c))

        cmd, data = output[0], output[1:-2]

        try:
            return SatelMessage(SatelCommand(cmd), data)
        except ValueError:
            _LOGGER.info("Ignoring unknown frame: %s", cmd)
            return None


@unique
class AlarmState(Enum):
    """Represents status of the alarm."""

    ARMED_MODE0 = 0
    ARMED_MODE1 = 1
    ARMED_MODE2 = 2
    ARMED_MODE3 = 3
    ARMED_SUPPRESSED = 4
    ENTRY_TIME = 5
    EXIT_COUNTDOWN_OVER_10 = 6
    EXIT_COUNTDOWN_UNDER_10 = 7
    TRIGGERED = 8
    TRIGGERED_FIRE = 9
    DISARMED = 10


class SatelCommandQueue(asyncio.Queue):
    """
    Queue for sending commands to Satel Integra alarm system and group (merge) multiple calls
    to the same command into one.
    """

    def _init(self, maxsize=0):
        self._queue = collections.deque()

    def _get(self) -> SatelMessage:
        msg = self._queue.popleft()
        _LOGGER.debug("command queue: get   %s", msg)
        return msg

    def _put(self, msg: SatelMessage):
        if not isinstance(msg, SatelMessage):
            raise ValueError("Only SatelMessage objects are allowed, got %s" % type(msg))

        # find the same command in the queue
        exising_msg = next(filter(msg.compare_cmd, self._queue), None)
        if exising_msg and msg.cmd.mergeable:
            _LOGGER.debug("command queue: merge %s", msg)
            exising_msg.merge(msg)
        else:
            _LOGGER.debug("command queue: put   %s", msg)
            self._queue.append(msg)

    def clear(self):
        self._queue.clear()


class AsyncSatel:
    """Asynchronous interface to talk to Satel Integra alarm system."""

    def __init__(self, host, port, loop, monitored_zones=[], monitored_outputs=[], partitions=[]):
        """Init the Satel alarm data."""
        self._host = host
        self._port = port
        self._loop = loop
        self._monitored_zones = monitored_zones
        self.violated_zones = []
        self._monitored_outputs = monitored_outputs
        self.violated_outputs = []
        self.partition_states = {}
        self._keep_alive_timeout = 20
        self._reconnection_timeout = 15
        self._reader = None
        self._writer = None
        self.closed = False
        self._alarm_status_callback = None
        self._zone_changed_callback = None
        self._output_changed_callback = None
        self._partitions = partitions
        self._command_status_event = asyncio.Event()
        self._command_status = False
        self._command_queue = SatelCommandQueue()

        self._message_handlers = {
            SatelCommand.RESULT:                    [self._command_result],
            SatelCommand.ZONE_VIOLATED:             [self._zone_violated],
            SatelCommand.OUTPUT_STATE:              [self._output_changed],
            SatelCommand.DEVICE_INFO:               [self._device_info],
            SatelCommand.ZONE_TEMP:                 [self._zone_temp_received],
            SatelCommand.DOORS_OPENED:              [self._doors_opened],
            SatelCommand.ARMED_MODE0:               [lambda msg: self._armed(AlarmState.ARMED_MODE0, msg)],
            SatelCommand.ARMED_MODE1:               [lambda msg: self._armed(AlarmState.ARMED_MODE1, msg)],
            SatelCommand.ARMED_MODE2:               [lambda msg: self._armed(AlarmState.ARMED_MODE2, msg)],
            SatelCommand.ARMED_MODE3:               [lambda msg: self._armed(AlarmState.ARMED_MODE3, msg)],
            # for INTEGRA v1.12 (released 2013-11-29) and newer, both 0x09 and 0x0A commands indicate
            # really armed partitions, regardless of suppression arm status time
            SatelCommand.ARMED_SUPPRESSED:          [lambda msg: self._armed(AlarmState.ARMED_MODE0, msg)],
            SatelCommand.ENTRY_TIME:                [lambda msg: self._armed(AlarmState.ENTRY_TIME, msg)],
            SatelCommand.EXIT_COUNTDOWN_OVER_10:    [lambda msg: self._armed(AlarmState.EXIT_COUNTDOWN_OVER_10, msg)],
            SatelCommand.EXIT_COUNTDOWN_UNDER_10:   [lambda msg: self._armed(AlarmState.EXIT_COUNTDOWN_UNDER_10, msg)],
            SatelCommand.TRIGGERED:                 [lambda msg: self._armed(AlarmState.TRIGGERED, msg)],
            SatelCommand.TRIGGERED_FIRE:            [lambda msg: self._armed(AlarmState.TRIGGERED_FIRE, msg)],
        }

        if loop:
            loop.create_task(self.sender_worker())
        else:
            # loop can be null only during test-cases
            pass

    @property
    def connected(self):
        """Return true if there is connection to the alarm."""
        return self._writer and self._reader

    async def connect(self):

        """Make a TCP connection to the alarm system."""
        _LOGGER.debug("Connecting...")

        try:
            self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
            self._command_queue.clear()
            _LOGGER.debug("Connected!")

        except Exception as e:
            _LOGGER.warning(
                "Exception during connecting: %s.", e)
            self._writer = None
            self._reader = None
            return False

        return True

    def _zone_violated(self, msg):

        status = {"zones": {}}

        violated_zones = msg.list_set_bits(0, 32)
        self.violated_zones = violated_zones
        _LOGGER.debug("Violated zones: %s", violated_zones)
        for zone in self._monitored_zones:
            status["zones"][zone] = \
                1 if zone in violated_zones else 0

        _LOGGER.debug("Returning status: %s", status)

        if self._zone_changed_callback:
            self._zone_changed_callback(status)

        return status

    def _output_changed(self, msg):
        """0x17   outputs state 0x17   + 16/32 bytes"""
        output_states = msg.list_set_bits(0, 32)
        self.violated_outputs = output_states
        _LOGGER.debug("Output states: %s, monitored outputs: %s",output_states, self._monitored_outputs)

        status = {"outputs": {out: 1 if out in output_states else 0 for out in self._monitored_outputs}}
        _LOGGER.debug("Returning status: %s", status)

        if self._output_changed_callback:
            self._output_changed_callback(status)

        return output_states

    def _command_result(self, msg: SatelMessage):
        status = {"error": "Some problem!"}
        error_code = msg.msg_data[0:1]
        if error_code in [b'\x00', b'\xFF']:
            status = {"error": "OK"}
        elif error_code == b'\x01':
            status = {"error": "User code not found"}

        _LOGGER.debug("Received result %s", status)
        self._command_status = status
        self._command_status_event.set()
        return status

    def _zone_temp_received(self, msg: SatelMessage):
        zone = msg.msg_data[0]
        temp = int.from_bytes(msg.msg_data[-2:], byteorder='big', signed=True)
        temp = 0.5 * temp - 55.0
        _LOGGER.debug("Zone %d temperature received: %.1f", zone, temp)
        self._command_status_event.set()
        return [zone, temp]

    def _doors_opened(self, msg: SatelMessage):
        status = {"doors": {}}
        doors = msg.list_set_bits(0, 32)
        _LOGGER.debug("Doors opened: %s", doors)
        for door in doors:
            status["doors"][door] = 1


    async def _send_message(self, msg):
        self._command_queue.put_nowait(msg)

    async def _send_frame(self, data):
        _LOGGER.debug("--- Sending frame  %s", hexlify(data))

        if not self._writer:
            _LOGGER.warning("Ignoring data because we're disconnected!")
        try:
            self._writer.write(data)
            await self._writer.drain()
            return True
        except Exception as e:
            _LOGGER.warning(
                "Exception during sending data: %s.", e)
            self._writer = None
            self._reader = None
            return False

    async def _read_frame(self):
        if not self._reader:
            return []

        try:
            data = await self._reader.readuntil(b'\xFE\x0D')
            _LOGGER.debug("-- Received frame  %s", hexlify(data))
            return data

        except Exception as e:
            _LOGGER.warning("Got exception: %s. Most likely the other side has disconnected!!", e)
            self._writer = None
            self._reader = None

            if self._alarm_status_callback:
                self._alarm_status_callback()


    async def sender_worker(self):
        """Keeps sending commands from the queue and waiting for answers"""
        while not self.closed:
            msg = await self._command_queue.get()
            frame = msg.encode_frame()
            try:
                if await self._send_frame(frame):
                    await asyncio.wait_for(asyncio.shield(self._command_status_event.wait()), timeout=10)
                    self._command_status_event.clear()
                self._command_queue.task_done()
            except TimeoutError:
                self._command_queue.task_done()
                _LOGGER.warning("Timeout while waiting for confirmation")
            except Exception:
                self._command_queue.task_done()
                _LOGGER.warning("Error while waiting for confirmation")

    async def start_monitoring(self):
        """Start monitoring for interesting events."""
        monitored_cmds = [SatelCommand.ZONE_VIOLATED, SatelCommand.ARMED_MODE0, SatelCommand.ARMED_MODE1,
                          SatelCommand.ARMED_MODE2, SatelCommand.ARMED_MODE3, SatelCommand.ARMED_SUPPRESSED,
                          SatelCommand.ENTRY_TIME, SatelCommand.EXIT_COUNTDOWN_OVER_10, SatelCommand.EXIT_COUNTDOWN_UNDER_10,
                          SatelCommand.TRIGGERED, SatelCommand.TRIGGERED_FIRE, SatelCommand.OUTPUT_STATE,
                          SatelCommand.ZONES_BYPASSED, SatelCommand.DOORS_OPENED]

        data = partition_bytes([cmd.value + 1 for cmd in monitored_cmds], 12)
        await self._send_message(SatelMessage(SatelCommand.CMD_START_MONITORING, bytearray(data)))

    async def arm(self, code, partition_list, mode=0):
        """Send arming command to the alarm. Modes allowed: from 0 till 3."""
        await self._send_message(SatelMessage(
            SatelCommand.CMD_ARM_MODE_0 + mode,
            code=code, partitions=partition_list))

    async def disarm(self, code, partition_list):
        """Send command to disarm."""
        await self._send_message(SatelMessage(
            SatelCommand.CMD_DISARM,
            code=code, partitions=partition_list))

    async def clear_alarm(self, code, partition_list):
        """Send command to clear the alarm."""
        await self._send_message(SatelMessage(
            SatelCommand.CMD_CLEAR_ALARM,
            code=code, partitions=partition_list))

    async def set_output(self, code, output_id, state):
        """Send output turn on/off command"""
        await self._send_message(SatelMessage(
            SatelCommand.CMD_OUTPUT_ON if state else SatelCommand.CMD_OUTPUT_OFF,
            code=code, outputs=[output_id]))

    async def read_temp(self, zone):
        """Read temperature from the zone."""
        await self._send_message(SatelMessage(SatelCommand.CMD_READ_ZONE_TEMP, bytearray([zone])))

    def _armed(self, mode, msg: SatelMessage):
        partitions = msg.list_set_bits(0, 4)

        _LOGGER.debug("Update: list of partitions in mode %s: %s",mode, partitions)

        self.partition_states[mode] = partitions

        if self._alarm_status_callback:
            self._alarm_status_callback()

    async def keep_alive(self):
        """A workaround for Satel Integra disconnecting after 25s.

        Every interval it sends some random question to the device, ignoring
        answer - just to keep connection alive.
        """
        while not self.closed:
            await asyncio.sleep(self._keep_alive_timeout)
            if self.connected:
                await self._send_message(SatelMessage(SatelCommand.DEVICE_INFO, bytearray(b'\x01\x01')))

    def _device_info(self, msg):
        """Dummy handler for keep_alive responses"""
        self._command_status = None
        self._command_status_event.set()

    def _dispatch_frame(self, frame):
        if not frame:
            _LOGGER.warning("Got empty response. We think it's disconnect.")
            self._writer = None
            self._reader = None
            if self._alarm_status_callback:
                self._alarm_status_callback()
            return

        msg = SatelMessage.decode_frame(frame)
        if msg:
            if msg.cmd in self._message_handlers:
                _LOGGER.debug("Calling handlers for %s", msg.cmd)
                for handler in self._message_handlers[msg.cmd]:
                    handler(msg)
            else:
                _LOGGER.info("Skipping command: %s", msg.cmd)

    async def monitor_status(self, alarm_status_callback=None,
                             zone_changed_callback=None,
                             output_changed_callback=None):
        """Start monitoring of the alarm status.

        Send command to satel integra to start sending updates. Read in a
        loop and call respective callbacks when received messages.
        """
        self._alarm_status_callback = alarm_status_callback
        self._zone_changed_callback = zone_changed_callback
        self._output_changed_callback = output_changed_callback

        _LOGGER.info("Starting monitor_status loop")

        while not self.closed:
            while not self.connected:
                _LOGGER.info("Not connected, re-connecting... ")
                await self.connect()
                if not self.connected:
                    _LOGGER.warning("Not connected, sleeping for 10s... ")
                    await asyncio.sleep(self._reconnection_timeout)
                    continue
            await self.start_monitoring()
            if not self.connected:
                _LOGGER.warning("Start monitoring failed, sleeping for 10s...")
                await asyncio.sleep(self._reconnection_timeout)
                continue
            while True:
                frame = await self._read_frame()
                self._dispatch_frame(frame)
                if not self.connected:
                    _LOGGER.info("Got connection broken, reconnecting!")
                    break
        _LOGGER.info("Closed, quit monitoring.")

    def close(self):
        """Stop monitoring and close connection."""
        _LOGGER.debug("Closing...")
        self.closed = True
        if self.connected:
            self._writer.close()

    def add_handler(self, cmd, handler):
        """Add handler for given command."""
        self._message_handlers.setdefault(cmd, []).append(handler)

    def remove_handler(self, cmd, handler):
        """Remove handler for given command."""
        self._message_handlers.setdefault(cmd, []).remove(handler)

    async def wait_for_response(self, response_cmd, message_handler, timeout=5):
        """Send message and wait for response.
        The handler should return None to ignore the message and keep waiting.
        """
        future = asyncio.get_running_loop().create_future() #asyncio.handler_called = asyncio.Event()

        def err_callback(msg):
            if msg.msg_data[0] != 0x00 and msg.msg_data[0] != 0xFF:
                future.set_exception(Exception("Got error: %s" % msg.msg_data))

        def callback(msg):
            result = message_handler(msg)
            if result is not None:
                future.set_result(result)

        try:
            self.add_handler(response_cmd, callback)
            self.add_handler(SatelCommand.RESULT, err_callback)
            return await asyncio.wait_for(future, 2.6)
        except asyncio.TimeoutError:
            raise TimeoutError("Timeout while waiting for response command %s" % response_cmd)
        finally:
            self.remove_handler(response_cmd, callback)
            self.remove_handler(SatelCommand.RESULT, err_callback)

    async def read_temp_and_wait(self, zone):
        """Read temperature from the zone."""
        def message_handler(msg):
            zone_received, temp = self._zone_temp_received(msg)
            return temp if zone == zone_received else None

        await self._send_message(SatelMessage(SatelCommand.CMD_READ_ZONE_TEMP, bytearray([zone])))
        return await self.wait_for_response(SatelCommand.ZONE_TEMP, message_handler)

    async def read_device_info_and_wait(self, type, number):
        def message_handler(msg):
            if msg.msg_data[0] != type or msg.msg_data[1] != number:
                return None
            _LOGGER.info("Got device info: %s", msg.msg_data)
            return msg.msg_data

        await self._send_message(SatelMessage(SatelCommand.CMD_DEVICE_INFO, bytearray([type, number])))
        return await self.wait_for_response(SatelCommand.CMD_DEVICE_INFO, message_handler)



def demo(host, port):
    """Basic demo of the monitoring capabilities."""
    # logging.basicConfig(level=logging.DEBUG)

    loop = asyncio.get_event_loop()
    stl = AsyncSatel(host,
                     port,
                     loop,
                     [1, 2, 3, 4, 5, 6, 7, 8, 12, 13, 14, 15, 16, 17, 18, 19,
                      20, 21, 22, 23, 25, 26, 27, 28, 29, 30],
                     [8, 9, 10]
                     )

    loop.run_until_complete(stl.connect())
    loop.create_task(stl.arm("3333", (1,)))
    loop.create_task(stl.disarm("3333",(1,)))
    loop.create_task(stl.sender_worker())

    loop.create_task(stl.keep_alive())
    loop.create_task(stl.monitor_status())

    loop.run_forever()
    loop.close()
