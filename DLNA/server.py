import os
import random
import sys
import logging
import threading

import cherrypy
import portend
from cherrypy._cpserver import Server
from cherrypy.process.plugins import Monitor

from .utils import Setting, XMLPath, SettingProperty, SETTING_DIR
from .plugin import ProtocolPlugin, RendererPlugin, SSDPPlugin
from .protocol import Protocol

logger = logging.getLogger("server")
logger.setLevel(logging.DEBUG)


def auto_change_port(fun):
    """参见 AutoPortServer"""

    def wrapper(self):
        try:
            return fun(self)
        except portend.Timeout as e:
            logger.error(e)
            bind_host, bind_port = self.bind_addr
            if bind_port == 0:
                raise e
            else:
                self.httpserver = None
                self.bind_addr = (bind_host, 0)
                self.start()

    return wrapper


class AutoPortServer(Server):
    """
    修改后的服务器可以优先使用预设端口（Setting.DEFAULT_PORT）。
    当预设端口或配置文件中的端口无法使用时，
    使用系统随机分配的端口
    """

    @auto_change_port
    def start(self):
        super(AutoPortServer, self).start()

    @auto_change_port
    def _start_http_thread(self):
        try:
            self.httpserver.start()
        except KeyboardInterrupt:
            self.bus.log('<Ctrl-C> 被按下：正在关闭HTTP服务器')
            self.interrupt = sys.exc_info()[1]
            self.bus.exit()
        except SystemExit:
            self.bus.log('SystemExit 被触发：正在关闭HTTP服务器')
            self.interrupt = sys.exc_info()[1]
            self.bus.exit()
            raise
        except Exception:
            self.interrupt = sys.exc_info()[1]
            if 'WinError 10013' in str(self.interrupt):
                self.bus.log('HTTP服务器错误：WinError 10013')
                raise portend.Timeout
            else:
                self.bus.log('HTTP服务器错误：正在关闭',
                             traceback=True, level=40)
                self.interrupt = sys.exc_info()[1]
                self.bus.exit()
                raise


class Service:

    def __init__(self, renderer, protocol):
        self.thread = None
        # 替换默认服务器
        cherrypy.server.unsubscribe()
        cherrypy.server = AutoPortServer()
        cherrypy.server.bind_addr = ('0.0.0.0', Setting.get_port())
        cherrypy.server.subscribe()
        # 启动插件
        self.ssdp_plugin = SSDPPlugin(cherrypy.engine)
        self.ssdp_plugin.subscribe()
        self._renderer = renderer
        self.renderer_plugin = RendererPlugin(cherrypy.engine, renderer)
        self.renderer_plugin.subscribe()
        self._protocol = protocol
        self.protocol_plugin = ProtocolPlugin(cherrypy.engine, protocol)
        self.protocol_plugin.subscribe()
        self.ssdp_monitor_counter = 0  # 每30秒重启一次ssdp
        self.ssdp_monitor = Monitor(cherrypy.engine, self.notify, 3, name="SSDP_NOTIFY_THREAD")
        self.ssdp_monitor.subscribe()
        cherrypy.config.update({
            'log.screen': True,
            # 'log.access_file': os.path.join(SETTING_DIR, 'DLNA.log'),
            # 'log.error_file': os.path.join(SETTING_DIR, 'DLNA.log'),
        })
        # cherrypy.engine.autoreload.files.add(Setting.setting_path)
        cherrypy_config = {
            '/dlna': {
                'tools.staticdir.root': XMLPath.BASE_PATH.value,
                'tools.staticdir.on': True,
                'tools.staticdir.dir': "xml"
            },
            '/assets': {
                'tools.staticdir.root': XMLPath.BASE_PATH.value,
                'tools.staticdir.on': True,
                'tools.staticdir.dir': "assets"
            },
            '/': {
                'request.dispatch': cherrypy.dispatch.MethodDispatcher(),
                'tools.response_headers.on': True,
                'tools.response_headers.headers':
                    [('Content-Type', 'text/xml; charset="utf-8"'),
                     ('Server', Setting.get_server_info())],
            }
        }

        self.cherrypy_application = cherrypy.tree.mount(self.protocol.handler, '/', config=cherrypy_config)
        cherrypy.engine.signals.subscribe()

    @property
    def renderer(self):
        return self._renderer

    @renderer.setter
    def renderer(self, value):
        self._renderer = value
        self.renderer_plugin.set_renderer(self._renderer)

    @property
    def protocol(self):
        return self._protocol

    @protocol.setter
    def protocol(self, value: Protocol):
        self.stop()
        self._protocol = value
        self.protocol_plugin.set_protocol(self._protocol)
        self.cherrypy_application.root = self._protocol.handler
        self._protocol.handler.reload()

    def notify(self):
        """SSDP通知
        使用cherrypy内置插件Monitor来触发此方法
        另见：plugin.py -> class SSDPPlugin -> notify
        """
        self.ssdp_monitor_counter += 1
        if Setting.is_ip_changed() or self.ssdp_monitor_counter == 10:
            self.ssdp_monitor_counter = 0
            cherrypy.engine.publish('ssdp_update_ip')
        cherrypy.engine.publish('ssdp_notify')

    def run(self):
        """启动DLNA线程
        """
        cherrypy.engine.start()
        # 更新当前端口
        _, port = cherrypy.server.bound_addr
        logger.info("服务器当前运行在端口：{}".format(port))
        if port != Setting.get(SettingProperty.ApplicationPort, 0):
            # todo 验证正确性
            usn = Setting.get_usn(refresh=True)
            logger.error("更改usn为：{}".format(usn))
            Setting.set(SettingProperty.ApplicationPort, port)
            name = "DLNA({0:04d})".format(random.randint(0, 9999))
            logger.error("更改名称为：{}".format(name))
            Setting.set_temp_friendly_name(name)
            self.protocol.handler.reload()
            cherrypy.engine.publish('ssdp_update_ip')
        # 服务已启动
        cherrypy.engine.block()
        # 服务已停止
        logger.info("服务已停止")

    def stop(self):
        """停止DLNA线程
        """
        Setting.stop_service()
        if self.thread is not None:
            self.thread.join()

    def run_async(self):
        if Setting.is_service_running():
            return
        self.thread = threading.Thread(target=self.run, name="SERVICE_THREAD")
        self.thread.start()
