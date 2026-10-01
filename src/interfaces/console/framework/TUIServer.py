import re
import select
import socket
import threading
import time
import traceback
from typing import Set, Optional, Callable, Dict

from .TUIScreen import TUIScreen
from .LinkProbe import LinkProbe, LinkStatus, NO_LINK_STATUS
from . import Curtain
from common.logging import trace, info, error, note

ACCEPT_TIMEOUT = 1.0      # how long a stopping server waits for its accept loop to notice
FRAME_INTERVAL = 1 / 30   # the animation runs at 30 fps; a frame sleeps only what is left of its slot
IDLE_INTERVAL = 1 / 5     # ...until the picture has stood still for IDLE_AFTER: then frames are drawn this often
IDLE_AFTER = 1.0          # seconds of unchanged frames before the loop drops to the idle rate


class TUIServer:
    """Renders TUI frames on a timer and broadcasts them to connected TCP terminal clients.

    Clients can connect using: nc localhost 5151 (or tui.py). Runs daemon threads for accepting clients,
    the animation loop and per-client keyboard input.

    The loop draws at 30 fps while the picture moves and drops to IDLE_INTERVAL once `get_frame`
    has found nothing to send for IDLE_AFTER seconds — a full redraw of a still screen is the
    biggest idle cost of the process. A keystroke, a client connecting or resizing, or a frame
    with changes brings the full rate back at once (`wake`).
    """

    FULL_BLOCK_TERMINALS = {"Apple_Terminal"}   # TERM_PROGRAM values whose `▀` rendering breaks the Crystal

    def __init__(self, draw_screen_callback, host: str = '0.0.0.0', port: int = 5151,
                 input_callback: Optional[Callable[[str, tuple], None]] = None):
        """
        Args:
            draw_screen_callback: Function to draw each frame
            host: Host to bind to
            port: Port to listen on
            input_callback: Optional callback for keyboard input (key, client_addr)
        """
        self.host = host
        self.port = port
        self.clients: Set[socket.socket] = set()
        self.clients_lock = threading.Lock()
        self.client_dimensions = {}  # Map socket to (width, height)
        self.client_terminals: Dict[socket.socket, str] = {}   # the TERM_PROGRAM tui.py sent after its dimensions, "" when none
        self.link_probes: Dict[socket.socket, LinkProbe] = {}   # one per client, keyed like the dimensions
        self.probing = False   # the last frame drawn wanted the link status; probes ride frames only then
        self.last_resized_client = None  # Track which client was last resized
        self.force_full_redraw = False  # Set when a new client connects so next frame is a full redraw
        self.first_client_pending = True   # the first client of the process gets the curtains before its setup
        self._wake = threading.Event()     # set to cut the current frame's sleep short and go back to 30 fps
        self.idle = False                  # True while the loop draws at IDLE_INTERVAL
        self.running = False
        self.server_socket = None
        self.draw_screen_callback = draw_screen_callback
        self.input_callback = input_callback
        self.screen = TUIScreen()

    def start(self):
        """Start the server"""
        self.running = True
        max_retries = 10
        for attempt in range(max_retries):
            try:
                self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.server_socket.bind((self.host, self.port))
                self.server_socket.listen(5)

                suffix = f" (after {attempt}s)" if attempt > 0 else ""
                info(f"TUI Server listening on {self.host}:{self.port}{suffix}")

                accept_thread = threading.Thread(target=self._accept_clients)
                accept_thread.daemon = True
                accept_thread.start()

                animation_thread = threading.Thread(target=self._animation_loop)
                animation_thread.daemon = True
                animation_thread.start()
                return
            except OSError:
                if self.server_socket:
                    self.server_socket.close()
                    self.server_socket = None
                if attempt < max_retries - 1:
                    info(f"TUI port in use, retrying in 1s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(1)
                else:
                    self.running = False
                    note("TUI port still in use after 10s, giving up.")

    def _accept_clients(self):
        """Accept new client connections"""
        while self.running:
            try:
                self.server_socket.settimeout(ACCEPT_TIMEOUT)
                client_socket, addr = self.server_socket.accept()

                # Set TCP_NODELAY for real-time streaming
                client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

                # Read terminal dimensions from client first
                try:
                    client_socket.settimeout(5.0)  # 5 second timeout for dimension reading
                    dimension_data = client_socket.recv(1024).decode('utf-8').strip()
                    parts = dimension_data.split()
                    terminal = ""
                    if len(parts) in (2, 3):
                        cols, rows = int(parts[1]), int(parts[0])
                        terminal = parts[2] if len(parts) == 3 else ""   # tui.py appends its TERM_PROGRAM
                    else:
                        # Default dimensions if format is wrong
                        cols, rows = 80, 24
                        info(f"Client {addr} sent invalid dimensions, using default 80x24")
                except (socket.timeout, ValueError, UnicodeDecodeError):
                    # Default dimensions if we can't read them
                    cols, rows, terminal = 80, 24, ""
                    info(f"Client {addr} timeout reading dimensions, using default 80x24")

                # The first client after a restart sees the curtains close over whatever its
                # terminal shows before the setup clears it; it is registered only afterwards,
                # so no frame is broadcast over them.
                first = self.first_client_pending
                if first:
                    self.first_client_pending = False
                    self._draw_curtains(client_socket, addr, cols, rows)
                self._setup_client(client_socket)
                if first:
                    cols, rows = self._drop_typed_ahead(client_socket, addr, cols, rows)

                with self.clients_lock:
                    self.clients.add(client_socket)
                    self.client_dimensions[client_socket] = (cols, rows)
                    self.client_terminals[client_socket] = terminal
                    self.link_probes[client_socket] = LinkProbe(str(addr))
                    self.last_resized_client = client_socket
                    self.force_full_redraw = True
                self.wake()

                info(f"Client connected to TUI server: {addr} ({cols} x {rows}, terminal "
                     f"{terminal or 'unknown'}{'' if self.half_blocks_for(terminal) else ', full blocks'}), "
                     f"forcing full redraw")

                # Start input handler thread if callback is provided
                if self.input_callback:
                    input_thread = threading.Thread(
                        target=self._handle_client_input,
                        args=(client_socket, addr)
                    )
                    input_thread.daemon = True
                    input_thread.start()

            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    error(f"Error accepting client: {e}")

    def _draw_curtains(self, client: socket.socket, addr, cols: int, rows: int):
        """Send the curtain frames to one client at the animation's frame rate."""
        info(f"TUI Server: closing the curtains on {addr} ({cols} x {rows}).")
        due = time.perf_counter() + FRAME_INTERVAL
        try:
            for data in Curtain.frames(cols, rows, round(1 / FRAME_INTERVAL)):
                client.sendall(data)
                due = self._next_frame(due, FRAME_INTERVAL)
        except (BrokenPipeError, ConnectionResetError, socket.error) as e:
            trace(f"TUI Server: curtains cut short on {addr}: {e}")

    @staticmethod
    def _drop_typed_ahead(client: socket.socket, addr, cols: int, rows: int):
        """Discard what the client sent while the curtains played — a key would skip the
        intro before it started — keeping only a resize; returns the dimensions to use."""
        dropped = 0
        try:
            while select.select([client], [], [], 0)[0]:
                data = client.recv(4096)
                if not data:
                    break
                dropped += len(data)
                for line in data.decode('utf-8', errors='replace').split('\n'):
                    parts = line.split()
                    if len(parts) == 3 and parts[0] == "RESIZE":
                        rows, cols = int(parts[1]), int(parts[2])
        except (ValueError, socket.error) as e:
            trace(f"TUI Server: reading {addr}'s type-ahead: {e}")
        if dropped:
            trace(f"TUI Server: dropped {dropped} byte(s) {addr} typed during the curtains")
        return cols, rows

    @staticmethod
    def _setup_client(client: socket.socket):
        """Send initial setup commands to client"""
        try:
            setup = (
                TUIScreen.BEGIN_SYNC +
                TUIScreen.ENTER_ALT_SCREEN +
                TUIScreen.PUSH_KEY_REPORTING +
                TUIScreen.RESET + TUIScreen.BACKGROUND_SEQ + TUIScreen.CLEAR_SCREEN +
                TUIScreen.HIDE_CURSOR +
                TUIScreen.END_SYNC
            )
            client.sendall(setup.encode('utf-8'))
        except Exception as e:
            trace(f"Failed to setup TUI client: {e}")

    def _broadcast(self, data: bytes):
        """Broadcast a frame to all connected clients, each with its link probe appended when
        a tab is showing the link status and one is due (after the frame, so the terminal
        answers once the frame is on screen)."""
        with self.clients_lock:
            disconnected = set()

            for client in self.clients:
                probe = self.link_probes.get(client) if self.probing else None
                try:
                    client.sendall(data + probe.request() if probe else data)
                except (BrokenPipeError, ConnectionResetError, socket.error):
                    disconnected.add(client)

            for client in disconnected:
                self._drop_client(client, "disconnected from TUI server")

    @staticmethod
    def half_blocks_for(terminal: str) -> bool:
        """Whether a client's terminal renders the Crystal's `▀` half-block pixels; Terminal.app
        does not (gaps between the halves, no true color), so it gets full cells instead."""
        return terminal not in TUIServer.FULL_BLOCK_TERMINALS

    def _drop_client(self, client: socket.socket, why: str):
        """Forget and close a client; the caller holds clients_lock."""
        self.clients.discard(client)
        self.client_dimensions.pop(client, None)
        self.client_terminals.pop(client, None)
        self.link_probes.pop(client, None)
        if self.last_resized_client == client:
            self.last_resized_client = None
        try:
            client.close()
        except Exception:
            pass
        trace(f"Client {why}")

    def link_status(self) -> LinkStatus:
        """The worst display latency over the connected clients, for the tabs to show."""
        with self.clients_lock:
            statuses = [probe.status() for probe in self.link_probes.values()]
        measured = [s for s in statuses if s.lag is not None]
        if measured:
            return max(measured, key=lambda s: s.lag)
        if statuses and all(s.unsupported for s in statuses):
            return statuses[0]
        return NO_LINK_STATUS

    def wake(self):
        """Cut the current frame's sleep short and draw the next frame at the full rate: for
        input and client changes, which must show without waiting out an idle slot."""
        self._wake.set()

    def _next_frame(self, due: float, interval: float) -> float:
        """Sleep until `due`, the end of the current frame's slot, and return the end of the
        next one, `interval` away. A frame that overran its slot starts the next slot now
        instead of racing to catch up; a `wake` ends the sleep early and the next slot starts
        now as well. The sleep never exceeds `interval`: the frame that ends an idle was
        scheduled an idle slot ahead, and the next one must not wait that out."""
        now = time.perf_counter()
        due = min(due, now + interval)
        if due > now and self._wake.wait(due - now):
            return time.perf_counter() + interval
        if due > now:
            return due + interval
        return now + interval

    def _animation_loop(self):
        """Main animation loop that broadcasts to all clients"""
        frame = 0
        due = time.perf_counter() + FRAME_INTERVAL
        still_since: Optional[float] = None   # when the frames stopped changing; None while they change

        while self.running:
            with self.clients_lock:
                has_clients = bool(self.clients)
                if not has_clients:
                    cols, rows = 80, 24
                else:
                    if self.last_resized_client and self.last_resized_client in self.clients:
                        client_to_use = self.last_resized_client
                    else:
                        client_to_use = next(iter(self.clients))
                        self.last_resized_client = client_to_use
                    cols, rows = self.client_dimensions.get(client_to_use, (80, 24))
                    half_blocks = self.half_blocks_for(self.client_terminals.get(client_to_use, ""))

            if not has_clients:
                self._wake.wait(0.5)   # a connecting client wakes the loop
                self._wake.clear()
                due = time.perf_counter() + FRAME_INTERVAL
                continue

            self.screen.resize(cols, rows)
            screen = self.screen
            if screen.half_blocks != half_blocks:
                screen.half_blocks = half_blocks
                self.force_full_redraw = True
                info(f"TUI Server: Crystal pixels are {'half' if half_blocks else 'full'} blocks now, "
                     f"for {self.client_terminals.get(client_to_use) or 'an unknown terminal'}")
            screen.link_status = self.link_status() if self.probing else NO_LINK_STATUS
            screen.link_probe_wanted = False

            screen.clear_buffer()

            try:
                self.draw_screen_callback(frame, screen)
            except Exception as e:
                error(f"Exception: {e}")
                trace(traceback.format_exc())

            if screen.link_probe_wanted != self.probing:
                self.probing = screen.link_probe_wanted
                trace(f"TUI Server: link probes {'on' if self.probing else 'off'} — "
                      f"{'a' if self.probing else 'no'} tab shows the link status")

            if self.force_full_redraw:
                self.force_full_redraw = False
                screen.invalidate_front_buffer()
                trace("TUI Server: invalidated front buffer for full redraw")

            frame_data = screen.get_frame(use_sync=True)
            if frame_data:
                self._broadcast(frame_data)

            now = time.perf_counter()
            if frame_data or self._wake.is_set():
                still_since = None
                if self.idle:
                    self.idle = False
                    trace("TUI Server: the picture moves again — back to 30 fps")
            elif still_since is None:
                still_since = now
            elif not self.idle and now - still_since >= IDLE_AFTER:
                self.idle = True
                trace(f"TUI Server: nothing changed for {IDLE_AFTER:.0f}s — drawing {1 / IDLE_INTERVAL:.0f} fps until it does")
            self._wake.clear()

            frame += 1
            due = self._next_frame(due, IDLE_INTERVAL if self.idle else FRAME_INTERVAL)

    def stop(self):
        """Stop the server"""
        self.running = False
        self.wake()

        # The alternate screen is left as it is: the last frame stays under tui.py's reconnect
        # box for the next process's curtains to close over (tui.py leaves the alternate
        # screen itself when it quits).
        cleanup = (
            TUIScreen.BEGIN_SYNC +
            TUIScreen.POP_KEY_REPORTING +
            TUIScreen.SHOW_CURSOR +
            TUIScreen.RESET +
            TUIScreen.END_SYNC
        )
        self._broadcast(cleanup.encode('utf-8'))

        # Close all client connections
        with self.clients_lock:
            for client in self.clients:
                client.close()
            self.clients.clear()
            self.client_dimensions.clear()

        # Close server socket
        if self.server_socket:
            self.server_socket.close()

        info("TUI Server stopped")

    _CSI_PARAM_CHARS = "0123456789;:?<=>"
    # ESC followed by one of these is an Alt/Meta key chord (readline's word keys, and
    # Alt+Enter as a legacy terminal sends it), not a bare Escape followed by the character.
    _ESC_CHORDS = ("b", "f", "\x7f", "\r", "\n")

    def _emit_key(self, key: str, addr):
        self.wake()
        if self.input_callback:
            try:
                self.input_callback(key, addr)
            except Exception as e:
                error(f"Error in input callback: {e}")

    _CURSOR_REPORT = re.compile(r"\x1b\[\d+;\d+R")

    def _process_input_buffer(self, buf: str, addr, probe: Optional[LinkProbe] = None) -> str:
        """Extract complete keystrokes from the buffer and hand each to the input callback.
        Returns the incomplete escape sequence (if any) to prepend to the next read. A cursor
        position report (`ESC [ row ; col R`) answers `probe`'s DSR when one is outstanding;
        without a probe it is handed on as a key like any other CSI sequence.

        Escape sequences are parsed generically: `ESC [` parameters and a final byte (CSI:
        arrows, Home/End, Delete, PgUp/PgDn, modifier+arrow, paste markers), `ESC O x` (SS3:
        Home/End on some terminals) and the `ESC b/f/DEL/CR` chords. A bare Escape ahead of
        any other character is the Escape key; a lone trailing Escape is kept until the
        next read (or flushed by the input loop when nothing follows)."""
        while buf:
            if buf[0] != '\x1b':
                self._emit_key(buf[0], addr)
                buf = buf[1:]
                continue
            if len(buf) == 1:
                return buf
            nxt = buf[1]
            if nxt == '[':
                i = 2
                while i < len(buf) and buf[i] in self._CSI_PARAM_CHARS:
                    i += 1
                if i >= len(buf):
                    return buf
                if '@' <= buf[i] <= '~':
                    seq = buf[:i + 1]
                    if probe is not None and probe.sent and self._CURSOR_REPORT.fullmatch(seq):
                        probe.reply()
                    else:
                        self._emit_key(seq, addr)
                    buf = buf[i + 1:]
                else:
                    trace(f"Dropping malformed escape sequence from {addr}: {buf[:i + 1]!r}")
                    buf = buf[i + 1:]
                continue
            if nxt == 'O':
                if len(buf) < 3:
                    return buf
                self._emit_key(buf[:3], addr)
                buf = buf[3:]
                continue
            if nxt in self._ESC_CHORDS:
                self._emit_key(buf[:2], addr)
                buf = buf[2:]
                continue
            self._emit_key('\x1b', addr)
            buf = buf[1:]
        return ""

    def _handle_client_input(self, client_socket: socket.socket, addr):
        """Handle keyboard input and resize commands from a specific client"""
        try:
            buffer = ""
            probe = self.link_probes.get(client_socket)

            while self.running and client_socket in self.clients:
                try:
                    # Use select to check for available input with timeout
                    readable, _, _ = select.select([client_socket], [], [], 0.1)

                    if readable:
                        # Read available data (may be multiple characters)
                        data = client_socket.recv(1024)

                        if not data:
                            # Client disconnected
                            break

                        # Convert bytes to string and add to buffer
                        buffer += data.decode('utf-8', errors='replace')

                        # Check for resize commands in the buffer
                        while '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)

                            if line.startswith("RESIZE "):
                                # Handle resize command
                                try:
                                    parts = line.split()
                                    if len(parts) == 3:
                                        rows, cols = int(parts[1]), int(parts[2])
                                        with self.clients_lock:
                                            self.client_dimensions[client_socket] = (cols, rows)
                                            self.last_resized_client = client_socket
                                        self.wake()
                                        trace(f"TUI Client {addr} resized to {cols} x {rows}")
                                except (ValueError, IndexError) as e:
                                    trace(f"Invalid resize command from {addr}: {line} ({e})")
                            else:
                                # Process remaining part as regular input if not empty
                                self._process_input_buffer(line, addr, probe)

                        # Process remaining characters in buffer (no newline yet)
                        buffer = self._process_input_buffer(buffer, addr, probe)
                    elif buffer == '\x1b':
                        # Nothing followed the Escape within the select timeout: it was the key.
                        self._emit_key('\x1b', addr)
                        buffer = ""

                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        trace(f"Error reading from client {addr}: {e}")
                    break
        
        except Exception as e:
            error(f"Fatal error in input handler for {addr}: {e}")
            trace(traceback.format_exc())
        finally:
            with self.clients_lock:
                if client_socket in self.clients:
                    self._drop_client(client_socket, f"{addr} disconnected during input handling")
