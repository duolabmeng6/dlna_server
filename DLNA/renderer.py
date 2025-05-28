import gettext
import logging
import cherrypy

from .protocol import Protocol

logger = logging.getLogger("Renderer")
logger.setLevel(logging.INFO)


class Renderer:
    """媒体渲染器基类
    通过继承此类，
    你可以使用各种播放器作为媒体渲染器
    参见：MPVRender类
    """
    support_platform = set()

    def __init__(self, lang=gettext.gettext):
        global _
        _ = lang
        self.running = False
        self.renderer_setting = RendererSetting()

    def start(self):
        """启动渲染器线程
        """
        self.running = True

    def stop(self):
        """停止渲染器线程
        """
        self.running = False
        cherrypy.engine.publish('renderer_av_stop')

    def reload(self):
        self.stop()
        self.start()

    def methods(self):
        return list(filter(lambda m: m.startswith('set_media_') and callable(getattr(self, m)), dir(self)))

    @property
    def protocol(self) -> Protocol:
        protocols = cherrypy.engine.publish('get_protocol')
        if len(protocols) == 0:
            logger.error("无法找到可用的协议。")
            return Protocol()
        return protocols.pop()

    # 如果你想编写一个适配其他视频播放器的新渲染器，
    # 请重写以下方法来控制你使用的视频播放器。
    # 详情请参考 DLNA_renderer/mpv.py:MPVRender

    def set_media_stop(self):
        pass

    def set_media_pause(self):
        pass

    def set_media_resume(self):
        pass

    def set_media_volume(self, data):
        """ data : 整数，范围从0到100
        """
        pass

    def set_media_mute(self, data):
        """ data : 布尔值
        """
        pass

    def set_media_url(self, url: str, title: str = ""):
        """
        :param url: 媒体URL
        :param title: 媒体标题
        :return:
        """
        pass

    def set_media_title(self, data):
        """ data : 字符串
        """
        pass

    def set_media_position(self, data):
        """ data : 字符串位置，格式为00:00:00
        """
        pass

    def set_media_sub_file(self, data):
        """ 设置字幕文件路径
        :param data: {'url': '/home/ubuntu/danmaku.ass',
                      'title': 'danmaku'}
        :return:
        """
        pass

    def set_media_sub_show(self, data: bool):
        """ 设置字幕可见性
        :param data: 布尔值
        :return:
        """
        pass

    def set_media_text(self, data: str, duration: int = 1000):
        """ 在视频播放器屏幕上显示文本
        :param data: 字符串，文本内容
        :param duration: 毫秒
        :return:
        """
        pass

    def set_media_speed(self, data: float):
        pass

    # 以下方法通常用于根据从播放器获得的状态更新DLNA渲染器的状态。
    # 因此，当播放器状态发生变化时，调用以下方法。
    # 例如，当你点击播放器的暂停按钮时，
    # 调用self.set_state('TransportState', 'PAUSED_PLAYBACK')
    # 然后，DLNA客户端（如你的手机）将
    # 自动获取此信息并更新到前端。

    def set_state_position(self, data: str):
        """
        :param data: 字符串，例如：00:00:00
        :return:
        """
        self.protocol.set_state_position(data)

    def set_state_duration(self, data: str):
        """
        :param data: 字符串，例如：00:00:00
        :return:
        """
        self.protocol.set_state_duration(data)

    def set_state_pause(self):
        self.protocol.set_state_pause()

    def set_state_play(self):
        self.protocol.set_state_play()

    def set_state_stop(self):
        self.protocol.set_state_stop()

    def set_state_eof(self):
        self.protocol.set_state_eof()

    def set_state_transport(self, data: str):
        """
        :param data: 字符串，可选值：[PLAYING, PAUSED_PLAYBACK, STOPPED, NO_MEDIA_PRESENT]
        :return:
        """
        self.protocol.set_state_transport(data)

    def set_state_transport_error(self):
        """
        :return:
        """
        self.protocol.set_state_transport_error()

    def set_state_mute(self, data: bool):
        """
        :param data: 布尔值
        :return:
        """
        self.protocol.set_state_mute(data)

    def set_state_volume(self, data: int):
        """
        :param data: 整数，范围从0到100
        :return:
        """
        self.protocol.set_state_volume(data)

    def set_state_speed(self, data: str):
        self.protocol.set_state_speed(data)

    def set_state_subtitle(self, data: bool):
        self.protocol.set_state_display_subtitle(data)

    def set_state_url(self, data: str):
        self.protocol.set_state_url(data)

    def set_state(self, state_name, state_value):
        self.protocol.set_state(state_name, state_value)

    def get_state(self, state_name):
        return self.protocol.get_state(state_name)


class RendererSetting:
    """ Dummy menu settings class
    """

    def build_menu(self):
        return []
