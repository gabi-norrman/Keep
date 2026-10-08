"""Plain stand-ins for the protobuf objects neonize hands the integration: the formatter reads
them by attribute, so a SimpleNamespace with the same field names is all a test needs."""
from types import SimpleNamespace

OWNER = "46701740605"
ALICE = "46701740610"
BOB = "46701740611"
GROUP = "120363000000000001"


def jid(user: str, server: str = "s.whatsapp.net") -> SimpleNamespace:
    return SimpleNamespace(User=user, Server=server, Device=0, RawAgent=0, IsEmpty=False)


def text_message(text: str) -> SimpleNamespace:
    return SimpleNamespace(conversation=text)


def extended_message(text: str) -> SimpleNamespace:
    return SimpleNamespace(conversation="", extendedTextMessage=SimpleNamespace(text=text))


def image_message(caption: str = "", mimetype: str = "image/jpeg") -> SimpleNamespace:
    return SimpleNamespace(conversation="", imageMessage=SimpleNamespace(caption=caption, mimetype=mimetype))


def video_message(caption: str = "") -> SimpleNamespace:
    return SimpleNamespace(conversation="", videoMessage=SimpleNamespace(caption=caption, mimetype="video/mp4"))


def audio_message(seconds: int = 7, ptt: bool = True) -> SimpleNamespace:
    return SimpleNamespace(conversation="", audioMessage=SimpleNamespace(seconds=seconds, PTT=ptt))


def document_message(name: str) -> SimpleNamespace:
    return SimpleNamespace(conversation="", documentMessage=SimpleNamespace(fileName=name, title=""))


def protocol_message() -> SimpleNamespace:
    return SimpleNamespace(conversation="", protocolMessage=SimpleNamespace(type=0))


def ephemeral(inner) -> SimpleNamespace:
    return SimpleNamespace(conversation="", ephemeralMessage=SimpleNamespace(message=inner))


def live_event(message, chat: str, sender: str, from_me: bool = False, push_name: str = "", timestamp: int = 1_700_000_000_000,
               sender_alt=None, edit: str = "", chat_server: str = "s.whatsapp.net", sender_server: str = "s.whatsapp.net"):
    """A MessageEv as the integration reads it; `timestamp` is unix milliseconds, as neonize stamps it."""
    source = SimpleNamespace(Chat=jid(chat, chat_server), Sender=jid(sender, sender_server), SenderAlt=sender_alt,
                             IsFromMe=from_me, IsGroup=chat_server == "g.us")
    info = SimpleNamespace(MessageSource=source, Timestamp=timestamp, Pushname=push_name, Edit=edit, ID="ABC")
    return SimpleNamespace(Info=info, Message=message)


def history_event(conversations, sync_type: int = 3, progress: int = 50, lid_mappings=()):
    """A HistorySyncEv: `conversations` is a list of (chat jid string, title, [(sender or "", from_me, ts, message)])."""
    convs = []
    for chat, title, rows in conversations:
        messages = [SimpleNamespace(message=SimpleNamespace(
            key=SimpleNamespace(remoteJID=chat, fromMe=from_me, participant=sender, ID=f"H{i}"),
            messageTimestamp=ts, pushName="", message=message))
            for i, (sender, from_me, ts, message) in enumerate(rows)]
        convs.append(SimpleNamespace(ID=chat, name=title, displayName="", lidJID="", pnJID="", messages=messages))
    data = SimpleNamespace(conversations=convs, syncType=sync_type, progress=progress,
                           phoneNumberToLidMappings=[SimpleNamespace(pnJID=pn, lidJID=lid) for pn, lid in lid_mappings])
    return SimpleNamespace(Data=data)


class FakeContactStore:
    def __init__(self, contacts: dict):
        """contacts: user → (full name, first name, push name)."""
        self._contacts = contacts

    def get_contact(self, user_jid):
        entry = self._contacts.get(user_jid.User)
        if not entry:
            return SimpleNamespace(Found=False, FullName="", FirstName="", PushName="", BusinessName="")
        full, first, push = entry
        return SimpleNamespace(Found=True, FullName=full, FirstName=first, PushName=push, BusinessName="")

    def get_all_contacts(self):
        return [SimpleNamespace(JID=jid(user), Info=self.get_contact(jid(user))) for user in self._contacts]


class FakeClient:
    """What the integration calls on neonize's client, recorded."""

    def __init__(self, contacts: dict | None = None, groups: dict | None = None, on_whatsapp: set | None = None,
                 lids: dict | None = None):
        self.contact = FakeContactStore(contacts or {})
        self._groups = groups or {}              # group user → title
        self._on_whatsapp = on_whatsapp          # None: everyone asked for is on WhatsApp
        self._lids = lids or {}                  # phone user → lid user, for an account with number privacy on
        self.sent: list[tuple[str, str]] = []
        self.downloads: dict[int, bytes] = {}
        self.logged_in = True
        self.stopped = False

    @property
    def is_logged_in(self):
        return self.logged_in

    def get_group_info(self, group_jid):
        return SimpleNamespace(GroupName=SimpleNamespace(Name=self._groups.get(group_jid.User, "")))

    def get_joined_groups(self):
        return [SimpleNamespace(JID=jid(user, "g.us"), GroupName=SimpleNamespace(Name=title))
                for user, title in self._groups.items()]

    def get_pn_from_lid(self, lid_jid):
        raise RuntimeError("no mapping")

    def is_on_whatsapp(self, *numbers):
        answers = []
        for number in numbers:
            user = number.lstrip("+")
            is_in = self._on_whatsapp is None or user in self._on_whatsapp
            answer_jid = jid(self._lids[user], "lid") if user in self._lids else jid(user)
            answers.append(SimpleNamespace(Query=number, IsIn=is_in, JID=answer_jid))
        return answers

    def send_message(self, to, message, **kwargs):
        self.sent.append((f"{to.User}@{to.Server}", message))
        return SimpleNamespace(ID="SENT1")

    def download_any(self, message):
        return self.downloads.get(id(message), b"")

    def get_me(self):
        return SimpleNamespace(JID=jid(OWNER), PushName="Marcus")

    def stop(self):
        self.stopped = True

    def logout(self):
        self.logged_in = False
