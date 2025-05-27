#
# Cherrypy插件
# Cherrypy使用插件来运行后台线程
#

from cherrypy.process import plugins
import logging
import threading

from .ssdp import SSDPServer
from .utils import Setting

logger = logging.getLogger("PLUGIN")


class RendererPlugin(plugins.SimplePlugin):
    """运行后台播放器线程
    """

    def __init__(self, bus, renderer):
        logger.info('初始化RenderPlugin')
        super(RendererPlugin, self).__init__(bus)
        self.renderer = renderer

    def start(self):
        """启动RenderPlugin
        """
        logger.info('正在启动RenderPlugin')
        self.renderer.start()
        self.bus.subscribe('reload_renderer', self.renderer.reload)
        self.bus.subscribe('get_renderer', self.get_renderer)
        self.bus.subscribe('set_renderer', self.set_renderer)
        for method in self.renderer.methods():
            self.bus.subscribe(method, getattr(self.renderer, method))

    def stop(self):
        """停止RenderPlugin
        """
        logger.info('正在停止RenderPlugin')
        self.bus.unsubscribe('reload_renderer', self.renderer.reload)
        self.bus.unsubscribe('get_renderer', self.get_renderer)
        self.bus.unsubscribe('set_renderer', self.set_renderer)
        for method in self.renderer.methods():
            self.bus.unsubscribe(method, getattr(self.renderer, method))
        self.renderer.stop()

    def get_renderer(self):
        return self.renderer

    def set_renderer(self, renderer):
        self.stop()
        self.renderer = renderer
        self.start()


class ProtocolPlugin(plugins.SimplePlugin):
    """运行后台协议线程
    """

    def __init__(self, bus, protocol):
        logger.info('初始化ProtocolPlugin')
        super(ProtocolPlugin, self).__init__(bus)
        self.protocol = protocol

    def reload_protocol(self):
        """重新加载协议
        """
        self.protocol.stop()
        self.protocol.start()

    def start(self):
        """启动ProtocolPlugin
        """
        logger.info('正在启动ProtocolPlugin')
        self.protocol.start()
        self.bus.subscribe('reload_protocol', self.protocol.reload)
        self.bus.subscribe('get_protocol', self.get_protocol)
        self.bus.subscribe('set_protocol', self.set_protocol)
        for method in self.protocol.methods():
            self.bus.subscribe(method, getattr(self.protocol, method))

    def stop(self):
        """停止ProtocolPlugin
        """
        logger.info('正在停止ProtocolPlugin')
        self.bus.unsubscribe('reload_protocol', self.protocol.reload)
        self.bus.unsubscribe('get_protocol', self.get_protocol)
        self.bus.unsubscribe('set_protocol', self.set_protocol)
        for method in self.protocol.methods():
            self.bus.unsubscribe(method, getattr(self.protocol, method))
        self.protocol.stop()

    def get_protocol(self):
        return self.protocol

    def set_protocol(self, protocol):
        self.stop()
        self.protocol = protocol
        self.start()


class SSDPPlugin(plugins.SimplePlugin):
    """运行后台SSDP线程
    """

    def __init__(self, bus):
        logger.info('初始化SSDPPlugin')
        super(SSDPPlugin, self).__init__(bus)
        self.restart_lock = threading.Lock()
        self.ssdp = SSDPServer()
        self.devices = []
        self.build_device_info()

    def build_device_info(self):
        self.devices = [
            'uuid:{}::upnp:rootdevice'.format(Setting.get_usn()),
            'uuid:{}'.format(Setting.get_usn()),
            'uuid:{}::urn:schemas-upnp-org:device:MediaRenderer:1'.format(
                Setting.get_usn()),
            'uuid:{}::urn:schemas-upnp-org:service:RenderingControl:1'.format(
                Setting.get_usn()),
            'uuid:{}::urn:schemas-upnp-org:service:ConnectionManager:1'.format(
                Setting.get_usn()),
            'uuid:{}::urn:schemas-upnp-org:service:AVTransport:1'.format(
                Setting.get_usn())
        ]

    def notify(self):
        """SSDP执行通知
        """
        for device in self.devices:
            self.ssdp.do_notify(device)

    def register(self):
        """注册设备
        """
        for device in self.devices:
            self.ssdp.register(device,
                               device[43:] if device[43:] != '' else device,
                               'http://{{}}:{}/description.xml'.format(Setting.get_port()),
                               Setting.get_server_info(),
                               'max-age=66')

    def unregister(self):
        """注销设备
        """
        for device in self.devices:
            self.ssdp.unregister(device)

    def update_ip(self):
        """更新设备IP地址
        """
        with self.restart_lock:
            self.ssdp.stop(byebye=False)
            self.build_device_info()
            self.register()
            self.ssdp.start()

    def start(self):
        """启动SSDPPlugin
        """
        logger.info('正在启动SSDPPlugin')
        self.register()
        self.ssdp.start()
        self.bus.subscribe('ssdp_notify', self.notify)
        self.bus.subscribe('ssdp_update_ip', self.update_ip)

    def stop(self):
        """停止SSDPPlugin
        """
        logger.info('正在停止SSDPPlugin')
        self.bus.unsubscribe('ssdp_notify', self.notify)
        self.bus.unsubscribe('ssdp_update_ip', self.update_ip)
        with self.restart_lock:
            self.ssdp.stop(byebye=True)
