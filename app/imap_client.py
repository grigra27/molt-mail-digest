import imaplib
from typing import List, Tuple, Optional


class ImapClient:
    def __init__(self, host: str, port: int, user: str, password: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self._imap: Optional[imaplib.IMAP4_SSL] = None

    def __enter__(self) -> "ImapClient":
        imap = imaplib.IMAP4_SSL(self.host, self.port)
        imap.login(self.user, self.password)
        self._imap = imap
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._imap is not None:
            try:
                self._imap.logout()
            except Exception:
                pass

    @property
    def imap(self) -> imaplib.IMAP4_SSL:
        if self._imap is None:
            raise RuntimeError("IMAP not connected")
        return self._imap

    def select_folder(self, folder: str) -> str:
        # Returns UIDVALIDITY (if provided)
        typ, data = self.imap.select(folder, readonly=True)
        if typ != "OK":
            raise RuntimeError(f"Cannot select folder {folder}: {data}")

        # Try to read UIDVALIDITY
        typ2, data2 = self.imap.response("UIDVALIDITY")
        if typ2 == "OK" and data2 and isinstance(data2[0], bytes):
            return data2[0].decode(errors="ignore")
        return ""

    def fetch_uids_since(self, last_uid: int, max_results: int) -> List[int]:
        # Search by UID range
        criteria = f"(UID {last_uid + 1}:*)"
        typ, data = self.imap.uid("SEARCH", None, criteria)
        if typ != "OK":
            raise RuntimeError(f"UID SEARCH failed: {data}")

        if not data or not data[0]:
            return []

        uids = [int(x) for x in data[0].split()]
        # Take newest last (keep order increasing)
        if len(uids) > max_results:
            uids = uids[-max_results:]
        return uids

    def fetch_rfc822(self, uid: int) -> bytes:
        typ, data = self.imap.uid("FETCH", str(uid), "(RFC822)")
        if typ != "OK" or not data or not data[0]:
            raise RuntimeError(f"UID FETCH failed for {uid}: {data}")
        # data[0] is like (b'123 (RFC822 {bytes}', b'...raw...')
        raw = data[0][1]
        return raw
