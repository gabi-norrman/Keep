"""The asyncio side of the Plejd integration on a thread of its own. It loads the site from the
Plejd cloud through pyplejd (retrying while the cloud is unreachable, giving up on a refused
login or a site it cannot pick), runs the Bluetooth scanner that tells pyplejd which mesh units
are in range, keeps the link alive with pyplejd's ping — which connects to the strongest unit
seen and reconnects after a drop —, syncs the mesh clock hourly, and keeps every device's last
reported state. The sync integration hands it coroutines to run and reads the states.

pyplejd 0.22.0 never hands its disconnect callback to bleak, so after a Bluetooth drop its
client object stays and `manager.connected` stays True on a dead link: every ping and write then
fails on it forever. The link therefore judges the connection by the bleak client itself,
registers its own disconnect callback on each new client, drops a stale client before the next
connect, and watches pyplejd's writes, which swallow their failures."""
import asyncio
import threading
import time
from typing import Optional

from bleak import BleakScanner
from pyplejd import PLEJD_SERVICE, AuthenticationError, PlejdManager, get_sites
from pyplejd import ConnectionError as PlejdCloudUnreachable

from common.logging import info, trace
from .PlejdModel import COVER_TYPE, SCENE_TYPE, kind_of, mac_from_advertisement, state_text

PING_INTERVAL = 180         # pyplejd's own keep-alive period: the mesh drops an idle link
TIME_SYNC_INTERVAL = 3600   # the mesh clock drifts; Home Assistant's component syncs it hourly too
CLOUD_RETRY = 60            # seconds before the site is fetched again when the cloud was unreachable
TICK = 5                    # how long the loop sleeps between looks at its timers
FIRST_SCAN = 6              # seconds of scanning before the first connect, so the strongest unit is known
WEAK_RSSI = -80             # dBm below which a unit makes a link that drops
STOP_TIMEOUT = 10


class SiteChoice(Exception):
    """The account's sites do not give one to use; the message says what to set."""


class NotConnected(Exception):
    """The link's loop is not running, so nothing can be sent."""


class WriteFailed(Exception):
    """pyplejd could not deliver a write to the mesh."""


def choose_site(sites: list[dict], wanted: str) -> dict:
    """The site (`siteId`, `title`, `deviceCount`) *wanted* names by id or title, case-
    insensitively; with nothing wanted, the only site the account has."""
    wanted = (wanted or "").strip()
    names = ", ".join(f'"{s["title"]}"' for s in sites)
    if wanted:
        for site in sites:
            if wanted.lower() in (site["siteId"].lower(), site["title"].lower()):
                return site
        raise SiteChoice(f'The Plejd account has no site "{wanted}"; it has {names or "none"} — set plejd.site to one of them.')
    if len(sites) == 1:
        return sites[0]
    if not sites:
        raise SiteChoice("The Plejd account has no site.")
    raise SiteChoice(f"The Plejd account has {len(sites)} sites — set plejd.site to one of {names}.")


