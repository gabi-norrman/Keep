import json
import threading
from typing import Optional
from urllib.parse import urlencode
from assistant.SystemNote import system_note

from ...SingletonMixin import SingletonMixin
from common.logging import info, error, trace, fine
from ....tools.ImageAnalyzer import resize_to_jpeg

DESIRED_KEYS = [
    "resJPEGMedRes", "resJPEGMedWidth", "resJPEGMedHeight",
    "resJPEGFullRes", "resJPEGFullWidth", "resJPEGFullHeight",
    "resOriginalRes", "resOriginalWidth", "resOriginalHeight",
]

DOWNLOAD_FIELD_PREFERENCE = ["resJPEGMedRes", "resJPEGFullRes", "resOriginalRes"]

# Video records carry several renditions (probed against CloudKit 2026-09-02/03):
# resVidMedRes is a 720p MP4 at ~0.45 MB/s, resVidSmallRes a 360p MP4 at ~0.13 MB/s, resVidLargeRes
# a 1080p MOV that only sub-second clips keep under the cap — it is the last resort for masters that
# carry nothing else. Originals are never requested. A slo-mo clip is stored as an adjustment, so its
# renditions sit on the CPLAsset record while the CPLMaster has only the original and the posters;
# the asset record is looked up when the master yields no rendition.
VIDEO_RENDITION_PREFERENCE = ["resVidMed", "resVidSmall", "resVidLarge"]
VIDEO_DESIRED_KEYS = [f"{prefix}{suffix}" for prefix in VIDEO_RENDITION_PREFERENCE
                      for suffix in ("Res", "FileType", "Width", "Height")]
VIDEO_FILE_TYPE_MIME = {
    "public.mpeg-4": "video/mp4",
    "com.apple.quicktime-movie": "video/quicktime",
}
DEFAULT_VIDEO_MIME = "video/mp4"
# Video masters also carry poster-frame JPEGs, already upright. resJPEGThumbRes is ~300 px on the
# short side; resJPEGMedRes is a full-size fallback.
POSTER_FIELD_PREFERENCE = ["resJPEGThumbRes", "resJPEGMedRes"]
# Telegram wants a video thumbnail as JPEG, at most 320 px per side and under 200 kB.
POSTER_MAX_DIM = 320
# A 720p rendition is up to ~135 MB at the default 300 s duration cap; this is the read timeout
# per chunk, not for the whole body.
VIDEO_DOWNLOAD_TIMEOUT = 120

FALLBACK_PRIMARY_ZONE = {"zoneName": "PrimarySync", "zoneType": "REGULAR_CUSTOM_ZONE"}


class CorruptImageError(Exception):
    """Raised when image data is permanently corrupt (e.g. truncated)."""
    pass


class PhotoNotInCloudError(Exception):
    """Raised when CloudKit definitively reports the photo doesn't exist in any zone."""
    pass


class VideoTooLargeError(Exception):
    """Raised when even the smallest video rendition is above the caller's size cap."""

    def __init__(self, smallest_size: int, max_bytes: int):
        self.smallest_size = smallest_size
        self.max_bytes = max_bytes
        super().__init__(f"smallest video rendition is {smallest_size / 1024 / 1024:.1f} MB, "
                         f"cap is {max_bytes / 1024 / 1024:.0f} MB")


class NoVideoRenditionError(Exception):
    """Raised when the clip's iCloud records exist but none carries a video rendition."""

    def __init__(self, record_names: list[str]):
        self.record_names = record_names
        super().__init__(f"no video rendition on {' or '.join(record_names)}")


# Exceptions that carry a verdict about the asset itself and must reach the caller untouched.
_PASSTHROUGH_ERRORS = (CorruptImageError, PhotoNotInCloudError, VideoTooLargeError, NoVideoRenditionError)


_RECORD_NOT_FOUND = object()


