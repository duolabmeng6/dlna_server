import logging
from DLNA.protocol import DLNAProtocol
from DLNA.server import Service
from DLNA.renderer import Renderer
from DLNA.utils import Setting

logger = logging.getLogger("DLNAServer")
logger.setLevel(logging.DEBUG)

class DLNAServer:
    def __init__(self, name="DLNA Server"):
        self.name = name
        self.service = None
        self.renderer = None
        self.cast_callbacks = []
        self.running = False
        # 初始化设置设备名称
        Setting.temp_friendly_name = name
    
    def add_cast_callback(self, callback):
        """添加投屏回调函数"""
        if callback not in self.cast_callbacks:
            self.cast_callbacks.append(callback)
    
    def remove_cast_callback(self, callback):
        """移除投屏回调函数"""
        if callback in self.cast_callbacks:
            self.cast_callbacks.remove(callback)
    
    def _notify_cast(self, url, title):
        """通知所有回调函数"""
        for callback in self.cast_callbacks:
            try:
                callback(url, title)
            except Exception as e:
                logger.error(f"Cast callback error: {e}")
    
    def set_name(self, name):
        """设置设备名称"""
        self.name = name
        Setting.temp_friendly_name = name
        # 如果服务正在运行，需要重启服务以应用新名称
        if self.running:
            self.restart()
    
    def restart(self):
        """重启服务器"""
        if self.running:
            self.stop()
        self.start()
    
    def start(self):
        """启动 DLNA 服务器"""
        if self.running:
            return
        
        # 创建自定义 Renderer
        class CustomRenderer(Renderer):
            def __init__(self, server):
                super().__init__()
                self.server = server
                self.current_url = None
                self.current_title = None
                self._notified = False  # 添加标志位，避免重复通知
            
            def set_media_url(self, uri, start="0"):
                if uri == self.current_url:  # 如果URL没有变化，不处理
                    return
                self.current_url = uri
                # self._notified = False  # 重置通知标志
                # if self.current_title:  # 如果标题已经设置，通知投屏
                #     self._notify_if_ready()
            
            def set_media_title(self, title):
                if title == self.current_title:  # 如果标题没有变化，不处理
                    return
                self.current_title = title
                self._notified = False  # 重置通知标志
                if self.current_url:  # 如果URL已经设置，通知投屏
                    self._notify_if_ready()
            
            def _notify_if_ready(self):
                """当URL和标题都准备好时发送通知"""
                if not self._notified and self.current_url and self.current_title:
                    self.server._notify_cast(self.current_url, self.current_title)
                    self._notified = True  # 设置通知标志，避免重复通知
        
        try:
            # 确保设置了正确的名称
            Setting.temp_friendly_name = self.name
            
            # 创建服务实例
            self.renderer = CustomRenderer(self)
            protocol = DLNAProtocol()
            self.service = Service(renderer=self.renderer, protocol=protocol)
            self.running = True
            
            # 启动服务
            self.service.run()
            
        except Exception as e:
            logger.error(f"Failed to start DLNA server: {e}")
            self.running = False
            raise
    
    def stop(self):
        """停止 DLNA 服务器"""
        if not self.running:
            return
        
        try:
            if self.service:
                self.service.stop()
            self.running = False
            self.service = None
            self.renderer = None
        except Exception as e:
            logger.error(f"Failed to stop DLNA server: {e}")
            raise 