import os
import sys
import uuid
import json
import time
import logging
import platform
import cherrypy
import subprocess
import socket
import psutil
from enum import Enum



logger = logging.getLogger("Utils")
DEFAULT_PORT = 0
SETTING_DIR = os.path.dirname(os.path.abspath(__file__))


class SettingProperty(Enum):
    USN = 0
    CheckUpdate = 1
    StartAtLogin = 2
    MenubarIcon = 3
    ApplicationPort = 4
    DLNA_FriendlyName = 5
    DLNA_Renderer = 6
    DLNA_Protocol = 7
    Blocked_Interfaces = 8
    Additional_Interfaces = 9


class Setting:
    setting = {}
    version = None
    setting_path = os.path.join(SETTING_DIR, "setting.json")
    last_ip = None
    base_path = None
    friendly_name = "DLNA{})".format(platform.node())
    temp_friendly_name = None
    mpv_default_path = 'mpv'

    @staticmethod
    def save():
        """保存用户设置
        """
        if not os.path.exists(SETTING_DIR):
            os.makedirs(SETTING_DIR)
        with open(Setting.setting_path, "w") as f:
            json.dump(obj=Setting.setting, fp=f, sort_keys=True, indent=4)

    @staticmethod
    def load():
        """加载用户设置
        """
        logger.info("Load Setting")
        Setting.version = "0.0"
        if bool(Setting.setting) is False:
            if not os.path.exists(Setting.setting_path):
                Setting.setting = {}
            else:
                try:
                    with open(Setting.setting_path, "r") as f:
                        Setting.setting = json.load(fp=f)
                    logger.error(Setting.setting)
                except Exception as e:
                    logger.error(e)
        return Setting.setting

    @staticmethod
    def reload():
        Setting.setting = None
        Setting.load()

    @staticmethod
    def get_system_version():
        """获取系统版本
        """
        return str(platform.release())

    @staticmethod
    def get_system():
        """获取系统名称
        """
        return str(platform.system())

    @staticmethod
    def get_version():
        """获取应用程序版本
        """
        return Setting.version

    @staticmethod
    def get_friendly_name():
        """获取应用程序友好名称
        此名称将显示在DLNA客户端的设备搜索列表中
        并作为播放器窗口的默认名称。
        """
        if Setting.temp_friendly_name:
            return Setting.temp_friendly_name
        return Setting.get(SettingProperty.DLNA_FriendlyName, Setting.friendly_name)

    @staticmethod
    def set_temp_friendly_name(name):
        Setting.temp_friendly_name = name

    @staticmethod
    def get_usn(refresh=False):
        """获取设备唯一标识
        """
        dlna_id = str(uuid.uuid4())
        if not refresh:
            dlna_id_temp = Setting.get(SettingProperty.USN, dlna_id)
            if dlna_id == dlna_id_temp:
                Setting.set(SettingProperty.USN, dlna_id)
            return dlna_id_temp
        else:
            Setting.set(SettingProperty.USN, dlna_id)
            return dlna_id

    @staticmethod
    def is_ip_changed():
        if Setting.last_ip != Setting.get_ip():
            return True
        return False

    @staticmethod
    def get_ip():
        last_ip = []
        # 获取所有网络接口
        interfaces = set(Setting.get(SettingProperty.Additional_Interfaces, []))
        
        # 获取所有网络接口信息
        for iface, addrs in psutil.net_if_addrs().items():
            # 检查是否在被阻止的接口列表中
            if iface in Setting.get(SettingProperty.Blocked_Interfaces, []):
                continue
                
            for addr in addrs:
                # 只处理 IPv4 地址
                if addr.family == socket.AF_INET:
                    if addr.address and addr.netmask:
                        last_ip.append((addr.address, addr.netmask))
                        
        Setting.last_ip = set(last_ip)
        logger.debug(Setting.last_ip)
        return Setting.last_ip

    @staticmethod
    def get_port():
        """获取应用程序端口
        """
        return Setting.get(SettingProperty.ApplicationPort, DEFAULT_PORT)

    @staticmethod
    def get(property, default=1):
        """获取应用程序设置
        """
        if not bool(Setting.setting):
            Setting.load()
        if property.name in Setting.setting:
            return Setting.setting[property.name]
        Setting.setting[property.name] = default
        return default

    @staticmethod
    def set(property, data):
        """设置应用程序设置
        """
        Setting.setting[property.name] = data
        Setting.save()

    @staticmethod
    def system_shell(shell):
        result = subprocess.run(shell, stdout=subprocess.PIPE)
        return result.returncode, result.stdout.decode('UTF-8').strip()

    @staticmethod
    def get_base_path(path="."):
        """PyInstaller会创建一个临时文件夹并将路径存储在_MEIPASS中
            https://stackoverflow.com/a/13790741
            另见：https://pyinstaller.readthedocs.io/en/stable/runtime-information.html#run-time-information
        """
        if Setting.base_path is not None:
            return os.path.join(Setting.base_path, path)
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            Setting.base_path = sys._MEIPASS
        else:
            Setting.base_path = os.path.join(os.path.dirname(__file__), '.')
        return os.path.join(Setting.base_path, path)

    @staticmethod
    def get_server_info():
        return '{}/{} UPnP/1.0 DLNA/{}'.format(Setting.get_system(),
                                                 Setting.get_system_version(),
                                                 Setting.get_version())

    @staticmethod
    def get_system_env():
        # Get system env(for GNU/Linux and *BSD).
        # https://pyinstaller.readthedocs.io/en/stable/runtime-information.html#run-time-information
        env = dict(os.environ)
        logger.debug(env)
        lp_key = 'LD_LIBRARY_PATH'
        lp_orig = env.get(lp_key + '_ORIG')
        if lp_orig is not None:
            env[lp_key] = lp_orig
        else:
            env.pop(lp_key, None)
        return env

    @staticmethod
    def stop_service():
        """Stop all DLNA threads
        stop MPV
        stop DLNA HTTP Server
        stop SSDP
        stop SSDP notify thread
        """
        if cherrypy.engine.state in [cherrypy.engine.states.STOPPED,
                                     cherrypy.engine.states.STOPPING,
                                     cherrypy.engine.states.EXITING,
                                     ]:
            return
        while cherrypy.engine.state != cherrypy.engine.states.STARTED:
            time.sleep(0.5)
        cherrypy.engine.exit()

    @staticmethod
    def is_service_running():
        return cherrypy.engine.state in [cherrypy.engine.states.STARTING,
                                         cherrypy.engine.states.STARTED,
                                         ]




class XMLPath(Enum):
    BASE_PATH = os.path.dirname(__file__)
    DESCRIPTION = BASE_PATH + '/xml/Description.xml'
    AV_TRANSPORT = BASE_PATH + '/xml/AVTransport.xml'
    CONNECTION_MANAGER = BASE_PATH + '/xml/ConnectionManager.xml'
    RENDERING_CONTROL = BASE_PATH + '/xml/RenderingControl.xml'
    PROTOCOL_INFO = BASE_PATH + '/xml/SinkProtocolInfo.csv'


def load_xml(path):
    with open(path, encoding="utf-8") as f:
        xml = f.read()
    return xml




