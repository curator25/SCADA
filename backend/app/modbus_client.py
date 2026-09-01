import io                                        # In-memory buffers to swallow library output
import time                                      # Monotonic clock for log throttling
import socket                                    # Low-level network errors
import struct                                    # Decoding raw registers into floats
import inspect                                   # Detect the pymodbus slave/device_id kwarg
import threading                                 # One read at a time per client
import contextlib                                # Redirect stdout/stderr around connect()
from pymodbus.client.tcp import ModbusTcpClient  # Modbus TCP client


# --------------------------------------------------------------------------- #
# Register decoding
#
# Modbus registers are 16-bit, so an F32 value spans TWO registers. Vendors
# disagree on the ordering, so both axes are configurable:
#
#   byte_order  - order of the two bytes INSIDE each register
#   word_order  - which register carries the high word
#
#   big  / big     -> ABCD   (plain big endian)
#   big  / little  -> CDAB   (word-swapped)
#   little / little-> DCBA   (full little endian)  <-- this device
#   little / big   -> BADC   (byte-swapped)
#
# We decode with struct rather than pymodbus helpers so this keeps working
# across pymodbus versions (BinaryPayloadDecoder was deprecated/removed).
# --------------------------------------------------------------------------- #
def _swap_bytes(register):
    """Swap the two bytes inside a single 16-bit register."""
    return ((register & 0x00FF) << 8) | ((register & 0xFF00) >> 8)


def decode_f32(high, low, word_order="big", byte_order="big"):
    """Combine two 16-bit registers into a 32-bit float."""
    if byte_order == "little":
        high, low = _swap_bytes(high), _swap_bytes(low)
    if word_order == "little":
        high, low = low, high
    return struct.unpack(">f", struct.pack(">HH", high, low))[0]


def _to_signed16(value):
    """Interpret a raw 16-bit register as a signed integer."""
    return value - 0x10000 if value >= 0x8000 else value


def decode_parameters(registers, parameters, register_start, word_order="big", byte_order="big"):
    """Turn a raw register block into {parameter_name: value|None}.

    A value of None means "could not read" - it is never silently turned into
    0.0, so a dead sensor is always distinguishable from a genuine zero.
    """
    decoded = {}

    for param in parameters:
        name = param.get("name")
        datatype = str(param.get("datatype", "f32")).lower()
        scale = param.get("scale", 1) or 1
        index = param.get("register", 0) - register_start   # Offset inside the block

        try:
            if index < 0:
                raise IndexError("register is before register_start")

            if datatype in ("f32", "float32", "float"):
                if index + 1 >= len(registers):
                    raise IndexError("register block too short for f32")
                value = decode_f32(registers[index], registers[index + 1], word_order, byte_order)

            elif datatype in ("int16", "i16"):
                value = _to_signed16(registers[index])

            elif datatype in ("uint16", "u16"):
                value = registers[index]

            else:
                raise ValueError(f"unsupported datatype '{datatype}'")

            decoded[name] = value * scale

        except Exception:
            decoded[name] = None    # Mark unreadable rather than faking a number

    return decoded


