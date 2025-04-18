
from dataclasses import dataclass
from typing import Optional
import imaplib

@dataclass
class TradeSignal:
    action: str
    symbol: str
    quantity: float

@dataclass
class Bot:
    name: str
    exchange: str
    symbol: str
    quantity: float
    position: str = "neutral"
    paused: bool = False
    api_key: str = None
    api_secret: str = None
    account_id: str = None
    login: str = None
    password: str = None
    server: str = None
    slopping: int = None
    deviation: int = None
    magic_number: int = None
    email_address: str = None
    email_password: str = None
    imap_server: str = None
    email_subject: str = None
    imap_session: imaplib.IMAP4_SSL = None
    monitoring_task = None