class PlejdLink:
    """One site's cloud data and mesh link, driven by an event loop on a daemon thread."""

    def __init__(self, username: str, password: str, site: str = ""):
        self._credentials = (username, password, site)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._wake: Optional[asyncio.Event] = None
        self._stopping = False
        self._ping_wanted = False
        self._write_failures = 0
        self._ready = threading.Event()         # the site is loaded and the scanner runs
        self._mesh = threading.Event()          # the mesh link is up (cleared on a drop)
        self._lock = threading.Lock()
        self._states: dict[tuple, dict] = {}    # device identifier → its last reported state
        self.manager: Optional[PlejdManager] = None
        self.site_title: Optional[str] = None
        self.error: Optional[str] = None        # why there will be no site: the health check's verdict
        self.waiting = "the Plejd site is still loading"     # why there is none yet
        self.units_seen = 0                     # mesh units of the site heard on the air since start
        self.connected_at: Optional[float] = None
        self.scanning_since: Optional[float] = None     # monotonic time the scanner started

    # --- lifecycle, from any thread -----------------------------------------------------------------

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._thread_main, name="plejd", daemon=True)
        self._thread.start()

    def stop(self):
        self._stopping = True
        self._nudge()
        if self._thread is not None:
            self._thread.join(STOP_TIMEOUT)
            self._thread = None

    def wait_ready(self, timeout: float) -> bool:
        return self._ready.wait(timeout)

    def wait_connected(self, timeout: float) -> bool:
        """Blocks until the mesh link is up or *timeout* passes; whether it is up."""
        return self._mesh.wait(timeout) and self.connected

    def reconnect(self, timeout: float) -> bool:
        """Asks the loop for a connect now and waits up to *timeout* for it; whether the link is up."""
        info("Plejd: no mesh link — trying to reconnect at once")
        self._mesh.clear()
        self.request_ping()
        return self.wait_connected(timeout)

    def scan_time_left(self, window: float) -> float:
        """Seconds of the first *window* seconds of scanning still ahead — 0 once they have
        passed or while the scanner has not started. A fresh scanner hears the units of a
        site one by one over several seconds and the link follows the first one heard, so
        a verdict on the mesh inside that window is early."""
        if self.scanning_since is None:
            return 0.0
        return max(0.0, window - (time.monotonic() - self.scanning_since))

    @property
    def connected(self) -> bool:
        """Whether the mesh link is up — by the Bluetooth client itself, not pyplejd's flag,
        which stays True on a client bleak has lost."""
        if self.manager is None or not self.manager.connected:
            return False
        client = self.manager.mesh._client
        return client is not None and bool(client.is_connected)

    async def send(self, action):
        """Runs *action* (a pyplejd device coroutine) on the loop; WriteFailed when one of its
        writes did not reach the mesh."""
        failures = self._write_failures
        await action()
        if self._write_failures > failures:
            raise WriteFailed("the write did not reach the Plejd mesh")

    @property
    def devices(self) -> list:
        return list(self.manager.devices) if self.manager is not None else []

    def states(self) -> dict[tuple, dict]:
        with self._lock:
            return {key: dict(state) for key, state in self._states.items()}

    def run(self, coro, timeout: float):
        """Runs *coro* on the link's loop and returns its result; TimeoutError past *timeout*,
        NotConnected when the loop is not running."""
        if self._loop is None or not self._loop.is_running():
            coro.close()
            raise NotConnected("the Plejd link is not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise

    def request_ping(self):
        """Asks the loop to ping (connect) at its next turn, from any thread."""
        self._ping_wanted = True
        self._nudge()

    def _nudge(self):
        if self._loop is not None and self._wake is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._wake.set)

    # --- the loop thread ----------------------------------------------------------------------------

    def _thread_main(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as e:
            self.error = f"Plejd stopped: {type(e).__name__}: {e}"
            info(self.error)
        finally:
            self._ready.clear()
            self._mesh.clear()
            self._loop.close()

    async def _main(self):
        self._wake = asyncio.Event()
        manager = await self._load_site()
        if manager is None:
            return
        scanner = BleakScanner(self._on_advertisement, service_uuids=[PLEJD_SERVICE])
        await scanner.start()
        info(f"Plejd: scanning for the units of {self.site_title}")
        self.scanning_since = time.monotonic()
        self._ready.set()
        last_ping = last_sync = 0.0
        try:
            while not self._stopping:
                now = time.monotonic()
                settling = self.connected_at is None and self.scan_time_left(FIRST_SCAN) > 0
                if not settling and (self._ping_wanted or now - last_ping >= PING_INTERVAL):
                    self._ping_wanted, last_ping = False, now
                    await self._ping()
                if self.connected and now - last_sync >= TIME_SYNC_INTERVAL:
                    last_sync = now
                    await self._sync_time()
                await self._sleep(self.scan_time_left(FIRST_SCAN) if settling else TICK)
        finally:
            await scanner.stop()
            if manager.connected:
                await manager.disconnect()
            info("Plejd: link stopped")

    async def _sleep(self, seconds: float):
        """Sleeps unless stop() or request_ping() wakes the loop first."""
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except TimeoutError:
            pass
        self._wake.clear()

    async def _load_site(self) -> Optional[PlejdManager]:
        username, password, wanted = self._credentials
        while not self._stopping:
            try:
                site = choose_site(await get_sites(username, password), wanted)
                manager = PlejdManager(username, password, site["siteId"])
                await manager.init()
            except AuthenticationError as e:
                self.error = f"Plejd cloud login refused: {e} — check plejd.username and plejd.password."
                info(self.error)
                return None
            except SiteChoice as e:
                self.error = str(e)
                info(self.error)
                return None
            except PlejdCloudUnreachable:
                self.waiting = f"the Plejd cloud is unreachable — the site is fetched again every {CLOUD_RETRY} s"
                info(f"Plejd: the cloud is unreachable — trying again in {CLOUD_RETRY} s")
                await self._sleep(CLOUD_RETRY)
                continue
            self.site_title = site["title"]
            manager.connection_monitor = self._on_connection
            self._watch_writes(manager.mesh)
            for device in manager.devices:
                device.subscribe(lambda state, d=device: self._on_state(d, state))
            self.manager = manager
            kinds = [kind_of(d) for d in manager.devices]
            scenes, covers = kinds.count(SCENE_TYPE), kinds.count(COVER_TYPE)
            info(f"Plejd: site {site['title']} loaded — {len(kinds) - scenes - covers} devices, {covers} covers, "
                 f"{scenes} scenes, {len(manager.hardware)} mesh units")
            return manager
        return None

    def _on_advertisement(self, device, advertisement):
        """A unit of the site on the air: pyplejd learns it (with the BLEDevice to connect to),
        and the first one triggers the connect."""
        mac = mac_from_advertisement(advertisement.manufacturer_data)
        hardware = self.manager.hardware.get(mac) if mac and self.manager is not None else None
        if hardware is None:
            return
        if hardware.see(advertisement.rssi, device):
            self.units_seen += 1
            info(f"Plejd: unit {mac} in range (rssi {advertisement.rssi})")
            self.request_ping()

    def _watch_writes(self, mesh):
        """Wraps pyplejd's mesh write, which logs and swallows a failed delivery, so that
        send() can tell and the link gets a reconnect."""
        write = mesh._write

        async def watched(payloads):
            ok = await write(payloads)
            if ok is False:
                self._write_failures += 1
                if not self.connected:
                    self._on_connection(False)
            return ok

        mesh._write = watched

    async def _ping(self):
        if self.units_seen == 0:
            trace("Plejd: no unit of the site in Bluetooth range yet")
            return
        if self.manager.connected and not self.connected:
            info("Plejd: pyplejd holds a Bluetooth client that is no longer connected — dropping it")
            await self._drop_client()
        was_connected = self.connected
        try:
            ok = await self.manager.ping(retry=False)
        except Exception as e:
            info(f"Plejd: ping failed: {type(e).__name__}: {e}")
            ok = False
        ok = ok and self.connected
        if ok and not was_connected:
            self.connected_at = time.time()
            self._watch_client()
            info(f"Plejd: connected to the mesh{self._gateway_text()}")
        elif not ok:
            info("Plejd: the mesh did not answer — reconnecting at the next ping")
            if self.manager.connected:
                await self._drop_client()
                if not was_connected:
                    self._ping_wanted = False   # a unit that connects but does not answer waits for the next ping
        if ok:
            self._mesh.set()
        else:
            self._mesh.clear()

    async def _drop_client(self):
        """Disconnects pyplejd so its next ping makes a real connect; that fires _on_connection(False)."""
        try:
            await self.manager.disconnect()
        except Exception as e:
            info(f"Plejd: disconnecting the dead link failed: {type(e).__name__}: {e}")
        if self.manager.connected:
            self.manager.mesh._client = None
            self.manager.connect_callback(False)

    def _watch_client(self):
        """Registers a disconnect callback on pyplejd's fresh bleak client, which pyplejd
        itself fails to do, so a drop is noticed at once instead of at the next ping."""
        client = self.manager.mesh._client
        backend = getattr(client, "_backend", None)
        if backend is None or not hasattr(backend, "set_disconnected_callback"):
            info("Plejd: cannot watch the Bluetooth client for drops — the keep-alive ping will notice them")
            return

        def dropped():
            if self.manager.mesh._client is client:
                info("Plejd: Bluetooth reports the mesh link lost")
                self._on_connection(False)

        backend.set_disconnected_callback(dropped)

    def _gateway_text(self) -> str:
        """' through unit … (rssi …)' for the unit pyplejd connected to, with a warning when weak."""
        node = getattr(self.manager.mesh, "_gateway_node", None)
        if node is None or node.rssi is None:
            return ""
        heard = sorted((h.rssi for h in self.manager.hardware.values() if h.rssi is not None), reverse=True)
        text = f" through unit {node.BLEaddress} (rssi {node.rssi}, {len(heard)} units heard)"
        if node.rssi < WEAK_RSSI:
            text += f" — weaker than {WEAK_RSSI} dBm, the link may drop"
        return text

    async def _sync_time(self):
        try:
            await self.manager.broadcast_time()
        except Exception as e:
            info(f"Plejd: clock sync failed: {type(e).__name__}: {e}")

    def _on_connection(self, connected: bool):
        if connected:
            return
        if self._mesh.is_set():
            info("Plejd: the mesh link dropped")
        self._mesh.clear()
        if not self._stopping:
            self.request_ping()

    def _on_state(self, device, state: dict):
        with self._lock:
            before = state_text(device, self._states.get(device.identifier, {}))
            self._states[device.identifier] = dict(state)
        after = state_text(device, state)
        if after != before:
            trace(f"Plejd: {device.name} is {after}")