class ICloudPhotoDownloader(SingletonMixin):
    """Downloads photo JPEGs from iCloud Photos via CloudKit using the ICloudIntegration session."""

    def __init__(self):
        self._service_endpoint: Optional[str] = None
        self._session = None
        self._params: Optional[dict] = None
        self._primary_zone: Optional[dict] = None
        self._shared_zone: Optional[dict] = None
        self._ready = False
        self._not_configured = False
        self._init_lock = threading.Lock()

    def _ensure_service(self) -> bool:
        """Initialize CloudKit session from ICloudIntegration. Returns True if ready."""
        if self._ready:
            return True
        if self._not_configured:
            return False

        with self._init_lock:
            if self._ready:
                return True
            if self._not_configured:
                return False

            from ..ICloudIntegration import ICloudIntegration, iCloudConfigurationError
            try:
                api = ICloudIntegration.get_instance().api
            except iCloudConfigurationError as e:
                # ICloudIntegration.get_instance() re-raises on every call when config is
                # missing (a failed __init__ is never cached) — remember it here so we
                # don't retry and log this per photo, per retry cycle.
                info(f"ICloudPhotoDownloader: iCloud not configured ({e}) — "
                     f"API downloads disabled, using brctl fallback only")
                self._not_configured = True
                return False
            if not api:
                trace("ICloudPhotoDownloader: iCloud API not available")
                return False

            try:
                try:
                    info("ICloudPhotoDownloader: requesting PCS authorization for Photos...")
                    api._request_pcs_for_service("photos")
                    info("ICloudPhotoDownloader: PCS authorized for Photos")
                except Exception as pcs_err:
                    # PCS is only required for Advanced Data Protection accounts.
                    # Non-ADP accounts may get 422 from the setup endpoint — safe to continue.
                    info(f"ICloudPhotoDownloader: PCS authorization skipped ({pcs_err}), "
                         f"proceeding without PCS")

                service_root = api.get_webservice_url("ckdatabasews")
                endpoint = f"{service_root}/database/1/com.apple.photos.cloud/production/private"
                session = api.session
                params = dict(api.params)
                params["dsid"] = api.data["dsInfo"]["dsid"]
                params["remapEnums"] = True
                params["getCurrentSyncToken"] = True

                self._service_endpoint = endpoint
                self._session = session
                self._params = params
                trace("ICloudPhotoDownloader: CloudKit service initialized")

                self._discover_zones()
                self._ready = True
                return True
            except Exception as e:
                error(f"ICloudPhotoDownloader: failed to initialize CloudKit service: {e}")
                self._reset()
                return False

    def _discover_zones(self):
        """Discover CloudKit zones — store full zone IDs including ownerRecordName."""
        try:
            url = f"{self._service_endpoint}/changes/database?{urlencode(self._params)}"
            resp = self._session.post(url, data="{}", headers={"Content-Type": "plain/text"})
            if resp.status_code != 200:
                trace(f"ICloudPhotoDownloader: zone discovery failed, HTTP {resp.status_code}")
                return
            data = resp.json()
            for zone in data.get("zones", []):
                if zone.get("deleted"):
                    continue
                zone_id = zone.get("zoneID", {})
                zone_name = zone_id.get("zoneName", "")
                if zone_name == "PrimarySync":
                    self._primary_zone = zone_id
                elif "SharedSync" in zone_name:
                    self._shared_zone = zone_id
                    trace(f"ICloudPhotoDownloader: discovered shared library zone: {zone_name}")
            if not self._primary_zone:
                info("ICloudPhotoDownloader: PrimarySync not in zone list, using fallback")
                self._primary_zone = FALLBACK_PRIMARY_ZONE
            if not self._shared_zone:
                trace("ICloudPhotoDownloader: no shared library zone found")
        except Exception as e:
            trace(f"ICloudPhotoDownloader: zone discovery error: {e}")
            if not self._primary_zone:
                self._primary_zone = FALLBACK_PRIMARY_ZONE

    @staticmethod
    def _is_auth_error(exc: Exception) -> bool:
        """Check if an exception indicates an authentication/authorization failure."""
        msg = str(exc).lower()
        return "unauthorized" in msg or "401" in msg or "403" in msg

    @staticmethod
    def _notify_session_refresh_failed(exc: Exception):
        """Leave a system note when the iCloud session refresh fails (may need 2FA)."""
        system_note("icloud_photos",
            "iCloud Photos: session refresh failed — photo downloads are broken. "
            "You may need to complete 2FA in the Keep TUI console (iCloud tab). "
            f"Error: {exc}"
        )

    def _reset(self):
        self._ready = False
        self._session = None
        self._service_endpoint = None
        self._params = None
        self._primary_zone = None
        self._shared_zone = None

    def download_photo_bytes(self, cloud_master_guid: str, library_scope: int = 0) -> Optional[bytes]:
        """Download photo bytes from iCloud via CloudKit records/lookup.
        Tries SharedSync first (most photos live there), falls back to PrimarySync.
        Returns resized JPEG bytes, or None on failure."""
        return self._with_session_retry(cloud_master_guid, lambda: self._do_download(cloud_master_guid))

    def download_video_bytes(self, cloud_master_guid: str, library_scope: int = 0,
                             max_bytes: int = 20 * 1024 * 1024,
                             cloud_asset_guid: Optional[str] = None) -> Optional[tuple[bytes, str]]:
        """Download the largest video rendition that fits *max_bytes* (720p, else 360p, else 1080p),
        untouched. The master record is tried first, then the asset record named by
        *cloud_asset_guid* (where slo-mo clips keep their renditions). Returns (raw bytes, mime
        type), or None on a transient failure. Raises VideoTooLargeError when even the smallest
        rendition is above the cap, NoVideoRenditionError when the records exist but carry no
        video rendition, and PhotoNotInCloudError when the record is gone."""
        return self._with_session_retry(
            cloud_master_guid,
            lambda: self._do_download_video(cloud_master_guid, max_bytes, cloud_asset_guid))

    def download_video_poster(self, cloud_master_guid: str, library_scope: int = 0) -> Optional[bytes]:
        """The clip's poster frame as a JPEG no larger than POSTER_MAX_DIM per side (a chat
        thumbnail), or None when the record has no poster or the download failed. Raises
        PhotoNotInCloudError when the record is gone."""
        return self._with_session_retry(
            cloud_master_guid, lambda: self._do_download_poster(cloud_master_guid))

    def _with_session_retry(self, record_name: str, download):
        """Run *download* against the CloudKit session; on a 401/403 refresh the iCloud session
        and retry once. Asset verdicts (corrupt / not in cloud / too large) pass through."""
        if not self._ensure_service():
            return None

        try:
            return download()
        except _PASSTHROUGH_ERRORS:
            raise
        except Exception as e:
            if not self._is_auth_error(e):
                error(f"ICloudPhotoDownloader: failed to download {record_name}: {e}")
                self._reset()
                return None

            # Auth error (401/403) — refresh iCloud session and retry once
            from ..ICloudIntegration import ICloudIntegration
            icloud = ICloudIntegration.get_instance()
            if icloud._api and icloud._api.requires_2fa:
                # Refreshing here would race an in-flight 2FA flow, but doing
                # nothing leaves photos broken until someone notices — kick the
                # automatic SMS 2FA flow instead (it no-ops if a code is already
                # pending, and is rate-limited).
                info("ICloudPhotoDownloader: auth error while 2FA is pending — "
                     "triggering automatic SMS 2FA flow instead of a session refresh")
                icloud.on_needs_2fa()
                self._reset()
                return None

            info(f"ICloudPhotoDownloader: auth error ({e}), refreshing iCloud session...")
            self._reset()
            try:
                icloud.refresh_connection()
            except Exception as refresh_err:
                error(f"ICloudPhotoDownloader: session refresh failed: {refresh_err}")
                self._notify_session_refresh_failed(refresh_err)
                return None

            if not self._ensure_service():
                return None
            try:
                return download()
            except _PASSTHROUGH_ERRORS:
                raise
            except Exception as retry_err:
                error(f"ICloudPhotoDownloader: retry failed after session refresh: {retry_err}")
                self._reset()
                return None

    def _do_download(self, cloud_master_guid: str) -> Optional[bytes]:
        """Perform the actual CloudKit lookup and download."""
        download_url = self._lookup_in_zones(cloud_master_guid, self._lookup_download_url)
        if not download_url:
            return None

        fine(f"iCloud download URL: {download_url}")
        raw_bytes = self._download_raw(download_url)
        if not raw_bytes:
            return None

        return self._resize_to_jpeg(raw_bytes)

    def _do_download_video(self, cloud_master_guid: str, max_bytes: int,
                           cloud_asset_guid: Optional[str]) -> Optional[tuple[bytes, str]]:
        record_names = [cloud_master_guid]
        if cloud_asset_guid and cloud_asset_guid != cloud_master_guid:
            record_names.append(cloud_asset_guid)

        rendition = None
        for record_name in record_names:
            fields = self._lookup_in_zones(
                record_name,
                lambda name, zone: self._lookup_record_fields(name, zone, VIDEO_DESIRED_KEYS))
            if fields is None:
                return None
            rendition = self._pick_video_rendition(fields, max_bytes)
            if rendition is not None:
                break
            trace(f"ICloudPhotoDownloader: no video rendition on {record_name}"
                  + (", trying the asset record" if record_name != record_names[-1] else ""))
        if rendition is None:
            raise NoVideoRenditionError(record_names)

        prefix, url, size, mime = rendition
        info(f"ICloudPhotoDownloader: using {prefix}Res ({size / 1024 / 1024:.1f} MB, {mime}) "
             f"for {record_name}")

        raw_bytes = self._download_raw(url, timeout=VIDEO_DOWNLOAD_TIMEOUT, stream=True)
        if not raw_bytes:
            return None
        return raw_bytes, mime

    def _do_download_poster(self, cloud_master_guid: str) -> Optional[bytes]:
        fields = self._lookup_in_zones(
            cloud_master_guid,
            lambda name, zone: self._lookup_record_fields(name, zone, POSTER_FIELD_PREFERENCE))
        if not fields:
            return None
        for field_name in POSTER_FIELD_PREFERENCE:
            url = ((fields.get(field_name) or {}).get("value") or {}).get("downloadURL")
            if url:
                break
        else:
            trace(f"ICloudPhotoDownloader: no poster frame on {cloud_master_guid}")
            return None
        raw_bytes = self._download_raw(url)
        if not raw_bytes:
            return None
        poster = resize_to_jpeg(raw_bytes, max_dim=POSTER_MAX_DIM)
        if poster:
            fine(f"ICloudPhotoDownloader: poster from {field_name} ({len(poster) / 1024:.0f} kB) "
                 f"for {cloud_master_guid}")
        return poster

    @staticmethod
    def _pick_video_rendition(fields: dict, max_bytes: int) -> Optional[tuple[str, str, int, str]]:
        """The first rendition in VIDEO_RENDITION_PREFERENCE whose CloudKit `size` fits the cap,
        as (prefix, download URL, size, mime). None when the record has no video renditions;
        VideoTooLargeError when it has some but none fit."""
        available = []
        for prefix in VIDEO_RENDITION_PREFERENCE:
            value = (fields.get(f"{prefix}Res") or {}).get("value") or {}
            url = value.get("downloadURL")
            if not url:
                continue
            size = int(value.get("size") or 0)
            file_type = (fields.get(f"{prefix}FileType") or {}).get("value")
            mime = VIDEO_FILE_TYPE_MIME.get(file_type, DEFAULT_VIDEO_MIME)
            available.append((prefix, url, size, mime))
        fine(f"ICloudPhotoDownloader: video renditions "
             f"{', '.join(f'{p}Res={s / 1024 / 1024:.1f}MB' for p, _, s, _ in available) or 'none'}")

        if not available:
            return None
        for rendition in available:
            if rendition[2] <= max_bytes:
                return rendition
        raise VideoTooLargeError(min(size for _, _, size, _ in available), max_bytes)

    def _lookup_in_zones(self, record_name: str, lookup):
        """Try *lookup(record_name, zone)* across the zones in priority order. Returns the first
        non-empty result, else an empty result from a zone that has the record (the other zone
        may hold a stub of a shared-library record with none of the desired keys), None if every
        zone failed, and raises PhotoNotInCloudError when every zone reported the record as
        not found."""
        zones_to_try = self._zone_order()
        all_not_found = bool(zones_to_try)
        empty_result = None
        for zone in zones_to_try:
            result = lookup(record_name, zone)
            if result is _RECORD_NOT_FOUND:
                continue
            all_not_found = False
            if result:
                return result
            if result is not None:
                empty_result = result

        if all_not_found:
            raise PhotoNotInCloudError(f"{record_name} not found in any iCloud zone")
        return empty_result

    def _zone_order(self) -> list[dict]:
        """Return zones to try in priority order. SharedSync first since most photos
        live there and ZLIBRARYSCOPE is often NULL even for shared library photos."""
        return [z for z in [self._shared_zone, self._primary_zone] if z]

    def _lookup_download_url(self, record_name: str, zone: dict) -> Optional[str]:
        """Look up a single CloudKit record by recordName, return best download URL."""
        fields = self._lookup_record_fields(record_name, zone, DESIRED_KEYS)
        if fields is _RECORD_NOT_FOUND or fields is None:
            return fields

        for field_name in DOWNLOAD_FIELD_PREFERENCE:
            field = fields.get(field_name)
            if field and field.get("value", {}).get("downloadURL"):
                fine(f"ICloudPhotoDownloader: using {field_name} for {record_name}")
                return field["value"]["downloadURL"]

        trace(f"ICloudPhotoDownloader: no download URL found for {record_name}")
        return None

    def _lookup_record_fields(self, record_name: str, zone: dict, desired_keys: list[str]):
        """Look up a single CloudKit record by recordName. Returns its `fields` dict,
        _RECORD_NOT_FOUND when the zone reports NOT_FOUND, or None on any other failure."""
        url = f"{self._service_endpoint}/records/lookup?{urlencode(self._params)}"

        body = {
            "records": [{"recordName": record_name}],
            "zoneID": zone,
            "desiredKeys": desired_keys,
        }

        resp = self._session.post(url, data=json.dumps(body), headers={"Content-Type": "plain/text"})
        if resp.status_code != 200:
            error(f"ICloudPhotoDownloader: lookup failed, HTTP {resp.status_code}")
            return None

        data = resp.json()
        records = data.get("records", [])
        if not records:
            trace(f"ICloudPhotoDownloader: no records returned for {record_name}")
            return None

        record = records[0]
        if record.get("serverErrorCode"):
            server_error = record["serverErrorCode"]
            if server_error == "NOT_FOUND":
                # Expected on every primary-library photo: SharedSync is probed first. The caller
                # logs when no zone has the record.
                return _RECORD_NOT_FOUND
            trace(f"ICloudPhotoDownloader: {server_error} for "
                  f"{record_name} in {zone.get('zoneName', '?')}")
            return None

        return record.get("fields", {})

    def _download_raw(self, download_url: str, timeout: int = 60, stream: bool = False) -> Optional[bytes]:
        """Download raw bytes from the CloudKit download URL. With *stream* the body is read in
        chunks so a stalled connection fails on the read timeout instead of hanging."""
        resp = self._session.get(download_url, timeout=timeout, stream=stream)
        if resp.status_code != 200:
            error(f"ICloudPhotoDownloader: download failed, HTTP {resp.status_code}")
            return None
        content = b"".join(resp.iter_content(chunk_size=1024 * 1024)) if stream else resp.content
        if len(content) < 100:
            trace("ICloudPhotoDownloader: downloaded content suspiciously small, skipping")
            return None
        return content

    @staticmethod
    def _resize_to_jpeg(raw_bytes: bytes) -> Optional[bytes]:
        """Resize image to max 1024px and convert to JPEG.
        Wraps truncation errors as CorruptImageError for the photo pipeline."""
        try:
            return resize_to_jpeg(raw_bytes)
        except Exception as e:
            if "truncated" in str(e):
                raise CorruptImageError(str(e))
            raise

