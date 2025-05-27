import logging
from DLNA.protocol import DLNAProtocol
from DLNA.server import Service
from DLNA.renderer import Renderer
from lxml import etree

logger = logging.getLogger("Macast")
logger.setLevel(logging.DEBUG)

def start_server():
    # 创建一个基础的 renderer 实例
    class SimpleRenderer(Renderer):
        current_url = None
        current_title = None
        
        def __init__(self):
            super().__init__()
        
        def set_media_url(self, uri, start="0"):
            self.current_url = uri
            
        def set_media_title(self, title):
            self.current_title = title
            print(f"Media Info - URL: {self.current_url}, Title: {self.current_title}")
            
    renderer = SimpleRenderer()
    protocol = DLNAProtocol()
    service = Service(renderer=renderer, protocol=protocol)
    
    try:
        # 直接运行服务（阻塞模式）
        service.run()
    except KeyboardInterrupt:
        service.stop()

if __name__ == '__main__':
    start_server()