class ModbusReader:
    """Owns the TCP connection and reads the configured register block."""

    def __init__(self, cfg, logger):
        self.cfg = cfg
        self.logger = logger
        self.client = None
        self.connected = False
        self._lock = threading.Lock()   # pymodbus clients are not thread-safe
        self._last_error_at = 0.0       # When we last wrote a failure to the log
        self._suppressed = 0            # How many failures we swallowed since then
        self._unit_kw = None            # Cached: "slave" or "device_id" for this pymodbus

    def _log_failure(self, message):
        """Log a failure, but not once per second.

        With a 1s poll interval an unreachable device would write ~86,000 lines
        a day and push real history out of the rotated logs. So we log the first
        failure immediately, then at most once per `error_log_interval_seconds`,
        reporting how many were suppressed in between.
        """
        interval = self.cfg.get("modbus", "error_log_interval_seconds", default=60)
        now = time.monotonic()

        if self._last_error_at == 0.0 or (now - self._last_error_at) >= interval:
            if self._suppressed:
                message = f"{message} (repeated {self._suppressed} more time(s))"
            self.logger.error(message)
            self._last_error_at = now
            self._suppressed = 0
        else:
            self._suppressed += 1

    def _reset_failure_log(self):
        """Called after a success so the next failure is reported immediately."""
        self._last_error_at = 0.0
        self._suppressed = 0

    def connect(self):
        """Open the Modbus TCP connection. Returns True on success."""
        host = self.cfg.get("modbus", "host")
        port = self.cfg.get("modbus", "port", default=502)
        timeout = self.cfg.get("modbus", "timeout", default=1)

        try:
            client = ModbusTcpClient(host=host, port=port, timeout=timeout)

            # pymodbus can print directly to the console on failure - swallow it
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                client.connect()

            if client.connected:
                if not self.connected:      # Only log the transition, not every retry
                    self.logger.info(f"Modbus connection established ({host}:{port})")
                self.client = client
                self.connected = True
                self._reset_failure_log()   # Next failure should be reported at once
                return True

            raise ConnectionRefusedError("Connection refused by target device")

        except ConnectionRefusedError:
            self._log_failure(f"Modbus connection refused ({host}:{port})")
        except socket.error as se:
            # 10061 = connection actively refused (device off / wrong IP or port)
            if getattr(se, "errno", None) == 10061:
                self._log_failure(f"Modbus TCP connection failed ({host}:{port})")
            else:
                self._log_failure(f"Socket error during Modbus connection: {se}")
        except Exception as e:
            self._log_failure(f"Unexpected Modbus connection error: {e}")

        self._drop()
        return False

    def _unit_kwarg(self):
        """Return the keyword this pymodbus build uses for the slave/unit id.

        Cached after the first lookup. pymodbus 3.9 renamed `slave` -> `device_id`.
        """
        if self._unit_kw is None:
            params = inspect.signature(self.client.read_holding_registers).parameters
            self._unit_kw = "device_id" if "device_id" in params else "slave"
        return self._unit_kw

    def read_block(self):
        """Read the whole configured register block. Returns a list or None."""
        with self._lock:
            if not self.client or not self.client.connected:
                if not self.connect():
                    return None

            count = self.cfg.get("modbus", "register_count")
            slave = self.cfg.get("modbus", "slave_id", default=1)
            reg_type = str(self.cfg.get("modbus", "register_type", default="holding")).lower()

            # Vendors document holding registers two different ways:
            #
            #   raw / protocol   0, 1, 2 ...        -> address_base 0
            #   4x reference     40001, 40002 ...   -> address_base 40001
            #
            # The wire protocol only ever carries the raw address, so a device
            # documented as "40500" is actually address 499. Getting this wrong
            # is the classic Modbus off-by-one: the read either fails outright
            # or silently returns a completely different block. Keeping it as
            # one config value means the registers can be written exactly as the
            # vendor documents them, and the convention flipped in one place.
            base = self.cfg.get("modbus", "address_base", default=0) or 0
            start = self.cfg.get("modbus", "register_start") - base

            if start < 0:
                self._log_failure(
                    f"register_start {start + base} is below address_base {base} - "
                    f"check modbus.address_base (0 for raw addresses, 40001 for 4x)"
                )
                return None

            # pymodbus renamed the unit argument across versions:
            #   <= 3.8  -> slave=      >= 3.9  -> device_id=
            # Detect which this build expects so the same code works on both.
            unit_kwarg = self._unit_kwarg()

            try:
                read = (
                    self.client.read_input_registers
                    if reg_type == "input"
                    else self.client.read_holding_registers
                )
                response = read(address=start, count=count, **{unit_kwarg: slave})

                if response.isError():
                    self._log_failure(f"Error reading Modbus data: {response}")
                    return None

                self._reset_failure_log()       # Healthy read - clear the throttle
                return response.registers

            except Exception as e:
                self._log_failure(f"Modbus read error: {e}")
                self._drop()        # Force a fresh connection on the next attempt
                return None

    def _drop(self):
        """Close and forget the current client."""
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
        self.client = None
        self.connected = False

    def close(self):
        with self._lock:
            self._drop()
