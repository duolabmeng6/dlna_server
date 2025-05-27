import os
import logging
from .protocol import DLNAProtocol
from .server import Service

logger = logging.getLogger("main")
logger.setLevel(logging.DEBUG)

def cli(renderer=None, protocol=None):
    if renderer is None:
        renderer = DLNAProtocol()
    if protocol is None:
        protocol = DLNAProtocol()
    service = Service(renderer=renderer, protocol=protocol)
    try:
        service.run()
    except KeyboardInterrupt:
        service.stop()
