import logging
from DLNA.protocol import DLNAProtocol
from DLNA.server import Service
from DLNA.renderer import Renderer
from DLNA.utils import Setting

logger = logging.getLogger("DLNAServer")
# logger.setLevel(logging.DEBU G)
logger.setLevel(logging.INFO)

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
            def __init__(self, server, external_renderer=None):
                super().__init__()
                self.server = server
                self.current_url = None
                self.current_title = None
                self.external_renderer = external_renderer
            
            def set_media_url(self, uri, title="0"):
                self.current_url = uri
                self.current_title = title
                self.server._notify_cast(self.current_url, self.current_title)
            
            def set_media_pause(self):
                logger.info("调用 set_media_pause")
                if self.external_renderer:
                    self.external_renderer.set_media_pause()
            
            def set_media_resume(self):
                logger.info("调用 set_media_resume")
                if self.external_renderer:
                    self.external_renderer.set_media_resume()
            
            def set_media_stop(self):
                logger.info("调用 set_media_stop")
                if self.external_renderer:
                    self.external_renderer.set_media_stop()
            
            def set_media_volume(self, volume):
                logger.info(f"调用 set_media_volume, volume={volume}")
                if self.external_renderer:
                    self.external_renderer.set_media_volume(volume)
            
            def set_media_mute(self, mute):
                logger.info(f"调用 set_media_mute, mute={mute}")
                if self.external_renderer:
                    self.external_renderer.set_media_mute(mute)
            
            def set_media_position(self, position):
                logger.info(f"调用 set_media_position, position={position}")
                if self.external_renderer:
                    self.external_renderer.set_media_position(position)
        
        try:
            # 确保设置了正确的名称
            Setting.temp_friendly_name = self.name
            
            # 获取外部渲染器 - 这个会从app.py中获取，由于无法直接引用，
            # 我们使用传入的外部渲染器实例
            external_renderer = None
            # 判断是否已经被调用时设置了外部渲染器
            for attr_name in dir(self):
                if attr_name == 'mpv_dlna_renderer':
                    external_renderer = getattr(self, 'mpv_dlna_renderer')
                    break
            
            # 创建服务实例
            self.renderer = CustomRenderer(self, external_renderer)
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